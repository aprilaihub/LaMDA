"""
Runs the per-edit attribution of ONE attempt of a finished run (the method is in Analysis/Attribution.py).

Offline step after a run: the variants of the attempt (reference + one group of edits, attempt - one group of edits)
are written next to the run and each goes through the same Vivado flow as the attempt (same part, stage and clock,
read from fabric_report.json), so their numbers are comparable with the baseline and the attempt in the report.

Files, all inside the run directory:

  attribution/attempt_<n>/plan.json            groups, hunks and the variants planned (written first, no Vivado needed)
  attribution/attempt_<n>/<variant>/           Design_Files/, vivado_project/, vivado_logs/ as for an attempt
  attribution/attempt_<n>/<variant>/result.json  flow result of the variant; an existing file is reused (a rerun
                                               after a crash only redoes the missing variants; `rerun=True` redoes all)
  attribution/attempt_<n>/attribution.json     the attribution table (Analysis/Attribution.build_attribution)
  attribution.jsonl                            one row per group, appended (Analysis/Attribution.attribution_rows)

Cost: two Vivado flows per group (one with leave_one_out=False). `plan_only=True` writes the plan and the variant
sources and stops, to check the grouping before spending tool time.
"""

import json
import os

import Attribution
from Pipeline.results import dump_json
from utils import create_outputs_folder

# What is kept of a flow result in result.json: everything the attribution needs, without the bulky digest.
_RESULT_KEYS = ("sim_passed", "synthesis_ok", "implementation_ok", "tool_ok", "failure", "metrics", "vivado_version")


class AttributionRun:
    """Attribution of one attempt of the run whose fabric_report.json is `report_path`."""

    def __init__(self, report_path, entry, flow, mode="signal", max_groups=8, leave_one_out=True,
                 plan_only=False, rerun=False, verbose=False):
        self.report_path = report_path
        self.run_dir = os.path.dirname(os.path.abspath(report_path))
        with open(report_path, "r") as f:
            self.report = json.load(f)
        self.entry, self.flow = entry, flow
        self.module = self.report["module"]
        self.mode, self.max_groups, self.leave_one_out = mode, max_groups, leave_one_out
        self.plan_only, self.rerun, self.verbose = plan_only, rerun, verbose

    # ------------------------------------------------------------------ inputs

    def attempt_record(self, attempt_number):
        """The record of an attempt in the report; None when absent. attempt_number None = the best attempt."""
        if attempt_number is None:
            attempt_number = self.report.get("best_attempt")
        return next((a for a in self.report["attempts"] if a["attempt"] == attempt_number), None)

    def attempt_code(self, attempt_number):
        """Verilog of an attempt, as saved by the run (Design_Files/<module>.v)."""
        path = os.path.join(self.run_dir, f"attempt_{attempt_number}", "Design_Files", f"{self.module}.v")
        with open(path, "r") as f:
            return f.read()

    def work_dir(self, attempt_number):
        return os.path.join(self.run_dir, "attribution", f"attempt_{attempt_number}")

    def variant_dirs(self, attempt_number, name):
        base = os.path.join(self.work_dir(attempt_number), name)
        return {"base": base, "design_files": os.path.join(base, "Design_Files"),
                "vivado_project": os.path.join(base, "vivado_project"), "vivado_logs": os.path.join(base, "vivado_logs")}

    # ------------------------------------------------------------------ variants

    def write_variant(self, dirs, code):
        """Design file plus the dataset's testbench and clock constraint, as for an attempt."""
        create_outputs_folder(*dirs.values())
        for name, text in ((f"{self.module}.v", code), (f"{self.module}_tb.v", self.entry["Testbench"]),
                           (f"{self.module}.xdc", self.entry["Clock Constraint"])):
            with open(os.path.join(dirs["design_files"], name), "w") as f:
                f.write(text)

    def measure(self, dirs):
        """Flow result of one variant: the stored result.json when present, else a Vivado run (and store it)."""
        path = os.path.join(dirs["base"], "result.json")
        if os.path.exists(path) and not self.rerun:
            with open(path, "r") as f:
                return json.load(f)
        result = self.flow.run(dirs, self.module)
        stored = {k: result.get(k) for k in _RESULT_KEYS}
        dump_json(path, stored)
        return stored

    # ------------------------------------------------------------------ the run

    def run(self, attempt_number=None):
        """
        Attribute one attempt (None = the best one). Returns the attribution table, or None with `plan_only`.
        Raises ValueError when the attempt is absent or has no metrics (nothing to attribute).
        """
        record = self.attempt_record(attempt_number)
        if record is None:
            raise ValueError(f"attempt {attempt_number if attempt_number is not None else '(best)'} not found in the report")
        number = record["attempt"]
        baseline = (self.report.get("baseline") or {}).get("metrics")
        if not record.get("metrics") or not baseline:
            raise ValueError(f"attempt {number} or the baseline has no metrics; nothing to attribute")

        reference, attempt = self.entry["reference_rtl"], self.attempt_code(number)
        plan = Attribution.plan_variants(reference, attempt, self.mode, self.max_groups, self.leave_one_out)
        create_outputs_folder(self.work_dir(number))
        dump_json(os.path.join(self.work_dir(number), "plan.json"), {
            "attempt": number, "stage": self.report["stage"], "grouping": plan["mode"], "requested_grouping": self.mode,
            "groups": plan["groups"], "hunks": plan["hunks"],
            "variants": [{k: v[k] for k in ("name", "kind", "group")} for v in plan["variants"]]})
        if len(plan["groups"]) < 2:
            print(f"Attempt {number}: {len(plan['groups'])} group of edits; nothing to separate.")

        results = {}
        for variant in plan["variants"]:
            dirs = self.variant_dirs(number, variant["name"])
            self.write_variant(dirs, variant["code"])
            if self.plan_only:
                continue
            if self.verbose:
                print(f"\n=== Attribution, attempt {number}: variant {variant['name']} ===")
            results[variant["name"]] = self.measure(dirs)
        if self.plan_only:
            return None

        table = Attribution.build_attribution(
            plan["groups"], results, baseline, record["metrics"], (record.get("edit_log") or {}).get("intents"))
        table.update({"attempt": number, "stage": self.report["stage"], "grouping": plan["mode"],
                      "variants": {name: Attribution.variant_status(r) for name, r in results.items()}})
        dump_json(os.path.join(self.work_dir(number), "attribution.json"), table)
        self.append_rows(Attribution.attribution_rows(table, {
            "design_id": self.report["design_id"], "module": self.module, "model": self.report.get("model"),
            "knowledge": self.report.get("knowledge"), "feedback_detail": self.report.get("feedback_detail"),
            "run_id": self.report.get("run_id"), "stage": self.report["stage"], "attempt": number,
            "attempt_outcome": record.get("outcome")}))
        return table

    def append_rows(self, rows):
        """Append rows to <run_dir>/attribution.jsonl."""
        with open(os.path.join(self.run_dir, "attribution.jsonl"), "a") as f:
            for row in rows:
                f.write(json.dumps(row, default=str) + "\n")
