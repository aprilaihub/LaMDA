"""
FPGA-Fabric-RTL-Gen Evaluation Pipeline.

Generates Verilog RTL that is explicitly aware of the AMD/Xilinx 7-Series FPGA
fabric (LUTs, CARRY4 fast-carry chains, DSP48E1, MUXF7/F8 wide muxes,
SRLC32E/SRL16E shift-register LUTs, block RAM). After generation, Vivado
synthesis is used to verify that the expected fabric primitives were actually
inferred; a bounded self-correction loop re-prompts the LLM (up to
--max_attempts total attempts) whenever functional simulation fails OR the
expected primitives are missing/insufficient.

This pipeline is fully self-contained: it does not import from, or modify,
Evaluation/ResBench/** or Evaluation/Custom/**. It only reuses the shared,
unmodified-behavior helpers in LaMDA-FPGA/utils.py, LLM_Interface/LLMClient.py,
and EDA_Interface/Vivado.py.
"""

import os
import re
import sys
import time
import json
import argparse
import tempfile
from datetime import datetime

FABRIC_RTL_GEN_DIR = os.path.dirname(os.path.abspath(__file__))
LAMDA_FPGA_ROOT = os.path.dirname(os.path.dirname(FABRIC_RTL_GEN_DIR))

sys.path.append(LAMDA_FPGA_ROOT)
sys.path.append(os.path.join(FABRIC_RTL_GEN_DIR, "Analysis"))

from LLM_Interface.LLMClient import LLMClient
from EDA_Interface.Vivado import VivadoInterface
from utils import (
    create_outputs_folder,
    extract_script,
    extract_design_info,
    check_simulation_passed,
    load_fabric_primer,
    build_fabric_system_prompt,
    build_fabric_design_prompt,
    build_primitive_instantiation_prompt,
    check_fabric_expectations,
)
from FabricAnalysis import FabricReportParser, InfEncoder

DATASET_FILE = os.path.join(FABRIC_RTL_GEN_DIR, "Dataset", "fabric_problems.json")
PRIMER_FILE = os.path.join(FABRIC_RTL_GEN_DIR, "fabric_primer_7series.md")
OUTPUTS_DIR = os.path.join(FABRIC_RTL_GEN_DIR, "Outputs")

STYLES = ("baseline", "fabric_aware", "explicit_primitive")

# Max number of extracted log entries (failing vectors / error lines) shown
# per self-correction feedback note, to keep the LLM prompt compact.
FEEDBACK_MAX_ENTRIES = 10

# System prompt for this pipeline only (deliberately NOT the shared utils.SYSTEM_PROMPT
# / utils.DESIGN_PROMPT, which are also reused unmodified by Evaluation/ResBench,
# Evaluation/Custom, and Tests). Kept local so this pipeline's system prompt can
# evolve independently, per its own "fully self-contained" design goal (see module
# docstring above). This also folds in the output-format/syntax rules that used to
# live in the shared DESIGN_PROMPT, since they are static Verilog-coding rules that
# belong at the system level, not the per-attempt user prompt.
FABRIC_SYSTEM_PROMPT = \
    """# Role
You are an expert in FPGA design and Verilog (IEEE 1364) RTL coding, targeting AMD/Xilinx 7-Series devices (Artix-7, Kintex-7, Virtex-7, Zynq-7000) synthesized and simulated with Vivado.

# Precision
Be precise and consistent with Verilog syntax and semantics so the generated code compiles and elaborates cleanly under Vivado. Target Verilog-2001 (IEEE 1364-2001) constructs within Vivado's synthesizable subset. Avoid non-synthesizable constructs inside the design (e.g. delay controls such as #10, `real` variables, or unsynthesizable `initial` blocks) and avoid SystemVerilog-only syntax unless explicitly instructed to use it.

# Correctness Rules
- Use non-blocking assignments (<=) exclusively inside sequential always @(posedge clk...) (or similar clocked) blocks, and blocking assignments (=) exclusively inside combinational always @(*) (or equivalent) blocks. Never mix the two assignment styles within the same always block.
- Never rename, reorder, add, remove, or change the bit width of any port in the module header given in the prompt. Reproduce the exact declared module header verbatim in the generated module. All inputs and outputs must be declared in the top module.
- When using begin/end blocks, follow this style:
  ```verilog
  if (condition) begin
      // code
  end else begin
      // code
  end
  ```

# Output Format
Design the Verilog code of the provided module, following the reported information. Provide these outputs, using these exact markers in the response:
1. Design name: start marker '// Start Design name', end marker '// End Design name'.
2. Verilog design: start marker '// Start Verilog Design', end marker '// End Verilog Design'.
The top module name must be the same as the design name.

# Behavior
Follow what is asked and do not ask clarifying questions; make the most reasonable assumption and proceed. Respond with ONLY the two marker-delimited sections above and no additional prose or explanation outside those markers. Do not wrap the Verilog design section itself in markdown code fences."""


def load_design_entry(dataset_path, design_id):
    """Load a single design entry from Dataset/fabric_problems.json by ID."""
    with open(dataset_path, 'r') as f:
        data = json.load(f)
    for entry in data.get("designs", []):
        if entry.get("ID") == design_id:
            return entry
    return None


class FabricAwarePipeline:
    """Main pipeline orchestrator for fabric-aware RTL generation and verification."""

    def __init__(self, args):
        self.args = args
        self.llm_client = LLMClient(args.model)
        self.vivado = VivadoInterface()
        self.parser = FabricReportParser()
        self.primer_text = load_fabric_primer(PRIMER_FILE)
        if args.style == "baseline":
            # No fabric knowledge at all: the naive, fabric-unaware control.
            self.system_prompt = FABRIC_SYSTEM_PROMPT
        else:
            # Both fabric_aware and explicit_primitive get the full primer;
            # they differ in how the *design* prompt instructs the LLM to use it.
            self.system_prompt = build_fabric_system_prompt(FABRIC_SYSTEM_PROMPT, self.primer_text)
        self.design_entry = None
        self.design_name = None
        self.total_tokens = 0
        self.total_llm_time = 0.0
        self.attempts = []
        # Unique per-invocation identifier (timestamp + PID) so that re-running
        # the pipeline never overwrites a previous run's outputs/Vivado logs,
        # even for the same design_id and style.
        self.run_id = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{os.getpid()}"

    def run(self):
        """Execute the fabric-aware pipeline with a bounded self-correction loop."""
        dataset_path = self.args.dataset_file or DATASET_FILE
        self.design_entry = load_design_entry(dataset_path, self.args.design_id)
        if self.design_entry is None:
            print(f"Design ID {self.args.design_id} not found in dataset: {dataset_path}")
            return 0

        self.design_name = self.design_entry["module"]

        # The style-specific reminder/hint (generic fabric-efficiency reminder
        # or structural-instantiation reminder + per-design fabric_hint/
        # primitive_hint) is static for the whole run: it depends only on
        # --style and this design_entry, never on the attempt number or prior
        # corrections. Fold it into the system prompt once here (baseline adds
        # nothing extra, since the output-format/syntax rules already live in
        # FABRIC_SYSTEM_PROMPT), so the per-attempt user prompt carries only
        # the dynamic content (problem statement, and any previous-attempt/
        # correction block on retries).
        style_design_prompt = self._build_style_design_prompt()
        if style_design_prompt:
            self.system_prompt = f"{self.system_prompt}\n\n{style_design_prompt}"

        if self.args.verbose:
            print(f"Run directory (unique per invocation): {self._run_dir()}")

        max_attempts = max(1, self.args.max_attempts)
        final_verdict = "FAIL"
        eda_total_time = 0.0
        previous_code = None
        correction_note = None
        feedback_info = None

        try:
            for attempt_index in range(max_attempts):
                attempt_dir = self._attempt_dirs(attempt_index)
                create_outputs_folder(*attempt_dir.values())

                if self.args.verbose:
                    print(f"\n=== Attempt {attempt_index + 1}/{max_attempts}: Generating Verilog Design ===")
                self._generate_design(attempt_dir, attempt_index, previous_code, correction_note, feedback_info)
                self._write_fixed_files(attempt_dir)

                eda_start = time.time()

                if self.args.verbose:
                    print("\n=== Running Simulation ===")
                sim_passed = self._run_simulation(attempt_dir)

                primitives = {}
                resources = {}
                per_primitive = {}
                constraints_met = True
                per_constraint = {}
                fabric_met = False
                synth_ok = False
                impl_ok = None
                timing_info = {}
                critical_warnings_info = {}
                dsp_mapping = []

                if sim_passed:
                    if self.args.verbose:
                        print("\n=== Running Synthesis ===")
                    synth_ok = self._run_synthesis(attempt_dir)
                    if synth_ok:
                        primitives = self._parse_primitives(attempt_dir)
                        resources = self._parse_resources(attempt_dir)
                        fabric_met, per_primitive = check_fabric_expectations(
                            primitives, self.design_entry.get("expected_primitives", {})
                        )
                        # Always parsed (not just as a failure fallback): the confirmed
                        # multi-driven-net case shows synthesis can report success while
                        # hiding a critical warning, so these signals must be captured
                        # for every successful synthesis, regardless of pass/fail verdict.
                        timing_info = self._parse_timing(attempt_dir)
                        critical_warnings_info = self._parse_critical_warnings(attempt_dir)
                        dsp_mapping = self._parse_dsp_mapping(attempt_dir)
                        constraints_met, per_constraint = self._evaluate_hard_constraints(
                            primitives, timing=timing_info,
                            critical_warnings_info=critical_warnings_info,
                            dsp_mapping=dsp_mapping,
                            resources=resources,
                        )

                eda_total_time += time.time() - eda_start

                attempt_record = {
                    "attempt": attempt_index + 1,
                    "sim_passed": sim_passed,
                    "synthesis_ok": synth_ok,
                    "actual_primitives": primitives,
                    "actual_resources": resources,
                    "expected_primitives": self.design_entry.get("expected_primitives", {}),
                    "fabric_expectations_met": fabric_met,
                    "per_primitive": per_primitive,
                    "hard_constraints": self.design_entry.get("hard_constraints", {}),
                    "hard_constraints_met": constraints_met,
                    "per_constraint": per_constraint,
                    "timing": timing_info,
                    "critical_warnings_total": critical_warnings_info.get("critical_warnings_total"),
                    "multi_driven_nets": critical_warnings_info.get("multi_driven_nets", {}),
                    "dsp_mapping": dsp_mapping,
                    "implementation_ok": None,
                }

                attempt_success = self._is_attempt_successful(
                    sim_passed, synth_ok, fabric_met, constraints_met
                )
                is_last_attempt = attempt_index == max_attempts - 1

                run_impl = self.args.stop_stage == "impl" and (
                    attempt_success or (is_last_attempt and synth_ok)
                )
                if run_impl:
                    if self.args.verbose:
                        label = "Running Implementation" if attempt_success else \
                            "Running Implementation (final attempt, reporting only)"
                        print(f"\n=== {label} ===")
                    impl_ok = self._run_implementation(attempt_dir)
                    attempt_record["implementation_ok"] = impl_ok
                elif self.args.verbose and synth_ok and self.args.stop_stage == "synth":
                    print("\n=== Skipping Implementation (--stop_stage=synth) ===")

                self.attempts.append(attempt_record)

                if attempt_success:
                    final_verdict = "PASS"
                    break

                if is_last_attempt:
                    final_verdict = "FAIL"
                    break

                # Prepare a corrective retry.
                design_file = os.path.join(attempt_dir["design_files"], f"{self.design_name}.v")
                if os.path.exists(design_file):
                    with open(design_file, 'r') as f:
                        previous_code = f.read()
                correction_note, feedback_info = self._build_correction_note(
                    attempt_dir, sim_passed, synth_ok, per_primitive,
                    constraints_met, per_constraint, timing_info
                )

            self._save_reports(final_verdict, eda_total_time)

            if self.args.verbose:
                print(f"\n=== Final Verdict: {final_verdict} (attempts used: {len(self.attempts)}) ===")

            return 1 if final_verdict == "PASS" else 0

        except Exception as e:
            print(f"Pipeline failed with error: {e}")
            import traceback
            traceback.print_exc()
            return 0

    def _run_dir(self):
        """
        Return the output directory for this specific pipeline invocation:

        Outputs/<style>/design_<id>_<module>/run_<timestamp>_<pid>/

        Namespacing by design_id + module + a unique run_id guarantees that no
        two invocations (same design rerun, different design, or concurrent
        runs) ever share a directory, so Vivado logs and all other artifacts
        from previous runs are never overwritten.
        """
        return os.path.join(
            OUTPUTS_DIR,
            self.args.style,
            f"design_{self.args.design_id}_{self.design_name}",
            f"run_{self.run_id}",
        )

    def _attempt_dirs(self, attempt_index):
        """Return the per-attempt output directory layout (nested under this run's unique directory)."""
        base = os.path.join(self._run_dir(), f"attempt_{attempt_index + 1}")
        return {
            "base": base,
            "design_files": os.path.join(base, "Design_Files"),
            "vivado_project": os.path.join(base, "vivado_project"),
            "vivado_logs": os.path.join(base, "vivado_logs"),
        }

    def _is_attempt_successful(self, sim_passed, synth_ok, fabric_met, constraints_met):
        """
        Decide whether an attempt counts as successful (stops the retry loop).

        For the 'baseline' style, fabric-primitive expectations AND hard
        resource constraints are recorded as control data points but never
        required for success: baseline is deliberately not told to target any
        primitive or resource budget, so a fabric/constraint mismatch should
        not trigger a self-correction retry. For 'fabric_aware' and
        'explicit_primitive', both fabric expectations and hard constraints
        must be met.
        """
        if not (sim_passed and synth_ok):
            return False
        if self.args.style == "baseline":
            return True
        return fabric_met and constraints_met

    def _build_base_prompt(self):
        """Assemble the module/Problem/Module header block from the dataset entry."""
        lines = []
        for key in ("module", "Problem", "Module header"):
            if key in self.design_entry:
                lines.append(f"{key}: {self.design_entry[key]}")
        constraints_prompt = self._build_constraint_guidance_prompt()
        if constraints_prompt:
            lines.append(constraints_prompt)
        return "\n".join(lines)

    def _build_constraint_guidance_prompt(self):
        """Render optional hard constraints from the dataset into the user prompt."""
        constraints = self.design_entry.get("hard_constraints", {}) or {}
        if not constraints:
            return ""

        lines = ["Hard constraints:"]

        required = constraints.get("required_primitives_min", {}) or {}
        if required:
            lines.append("- Required primitive minimums:")
            for name, minimum in required.items():
                lines.append(f"  - {name} >= {minimum}")

        caps = constraints.get("resource_caps", {}) or {}
        if caps:
            lines.append("- Resource caps:")
            for name, maximum in caps.items():
                lines.append(f"  - {name} <= {maximum}")

        forbidden = constraints.get("forbidden_primitives", []) or []
        if forbidden:
            lines.append("- Forbidden primitives:")
            for name in forbidden:
                lines.append(f"  - {name} must be 0")

        timing = constraints.get("timing", {}) or {}
        min_slack_ns = timing.get("min_slack_ns")
        if min_slack_ns is not None:
            lines.append("- Timing:")
            lines.append(
                f"  - Post-synthesis worst negative slack must be >= {min_slack_ns} ns "
                "against the provided clock constraint (timing must be MET, not VIOLATED)."
            )

        max_critical_warnings = constraints.get("max_critical_warnings")
        if max_critical_warnings is not None:
            lines.append("- Critical warnings:")
            lines.append(
                f"  - Synthesis must emit at most {max_critical_warnings} CRITICAL WARNING message(s) "
                "(e.g. avoid multi-driven nets; a critical warning can let synthesis report success "
                "while producing an incorrect netlist)."
            )

        dsp_register_config = constraints.get("dsp_register_config", {}) or {}
        if dsp_register_config:
            lines.append("- DSP register configuration (as reported in Vivado's DSP Final Report):")
            for reg_name, expected in dsp_register_config.items():
                state = "enabled (1)" if expected else "disabled (0)"
                lines.append(f"  - {reg_name} must be {state}")

        primitive_alternatives = constraints.get("primitive_alternatives", {}) or {}
        at_least_one_of = primitive_alternatives.get("at_least_one_of", []) or []
        min_total = primitive_alternatives.get("min_total")
        if at_least_one_of and min_total is not None:
            lines.append("- Primitive alternatives (any combination is acceptable):")
            lines.append(
                f"  - Combined count across {{{', '.join(at_least_one_of)}}} must be >= {min_total}"
            )

        dsp_datapath_pattern = constraints.get("dsp_datapath_pattern")
        if dsp_datapath_pattern:
            lines.append("- DSP datapath packing:")
            lines.append(
                "  - At least one inferred DSP48E1 instance's mapping (as reported in "
                f"Vivado's DSP Final Report) must match the pattern: {dsp_datapath_pattern} "
                "(i.e. the operation must be packed into a single DSP instance, not split "
                "across the DSP and fabric logic)."
            )

        return "\n".join(lines) if len(lines) > 1 else ""

    def _build_style_design_prompt(self):
        """Build the style-specific fabric reminder/hint for the system prompt.

        Output-format/syntax rules already live in FABRIC_SYSTEM_PROMPT, so this
        only returns the per-style fabric guidance:
        - baseline: nothing extra (the naive, fabric-unaware control).
        - fabric_aware: generic fabric-efficiency reminder + per-design
          fabric_hint, letting synthesis infer primitives.
        - explicit_primitive: instruction to structurally instantiate the
          target UNISIM primitive(s) + per-design primitive_hint describing
          exact wiring.
        """
        if self.args.style == "baseline":
            return ""
        if self.args.style == "explicit_primitive":
            primitive_hint = self.design_entry.get("primitive_hint")
            return build_primitive_instantiation_prompt("", primitive_hint).strip()
        fabric_hint = self.design_entry.get("fabric_hint")
        return build_fabric_design_prompt("", fabric_hint).strip()

    def _generate_design(self, attempt_dir, attempt_index, previous_code, correction_note, feedback_info):
        """Generate the Verilog design (only) for the current attempt."""
        base_prompt = self._build_base_prompt()

        prompt_parts = [base_prompt]
        if previous_code and correction_note:
            prompt_parts.append(
                "Your previous Verilog design attempt was:\n"
                f"```verilog\n{previous_code}\n```\n\n"
                f"{correction_note}\n"
                "Please provide a corrected design that still implements the same "
                "functional specification above, keeping the exact same module "
                "name and port list."
            )
        full_prompt = "\n\n".join(prompt_parts)

        start_time = time.time()
        content, tokens = self.llm_client.generate_content(
            prompt=full_prompt,
            system_prompt=self.system_prompt,
            max_tokens=self.args.max_tokens,
            temperature=self.args.temperature,
            top_p=self.args.top_p,
        )
        exec_time = time.time() - start_time
        self.total_tokens += tokens
        self.total_llm_time += exec_time

        self._write_llm_interaction(
            attempt_dir, attempt_index, feedback_info, full_prompt, content, tokens, exec_time
        )

        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.txt') as tmp:
            tmp.write(content)
            tmp_path = tmp.name

        try:
            # The dataset fixes the required module name (matching the
            # pre-authored testbench instantiation); we always save under
            # that name regardless of what the LLM reports as its design name.
            design_file = os.path.join(attempt_dir["design_files"], f"{self.design_name}.v")
            extract_script(
                tmp_path, design_file,
                "// Start Verilog Design\n", "\n// End Verilog Design",
                verbose=False
            )
        finally:
            os.unlink(tmp_path)

        if self.args.verbose:
            print(f"Design generated for '{self.design_name}' ({tokens} tokens, {exec_time:.2f}s)")

    def _write_llm_interaction(self, attempt_dir, attempt_index, feedback_info,
                                user_prompt, llm_response_raw, tokens, exec_time):
        """
        Persist the full LLM prompt/response for this attempt to its own file,
        llm_interaction.json (a peer of Design_Files/vivado_project/vivado_logs
        inside the attempt directory). This keeps a complete, per-attempt
        record of exactly what was sent to the LLM and what correction
        feedback (if any) was applied, separate from the aggregated
        fabric_report.json.
        """
        record = {
            "attempt": attempt_index + 1,
            "style": self.args.style,
            "feedback_mode": self.args.feedback_mode,
            "feedback_applied": feedback_info,
            "system_prompt": self.system_prompt,
            "user_prompt": user_prompt,
            "llm_response_raw": llm_response_raw,
            "tokens": tokens,
            "llm_time_s": round(exec_time, 3),
        }
        path = os.path.join(attempt_dir["base"], "llm_interaction.json")
        with open(path, 'w') as f:
            json.dump(record, f, indent=2)

    def _write_fixed_files(self, attempt_dir):
        """Write the dataset's fixed, pre-authored testbench and constraint files."""
        tb_path = os.path.join(attempt_dir["design_files"], f"{self.design_name}_tb.v")
        with open(tb_path, 'w') as f:
            f.write(self.design_entry["Testbench"])

        if "Clock Constraint" in self.design_entry:
            xdc_path = os.path.join(attempt_dir["design_files"], f"{self.design_name}.xdc")
            with open(xdc_path, 'w') as f:
                f.write(self.design_entry["Clock Constraint"])

    def _run_simulation(self, attempt_dir):
        """Run Vivado behavioral simulation; returns True if all tests passed."""
        try:
            self.vivado.run_vivado(
                project_name=self.design_name,
                project_dir=attempt_dir["vivado_project"],
                log_path=attempt_dir["vivado_logs"],
                design_files_dir=attempt_dir["design_files"],
                fpga_part=self.args.fpga_part,
                mode='sim',
                verbose=self.args.verbose,
            )
        except Exception as e:
            print(f"Simulation stage failed with error: {e}")
            return False

        sim_log = os.path.join(attempt_dir["vivado_logs"], f"{self.design_name}_sim.log")
        return check_simulation_passed(sim_log, verbose=self.args.verbose)

    def _run_synthesis(self, attempt_dir):
        """Run Vivado synthesis; returns True on success."""
        try:
            self.vivado.run_vivado(
                project_name=self.design_name,
                project_dir=attempt_dir["vivado_project"],
                log_path=attempt_dir["vivado_logs"],
                design_files_dir=attempt_dir["design_files"],
                fpga_part=self.args.fpga_part,
                mode='synth',
                verbose=self.args.verbose,
            )
            return True
        except Exception as e:
            print(f"Synthesis stage failed with error: {e}")
            return False

    def _run_implementation(self, attempt_dir):
        """Run Vivado implementation; returns True on success."""
        try:
            self.vivado.run_vivado(
                project_name=self.design_name,
                project_dir=attempt_dir["vivado_project"],
                log_path=attempt_dir["vivado_logs"],
                design_files_dir=attempt_dir["design_files"],
                fpga_part=self.args.fpga_part,
                mode='impl',
                verbose=self.args.verbose,
            )
            return True
        except Exception as e:
            print(f"Implementation stage failed with error: {e}")
            return False

    def _parse_primitives(self, attempt_dir):
        """Parse the synthesis utilization report for per-primitive counts."""
        util_file = os.path.join(
            attempt_dir["vivado_project"], "reports", "synthesis", "utilization_report.txt"
        )
        if not os.path.exists(util_file):
            return {}
        with open(util_file, 'r', errors='ignore') as f:
            content = f.read()
        result = self.parser.parse_utilization_report(content)
        return result.get("primitives", {})

    def _parse_resources(self, attempt_dir):
        """Parse the synthesis utilization report's Slice-Logic summary
        (LUT/register/BRAM/DSP/IO used+available+utilization%), e.g. for
        the LUT_USED/REGISTER_USED hard_constraints metrics."""
        util_file = os.path.join(
            attempt_dir["vivado_project"], "reports", "synthesis", "utilization_report.txt"
        )
        if not os.path.exists(util_file):
            return {}
        with open(util_file, 'r', errors='ignore') as f:
            content = f.read()
        result = self.parser.parse_utilization_report(content)
        return result.get("resources", {})

    def _parse_timing(self, attempt_dir):
        """
        Parse the post-synthesis timing report for slack/data-path-delay
        metrics, plus a critical-path cell-type breakdown (Phase 3 diagnosis)
        nested under the "critical_path" key. Returns {} if the report is
        missing (e.g. synthesis failed before report_timing ran).
        """
        timing_file = os.path.join(
            attempt_dir["vivado_project"], "reports", "synthesis", "timing_report.txt"
        )
        if not os.path.exists(timing_file):
            return {}
        with open(timing_file, 'r', errors='ignore') as f:
            content = f.read()
        result = self.parser.parse_timing_report(content)
        result["critical_path"] = self.parser.parse_critical_path_cells(content)
        return result

    def _parse_critical_warnings(self, attempt_dir):
        """
        Parse the synthesis log for ALL critical warnings (regardless of
        pass/fail), e.g. the confirmed multi-driven-net silent-pass case.
        Returns {} if the synthesis log is missing.
        """
        synth_log = os.path.join(attempt_dir["vivado_logs"], f"{self.design_name}_synth.log")
        if not os.path.exists(synth_log):
            return {}
        with open(synth_log, 'r', errors='ignore') as f:
            content = f.read()
        return self.parser.parse_critical_warnings(content)

    def _parse_dsp_mapping(self, attempt_dir):
        """
        Parse the synthesis log's "DSP Final Report" table for per-DSP48E1
        instance register configuration (AREG/BREG/.../PREG). Returns [] if
        the synthesis log is missing or no DSP was inferred.
        """
        synth_log = os.path.join(attempt_dir["vivado_logs"], f"{self.design_name}_synth.log")
        if not os.path.exists(synth_log):
            return []
        with open(synth_log, 'r', errors='ignore') as f:
            content = f.read()
        return self.parser.parse_dsp_mapping_table(content)

    @staticmethod
    def _ff_total_from_primitives(primitives):
        """Aggregate flip-flop-like primitives into a single FF_TOTAL metric."""
        ff_like = ("FDRE", "FDCE", "FDPE", "FDSE", "FDC", "FDP")
        return sum(int(primitives.get(name, 0)) for name in ff_like)

    def _constraint_metric_value(self, primitives, name, resources=None):
        """Resolve a constraint metric name into an integer value.

        Supports two derived metrics beyond a literal primitive lookup:
        - FF_TOTAL: sum of all flip-flop-like primitives.
        - LUT_USED / REGISTER_USED: Slice-Logic summary usage counts from
          report_utilization (requires the `resources` dict from
          `_parse_resources`; resolves to 0 if not provided).
        """
        if name == "FF_TOTAL":
            return self._ff_total_from_primitives(primitives)
        if name in ("LUT_USED", "REGISTER_USED"):
            resources = resources or {}
            key = "luts" if name == "LUT_USED" else "registers"
            return int(resources.get(key, {}).get("used", 0))
        return int(primitives.get(name, 0))

    def _evaluate_hard_constraints(self, primitives, timing=None, critical_warnings_info=None,
                                    dsp_mapping=None, resources=None):
        """
        Evaluate optional dataset hard constraints against actual synthesis results.

        Supported keys under design_entry['hard_constraints']:
        - required_primitives_min: {"PRIM or FF_TOTAL/LUT_USED/REGISTER_USED": min_count, ...}
        - resource_caps: {"PRIM or FF_TOTAL/LUT_USED/REGISTER_USED": max_count, ...}
        - forbidden_primitives: ["PRIM", ...]  (equivalent to max 0)
        - timing: {"min_slack_ns": float}  (post-synthesis worst slack, from
          report_timing; pre-route only, see README limitations)
        - max_critical_warnings: int  (total "CRITICAL WARNING:" lines in the
          synthesis log, e.g. multi-driven nets, regardless of pass/fail)
        - dsp_register_config: {"AREG"/"BREG"/.../"PREG": 0 or 1, ...}
          (every inferred DSP48E1 instance's register flag, from the
          synthesis log's "DSP Final Report" table, must match)
        - primitive_alternatives: {"at_least_one_of": ["PRIM", ...], "min_total": N}
          (sum of the named metrics/primitives must reach N; lets a design
          accept two architecturally distinct, equally-valid implementations
          instead of forcing one "correct" primitive)
        - dsp_datapath_pattern: a regex string that must match at least one
          inferred DSP48E1 instance's "DSP Mapping" string (e.g. "(P+A*B)'"),
          from the synthesis log's "DSP Final Report" table. This is a
          text-pattern heuristic (same caveat as the critical-path cell
          breakdown parser), not a formal netlist-topology check.
        """
        constraints = self.design_entry.get("hard_constraints", {}) or {}
        if not constraints:
            return True, {}

        timing = timing or {}
        critical_warnings_info = critical_warnings_info or {}
        dsp_mapping = dsp_mapping or []
        resources = resources or {}

        required = constraints.get("required_primitives_min", {}) or {}
        caps = constraints.get("resource_caps", {}) or {}
        forbidden = constraints.get("forbidden_primitives", []) or []
        timing_constraints = constraints.get("timing", {}) or {}
        max_critical_warnings = constraints.get("max_critical_warnings")
        dsp_register_config = constraints.get("dsp_register_config", {}) or {}
        primitive_alternatives = constraints.get("primitive_alternatives", {}) or {}
        dsp_datapath_pattern = constraints.get("dsp_datapath_pattern")

        per_constraint = {
            "required_primitives_min": {},
            "resource_caps": {},
            "forbidden_primitives": {},
            "timing": {},
            "max_critical_warnings": {},
            "dsp_register_config": {},
            "primitive_alternatives": {},
            "dsp_datapath_pattern": {},
        }
        all_met = True

        for name, minimum in required.items():
            actual = self._constraint_metric_value(primitives, name, resources)
            met = actual >= int(minimum)
            per_constraint["required_primitives_min"][name] = {
                "expected_min": int(minimum),
                "actual": actual,
                "met": met,
            }
            if not met:
                all_met = False

        for name, maximum in caps.items():
            actual = self._constraint_metric_value(primitives, name, resources)
            met = actual <= int(maximum)
            per_constraint["resource_caps"][name] = {
                "expected_max": int(maximum),
                "actual": actual,
                "met": met,
            }
            if not met:
                all_met = False

        for name in forbidden:
            actual = self._constraint_metric_value(primitives, name, resources)
            met = actual == 0
            per_constraint["forbidden_primitives"][name] = {
                "expected": 0,
                "actual": actual,
                "met": met,
            }
            if not met:
                all_met = False

        min_slack_ns = timing_constraints.get("min_slack_ns")
        if min_slack_ns is not None:
            actual_slack = timing.get("slack")
            met = actual_slack is not None and actual_slack >= float(min_slack_ns)
            per_constraint["timing"]["min_slack_ns"] = {
                "expected_min_ns": float(min_slack_ns),
                "actual_slack_ns": actual_slack,
                "met": met,
            }
            if not met:
                all_met = False

        if max_critical_warnings is not None:
            actual_cw = critical_warnings_info.get("critical_warnings_total", 0)
            met = actual_cw <= int(max_critical_warnings)
            per_constraint["max_critical_warnings"]["total"] = {
                "expected_max": int(max_critical_warnings),
                "actual": actual_cw,
                "met": met,
            }
            if not met:
                all_met = False

        for reg_name, expected in dsp_register_config.items():
            expected_val = int(expected)
            if not dsp_mapping:
                # No DSP instance was found in the synthesis log at all, so
                # the register configuration cannot be verified: treat as unmet.
                met = False
                actual_vals = []
            else:
                actual_vals = [row.get(reg_name) for row in dsp_mapping]
                met = all(val == expected_val for val in actual_vals)
            per_constraint["dsp_register_config"][reg_name] = {
                "expected": expected_val,
                "actual": actual_vals,
                "met": met,
            }
            if not met:
                all_met = False

        at_least_one_of = primitive_alternatives.get("at_least_one_of", []) or []
        min_total = primitive_alternatives.get("min_total")
        if at_least_one_of and min_total is not None:
            per_name_actual = {
                name: self._constraint_metric_value(primitives, name, resources)
                for name in at_least_one_of
            }
            actual_total = sum(per_name_actual.values())
            met = actual_total >= int(min_total)
            per_constraint["primitive_alternatives"]["at_least_one_of"] = {
                "names": list(at_least_one_of),
                "expected_min_total": int(min_total),
                "actual_per_name": per_name_actual,
                "actual_total": actual_total,
                "met": met,
            }
            if not met:
                all_met = False

        if dsp_datapath_pattern:
            if not dsp_mapping:
                met = False
                matched_mappings = []
            else:
                pattern = re.compile(dsp_datapath_pattern)
                matched_mappings = [
                    row.get("dsp_mapping") for row in dsp_mapping
                    if row.get("dsp_mapping") and pattern.search(row["dsp_mapping"])
                ]
                met = len(matched_mappings) > 0
            per_constraint["dsp_datapath_pattern"]["pattern"] = {
                "expected_pattern": dsp_datapath_pattern,
                "actual_dsp_mappings": [row.get("dsp_mapping") for row in dsp_mapping],
                "matched": matched_mappings,
                "met": met,
            }
            if not met:
                all_met = False

        return all_met, per_constraint

    @staticmethod
    def _implementation_status(implementation_ok):
        """Map the tri-state implementation_ok (True/False/None) to a report string.

        None means implementation was never invoked (e.g. --stop_stage=synth),
        which is distinct from an implementation that actually ran and failed.
        """
        if implementation_ok is None:
            return "SKIPPED"
        return "PASS" if implementation_ok else "FAIL"

    def _build_correction_note(self, attempt_dir, sim_passed, synth_ok, per_primitive,
                               constraints_met, per_constraint, timing_info=None):
        """
        Build a corrective instruction (and a structured feedback_info dict,
        for persistence in llm_interaction.json) for the next retry attempt.

        Stage-exclusive by design: only ONE stage's log is ever inspected.
        If simulation failed, synthesis never ran, so only the simulation log
        is used. If simulation passed but synthesis failed, only the
        synthesis log is used. Returns (note_text, feedback_info).
        """
        if not sim_passed:
            return self._simulation_feedback_note(attempt_dir)
        if not synth_ok:
            return self._synthesis_feedback_note(attempt_dir)
        if not constraints_met:
            violations = []
            for name, info in per_constraint.get("required_primitives_min", {}).items():
                if not info.get("met", True):
                    violations.append(
                        f"{name}: expected >= {info['expected_min']}, got {info['actual']}"
                    )
            for name, info in per_constraint.get("resource_caps", {}).items():
                if not info.get("met", True):
                    violations.append(
                        f"{name}: expected <= {info['expected_max']}, got {info['actual']}"
                    )
            for name, info in per_constraint.get("forbidden_primitives", {}).items():
                if not info.get("met", True):
                    violations.append(
                        f"{name}: expected 0, got {info['actual']}"
                    )

            timing_violated = False
            for name, info in per_constraint.get("timing", {}).items():
                if not info.get("met", True):
                    timing_violated = True
                    violations.append(
                        f"timing.{name}: expected slack >= {info['expected_min_ns']} ns, "
                        f"got {info['actual_slack_ns']} ns"
                    )
            for name, info in per_constraint.get("max_critical_warnings", {}).items():
                if not info.get("met", True):
                    violations.append(
                        f"max_critical_warnings: expected <= {info['expected_max']}, "
                        f"got {info['actual']} (e.g. multi-driven nets; verify no signal "
                        "is driven by more than one source)"
                    )
            for name, info in per_constraint.get("dsp_register_config", {}).items():
                if not info.get("met", True):
                    violations.append(
                        f"dsp_register_config.{name}: expected {info['expected']} on every "
                        f"inferred DSP48E1 instance, got {info['actual']}"
                    )
            for name, info in per_constraint.get("primitive_alternatives", {}).items():
                if not info.get("met", True):
                    violations.append(
                        f"primitive_alternatives: combined count across "
                        f"{info['names']} expected >= {info['expected_min_total']}, "
                        f"got {info['actual_total']} ({info['actual_per_name']})"
                    )
            for name, info in per_constraint.get("dsp_datapath_pattern", {}).items():
                if not info.get("met", True):
                    violations.append(
                        f"dsp_datapath_pattern: expected at least one inferred DSP48E1 "
                        f"instance's mapping to match /{info['expected_pattern']}/, got "
                        f"{info['actual_dsp_mappings']}"
                    )

            note = (
                "The previous design passed simulation/synthesis but violated hard resource "
                "constraints: " + "; ".join(violations) + ". "
                "Rewrite the RTL so these hard constraints are met while preserving the same "
                "functional behavior and module interface."
            )

            if timing_violated:
                critical_path = (timing_info or {}).get("critical_path") or {}
                breakdown = critical_path.get("breakdown") or {}
                if breakdown:
                    breakdown_str = ", ".join(f"{cell}x{count}" for cell, count in breakdown.items())
                    note += (
                        f" Critical-path diagnosis: the worst timing path traverses "
                        f"{critical_path.get('logic_levels_total')} logic level(s) through these "
                        f"cell types: {breakdown_str}. If the path is dominated by combinational "
                        "logic (e.g. a long CARRY4 carry chain or a deep LUT tree) rather than "
                        "routing, consider adding pipeline register stages, reducing combinational "
                        "depth, or restructuring arithmetic to close timing, while preserving the "
                        "same functional behavior."
                    )

            return note, {
                "type": "constraint_mismatch",
                "hard_constraints": self.design_entry.get("hard_constraints", {}),
                "per_constraint": per_constraint,
                "timing": timing_info,
            }

        missing = [
            f"{name}: expected >= {info['expected_min']}, got {info['actual']}"
            for name, info in per_primitive.items() if not info["met"]
        ]
        if self.args.style == "explicit_primitive":
            note = (
                "The previous design passed simulation but did NOT structurally infer the "
                "expected FPGA fabric primitive(s): " + "; ".join(missing) + ". "
                "Re-check your primitive instantiation against the 'Structural Instantiation "
                "Templates' section in the FPGA fabric knowledge above (port names, chaining, "
                "and control-port settings), and correct the instantiation while preserving "
                "the same functional behavior."
            )
        else:
            note = (
                "The previous design passed simulation but did NOT use the expected FPGA "
                "fabric primitives efficiently: " + "; ".join(missing) + ". "
                "Rewrite the RTL coding style (per the FPGA fabric knowledge above) so "
                "that the synthesis tool infers the expected primitive(s), while "
                "preserving the same functional behavior."
            )
        return note, {"type": "fabric_mismatch", "per_primitive": per_primitive}

    def _simulation_feedback_note(self, attempt_dir):
        """Build sim-failure feedback: full raw log or a capped structured extraction."""
        generic = (
            "The previous design FAILED functional simulation against the fixed "
            "testbench (the module name and port list must exactly match the "
            "specification above, and the behavior must be functionally correct). "
            "Fix the functional/interface issue."
        )
        sim_log = os.path.join(attempt_dir["vivado_logs"], f"{self.design_name}_sim.log")
        if not os.path.exists(sim_log):
            return generic, {"type": "simulation_failure", "feedback_mode": self.args.feedback_mode,
                              "log_available": False}

        with open(sim_log, 'r', errors='ignore') as f:
            content = f.read()

        if self.args.feedback_mode == "full":
            note = (
                "The previous design FAILED functional simulation. Below is the complete "
                "Vivado simulation log for this attempt; identify the root cause (a "
                "compile/elaboration error, or a mismatch between expected and actual "
                "testbench values) and fix the functional/interface issue.\n\n"
                f"```\n{content}\n```"
            )
            return note, {"type": "simulation_failure", "feedback_mode": "full", "log_path": sim_log}

        feedback = self.parser.extract_simulation_feedback(content, max_entries=FEEDBACK_MAX_ENTRIES)
        if feedback["compile_errors"]:
            lines = "\n".join(f"- {e}" for e in feedback["compile_errors"])
            omitted = feedback["compile_errors_total"] - len(feedback["compile_errors"])
            extra = f"\n(+{omitted} more compile error(s) omitted)" if omitted > 0 else ""
            note = (
                "The previous design FAILED to compile/elaborate for simulation. "
                f"Compile/elaboration errors:\n{lines}{extra}\n"
                "Fix these syntax/elaboration issues, keeping the exact same module "
                "name and port list."
            )
        elif feedback["failing_vectors"]:
            lines = "\n".join(f"- {v}" for v in feedback["failing_vectors"])
            omitted = feedback["failing_vectors_total"] - len(feedback["failing_vectors"])
            extra = f"\n(+{omitted} more failing vector(s) omitted)" if omitted > 0 else ""
            note = (
                "The previous design compiled and ran, but FAILED functional simulation "
                f"against the fixed testbench. Failing test vectors (expected vs. actual):\n"
                f"{lines}{extra}\n"
                "Analyze these mismatches and fix the functional/interface issue, keeping "
                "the exact same module name and port list."
            )
        else:
            note = generic
        return note, {"type": "simulation_failure", "feedback_mode": "compressed", **feedback}

    def _synthesis_feedback_note(self, attempt_dir):
        """Build synth-failure feedback: full raw log or a capped structured extraction."""
        generic = (
            "The previous design failed to synthesize in Vivado. Fix any syntax "
            "or synthesis-blocking constructs while preserving functionality."
        )
        synth_log = os.path.join(attempt_dir["vivado_logs"], f"{self.design_name}_synth.log")
        if not os.path.exists(synth_log):
            return generic, {"type": "synthesis_failure", "feedback_mode": self.args.feedback_mode,
                              "log_available": False}

        with open(synth_log, 'r', errors='ignore') as f:
            content = f.read()

        if self.args.feedback_mode == "full":
            note = (
                "The previous design failed to synthesize in Vivado. Below is the complete "
                "synthesis log for this attempt; identify the root cause and fix any syntax "
                "or synthesis-blocking constructs while preserving functionality.\n\n"
                f"```\n{content}\n```"
            )
            return note, {"type": "synthesis_failure", "feedback_mode": "full", "log_path": synth_log}

        feedback = self.parser.extract_synthesis_feedback(content, max_entries=FEEDBACK_MAX_ENTRIES)
        if feedback["errors"]:
            lines = "\n".join(f"- {e}" for e in feedback["errors"])
            omitted = feedback["errors_total"] - len(feedback["errors"])
            extra = f"\n(+{omitted} more error(s) omitted)" if omitted > 0 else ""
            note = (
                "The previous design failed to synthesize in Vivado. Synthesis errors:\n"
                f"{lines}{extra}\n"
                "Fix these issues while preserving functionality."
            )
        elif feedback["critical_warnings"]:
            lines = "\n".join(f"- {w}" for w in feedback["critical_warnings"])
            omitted = feedback["critical_warnings_total"] - len(feedback["critical_warnings"])
            extra = f"\n(+{omitted} more critical warning(s) omitted)" if omitted > 0 else ""
            note = (
                "The previous design failed to synthesize in Vivado (no explicit ERROR line "
                f"found; nearest critical warnings below):\n{lines}{extra}\n"
                "Fix any syntax or synthesis-blocking constructs while preserving functionality."
            )
        else:
            note = generic
        return note, {"type": "synthesis_failure", "feedback_mode": "compressed", **feedback}

    def _save_reports(self, final_verdict, eda_time):
        """Persist the per-attempt fabric report (in this run's own directory) and append to the aggregated results JSON."""
        run_dir = self._run_dir()
        create_outputs_folder(run_dir)
        last_attempt = self.attempts[-1] if self.attempts else {}

        fabric_report = {
            "design_id": self.args.design_id,
            "module": self.design_name,
            "model": self.args.model,
            "style": self.args.style,
            "run_id": self.run_id,
            "final_verdict": final_verdict,
            "attempts_used": len(self.attempts),
            "total_tokens": self.total_tokens,
            "total_llm_time_s": round(self.total_llm_time, 3),
            "eda_time_s": round(eda_time, 3),
            "attempts": self.attempts,
        }

        report_path = os.path.join(run_dir, "fabric_report.json")
        with open(report_path, 'w') as f:
            json.dump(fabric_report, f, indent=2, cls=InfEncoder)

        result_entry = {
            "ID": self.args.design_id,
            "module": self.design_name,
            "style": self.args.style,
            "run_id": self.run_id,
            "llm_model": self.args.model,
            "token_count": self.total_tokens,
            "llm_time [s]": round(self.total_llm_time, 3),
            "eda_time [s]": round(eda_time, 3),
            "attempts_used": len(self.attempts),
            "Functional Verification": "PASS" if last_attempt.get("sim_passed") else "FAIL",
            "Synthesis": "PASS" if last_attempt.get("synthesis_ok") else "FAIL",
            "Implementation": self._implementation_status(last_attempt.get("implementation_ok")),
            "primary_primitive": self.design_entry.get("primary_primitive"),
            "expected_primitives": self.design_entry.get("expected_primitives", {}),
            "hard_constraints": self.design_entry.get("hard_constraints", {}),
            "actual_primitives": last_attempt.get("actual_primitives", {}),
            "fabric_expectations_met": last_attempt.get("fabric_expectations_met", False),
            "hard_constraints_met": last_attempt.get("hard_constraints_met", True),
            "timing_slack_ns": last_attempt.get("timing", {}).get("slack"),
            "critical_warnings_total": last_attempt.get("critical_warnings_total"),
            "dsp_mapping": last_attempt.get("dsp_mapping", []),
        }

        output_json_path = self.args.output_json
        if not os.path.isabs(output_json_path):
            output_json_path = os.path.join(FABRIC_RTL_GEN_DIR, output_json_path)

        existing = []
        if os.path.exists(output_json_path):
            try:
                with open(output_json_path, 'r') as f:
                    existing = json.load(f)
                if not isinstance(existing, list):
                    existing = [existing]
            except (json.JSONDecodeError, OSError):
                existing = []
        existing.append(result_entry)
        with open(output_json_path, 'w') as f:
            json.dump(existing, f, indent=2, cls=InfEncoder)

        if self.args.verbose:
            print(f"Fabric report saved to {report_path}")
            print(f"Aggregated results appended to {output_json_path}")


def parse_arguments():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Run the LaMDA-FPGA Fabric-Aware RTL Generation pipeline (Xilinx 7-Series)",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )

    parser.add_argument("--design_id", type=int, required=True,
                       help="Design ID from Dataset/fabric_problems.json (1-9)")

    parser.add_argument("--model", type=str, default="gpt-4o",
                       help="LLM model name (e.g., gpt-4o, gemini-2.0-flash-exp)")
    parser.add_argument("--max_tokens", type=int, default=3000,
                       help="Maximum tokens for LLM generation")
    parser.add_argument("--temperature", type=float, default=1.0,
                       help="Temperature for LLM sampling")
    parser.add_argument("--top_p", type=float, default=1.0,
                       help="Top-p for LLM sampling")

    parser.add_argument("--fpga_part", type=str, default="xc7a100tcsg324-1",
                       help="Target FPGA part number for Vivado (default: Artix-7)")

    parser.add_argument("--style", type=str, choices=list(STYLES), default="fabric_aware",
                       help="RTL generation style: 'baseline' (no fabric guidance, the "
                            "naive control), 'fabric_aware' (idiomatic coding style hints "
                            "that let synthesis infer primitives, the pipeline's default), "
                            "or 'explicit_primitive' (LLM structurally instantiates the "
                            "target UNISIM primitive(s) by name).")

    parser.add_argument("--max_attempts", type=int, default=2,
                       help="Maximum total number of generation attempts, including the "
                            "first (i.e. this many loop calls total; a value of 1 disables "
                            "self-correction retries entirely).")

    parser.add_argument("--stop_stage", type=str, choices=["synth", "impl"], default="impl",
                       help="Final Vivado stage to run: 'synth' stops after synthesis "
                            "(skips place & route/implementation entirely), 'impl' runs "
                            "the full flow including implementation. Simulation and "
                            "synthesis always run; only implementation can be skipped.")

    parser.add_argument("--feedback_mode", type=str, choices=["compressed", "full"], default="compressed",
                       help="How much Vivado sim/synth log content to feed back to the LLM "
                            "on a self-correction retry: 'compressed' extracts a capped list "
                            "of actionable errors/failing test vectors (default), 'full' "
                            "embeds the complete raw content of the single relevant log file "
                            "(sim log if simulation failed, synth log if synthesis failed) "
                            "verbatim, with no trimming.")

    parser.add_argument("--dataset_file", type=str, default=None,
                       help="Optional override path to the fabric problems dataset JSON")
    parser.add_argument("--output_json", type=str, default="fabric_exp_results.json",
                       help="Aggregated results JSON filename (or absolute path)")

    parser.add_argument("--verbose", action="store_true",
                       help="Enable verbose logging")

    return parser.parse_args()


def main():
    """Main entry point for the fabric-aware RTL generation pipeline."""
    args = parse_arguments()

    print("=" * 60)
    print("LaMDA-FPGA Fabric-Aware RTL Generation Pipeline (Xilinx 7-Series)")
    print("=" * 60)
    print(f"Design ID: {args.design_id}")
    print(f"Model: {args.model}")
    print(f"Style: {args.style}")
    print(f"FPGA Part: {args.fpga_part}")
    print(f"Max attempts: {args.max_attempts}")
    print(f"Stop stage: {args.stop_stage}")
    print("=" * 60)

    pipeline = FabricAwarePipeline(args)
    result = pipeline.run()

    return result


if __name__ == "__main__":
    exit(0 if main() else 1)
