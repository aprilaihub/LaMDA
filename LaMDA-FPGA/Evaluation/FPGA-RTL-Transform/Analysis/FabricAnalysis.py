"""
Vivado report / log parsers of the FPGA-RTL-Transform pipeline (pure Python, no Vivado / LLM dependency).

FabricReportParser turns the text reports written by EDA_Interface/Vivado.py (utilization incl. the per-primitive
table, timing summary, worst timing paths, high-fanout nets, control sets, DSP mapping table, pulse width, power,
logs) into plain dicts, and extracts the compact error summaries used as simulation / synthesis feedback.
Every parser is tolerant: a missing section yields an empty result. Fixtures: Tests/fixtures/.
"""

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
        # The category column is free text in real reports, e.g. "Flop & Latch", "Block Arithmetic".
        row_pattern = re.compile(r'^\s*\|\s*([A-Za-z0-9_]+)\s*\|\s*(\d+)\s*\|[^|]+\|\s*$')

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
    def parse_timing_summary(content):
        """
        Parse the "Design Timing Summary" table of Vivado's report_timing_summary:

            WNS(ns)  TNS(ns)  TNS Failing Endpoints  TNS Total Endpoints  WHS(ns) ...
            -------  -------  ---------------------  -------------------  ------- ...
              0.512    0.000                      0                   36    0.123 ...

        Returns {"type": "timing_summary", "wns_ns", "tns_ns", "failing_endpoints",
        "total_endpoints", "whs_ns", "wpws_ns"}; each value is None when absent or
        not numeric ("NA": no constrained register-to-register path, for example when
        every register is absorbed into a DSP48E1 / block RAM). `wpws_ns` is the worst
        pulse-width slack, which includes the DSP/BRAM min-period checks.
        """
        result = {"type": "timing_summary", "wns_ns": None, "tns_ns": None,
                  "failing_endpoints": None, "total_endpoints": None, "whs_ns": None,
                  "wpws_ns": None}
        match = re.search(r'WNS\(ns\)[^\n]*\n\s*-[-\s]*\n\s*([^\n]+)', content)
        if not match:
            return result
        fields = match.group(1).split()

        def num(idx, cast=float):
            if idx >= len(fields):
                return None
            try:
                return cast(fields[idx])
            except ValueError:
                return None

        result["wns_ns"] = num(0)
        result["tns_ns"] = num(1)
        result["failing_endpoints"] = num(2, int)
        result["total_endpoints"] = num(3, int)
        result["whs_ns"] = num(4)
        result["wpws_ns"] = num(8)
        return result

    _PULSE_WIDTH_CHECKS = ("Min Period", "Low Pulse Width", "High Pulse Width", "Max Skew", "Max Delay")

    @classmethod
    def parse_pulse_width_report(cls, content, max_rows=20):
        """
        Parse Vivado's report_pulse_width table:

            Check Type  Corner  Lib Pin      Reference Pin  Required(ns)  Actual(ns)  Slack(ns)  Location  Pin
            Min Period  n/a     DSP48E1/CLK  n/a            3.884         5.000       1.116                c00_reg/CLK

        A 'Min Period' row on a DSP48E1 / RAMB pin is the smallest clock period the primitive
        supports with its current register configuration. When a design has no constrained
        register-to-register path (WNS is NA), this is the only timing evidence for the
        absorbed logic: Fmax <= 1000 / min_period_ns.

        Returns {"type": "pulse_width", "checks": [...], "min_period_ns": largest required Min Period
        (None if there is no such row), "min_period_lib_pin", "min_period_pin", "wpws_ns": worst slack}.
        """
        checks = []
        pattern = re.compile(r'^(' + '|'.join(cls._PULSE_WIDTH_CHECKS) + r')\s+(.*\S)\s*$', re.MULTILINE)
        for match in pattern.finditer(content or ""):
            tokens = match.group(2).split()
            if len(tokens) < 7:
                continue
            try:
                required, actual, slack = float(tokens[3]), float(tokens[4]), float(tokens[5])
            except ValueError:
                continue
            checks.append({
                "check": match.group(1), "corner": tokens[0], "lib_pin": tokens[1],
                "required_ns": required, "actual_ns": actual, "slack_ns": slack, "pin": tokens[-1],
            })
        result = {"type": "pulse_width", "checks": checks[:max_rows], "min_period_ns": None,
                  "min_period_lib_pin": None, "min_period_pin": None, "wpws_ns": None}
        if checks:
            result["wpws_ns"] = min(c["slack_ns"] for c in checks)
        periods = [c for c in checks if c["check"] == "Min Period"]
        if periods:
            worst = max(periods, key=lambda c: c["required_ns"])
            result["min_period_ns"] = worst["required_ns"]
            result["min_period_lib_pin"] = worst["lib_pin"]
            result["min_period_pin"] = worst["pin"]
        return result

    @staticmethod
    def parse_timing_paths(content, max_paths=5):
        """
        Parse a multi-path report_timing output (one block per path starting at the
        'Slack (MET|VIOLATED) :' or 'Slack:' line). Per path: slack_ns (inf for unconstrained
        paths), source, destination, requirement_ns, data_path_delay_ns, logic_delay_ns,
        route_delay_ns, logic_levels, breakdown (cell type -> count).
        """
        paths = []
        chunks = re.split(r'(?m)^(?=Slack\s*(?:\(|:))', content or "")
        for chunk in chunks:
            if not re.match(r'Slack\s*(?:\(|:)', chunk):
                continue
            slack_match = re.match(r'Slack\s*(?:\((?:MET|VIOLATED)\))?\s*:\s*(inf|-?[\d.]+)', chunk, re.IGNORECASE)
            if not slack_match:
                continue
            slack_str = slack_match.group(1)
            entry = {"slack_ns": float("inf") if slack_str.lower() == "inf" else float(slack_str)}
            src = re.search(r'^\s*Source:\s+(\S+)', chunk, re.MULTILINE)
            dst = re.search(r'^\s*Destination:\s+(\S+)', chunk, re.MULTILINE)
            entry["source"] = src.group(1) if src else None
            entry["destination"] = dst.group(1) if dst else None
            req = re.search(r'Requirement:\s*([-\d.]+)ns', chunk)
            entry["requirement_ns"] = float(req.group(1)) if req else None
            delay = re.search(
                r'Data Path Delay:\s*([-\d.]+)ns\s*\(logic\s*([-\d.]+)ns\s*\([\d.]+%\)\s*route\s*([-\d.]+)ns', chunk)
            if delay:
                entry["data_path_delay_ns"] = float(delay.group(1))
                entry["logic_delay_ns"] = float(delay.group(2))
                entry["route_delay_ns"] = float(delay.group(3))
            else:
                simple = re.search(r'Data Path Delay:\s*([-\d.]+)ns', chunk)
                entry["data_path_delay_ns"] = float(simple.group(1)) if simple else None
                entry["logic_delay_ns"] = entry["route_delay_ns"] = None
            levels = re.search(r'Logic Levels:\s*(\d+)\s*\(([^)]*)\)', chunk)
            entry["logic_levels"] = int(levels.group(1)) if levels else None
            breakdown = {}
            if levels:
                for part in levels.group(2).split():
                    cell = re.match(r'([A-Za-z0-9_]+)=(\d+)', part)
                    if cell:
                        breakdown[cell.group(1)] = int(cell.group(2))
            entry["breakdown"] = breakdown
            paths.append(entry)
            if len(paths) >= max_paths:
                break
        return paths

    @staticmethod
    def parse_high_fanout_nets(content, max_nets=10):
        """Parse report_high_fanout_nets 'Fanout Summary': [{net, fanout, driver, worst_slack_ns}]."""
        nets = []
        row = re.compile(r'^\|\s*(\S+)\s*\|\s*(\d+)\s*\|\s*(\S+)\s*\|\s*(-?[\d.]+|NA)\s*\|', re.MULTILINE)
        for match in row.finditer(content or ""):
            slack = match.group(4)
            nets.append({
                "net": match.group(1), "fanout": int(match.group(2)), "driver": match.group(3),
                "worst_slack_ns": None if slack == "NA" else float(slack),
            })
            if len(nets) >= max_nets:
                break
        return nets

    @staticmethod
    def parse_control_sets(content):
        """
        Parse report_control_sets: {"total": number of control sets, "ff_distribution": [{clock_enable,
        sync_set_reset, async_set_reset, registers}] for the rows that hold registers}.
        """
        result = {"total": None, "ff_distribution": []}
        total = re.search(r'\|\s*Total control sets\s*\|\s*(\d+)\s*\|', content or "")
        if total:
            result["total"] = int(total.group(1))
        row = re.compile(r'^\|\s*(Yes|No)\s*\|\s*(Yes|No)\s*\|\s*(Yes|No)\s*\|\s*(\d+)\s*\|\s*\d+\s*\|', re.MULTILINE)
        for match in row.finditer(content or ""):
            registers = int(match.group(4))
            if registers:
                result["ff_distribution"].append({
                    "clock_enable": match.group(1) == "Yes", "sync_set_reset": match.group(2) == "Yes",
                    "async_set_reset": match.group(3) == "Yes", "registers": registers,
                })
        return result

    @staticmethod
    def parse_clock_period_ns(xdc_text):
        """Return the first `create_clock -period X` value (ns) in an XDC string, or None."""
        match = re.search(r'create_clock\s+[^\n]*?-period\s+([\d.]+)', xdc_text or "")
        return float(match.group(1)) if match else None

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

    _VECTOR_CYCLE_RE = re.compile(r'\bcycle=(-?\d+)\b')
    _VECTOR_SIGNAL_RE = re.compile(r'(\w+)=(-?\d+)\s*\(expected\s*(-?\d+)\)')

    @classmethod
    def detect_latency_shift(cls, log_content, max_shift=4, min_samples=5, min_match_ratio=0.8):
        """
        Inspect a simulation log's per-vector `FAIL` lines (format
        "cycle=N ... | sig=actual (expected expected) | FAIL", one or more
        `sig=actual (expected expected)` groups per line) and detect whether
        the design's actual output is a *constant* cycle shift of the
        expected output — the signature of a pipeline register stage that
        was added or removed relative to the reference's latency contract.

        For each signal name found, and for each integer shift k in
        range(-max_shift, max_shift+1) excluding 0, checks how often
        actual[cycle] == expected[cycle + k] across all cycles where both
        values are present. The best (signal, k) pair that clears
        min_samples/min_match_ratio is reported, preferring the smallest
        abs(k) on ties.

        Returns a dict: {"detected": False} if nothing crossed the
        threshold, otherwise {"detected": True, "signal": str,
        "shift_cycles": int, "match_ratio": float, "samples": int}.
        """
        per_signal_actual = {}
        per_signal_expected = {}

        for line in log_content.splitlines():
            if 'FAIL' not in line:
                continue
            cycle_match = cls._VECTOR_CYCLE_RE.search(line)
            if not cycle_match:
                continue
            cycle = int(cycle_match.group(1))
            for sig, actual, expected in cls._VECTOR_SIGNAL_RE.findall(line):
                per_signal_actual.setdefault(sig, {})[cycle] = int(actual)
                per_signal_expected.setdefault(sig, {})[cycle] = int(expected)

        best = None
        for sig, actual_map in per_signal_actual.items():
            expected_map = per_signal_expected.get(sig, {})
            if not expected_map:
                continue
            for k in range(-max_shift, max_shift + 1):
                if k == 0:
                    continue
                matches = 0
                samples = 0
                for cycle, actual_val in actual_map.items():
                    expected_val = expected_map.get(cycle + k)
                    if expected_val is None:
                        continue
                    samples += 1
                    if actual_val == expected_val:
                        matches += 1
                if samples < min_samples:
                    continue
                match_ratio = matches / samples
                if match_ratio < min_match_ratio:
                    continue
                candidate = (sig, k, match_ratio, samples)
                if best is None:
                    best = candidate
                else:
                    best_sig, best_k, best_ratio, best_samples = best
                    if (match_ratio, -abs(k), samples) > (best_ratio, -abs(best_k), best_samples):
                        best = candidate

        if best is None:
            return {"detected": False}

        sig, k, match_ratio, samples = best
        return {
            "detected": True,
            "signal": sig,
            "shift_cycles": k,
            "match_ratio": match_ratio,
            "samples": samples,
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
