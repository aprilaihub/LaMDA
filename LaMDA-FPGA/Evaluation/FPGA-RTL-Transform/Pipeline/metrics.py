"""
Metrics and report digest of ONE design, read from the Vivado reports that the flow (Pipeline/flow.py) wrote.

Split from flow.py so that running the tools (flow.py) and interpreting their reports (this file) can evolve
independently. Nothing here starts Vivado: every method only reads files of the per-attempt directory layout
    dirs = {"base", "design_files", "vivado_project", "vivado_logs"}
and is therefore testable offline with real report fixtures (Tests/test_flow_metrics.py).

Stage awareness: `stage` is "synthesis" (--stop_stage synth) or "implementation" (--stop_stage impl). Metrics and
digest are always taken from the reports of THAT stage only, so a synthesis-only run never looks at (or reports
numbers from) implementation files.
"""

import math
import os

# Importing this module imports the Pipeline package first, which puts Analysis/ and LaMDA-FPGA/ on sys.path.
from FabricAnalysis import FabricReportParser
from MappingAnalysis import MappingAnalyzer
from LogMining import LogMiner
import ReportDigest

# Flip-flop primitives summed into the FF metric.
FF_PRIMITIVES = ("FDRE", "FDCE", "FDPE", "FDSE", "FDC", "FDP")


def read_text(path):
    """Text of a file; '' when it does not exist."""
    if not os.path.exists(path):
        return ""
    with open(path, "r", errors="ignore") as f:
        return f.read()


def read_report(dirs, stage, name):
    """Text of <vivado_project>/reports/<stage>/<name> ('' when absent)."""
    return read_text(os.path.join(dirs["vivado_project"], "reports", stage, name))


class MetricsCollector:
    """Turns the reports of one stage into the comparable metrics, the parsed details and the report digest."""

    def __init__(self, stage, clock_period_ns, parser=None, verbose=False):
        self.stage = stage                      # "synthesis" or "implementation": where the metrics come from
        self.clock_period_ns = clock_period_ns
        self.parser = parser or FabricReportParser()
        self.verbose = verbose

    # ------------------------------------------------------------------ metrics

    def collect_metrics(self, dirs):
        """
        Metrics of the design from the reports of self.stage; None when the reports are missing.
        Returns (metrics, details): `metrics` is what is compared (and stored in the results), `details` keeps the
        parsed reports for the digest and the feedback.
        """
        stage = self.stage
        util = self.parser.parse_utilization_report(read_report(dirs, stage, "utilization_report.txt"))
        primitives, resources = util.get("primitives", {}), util.get("resources", {})
        if not primitives and not resources:
            return None

        timing_text = read_report(dirs, stage, "timing_report.txt")
        timing = self.parser.parse_timing_report(timing_text) if timing_text else {}
        if timing_text:
            timing["critical_path"] = self.parser.parse_critical_path_cells(timing_text)
        summary_text = read_report(dirs, stage, "timing_summary.txt")
        summary = self.parser.parse_timing_summary(summary_text) if summary_text else {}
        evidence = self._timing_evidence(dirs, stage)

        wns = summary.get("wns_ns")
        if wns is None:
            wns = timing.get("slack")
        if wns is not None and not math.isfinite(wns):
            wns = None
        critical_path = timing.get("critical_path") or {}
        period = self.clock_period_ns
        pulse = evidence.get("pulse_width") or {}
        min_period = pulse.get("min_period_ns")

        # Effective clock period = the larger of (a) the period the worst register-to-register path allows
        # (clock period - WNS) and (b) the primitive minimum period (DSP48E1 / RAMB / BUFG 'Min Period'
        # pulse-width check). (b) is the only timing evidence when WNS is NA, i.e. when no constrained
        # register-to-register path remains (every register absorbed into a DSP48E1 or block RAM).
        effective_period, basis = None, "none"
        if period and wns is not None and (period - wns) > 0:
            effective_period, basis = period - wns, "reg2reg_wns"
        if min_period and min_period > 0 and (effective_period is None or min_period > effective_period):
            effective_period, basis = min_period, "min_period"

        ff_total = sum(int(primitives.get(name, 0)) for name in FF_PRIMITIVES)
        if not primitives:
            ff_total = int(resources.get("registers", {}).get("used", 0))
        metrics = {
            "LUT_USED": int(resources.get("luts", {}).get("used", 0)),
            "FF_TOTAL": ff_total,
            "DSP48E1": int(primitives.get("DSP48E1", resources.get("dsp", {}).get("used", 0))),
            "BRAM": resources.get("bram", {}).get("used", 0),
            "wns_ns": wns,
            "tns_ns": summary.get("tns_ns"),
            "logic_levels": critical_path.get("logic_levels_total") if wns is not None else None,
            "data_path_delay_ns": timing.get("data_path_delay_ns") if wns is not None else None,
            "clock_period_ns": period,
            "fmax_mhz": round(1000.0 / effective_period, 2) if effective_period else None,
            "timing_basis": basis,
            "min_period_ns": min_period,
            "timing_slack_ns": round(period - effective_period, 3) if (period and effective_period) else None,
            "wpws_ns": summary.get("wpws_ns", pulse.get("wpws_ns")),
            "stage": stage,
        }
        details = {"primitives": primitives, "timing": timing, "timing_evidence": evidence}
        return metrics, details

    def _timing_evidence(self, dirs, stage):
        """Pulse width (DSP/BRAM minimum period), worst paths, high-fanout nets, control sets. Each one is optional."""
        evidence = {}
        try:
            for key, name, parse in (
                ("pulse_width", "pulse_width.txt", self.parser.parse_pulse_width_report),
                ("timing_paths", "timing_paths.txt", lambda t: self.parser.parse_timing_paths(t, max_paths=3)),
                ("high_fanout", "high_fanout.txt", lambda t: self.parser.parse_high_fanout_nets(t, max_nets=5)),
                ("control_sets", "control_sets.txt", self.parser.parse_control_sets),
            ):
                text = read_report(dirs, stage, name)
                if text:
                    evidence[key] = parse(text)
        except Exception as e:  # diagnostic only; never fail the flow
            if self.verbose:
                print(f"Timing evidence parsing failed: {e}")
        return evidence

    # ------------------------------------------------------------------ log facts

    def collect_log_facts(self, dirs, module):
        """
        What the tools DID, mined from the logs and from report_design_analysis (Analysis/LogMining.py):
            {"stage", "synth": {...}, "impl": {...} | None, "design_analysis": {...} | None}
        Stage rule: a synthesis-only run reads the synthesis log and the synthesis design analysis only, so "impl"
        is None and no implementation file is opened. {} on any problem (diagnostic only, never fails the flow).
        """
        try:
            facts = {"stage": self.stage,
                     "synth": LogMiner.mine_synth_log(read_text(os.path.join(dirs["vivado_logs"], f"{module}_synth.log"))),
                     "impl": None, "design_analysis": None}
            if self.stage == "implementation":
                facts["impl"] = LogMiner.mine_impl_log(
                    read_text(os.path.join(dirs["vivado_logs"], f"{module}_impl.log")))
            analysis_text = read_report(dirs, self.stage, "design_analysis.txt")
            if analysis_text:
                facts["design_analysis"] = LogMiner.mine_design_analysis(analysis_text)
            return facts
        except Exception as e:  # diagnostic only; never fail the flow
            if self.verbose:
                print(f"Log mining failed: {e}")
            return {}

    # ------------------------------------------------------------------ digest

    def build_digest(self, dirs, module, metrics, details):
        """Compressed digest of the reports (hierarchy, memories, DSP mapping, timing); {} on any problem."""
        try:
            synth_log = read_text(os.path.join(dirs["vivado_logs"], f"{module}_synth.log"))
            facts = MappingAnalyzer.analyze(synth_log, read_report(dirs, "synthesis", "utilization_report.txt"))
            evidence = details.get("timing_evidence") or {}
            return ReportDigest.build_digest(
                hier_text=read_report(dirs, self.stage, "hier_utilization.txt"),
                facts=facts,
                dsp_mapping=self.parser.parse_dsp_mapping_table(synth_log),
                timing_paths=evidence.get("timing_paths"),
                high_fanout=evidence.get("high_fanout"),
                control_sets=evidence.get("control_sets"),
                wns_ns=metrics.get("wns_ns"),
            )
        except Exception as e:  # diagnostic only; never fail the flow
            if self.verbose:
                print(f"Report digest failed: {e}")
            return {}
