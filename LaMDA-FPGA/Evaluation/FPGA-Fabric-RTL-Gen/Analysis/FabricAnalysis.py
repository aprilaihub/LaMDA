"""
Fabric-aware analysis utilities for the FPGA-Fabric-RTL-Gen pipeline.

This module is fully self-contained: it does not import from, or modify,
Evaluation/ResBench/Analysis/ResBenchAnalysis.py. It implements its own
report parser (FabricReportParser) capable of parsing Vivado's power,
utilization (including the per-primitive "Primitives" table), timing, and
log reports, plus CSV/aggregation helpers dedicated to this pipeline's
results schema.
"""

import csv
import json
import os
import re


class InfEncoder(json.JSONEncoder):
    """Custom JSON encoder that converts float('inf')/float('-inf')/NaN to strings."""

    def encode(self, obj):
        if isinstance(obj, float):
            if obj == float('inf'):
                return '"inf"'
            elif obj == float('-inf'):
                return '"-inf"'
            elif obj != obj:  # NaN check
                return '"NaN"'
        return super().encode(obj)

    def iterencode(self, obj, _one_shot=False):
        for chunk in super().iterencode(obj, _one_shot):
            chunk = chunk.replace('Infinity', '"inf"')
            chunk = chunk.replace('-Infinity', '"-inf"')
            chunk = chunk.replace('NaN', '"NaN"')
            yield chunk


class FabricReportParser:
    """Parses Vivado EDA reports, including per-primitive utilization counts."""

    @staticmethod
    def detect_report_type(content):
        """Detect the type of report from its content."""
        if "Total On-Chip Power" in content:
            return "power"
        elif "Slice LUTs" in content or "LUT as Logic" in content:
            return "utilization"
        elif "Slack" in content and "Data Path Delay" in content:
            return "timing"
        elif "Vivado" in content and "INFO:" in content:
            return "log"
        else:
            return "unknown"

    @staticmethod
    def parse_power_report(content):
        """Parse power report and extract power metrics."""
        power_data = {"type": "power"}
        total_match = re.search(r'Total On-Chip Power.*?([\d\.]+)', content)
        dynamic_match = re.search(r'Dynamic.*?([\d\.]+)', content)
        static_match = re.search(r'Static Power.*?([\d\.]+)', content)
        power_data['Total'] = float(total_match.group(1)) if total_match else None
        power_data['Dynamic'] = float(dynamic_match.group(1)) if dynamic_match else None
        power_data['Static'] = float(static_match.group(1)) if static_match else None
        return power_data

    @staticmethod
    def _extract_resource(content, name):
        """Extract a Slice-Logic style summary row: name | used | fixed | prohibited | available | util%."""
        pattern = rf'{name}\s*\|\s*(\d+)\s*\|\s*\d+\s*\|\s*\d+\s*\|\s*(\d+)\s*\|\s*([<>]?\d+\.\d+)\s*\|'
        match = re.search(pattern, content)
        if match:
            return {
                "used": int(match.group(1)),
                "available": int(match.group(2)),
                "utilization_percentage": float(match.group(3).replace("<", "0")),
            }
        return {"used": 0, "available": 0, "utilization_percentage": 0.0}

    @staticmethod
    def parse_primitives_table(content):
        """
        Parse Vivado's "Primitives" section of report_utilization, which lists
        per-primitive instance counts, e.g.:

            3. Primitives
            -------------

            +----------+------+----------------------+
            | Ref Name | Used | Functional Category  |
            +----------+------+----------------------+
            | LUT6     |  120 | LUT                  |
            | FDRE     |   64 | Register             |
            | CARRY4   |    8 | CarryLogic           |
            | DSP48E1  |    1 | DSP                  |
            +----------+------+----------------------+

        Returns:
            dict: mapping of primitive Ref Name -> used count (int).
        """
        primitives = {}

        header_match = re.search(r'\|\s*Ref Name\s*\|\s*Used\s*\|\s*Functional Category\s*\|', content)
        if not header_match:
            return primitives

        remainder = content[header_match.end():]
        row_pattern = re.compile(r'^\s*\|\s*([A-Za-z0-9_]+)\s*\|\s*(\d+)\s*\|\s*[A-Za-z0-9_ /]+\|\s*$')

        found_first_row = False
        for line in remainder.splitlines():
            stripped = line.strip()
            if not stripped:
                # A blank line only ends the table once we've seen at least one
                # data row; leading blank lines before the first row are skipped.
                if found_first_row:
                    break
                continue
            if set(stripped) <= {"+", "-"}:
                # Separator row like "+----+----+----+"; keep scanning.
                continue
            row_match = row_pattern.match(line)
            if row_match:
                ref_name = row_match.group(1)
                used = int(row_match.group(2))
                primitives[ref_name] = primitives.get(ref_name, 0) + used
                found_first_row = True
                continue
            # A non-table, non-blank line means the table section has ended.
            break

        return primitives

    @classmethod
    def parse_utilization_report(cls, content):
        """Parse utilization report: Slice-Logic summary plus per-primitive counts."""
        result = {"type": "utilization"}

        resources = {
            "luts": cls._extract_resource(content, "Slice LUTs"),
            "registers": cls._extract_resource(content, "Slice Registers"),
            "bram": cls._extract_resource(content, "Block RAM Tile"),
            "dsp": cls._extract_resource(content, "DSPs"),
            "io": cls._extract_resource(content, "Bonded IOB"),
        }
        result["resources"] = resources
        result["primitives"] = cls.parse_primitives_table(content)
        return result

    @staticmethod
    def parse_timing_report(content):
        """Parse timing report and extract timing metrics."""
        result = {"type": "timing"}

        slack_match = re.search(r'Slack\s*\((?:MET|VIOLATED)\)\s*:\s*(inf|[-\d.]+)', content, re.IGNORECASE)
        if not slack_match:
            slack_match = re.search(r'Slack\s*:\s*(inf|[-\d.]+)', content, re.IGNORECASE)
        if slack_match:
            slack_str = slack_match.group(1)
            result["slack"] = float("inf") if slack_str.lower() == "inf" else float(slack_str)
        else:
            result["slack"] = None

        data_path_match = re.search(
            r'Data Path Delay:\s*([-\d.]+)ns\s*\(logic\s*([-\d.]+)ns\s*\(([\d.]+)%\)\s*route\s*([-\d.]+)ns\s*\(([\d.]+)%\)\)',
            content
        )
        if data_path_match:
            result["data_path_delay_ns"] = float(data_path_match.group(1))
            result["logic_delay_ns"] = float(data_path_match.group(2))
            result["logic_delay_percent"] = float(data_path_match.group(3))
            result["route_delay_ns"] = float(data_path_match.group(4))
            result["route_delay_percent"] = float(data_path_match.group(5))
        else:
            data_path_simple = re.search(r'Data Path Delay:\s*([-\d.]+)ns', content)
            result["data_path_delay_ns"] = float(data_path_simple.group(1)) if data_path_simple else None
            result["logic_delay_ns"] = None
            result["logic_delay_percent"] = None
            result["route_delay_ns"] = None
            result["route_delay_percent"] = None

        return result

    @staticmethod
    def parse_log_file(content, severities=None):
        """Parse log file and extract errors/warnings and stage completion status."""
        result = {"type": "log"}
        if severities is None:
            severities = ["ERROR"]

        for severity in severities:
            key = severity.lower() + "s"
            lines = re.findall(rf'^.*?{severity}.*$', content, re.MULTILINE)
            extracted = []
            for line in lines:
                match = re.search(rf'{severity}\s*[:\-]?\s*(.*)', line)
                extracted.append(match.group(1).strip() if match else line.strip())
            result[key] = extracted

        stage_status = {
            "synthesis_completed": "synth_design completed successfully" in content or "Finished Synth" in content,
            "implementation_completed": (
                "place_design completed successfully" in content
                and "route_design completed successfully" in content
            ),
            "bitstream_generated": "write_bitstream completed successfully" in content,
        }
        result["stage_status"] = {stage: True for stage, done in stage_status.items() if done}
        return result

    @staticmethod
    def _capped(lines, max_entries):
        """Return (capped_list, total_count) for a list of extracted lines."""
        cleaned = [line.strip() for line in lines if line.strip()]
        return cleaned[:max_entries], len(cleaned)

    @classmethod
    def extract_simulation_feedback(cls, log_content, max_entries=10):
        """
        Extract a compact, actionable summary from a Vivado *simulation* log,
        for use in LLM self-correction feedback.

        Distinguishes two independent failure signals:
          - compile_errors: xvlog/xelab "ERROR: [...]" lines (compile/elaborate
            stage never even reached the testbench).
          - failing_vectors: per-test-vector lines emitted by the dataset's
            testbenches, which consistently print an upper-case "FAIL" token
            on a mismatching vector (e.g. "a=1 b=1 | product=0 (expected 1) |
            FAIL") and a lower-case "...tests failed" summary line. Matching
            the literal, case-sensitive substring "FAIL" therefore picks up
            only the actionable per-vector lines, never the summary line.

        Returns a dict with keys: compile_errors, compile_errors_total,
        failing_vectors, failing_vectors_total, testbench_ran (bool - whether
        the testbench appears to have executed at all, to distinguish a total
        compile/crash failure from a functional mismatch).
        """
        error_lines = re.findall(r'^.*?ERROR.*$', log_content, re.MULTILINE)
        compile_errors, compile_errors_total = cls._capped(error_lines, max_entries)

        fail_lines = re.findall(r'^.*\bFAIL\b.*$', log_content, re.MULTILINE)
        failing_vectors, failing_vectors_total = cls._capped(fail_lines, max_entries)

        testbench_ran = (
            "Testbench results" in log_content
            or re.search(r'\bPASS\b|\bFAIL\b', log_content) is not None
        )

        return {
            "compile_errors": compile_errors,
            "compile_errors_total": compile_errors_total,
            "failing_vectors": failing_vectors,
            "failing_vectors_total": failing_vectors_total,
            "testbench_ran": bool(testbench_ran),
        }

    @classmethod
    def extract_synthesis_feedback(cls, log_content, max_entries=10):
        """
        Extract a compact, actionable summary from a Vivado *synthesis* log,
        for use in LLM self-correction feedback.

        Returns a dict with keys: errors, errors_total (any "ERROR: [...]"
        line, typically "ERROR: [Synth 8-xxx] ..."), and critical_warnings /
        critical_warnings_total (only populated when no errors were found, as
        a fallback for cases where Vivado exits non-zero without emitting a
        literal ERROR-tagged line, e.g. a crash).
        """
        error_lines = re.findall(r'^.*?ERROR.*$', log_content, re.MULTILINE)
        errors, errors_total = cls._capped(error_lines, max_entries)

        critical_warnings, critical_warnings_total = [], 0
        if not errors:
            cw_lines = re.findall(r'^.*?CRITICAL WARNING.*$', log_content, re.MULTILINE)
            critical_warnings, critical_warnings_total = cls._capped(cw_lines, max_entries)

        return {
            "errors": errors,
            "errors_total": errors_total,
            "critical_warnings": critical_warnings,
            "critical_warnings_total": critical_warnings_total,
        }

    @classmethod
    def batch_parse(cls, directory, log_severities=None):
        """Parse all report/log files in a directory."""
        results = {}
        if not os.path.isdir(directory):
            return results
        for file in os.listdir(directory):
            if not (file.endswith(".txt") or file.endswith(".log")):
                continue
            path = os.path.join(directory, file)
            with open(path, 'r', errors="ignore") as f:
                content = f.read()
            if file.endswith(".log"):
                results[file] = cls.parse_log_file(content, severities=log_severities)
                continue
            report_type = cls.detect_report_type(content)
            if report_type == "power":
                results[file] = cls.parse_power_report(content)
            elif report_type == "utilization":
                results[file] = cls.parse_utilization_report(content)
            elif report_type == "timing":
                results[file] = cls.parse_timing_report(content)
        return results


# ---------------------------------------------------------------------------
# CSV / aggregation helpers dedicated to fabric_exp_results.json
# ---------------------------------------------------------------------------

_BASE_FIELDS = [
    "ID",
    "module",
    "style",
    "llm_model",
    "token_count",
    "llm_time [s]",
    "eda_time [s]",
    "attempts_used",
    "Functional Verification",
    "Synthesis",
    "Implementation",
    "primary_primitive",
    "fabric_expectations_met",
]


def _flatten_result(entry):
    """Flatten one fabric_exp_results.json entry into a single-level dict for CSV."""
    row = {field: entry.get(field) for field in _BASE_FIELDS}

    expected = entry.get("expected_primitives", {}) or {}
    actual = entry.get("actual_primitives", {}) or {}

    all_primitive_names = sorted(set(expected.keys()) | set(actual.keys()))
    for name in all_primitive_names:
        row[f"primitive_{name}_expected"] = expected.get(name)
        row[f"primitive_{name}_actual"] = actual.get(name, 0)

    return row


def fabric_json_to_csv(json_path, csv_path):
    """
    Flatten a fabric_exp_results.json (list of run results, possibly with
    nested expected_primitives/actual_primitives dicts) into a CSV file.

    Args:
        json_path: Path to the aggregated results JSON (list of entries).
        csv_path: Destination CSV path.

    Returns:
        int: number of rows written.
    """
    with open(json_path, 'r') as f:
        data = json.load(f)

    entries = data if isinstance(data, list) else [data]
    rows = [_flatten_result(entry) for entry in entries]

    fieldnames = list(_BASE_FIELDS)
    for row in rows:
        for key in row:
            if key not in fieldnames:
                fieldnames.append(key)

    os.makedirs(os.path.dirname(os.path.abspath(csv_path)), exist_ok=True)
    with open(csv_path, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)

    return len(rows)


def fabric_compress_csv(csv_path, summary_path):
    """
    Compute a compact aggregate summary (pass rates, average tokens/time/attempts)
    from a CSV produced by fabric_json_to_csv(), and write it as JSON.

    Args:
        csv_path: Path to the flattened results CSV.
        summary_path: Destination JSON path for the summary.

    Returns:
        dict: the computed summary (also written to summary_path).
    """
    def _to_bool(value):
        return str(value).strip().lower() in {"true", "pass", "1", "yes"}

    def _to_float(value, default=0.0):
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    rows = []
    with open(csv_path, 'r', newline='') as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    total = len(rows)
    summary = {
        "total_runs": total,
        "functional_verification_pass_rate": 0.0,
        "synthesis_pass_rate": 0.0,
        "implementation_pass_rate": 0.0,
        "fabric_expectations_met_rate": 0.0,
        "avg_attempts_used": 0.0,
        "avg_token_count": 0.0,
        "avg_llm_time_s": 0.0,
        "avg_eda_time_s": 0.0,
        "per_design": {},
    }

    if total == 0:
        with open(summary_path, 'w') as f:
            json.dump(summary, f, indent=2)
        return summary

    func_pass = sum(1 for r in rows if _to_bool(r.get("Functional Verification")))
    synth_pass = sum(1 for r in rows if _to_bool(r.get("Synthesis")))
    impl_pass = sum(1 for r in rows if _to_bool(r.get("Implementation")))
    fabric_met = sum(1 for r in rows if _to_bool(r.get("fabric_expectations_met")))

    summary["functional_verification_pass_rate"] = func_pass / total
    summary["synthesis_pass_rate"] = synth_pass / total
    summary["implementation_pass_rate"] = impl_pass / total
    summary["fabric_expectations_met_rate"] = fabric_met / total
    summary["avg_attempts_used"] = sum(_to_float(r.get("attempts_used")) for r in rows) / total
    summary["avg_token_count"] = sum(_to_float(r.get("token_count")) for r in rows) / total
    summary["avg_llm_time_s"] = sum(_to_float(r.get("llm_time [s]")) for r in rows) / total
    summary["avg_eda_time_s"] = sum(_to_float(r.get("eda_time [s]")) for r in rows) / total

    per_design = {}
    for r in rows:
        module = r.get("module") or "unknown"
        per_design.setdefault(module, {"runs": 0, "fabric_expectations_met": 0})
        per_design[module]["runs"] += 1
        if _to_bool(r.get("fabric_expectations_met")):
            per_design[module]["fabric_expectations_met"] += 1
    summary["per_design"] = per_design

    os.makedirs(os.path.dirname(os.path.abspath(summary_path)), exist_ok=True)
    with open(summary_path, 'w') as f:
        json.dump(summary, f, indent=2)

    return summary


def fabric_style_comparison(json_path, csv_path):
    """
    Build a per-module, per-style comparison CSV from an aggregated results
    JSON that may contain runs from multiple generation styles (baseline,
    fabric_aware, explicit_primitive), e.g. the result of concatenating
    several --style runs of the same design set into one --output_json file.

    One row is emitted per (module, style) pair found in the input, with
    columns for pass/fail status, attempts used, and per-primitive actual
    counts (prefixed with the primitive name) so that rows for the same
    module can be compared side by side across styles.

    Args:
        json_path: Path to the aggregated results JSON (list of entries,
            each expected to carry a "style" field; entries missing it are
            treated as style "unknown").
        csv_path: Destination CSV path.

    Returns:
        int: number of rows written.
    """
    with open(json_path, 'r') as f:
        data = json.load(f)

    entries = data if isinstance(data, list) else [data]

    rows = []
    primitive_names = set()
    for entry in entries:
        actual = entry.get("actual_primitives", {}) or {}
        expected = entry.get("expected_primitives", {}) or {}
        primitive_names.update(actual.keys())
        primitive_names.update(expected.keys())

        row = {
            "module": entry.get("module"),
            "style": entry.get("style", "unknown"),
            "ID": entry.get("ID"),
            "llm_model": entry.get("llm_model"),
            "attempts_used": entry.get("attempts_used"),
            "Functional Verification": entry.get("Functional Verification"),
            "Synthesis": entry.get("Synthesis"),
            "Implementation": entry.get("Implementation"),
            "fabric_expectations_met": entry.get("fabric_expectations_met"),
            "token_count": entry.get("token_count"),
            "llm_time [s]": entry.get("llm_time [s]"),
            "eda_time [s]": entry.get("eda_time [s]"),
            "_expected": expected,
            "_actual": actual,
        }
        rows.append(row)

    primitive_names = sorted(primitive_names)
    fieldnames = [
        "module", "style", "ID", "llm_model", "attempts_used",
        "Functional Verification", "Synthesis", "Implementation",
        "fabric_expectations_met", "token_count", "llm_time [s]", "eda_time [s]",
    ]
    for name in primitive_names:
        fieldnames.append(f"primitive_{name}_expected")
        fieldnames.append(f"primitive_{name}_actual")

    rows.sort(key=lambda r: (str(r.get("module")), str(r.get("style"))))

    os.makedirs(os.path.dirname(os.path.abspath(csv_path)), exist_ok=True)
    with open(csv_path, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            expected = row.pop("_expected")
            actual = row.pop("_actual")
            for name in primitive_names:
                row[f"primitive_{name}_expected"] = expected.get(name)
                row[f"primitive_{name}_actual"] = actual.get(name, 0)
            writer.writerow(row)

    return len(rows)
