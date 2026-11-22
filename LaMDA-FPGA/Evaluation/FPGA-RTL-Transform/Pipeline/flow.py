"""
Vivado flow of ONE design.

DesignFlow.run(dirs, module, design_present) runs, in this order, and stops at the first failing stage:

    simulation (xsim, cycle-exact testbench)  ->  synthesis  ->  implementation (only with stage "implementation")

and returns a plain dict ("flow result", see run()): pass/fail of every stage, the metrics used to judge the
design (LUT, FF, DSP, BRAM, Fmax), and the report digest used as feedback (Analysis/ReportDigest.py).
The same flow measures the reference RTL (attempt_0) and every LLM attempt, so their numbers are comparable.

This file only RUNS the tools. Reading their reports (metrics, digest) is done by Pipeline/metrics.py.

`dirs` is the per-attempt directory layout: {"base", "design_files", "vivado_project", "vivado_logs"}.
"""

import os
import re
import time

# Importing this module imports the Pipeline package first, which puts Analysis/ and LaMDA-FPGA/ on sys.path.
from EDA_Interface.Vivado import VivadoInterface
from utils import check_simulation_passed
from FabricAnalysis import FabricReportParser
from Pipeline.metrics import MetricsCollector, read_text

# The testbenches run about 50 us of simulated time; the Vivado interface defaults to 10 us.
os.environ.setdefault("VIVADO_SIM_RUNTIME", "200000ns")


class DesignFlow:
    """Runs the Vivado flow of one design; the reports are interpreted by a MetricsCollector."""

    def __init__(self, fpga_part, stage, clock_period_ns, verbose=False):
        self.fpga_part = fpga_part
        self.stage = stage                      # "synthesis" or "implementation": where the metrics come from
        self.clock_period_ns = clock_period_ns
        self.verbose = verbose
        self.vivado = VivadoInterface()
        self.collector = MetricsCollector(stage, clock_period_ns, FabricReportParser(), verbose)

    # ------------------------------------------------------------------ Vivado stages

    def _vivado_once(self, dirs, module, mode):
        try:
            self.vivado.run_vivado(
                project_name=module, project_dir=dirs["vivado_project"], log_path=dirs["vivado_logs"],
                design_files_dir=dirs["design_files"], fpga_part=self.fpga_part, mode=mode, verbose=self.verbose,
            )
            return True
        except Exception as e:
            print(f"Vivado stage '{mode}' failed with error: {e}")
            return False

    def _vivado(self, dirs, module, mode):
        """
        One Vivado run (mode 'sim', 'synth' or 'impl'); False when Vivado reports a failure.
        A synth / impl run that fails WITHOUT any 'ERROR:' line in its log is a tool crash (not a design problem):
        it is repeated once before the failure is reported.
        """
        if self._vivado_once(dirs, module, mode):
            return True
        if mode in ("synth", "impl") and "ERROR:" not in read_text(os.path.join(dirs["vivado_logs"], f"{module}_{mode}.log")):
            print(f"Vivado stage '{mode}' stopped without an error message; retrying once.")
            return self._vivado_once(dirs, module, mode)
        return False

    def _simulate(self, dirs, module):
        if not self._vivado(dirs, module, "sim"):
            return False
        return check_simulation_passed(os.path.join(dirs["vivado_logs"], f"{module}_sim.log"), verbose=self.verbose)

    @staticmethod
    def vivado_version(dirs, module):
        """Vivado version printed in the synthesis (or else simulation) log; None when not found."""
        for mode in ("synth", "sim"):
            log = read_text(os.path.join(dirs["vivado_logs"], f"{module}_{mode}.log"))
            match = re.search(r"Vivado\s+v?(\d{4}\.\d+(?:\.\d+)?)", log)
            if match:
                return match.group(1)
        return None

    # ------------------------------------------------------------------ the flow

    def run(self, dirs, module, design_present=True):
        """
        Simulation -> synthesis (-> implementation) of the design in dirs["design_files"].

        Flow result (dict):
          sim_passed, synthesis_ok, implementation_ok (None when not run), tool_ok (every stage ran and the
          metrics could be read), failure (None | "no_design_extracted" | "simulation" | "synthesis" |
          "implementation" | "reports_missing"), metrics, details, digest, log_facts, vivado_version, eda_time_s.
        """
        out = {
            "sim_passed": False, "synthesis_ok": False, "implementation_ok": None, "tool_ok": False,
            "failure": None, "metrics": None, "details": {}, "digest": {}, "log_facts": {}, "vivado_version": None,
        }
        started = time.time()
        try:
            if not design_present:
                out["failure"] = "no_design_extracted"
                return out

            if self.verbose:
                print("\n=== Running Simulation ===")
            out["sim_passed"] = self._simulate(dirs, module)
            if not out["sim_passed"]:
                out["failure"] = "simulation"
                return out

            if self.verbose:
                print("\n=== Running Synthesis ===")
            out["synthesis_ok"] = self._vivado(dirs, module, "synth")
            if not out["synthesis_ok"]:
                out["failure"] = "synthesis"
                return out
            out["vivado_version"] = self.vivado_version(dirs, module)

            if self.stage == "implementation":
                if self.verbose:
                    print("\n=== Running Implementation ===")
                out["implementation_ok"] = self._vivado(dirs, module, "impl")
                if not out["implementation_ok"]:
                    out["failure"] = "implementation"
                    return out

            collected = self.collector.collect_metrics(dirs)
            if collected is None:
                out["failure"] = "reports_missing"
                return out
            out["metrics"], out["details"] = collected
            out["digest"] = self.collector.build_digest(dirs, module, out["metrics"], out["details"])
            out["log_facts"] = self.collector.collect_log_facts(dirs, module)
            out["tool_ok"] = True
            return out
        finally:
            out["eda_time_s"] = time.time() - started
