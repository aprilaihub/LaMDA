"""
FPGA-RTL-Transform: LLM-driven RTL transformation of small accelerators (AMD/Xilinx 7-Series).

Dataset (Dataset/fabric_opt_problems.json, built by Dataset/build_dataset.py): seven designs, each a hierarchy
of modules with a cycle-exact self-checking testbench and a clock constraint: three 4x4 INT8 systolic arrays
(weight-, output-, input-stationary), an 8x8 output-stationary array, a 16-tap FIR filter, a 3x3 convolution with
a line buffer and a saturating dot-product engine (see the README, "Dataset").

Flow of one invocation (one design):
  attempt_0  the REFERENCE RTL goes through the same Vivado flow as every attempt (simulation, synthesis,
             implementation) to get the baseline metrics and the baseline report digest. No LLM call.
  attempt_1+ the LLM rewrites the design. Every attempt is simulated against the testbench, synthesized, (with
             --stop_stage impl) implemented, and compared with the baseline: LUT, FF, DSP, BRAM, Fmax are
             better / same / worse. The next attempt refines the best correct design so far.
             The loop ends after --max_attempts attempts (or at the first success with --stop_on_success).

Two experiment switches (see README.md):
  --knowledge        none | primers | primers+patterns   what the LLM knows about the 7-Series resources
                     (Primers/{lut,dsp,bram}.md; +patterns adds Primers/{transforms,reports}.md)
  --feedback_detail  none | metrics | reports | reports+logs | reports+logs+edits   what it gets back after an
                     attempt; each level adds one kind of evidence to the previous one (Pipeline/levels.py):
                     the verdict only; the metrics; a digest of the Vivado reports; what the tools did (logs); the
                     record of the earlier edits

Code map: Pipeline/flow.py (runs Vivado), Pipeline/metrics.py (metrics + digest from the reports), Pipeline/prompts.py
(prompts), Pipeline/feedback.py (notes), Pipeline/results.py (result files), Analysis/ (report parsers, digest,
verdict). This file holds the loop and the LLM call.
Self-contained: nothing is imported from Evaluation/ResBench or Evaluation/Custom.
"""

import argparse
import json
import os
import tempfile
import time
import traceback
from datetime import datetime

from Pipeline import TRANSFORM_DIR
from Pipeline import feedback, prompts, results
from Pipeline.flow import DesignFlow
from Pipeline.results import dump_json, now_iso
from LLM_Interface.LLMClient import LLMClient
from utils import create_outputs_folder, extract_script
from FabricAnalysis import FabricReportParser
import EditLog
import Verdict

OPT_DATASET_FILE = os.path.join(TRANSFORM_DIR, "Dataset", "fabric_opt_problems.json")
OUTPUTS_DIR = os.path.join(TRANSFORM_DIR, "Outputs")
# Aggregated results (one entry per invocation); relative to FPGA-RTL-Transform/. Lives in Outputs/ with the runs.
OPT_RESULTS_FILE = os.path.join("Outputs", "fabric_opt_results.json")
DEFAULT_MAX_ATTEMPTS = 6

# A failure reason is always printed; only the terminal echo is capped (the full text is saved in the report).
TERMINAL_NOTE_MAX_CHARS = 1500


def load_design_entry(dataset_path, design_id):
    """One design entry of the dataset by ID; None when absent."""
    with open(dataset_path, "r") as f:
        for entry in json.load(f).get("designs", []):
            if entry.get("ID") == design_id:
                return entry
    return None


class OptimizeRun:
    """One invocation: baseline measurement, then the LLM attempts."""

    def __init__(self, args):
        self.args = args
        # --baseline_only never calls an LLM, so no provider / API key is needed.
        self.llm_client = None if args.baseline_only else LLMClient(args.model, args.provider)
        # Unique per invocation (timestamp + PID): re-running never overwrites earlier outputs.
        self.run_id = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{os.getpid()}"
        self.stage = "implementation" if args.stop_stage == "impl" else "synthesis"

        self.entry = None
        self.module = None
        self.reference_code = ""
        self.flow = None
        self.system_prompt, self.prompt_sections = prompts.build_system_prompt(args.knowledge)

        self.baseline = None        # {"metrics", "primitives", "digest", "vivado_version"}
        self.baseline_error = None
        self.history = []           # one record per attempt
        self.attempt_code = {}      # attempt number -> Verilog
        self.best = None            # best correct attempt record
        self.base_attempt = None    # attempt whose code is the base of the next attempt
        self.success_attempt = None
        self.final_verdict = None   # PASS | FAIL | BASELINE_FAILED | BASELINE_ONLY (set by save_reports)
        self.stop_reason = None     # baseline_failed | baseline_only | success | max_attempts_reached | error
        self.total_tokens = 0
        self.total_llm_time = 0.0
        self.eda_time = 0.0

    # ------------------------------------------------------------------ directories

    def run_dir(self):
        """Outputs/<knowledge>/<feedback_detail>/design_<id>_<module>/run_<timestamp>_<pid>/"""
        return os.path.join(OUTPUTS_DIR, self.args.knowledge, self.args.feedback_detail,
                            f"design_{self.args.design_id}_{self.module}", f"run_{self.run_id}")

    def attempt_dirs(self, attempt_number):
        """Directory layout of attempt N (0 = baseline)."""
        base = os.path.join(self.run_dir(), f"attempt_{attempt_number}")
        return {"base": base, "design_files": os.path.join(base, "Design_Files"),
                "vivado_project": os.path.join(base, "vivado_project"), "vivado_logs": os.path.join(base, "vivado_logs")}

    def write_fixed_files(self, dirs):
        """The dataset's testbench and clock constraint go next to the design."""
        with open(os.path.join(dirs["design_files"], f"{self.module}_tb.v"), "w") as f:
            f.write(self.entry["Testbench"])
        with open(os.path.join(dirs["design_files"], f"{self.module}.xdc"), "w") as f:
            f.write(self.entry["Clock Constraint"])

    # ------------------------------------------------------------------ baseline

    def run_baseline(self):
        """Measure the reference RTL with the same flow as the attempts (attempt_0, no LLM call)."""
        dirs = self.attempt_dirs(0)
        create_outputs_folder(*dirs.values())
        with open(os.path.join(dirs["design_files"], f"{self.module}.v"), "w") as f:
            f.write(self.reference_code)
        self.write_fixed_files(dirs)
        self.append_llm_index({"attempt": 0, "llm_called": False,
                               "reason": "baseline measurement of the reference RTL (no LLM involved)"})
        if self.args.verbose:
            print("\n=== Baseline: measuring the reference RTL (attempt_0) ===")
        result = self.flow.run(dirs, self.module)
        self.eda_time += result["eda_time_s"]
        if not result["tool_ok"]:
            self.baseline_error = f"reference RTL failed at stage '{result['failure']}'"
            print(f"Baseline failed: {self.baseline_error}")
            return False
        self.baseline = {"metrics": result["metrics"], "primitives": result["details"].get("primitives", {}),
                         "digest": result["digest"], "log_facts": result["log_facts"],
                         "vivado_version": result["vivado_version"]}
        if self.args.verbose:
            print(f"Baseline metrics: {self.baseline['metrics']}")
        return True

    # ------------------------------------------------------------------ LLM call

    def append_llm_index(self, line):
        """One line per attempt in the run-level llm_interactions.jsonl."""
        create_outputs_folder(self.run_dir())
        with open(os.path.join(self.run_dir(), "llm_interactions.jsonl"), "a") as f:
            f.write(json.dumps(line, default=str) + "\n")

    def generate_design(self, dirs, attempt_number, previous_code, note, note_info):
        """
        Ask the LLM for the design of this attempt, save the Verilog as Design_Files/<module>.v and return the
        raw response. The interaction is written twice to the attempt's llm_interaction.json: the request before the
        call, the response (or the error) after it, so a failed call stays visible.
        """
        user_prompt = prompts.build_user_prompt(
            self.entry, self.reference_code, self.baseline, self.args.fpga_part, self.args.feedback_detail,
            self.history, self.best, self.base_attempt, self.attempt_code, previous_code, note)
        design_file = os.path.join(dirs["design_files"], f"{self.module}.v")
        path = os.path.join(dirs["base"], "llm_interaction.json")
        system_sha, user_sha = Verdict.sha256_text(self.system_prompt), Verdict.sha256_text(user_prompt)

        record = {
            "attempt": attempt_number, "status": "request_sent", "request_time": now_iso(),
            "knowledge": self.args.knowledge, "feedback_detail": self.args.feedback_detail,
            "feedback_applied": note_info,
            "generation_params": {"model": self.args.model, "max_tokens": self.args.max_tokens,
                                  "temperature": self.args.temperature, "top_p": self.args.top_p},
            "prompt_breakdown": {"system_sections": self.prompt_sections, "system_prompt_sha256": system_sha,
                                 "user_prompt_sha256": user_sha},
            "system_prompt": self.system_prompt, "user_prompt": user_prompt,
        }
        dump_json(path, record)
        index = {"attempt": attempt_number, "llm_called": True, "model": self.args.model,
                 "interaction_file": os.path.relpath(path, self.run_dir()), "system_prompt_sha256": system_sha,
                 "user_prompt_sha256": user_sha, "request_time": record["request_time"]}

        started = time.time()
        try:
            content, tokens = self.llm_client.generate_content(
                prompt=user_prompt, system_prompt=self.system_prompt, max_tokens=self.args.max_tokens,
                temperature=self.args.temperature, top_p=self.args.top_p)
        except Exception as exc:
            record.update(status="llm_error", error=f"{type(exc).__name__}: {exc}", response_time=now_iso(),
                          llm_time_s=round(time.time() - started, 3))
            dump_json(path, record)
            self.append_llm_index({**index, "status": "llm_error", "error": record["error"]})
            raise
        elapsed = time.time() - started
        self.total_tokens += tokens
        self.total_llm_time += elapsed
        record.update(status="ok", response_time=now_iso(), llm_response_raw=content, tokens=tokens,
                      llm_time_s=round(elapsed, 3))

        # The Verilog section holds every module of the design; it is saved under the top module's name.
        parsed = {"reasoning": Verdict.parse_reasoning(content)}
        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".txt") as tmp:
            tmp.write(content)
            tmp_path = tmp.name
        try:
            extract_script(tmp_path, design_file, "// Start Verilog Design\n", "\n// End Verilog Design", verbose=False)
        except ValueError as exc:
            parsed["parse_error"] = str(exc)   # no extractable Verilog: handled as an attempt failure
        finally:
            os.unlink(tmp_path)
        parsed["verilog_chars"] = os.path.getsize(design_file) if os.path.exists(design_file) else 0
        record["parsed"] = parsed
        dump_json(path, record)
        self.append_llm_index({**index, "status": "ok", "tokens": tokens, "llm_time_s": round(elapsed, 3),
                               "response_time": record["response_time"]})
        if self.args.verbose:
            print(f"Design generated for '{self.module}' ({tokens} tokens, {elapsed:.2f}s)")
        return content

    # ------------------------------------------------------------------ one attempt

    @staticmethod
    def rank_key(rec):
        """Lower is better: most metrics better than the baseline, then fewest worse."""
        values = list((rec["comparison"] or {}).values())
        return (-values.count("better"), values.count("worse"))

    def regressions_vs_best(self, metrics):
        """Metrics of this attempt that are worse than in the best attempt so far."""
        if not self.best or not metrics:
            return []
        worse = [k for k, v in Verdict.compare_to_reference(self.best["metrics"], metrics).items() if v == "worse"]
        return [f"{k} is worse than in attempt {self.best['attempt']}" for k in worse]

    def evaluate_attempt(self, dirs, attempt_number, response):
        """Run the flow on the generated design and build the attempt record."""
        design_file = os.path.join(dirs["design_files"], f"{self.module}.v")
        design_present = os.path.exists(design_file) and os.path.getsize(design_file) > 0
        if design_present:
            with open(design_file, "r") as f:
                self.attempt_code[attempt_number] = f.read()

        result = self.flow.run(dirs, self.module, design_present)
        self.eda_time += result["eda_time_s"]
        metrics, details = result["metrics"], result["details"]
        comparison = Verdict.compare_to_reference(self.baseline["metrics"], metrics) if metrics else {}
        timing = details.get("timing") or {}
        evidence = details.get("timing_evidence") or {}
        rec = {
            "attempt": attempt_number, "stage": self.stage,
            "sim_passed": result["sim_passed"], "synthesis_ok": result["synthesis_ok"],
            "implementation_ok": result["implementation_ok"], "tool_ok": result["tool_ok"],
            "failure": result["failure"], "extraction_failed": not design_present,
            "metrics": metrics, "comparison": comparison,
            "outcome": Verdict.classify_outcome(result["sim_passed"], result["tool_ok"], comparison),
            # Success = correct and at least one metric better than the baseline.
            "success": bool(result["sim_passed"] and result["tool_ok"] and "better" in comparison.values()),
            "reasoning": Verdict.parse_reasoning(response or ""),   # text between the Reasoning markers
            "regressions_vs_best": self.regressions_vs_best(metrics),
            "digest": result["digest"], "log_facts": result["log_facts"],
            "diag": {"primitives": details.get("primitives", {}), "critical_path": timing.get("critical_path", {}),
                     "logic_delay_ns": timing.get("logic_delay_ns"), "route_delay_ns": timing.get("route_delay_ns"),
                     "pulse_width": evidence.get("pulse_width", {})},
            "vivado_version": result["vivado_version"],
        }
        if design_present:
            self.record_edits(dirs, rec, response)
        return rec

    def record_edits(self, dirs, rec, response):
        """
        Objective record of the edits of this attempt (Analysis/EditLog.py): diffs against the reference and the
        base attempt, structural facts, the declared intents cross-checked against the Vivado facts. Written to
        the attempt directory (edit_log.json, *.patch) and to the run's transforms.jsonl. A failure here is
        recorded in the attempt record and never stops the run.
        """
        try:
            def facts(record):
                return {"metrics": record["metrics"], "primitives": (record.get("diag") or {}).get("primitives", {})}

            base_no = self.base_attempt
            base_rec = next((r for r in self.history if r["attempt"] == base_no), None)
            log, patches = EditLog.build_edit_log(
                rec["attempt"], base_no, self.reference_code, self.attempt_code.get(base_no),
                self.attempt_code[rec["attempt"]], response,
                {"metrics": self.baseline["metrics"], "primitives": self.baseline.get("primitives", {})},
                facts(base_rec) if base_rec else None, facts(rec))
            results.write_edit_files(dirs["base"], log, patches)
            results.append_transforms(self.run_dir(), results.transform_rows(log, rec))
            rec["edit_log"] = results.compact_edit_log(log)
        except Exception as exc:   # diagnostic only; the attempt result must not depend on it
            rec["edit_log_error"] = f"{type(exc).__name__}: {exc}"
            if self.args.verbose:
                traceback.print_exc()

    def build_note(self, dirs, rec):
        """The one feedback note for the next attempt, by the stage that failed (see Pipeline/feedback.py)."""
        if rec["extraction_failed"]:
            return feedback.extraction_note()
        if not rec["sim_passed"]:
            return feedback.simulation_note(os.path.join(dirs["vivado_logs"], f"{self.module}_sim.log"),
                                            self.entry["latency_contract"])
        if not rec["tool_ok"]:
            stage = "implementation" if rec["failure"] == "implementation" else "synthesis"
            if rec["failure"] == "reports_missing":
                return ("The Vivado reports of the previous attempt could not be read, so no metrics are available. "
                        "Provide a standard synthesizable design.", {"type": "reports_missing"})
            mode = "impl" if stage == "implementation" else "synth"
            return feedback.synthesis_note(os.path.join(dirs["vivado_logs"], f"{self.module}_{mode}.log"), stage)
        return feedback.result_note(rec, self.baseline, self.best, self.args.feedback_detail)

    # ------------------------------------------------------------------ main loop

    def run(self):
        self.entry = load_design_entry(self.args.dataset_file or OPT_DATASET_FILE, self.args.design_id)
        if self.entry is None:
            print(f"Design ID {self.args.design_id} not found in the dataset.")
            return 0
        self.module = self.entry["module"]
        self.reference_code = self.entry["reference_rtl"]
        period = FabricReportParser.parse_clock_period_ns(self.entry["Clock Constraint"])
        self.flow = DesignFlow(self.args.fpga_part, self.stage, period, verbose=self.args.verbose)
        if self.args.verbose:
            print(f"Run directory (unique per invocation): {self.run_dir()}")

        max_attempts = max(1, self.args.max_attempts)
        previous_code, note, note_info = None, None, None
        try:
            if not self.run_baseline():
                self.stop_reason = "baseline_failed"
                self.save_reports("BASELINE_FAILED")
                return 0
            if self.args.baseline_only:
                self.stop_reason = "baseline_only"
                self.save_reports("BASELINE_ONLY")
                return 1

            for index in range(max_attempts):
                number = index + 1
                dirs = self.attempt_dirs(number)
                create_outputs_folder(*dirs.values())
                if self.args.verbose:
                    print(f"\n=== Attempt {number}/{max_attempts}: optimizing the reference RTL ===")
                response = self.generate_design(dirs, number, previous_code, note, note_info)
                self.write_fixed_files(dirs)

                rec = self.evaluate_attempt(dirs, number, response)
                self.history.append(rec)
                if rec["sim_passed"] and rec["tool_ok"] and (
                        self.best is None or self.rank_key(rec) < self.rank_key(self.best)):
                    self.best = rec
                if self.args.verbose:
                    print(f"Attempt {number}: outcome={rec['outcome']} success={rec['success']} "
                          f"comparison={rec['comparison']} metrics={rec['metrics']}")

                if rec["success"] and self.success_attempt is None:
                    self.success_attempt = number
                    if self.args.stop_on_success:
                        self.stop_reason = "success"
                        break

                # The note is always worked out (even after the last attempt) so it is printed and saved.
                note, note_info = self.build_note(dirs, rec)
                rec["feedback_note"], rec["feedback_info"] = note, note_info
                passed = rec["sim_passed"] and rec["tool_ok"]
                if passed:
                    print(f"\nAttempt {number}: outcome {rec['outcome']}{' (new best)' if self.best is rec else ''}")
                else:
                    shown = note if len(note) <= TERMINAL_NOTE_MAX_CHARS else \
                        note[:TERMINAL_NOTE_MAX_CHARS] + "\n... (truncated; full reason saved in the JSON report)"
                    print(f"\nAttempt {number} FAILED: {shown}")

                if index == max_attempts - 1:
                    self.stop_reason = "max_attempts_reached"
                    break
                # Base of the next attempt: the best correct design while the last attempt was correct, the
                # last attempt itself while it still has to be fixed.
                self.base_attempt = self.best["attempt"] if (passed and self.best) else number
                previous_code = self.attempt_code.get(self.base_attempt)

            verdict = "PASS" if self.best and self.best["success"] else "FAIL"
            self.save_reports(verdict)
            if self.args.verbose:
                print(f"\n=== Final verdict: {verdict} (attempts used: {len(self.history)}, best attempt: "
                      f"{self.best['attempt'] if self.best else None}, stop reason: {self.stop_reason}) ===")
            return 1 if verdict == "PASS" else 0
        except Exception as e:
            self.stop_reason = "error"
            print(f"Pipeline failed with error: {e}")
            traceback.print_exc()
            return 0

    # ------------------------------------------------------------------ result files

    def save_reports(self, verdict):
        """fabric_report.json (everything, in the run directory) + one summary entry in --output_json."""
        self.final_verdict = verdict
        run_dir = self.run_dir()
        create_outputs_folder(run_dir)
        report_path = results.write_report(run_dir, results.build_report(self))
        results.append_result_json(self.args.output_json, results.build_summary_entry(self, report_path))
        if self.args.verbose:
            print(f"Optimization report saved to {report_path}")


def parse_arguments():
    parser = argparse.ArgumentParser(
        description="LLM-driven RTL transformation of systolic arrays and streaming datapaths (Xilinx 7-Series)",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument("--design_id", type=int, required=True,
                        help="Design ID in Dataset/fabric_opt_problems.json (1 sa_os_4x4, 2 sa_ws_4x4, 3 sa_is_4x4, 4 fir16, 5 conv3x3, 6 sat_dot32, 7 sa_os_8x8)")

    parser.add_argument("--knowledge", choices=prompts.KNOWLEDGE_LEVELS, default="primers",
                        help="'none': the LLM relies on its own knowledge of the 7-Series fabric. "
                             "'primers': the resource primers (Primers/lut.md, dsp.md, bram.md) are added to the "
                             "system prompt. 'primers+patterns': plus the generic transformation patterns "
                             "(Primers/transforms.md) and how to read the Vivado results (Primers/reports.md).")
    parser.add_argument("--feedback_detail", choices=feedback.FEEDBACK_DETAILS, default="reports",
                        help="Evidence the LLM gets after an attempt; every level adds one kind to the previous one. "
                             "'none': the verdict only. 'metrics': LUT/FF/DSP/BRAM/Fmax against the baseline. "
                             "'reports': plus a digest of the synthesis/implementation reports. 'reports+logs': "
                             "plus what the tools did (from the logs). 'reports+logs+edits': plus the record of "
                             "the edits of the earlier attempts and their measured effect.")

    parser.add_argument("--model", default="gpt-4o", help="LLM model name")
    parser.add_argument("--provider", default=None,
                        help="LLM provider (openai, openrouter, elm, gemini, anthropic). Defaults to $LLM_PROVIDER.")
    parser.add_argument("--max_tokens", type=int, default=12000,
                        help="Maximum tokens of one LLM answer (all modules + reasoning)")
    parser.add_argument("--temperature", type=float, default=1.0)
    parser.add_argument("--top_p", type=float, default=1.0)
    parser.add_argument("--fpga_part", default="xc7a100tcsg324-1", help="Target part (7-Series)")

    parser.add_argument("--max_attempts", type=int, default=DEFAULT_MAX_ATTEMPTS,
                        help="Number of LLM attempts (attempt_0, the reference baseline, is not counted)")
    parser.add_argument("--stop_on_success", action="store_true",
                        help="Stop at the first attempt that is correct and better than the baseline in at least "
                             "one metric. By default the loop keeps refining until the attempts are used up.")
    parser.add_argument("--baseline_only", action="store_true",
                        help="Only measure the reference RTL (attempt_0) and write the report; no LLM call.")
    parser.add_argument("--stop_stage", choices=["synth", "impl"], default="impl",
                        help="Last Vivado stage: 'synth' skips implementation (place & route), 'impl' runs it.")

    parser.add_argument("--dataset_file", default=None, help="Override of Dataset/fabric_opt_problems.json")
    parser.add_argument("--output_json", default=OPT_RESULTS_FILE, help="Aggregated results file (name or path)")
    parser.add_argument("--verbose", action="store_true")
    return parser.parse_args()


def main():
    args = parse_arguments()
    print("=" * 60)
    print("FPGA-RTL-Transform: RTL transformation of systolic arrays (Xilinx 7-Series)")
    print("=" * 60)
    print(f"Design ID: {args.design_id}   Model: {args.model}")
    print(f"Knowledge: {args.knowledge}   Feedback detail: {args.feedback_detail}")
    print(f"FPGA part: {args.fpga_part}   Stop stage: {args.stop_stage}")
    print(f"Max attempts: {args.max_attempts} (stop_on_success {args.stop_on_success})")
    print("=" * 60)
    return OptimizeRun(args).run()


if __name__ == "__main__":
    exit(0 if main() else 1)
