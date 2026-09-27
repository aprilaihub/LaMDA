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
        """Extract a Slice-Logic style summary row: name | used | fixed | prohibited | available | util%.

        Vivado sometimes appends a footnote marker (e.g. "Slice LUTs*") directly
        after the site-type name with no separating whitespace, so the pattern
        allows an optional literal "*" between the name and the column pipe.
        """
        pattern = rf'{name}\*?\s*\|\s*(\d+)\s*\|\s*\d+\s*\|\s*\d+\s*\|\s*(\d+)\s*\|\s*([<>]?\d+\.\d+)\s*\|'
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
    def parse_dsp_mapping_table(content):
        """
        Parse Vivado synthesis log's "DSP Final Report" table (only present in
        the raw *_synth.log transcript, not in report_utilization output),
        e.g.:

            DSP Final Report (the ' indicates corresponding REG is set)
            +--------------+-------------+--------+--------+--------+--------+--------+------+------+------+------+-------+------+------+
            |Module Name   | DSP Mapping | A Size | B Size | C Size | D Size | P Size | AREG | BREG | CREG | DREG | ADREG | MREG | PREG |
            +--------------+-------------+--------+--------+--------+--------+--------+------+------+------+------+-------+------+------+
            |mac_8x8_accum | (P+A*B)'    | 8      | 8      | -      | -      | 20     | 0    | 0    | -    | -    | -     | 0    | 1    |
            +--------------+-------------+--------+--------+--------+--------+--------+------+------+------+------+-------+------+------+

        Returns:
            list[dict]: one entry per DSP instance row, e.g.
                {"module": "mac_8x8_accum", "dsp_mapping": "(P+A*B)'",
                 "A_size": 8, "B_size": 8, "C_size": None, "D_size": None,
                 "P_size": 20, "AREG": 0, "BREG": 0, "CREG": None, "DREG": None,
                 "ADREG": None, "MREG": 0, "PREG": 1}
            Fields with value "-" (not applicable to that DSP mode) are None.
        """
        rows = []
        header_match = re.search(r'DSP Final Report', content)
        if not header_match:
            return rows

        field_names = ["A_size", "B_size", "C_size", "D_size", "P_size",
                       "AREG", "BREG", "CREG", "DREG", "ADREG", "MREG", "PREG"]
        row_pattern = re.compile(r'^\|' + r'([^|]+)\|' * 14 + r'\s*$')

        remainder = content[header_match.end():]
        found_first_row = False
        for line in remainder.splitlines():
            stripped = line.strip()
            if not stripped:
                if found_first_row:
                    break
                continue
            if set(stripped) <= {"+", "-"}:
                continue
            row_match = row_pattern.match(stripped)
            if not row_match:
                if found_first_row:
                    break
                continue
            cells = [c.strip() for c in row_match.groups()]
            if cells[0] == "Module Name":
                # The header row itself matches the same 14-column shape; skip it.
                continue
            row = {"module": cells[0], "dsp_mapping": cells[1]}
            for name, value in zip(field_names, cells[2:]):
                if value in ("-", ""):
                    row[name] = None
                else:
                    try:
                        row[name] = int(value)
                    except ValueError:
                        row[name] = value
            rows.append(row)
            found_first_row = True
        return rows

    @staticmethod
    def parse_critical_warnings(content):
        """
        Extract ALL "CRITICAL WARNING:" lines from a synthesis log, regardless
        of whether synthesis ultimately passed or failed (unlike
        extract_synthesis_feedback's critical_warnings field, which is only
        populated as a fallback when no ERROR lines are present). Also parses
        the "multi-driven net" sub-case specifically (a confirmed silent-pass
        risk: Vivado only downgrades this to a CRITICAL WARNING rather than an
        ERROR, so synthesis can "succeed" while producing an incorrect
        netlist), plus the "Report Check Netlist" summary table's
        multi_driven_nets warning count when present.

        Returns dict: {"critical_warnings": [...], "critical_warnings_total": int,
                       "multi_driven_nets": {net_name: [driver_pin, ...]},
                       "check_netlist_multi_driven_warnings": int or None}
        """
        cw_lines = re.findall(r'^.*?CRITICAL WARNING.*$', content, re.MULTILINE)
        cleaned = [line.strip() for line in cw_lines if line.strip()]

        multi_driven_nets = {}
        for line in cleaned:
            m = re.search(r"multi-driven net on pin (\S+) with \w+ driver pin '([^']+)'", line)
            if m:
                net, driver = m.group(1), m.group(2)
                drivers = multi_driven_nets.setdefault(net, [])
                if driver not in drivers:
                    drivers.append(driver)

        check_netlist_count = None
        m = re.search(r'\|\s*multi_driven_nets\s*\|\s*(\d+)\s*\|\s*(\d+)\s*\|', content)
        if m:
            check_netlist_count = int(m.group(2))

        return {
            "critical_warnings": cleaned,
            "critical_warnings_total": len(cleaned),
            "multi_driven_nets": multi_driven_nets,
            "check_netlist_multi_driven_warnings": check_netlist_count,
        }

    @staticmethod
    def parse_critical_path_cells(content):
        """
        Extract critical-path cell-type diagnostics from a Vivado timing
        report, for use in LLM self-correction feedback (critical-path
        diagnosis). Two complementary views are returned:

          - breakdown: dict of cell-type -> count, parsed directly from the
            report's own summary line, e.g.
                Logic Levels:           11  (CARRY4=8 IBUF=1 LUT2=1 OBUF=1)
            which Vivado emits for any resolved (non-"inf") path.

          - ordered_path: an ordered list of cell types as traversed along the
            reported critical path, parsed from "Location" column entries of
            the Data Path Delay table, e.g.
                CARRY4 (Prop_carry4_CI_CO[3])
                LUT2 (Prop_lut2_I0_O)
            Capped to the first 32 entries to keep feedback prompts compact.

        Returns dict: {"logic_levels_total": int or None,
                       "breakdown": {cell_type: count}, "ordered_path": [...]}
        """
        result = {"logic_levels_total": None, "breakdown": {}, "ordered_path": []}

        levels_match = re.search(r'Logic Levels:\s*(\d+)\s*\(([^)]*)\)', content)
        if levels_match:
            result["logic_levels_total"] = int(levels_match.group(1))
            breakdown = {}
            for part in levels_match.group(2).split():
                cell_match = re.match(r'([A-Za-z0-9_]+)=(\d+)', part)
                if cell_match:
                    breakdown[cell_match.group(1)] = int(cell_match.group(2))
            result["breakdown"] = breakdown

        result["ordered_path"] = re.findall(r'^\s*([A-Z][A-Za-z0-9]*)\s*\(Prop_', content, re.MULTILINE)[:32]
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
    "hard_constraints_met",
    "timing_slack_ns",
    "critical_warnings_total",
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
            "hard_constraints_met": entry.get("hard_constraints_met"),
            "timing_slack_ns": entry.get("timing_slack_ns"),
            "critical_warnings_total": entry.get("critical_warnings_total"),
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
        "fabric_expectations_met", "hard_constraints_met", "timing_slack_ns",
        "critical_warnings_total", "token_count", "llm_time [s]", "eda_time [s]",
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


# ---------------------------------------------------------------------------
# Plotting (Phase 5). Deliberately self-contained: FabricPlotter does NOT
# import from, or modify, Evaluation/ResBench/Analysis/ResBenchAnalysis.py.
# Its set_plot_style() method is a verbatim copy of DataPlotter.set_plot_style()
# from that module, so this pipeline's Analysis package has no cross-pipeline
# import dependency.
# ---------------------------------------------------------------------------

class FabricPlotter:
    """Publication-quality plots summarizing fabric_exp_results.json runs."""

    def set_plot_style(self, ax=None, fontsize=7, plot_area_inches=2.3, margin=0.3):
        """Apply publication-quality style to matplotlib axes with uniform fontsize."""
        import matplotlib as mpl
        figsize = (plot_area_inches + margin, plot_area_inches + margin)
        mpl.rcParams.update({
            'font.family': 'serif',
            'font.serif': ['Times New Roman', 'Times', 'DejaVu Serif', 'serif'],
            'axes.labelsize': fontsize,
            'axes.titlesize': fontsize,
            'xtick.labelsize': fontsize - 1,
            'ytick.labelsize': fontsize - 1,
            'axes.linewidth': 1.2,
            'xtick.direction': 'out',
            'ytick.direction': 'out',
            'xtick.color': 'black',
            'ytick.color': 'black',
            'axes.edgecolor': 'black',
            'figure.dpi': 300,
            'savefig.dpi': 300,
            'legend.fontsize': fontsize - 2,
            'legend.frameon': False,
            'axes.grid': False,
            'grid.alpha': 0.0,
            'axes.facecolor': 'white',
            'figure.facecolor': 'white',
            'figure.subplot.left': 0.13,
            'figure.subplot.right': 0.97,
            'figure.subplot.bottom': 0.13,
            'figure.subplot.top': 0.97,
        })
        if ax is not None:
            ax.spines['top'].set_visible(False)
            ax.spines['right'].set_visible(False)
            ax.spines['left'].set_linewidth(1.2)
            ax.spines['bottom'].set_linewidth(1.2)
            ax.spines['left'].set_color('black')
            ax.spines['bottom'].set_color('black')
            ax.tick_params(axis='both', which='both', color='black', direction='out', length=5, width=1.2)
        return figsize

    @staticmethod
    def _to_bool(value):
        return str(value).strip().lower() in {"true", "pass", "1", "yes"}

    def create_style_pass_rate_barplot(self, df, column="Functional Verification", fontsize=7):
        """Bar plot of pass rate (%) for `column`, grouped by generation style."""
        import matplotlib.pyplot as plt

        styles = sorted(df["style"].dropna().unique().tolist())
        rates = []
        for style in styles:
            subset = df[df["style"] == style]
            passed = subset[column].apply(self._to_bool).sum()
            rates.append(100.0 * passed / len(subset) if len(subset) else 0.0)

        figsize = self.set_plot_style(fontsize=fontsize)
        plt.figure(figsize=figsize)
        bars = plt.bar(styles, rates, color='skyblue', alpha=0.85)
        ax = plt.gca()
        self.set_plot_style(ax, fontsize)
        plt.xlabel('Style', fontsize=fontsize - 1)
        plt.ylabel(f'{column} pass rate (%)', fontsize=fontsize)
        plt.ylim(0, 105)
        for bar in bars:
            height = bar.get_height()
            plt.text(bar.get_x() + bar.get_width() / 2., height, f'{height:.0f}',
                      ha='center', va='bottom', fontsize=fontsize)
        plt.tight_layout()
        return plt.gcf()

    def create_attempts_used_boxplot(self, df, fontsize=7):
        """Box plot of attempts_used, grouped by generation style."""
        import matplotlib.pyplot as plt
        import seaborn as sns

        styles = sorted(df["style"].dropna().unique().tolist())
        figsize = self.set_plot_style(fontsize=fontsize)
        plt.figure(figsize=figsize)
        ax = sns.boxplot(
            data=df, x='style', y='attempts_used', order=styles,
            hue='style', legend=False, palette=['skyblue'] * len(styles),
            fliersize=3, linewidth=1.0,
            boxprops=dict(edgecolor='black'), whiskerprops=dict(color='black', linewidth=1.0),
            flierprops=dict(markeredgecolor='black'), medianprops=dict(color='black', linewidth=1.0),
            capprops=dict(color='black', linewidth=1.0),
        )
        self.set_plot_style(ax, fontsize)
        plt.xlabel('Style', fontsize=fontsize - 1)
        plt.ylabel('Attempts used', fontsize=fontsize)
        plt.tight_layout()
        return plt.gcf()

    def create_primitive_expected_vs_actual_barplot(self, df, module, fontsize=7):
        """Grouped bar plot comparing expected vs. actual counts for every
        primitive tracked for a single `module`, across all rows for that
        module in `df` (averaged if multiple runs are present)."""
        import matplotlib.pyplot as plt
        import numpy as np

        subset = df[df["module"] == module]
        if subset.empty:
            raise ValueError(f"No rows found for module '{module}'")

        expected_cols = [c for c in df.columns if c.startswith("primitive_") and c.endswith("_expected")]
        primitive_names = [c[len("primitive_"):-len("_expected")] for c in expected_cols]
        primitive_names = [
            name for name in primitive_names
            if subset[f"primitive_{name}_expected"].notna().any() or subset[f"primitive_{name}_actual"].notna().any()
        ]
        if not primitive_names:
            raise ValueError(f"No primitive columns found for module '{module}'")

        expected_vals = [subset[f"primitive_{name}_expected"].astype(float).mean() for name in primitive_names]
        actual_vals = [subset[f"primitive_{name}_actual"].astype(float).mean() for name in primitive_names]

        figsize = self.set_plot_style(fontsize=fontsize)
        plt.figure(figsize=figsize)
        x = np.arange(len(primitive_names))
        width = 0.35
        plt.bar(x - width / 2, expected_vals, width, label='Expected', color='skyblue', alpha=0.85)
        plt.bar(x + width / 2, actual_vals, width, label='Actual', color='salmon', alpha=0.85)
        ax = plt.gca()
        self.set_plot_style(ax, fontsize)
        plt.xticks(x, primitive_names, rotation=45, ha='right', fontsize=fontsize - 2)
        plt.ylabel('Count', fontsize=fontsize)
        plt.title(module, fontsize=fontsize)
        plt.legend()
        plt.tight_layout()
        return plt.gcf()

    def create_style_comparison_heatmap(self, comparison_df, column="Functional Verification", fontsize=7):
        """Heatmap of PASS/FAIL (1/0) for `column`, rows=module, columns=style."""
        import matplotlib.pyplot as plt
        import pandas as pd
        from matplotlib.colors import BoundaryNorm

        pivot = comparison_df.pivot_table(
            index="module", columns="style",
            values=column, aggfunc=lambda vals: int(self._to_bool(vals.iloc[0])),
        )
        pivot = pivot.reindex(sorted(pivot.columns), axis=1)

        figsize = self.set_plot_style(fontsize=fontsize)
        plt.figure(figsize=figsize)
        cmap = plt.cm.Blues
        norm = BoundaryNorm([-0.5, 0.5, 1.5], cmap.N)
        ax = plt.gca()
        im = ax.imshow(pivot.values.astype(float), cmap=cmap, norm=norm, aspect='auto')
        for i in range(len(pivot.index) + 1):
            ax.axhline(i - 0.5, color='black', linewidth=0.7, zorder=3)
        for j in range(len(pivot.columns) + 1):
            ax.axvline(j - 0.5, color='black', linewidth=0.7, zorder=3)
        ax.set_xticks(range(len(pivot.columns)))
        ax.set_xticklabels(pivot.columns, rotation=45, ha='right', fontsize=fontsize - 2)
        ax.set_yticks(range(len(pivot.index)))
        ax.set_yticklabels(pivot.index, fontsize=fontsize - 2)
        ax.set_title(f'{column}: PASS (dark) vs. FAIL (light)', fontsize=fontsize)
        self.set_plot_style(ax, fontsize)
        plt.tight_layout()
        return plt.gcf()

    def create_token_time_barplot(self, df, fontsize=7):
        """Grouped bar plot of average token_count and total time (llm + eda), by style."""
        import matplotlib.pyplot as plt
        import numpy as np

        styles = sorted(df["style"].dropna().unique().tolist())
        tokens = [df[df["style"] == s]["token_count"].astype(float).mean() for s in styles]
        times = [
            (df[df["style"] == s]["llm_time [s]"].astype(float)
             + df[df["style"] == s]["eda_time [s]"].astype(float)).mean()
            for s in styles
        ]

        figsize = self.set_plot_style(fontsize=fontsize)
        fig, ax1 = plt.subplots(figsize=figsize)
        x = np.arange(len(styles))
        width = 0.35
        ax1.bar(x - width / 2, tokens, width, label='Avg tokens', color='skyblue', alpha=0.85)
        self.set_plot_style(ax1, fontsize)
        ax1.set_ylabel('Avg token count', fontsize=fontsize)
        ax1.set_xticks(x)
        ax1.set_xticklabels(styles, fontsize=fontsize - 1)

        ax2 = ax1.twinx()
        ax2.bar(x + width / 2, times, width, label='Avg total time [s]', color='salmon', alpha=0.85)
        ax2.set_ylabel('Avg total time [s]', fontsize=fontsize)
        ax2.spines['top'].set_visible(False)

        lines1, labels1 = ax1.get_legend_handles_labels()
        lines2, labels2 = ax2.get_legend_handles_labels()
        ax1.legend(lines1 + lines2, labels1 + labels2, loc='upper right')
        plt.tight_layout()
        return fig

    def create_critical_warnings_barplot(self, df, fontsize=7):
        """Bar plot of average critical_warnings_total, by style.

        Forward-looking: degrades gracefully (raises ValueError) if the
        'critical_warnings_total' column is absent, e.g. results generated
        before Phase 2.5 was implemented.
        """
        import matplotlib.pyplot as plt

        if "critical_warnings_total" not in df.columns:
            raise ValueError("'critical_warnings_total' column not present in this results CSV")

        styles = sorted(df["style"].dropna().unique().tolist())
        avgs = [df[df["style"] == s]["critical_warnings_total"].astype(float).fillna(0).mean() for s in styles]

        figsize = self.set_plot_style(fontsize=fontsize)
        plt.figure(figsize=figsize)
        bars = plt.bar(styles, avgs, color='skyblue', alpha=0.85)
        ax = plt.gca()
        self.set_plot_style(ax, fontsize)
        plt.xlabel('Style', fontsize=fontsize - 1)
        plt.ylabel('Avg critical warnings', fontsize=fontsize)
        for bar in bars:
            height = bar.get_height()
            plt.text(bar.get_x() + bar.get_width() / 2., height, f'{height:.2f}',
                      ha='center', va='bottom', fontsize=fontsize)
        plt.tight_layout()
        return plt.gcf()

    def create_timing_resource_scatter(self, df, fontsize=7):
        """Scatter of timing_slack_ns vs. attempts_used, colored by style.

        Forward-looking: degrades gracefully (raises ValueError) if the
        'timing_slack_ns' column is absent, e.g. results generated before
        Phase 2 was implemented.
        """
        import matplotlib.pyplot as plt

        if "timing_slack_ns" not in df.columns:
            raise ValueError("'timing_slack_ns' column not present in this results CSV")

        subset = df[df["timing_slack_ns"].notna()].copy()
        subset = subset[subset["timing_slack_ns"] != "inf"]
        if subset.empty:
            raise ValueError("No finite timing_slack_ns values available to plot")
        subset["timing_slack_ns"] = subset["timing_slack_ns"].astype(float)

        styles = sorted(subset["style"].dropna().unique().tolist())
        colors = ['skyblue', 'salmon', 'lightgreen', 'plum', 'khaki']

        figsize = self.set_plot_style(fontsize=fontsize)
        plt.figure(figsize=figsize)
        for i, style in enumerate(styles):
            style_rows = subset[subset["style"] == style]
            plt.scatter(
                style_rows["attempts_used"].astype(float), style_rows["timing_slack_ns"],
                label=style, color=colors[i % len(colors)], alpha=0.85, edgecolor='black', linewidth=0.5,
            )
        ax = plt.gca()
        self.set_plot_style(ax, fontsize)
        plt.axhline(0.0, color='black', linewidth=0.8, linestyle='--')
        plt.xlabel('Attempts used', fontsize=fontsize - 1)
        plt.ylabel('Timing slack [ns]', fontsize=fontsize)
        plt.legend()
        plt.tight_layout()
        return plt.gcf()

    def plot_all(self, input_csv, comparison_csv=None, output_dir="Plots"):
        """
        Generate every available plot from `input_csv` (fabric_json_to_csv
        output) and, if provided and readable, `comparison_csv`
        (fabric_style_comparison output), saving PNGs into `output_dir`.

        Plots that require columns/data not present in the given CSVs (e.g.
        critical_warnings_total/timing_slack_ns from runs predating Phase 2/
        2.5, or the style comparison heatmap when comparison_csv is missing)
        are skipped with a printed warning rather than raising, so this
        method can be safely re-run against any historical results file.

        Returns:
            list[str]: paths of the PNG files actually written.
        """
        import matplotlib.pyplot as plt
        import pandas as pd

        os.makedirs(output_dir, exist_ok=True)
        written = []

        df = pd.read_csv(input_csv)

        def _save(fig, name):
            path = os.path.join(output_dir, name)
            fig.savefig(path)
            plt.close(fig)
            written.append(path)
            print(f"Wrote {path}")

        try:
            _save(self.create_style_pass_rate_barplot(df), "style_pass_rate.png")
        except Exception as e:
            print(f"Skipped style_pass_rate.png: {e}")

        try:
            _save(self.create_attempts_used_boxplot(df), "attempts_used_boxplot.png")
        except Exception as e:
            print(f"Skipped attempts_used_boxplot.png: {e}")

        try:
            _save(self.create_token_time_barplot(df), "token_time_barplot.png")
        except Exception as e:
            print(f"Skipped token_time_barplot.png: {e}")

        for module in sorted(df["module"].dropna().unique().tolist()):
            try:
                _save(
                    self.create_primitive_expected_vs_actual_barplot(df, module),
                    f"primitive_expected_vs_actual_{module}.png",
                )
            except Exception as e:
                print(f"Skipped primitive_expected_vs_actual_{module}.png: {e}")

        try:
            _save(self.create_critical_warnings_barplot(df), "critical_warnings_barplot.png")
        except Exception as e:
            print(f"Skipped critical_warnings_barplot.png: {e}")

        try:
            _save(self.create_timing_resource_scatter(df), "timing_resource_scatter.png")
        except Exception as e:
            print(f"Skipped timing_resource_scatter.png: {e}")

        if comparison_csv and os.path.exists(comparison_csv):
            comparison_df = pd.read_csv(comparison_csv)
            try:
                _save(
                    self.create_style_comparison_heatmap(comparison_df),
                    "style_comparison_heatmap.png",
                )
            except Exception as e:
                print(f"Skipped style_comparison_heatmap.png: {e}")
        else:
            print("Skipped style_comparison_heatmap.png: comparison_csv not provided or not found")

        return written
