"""
Result files of one invocation (Run.py keeps only the optimization loop):

  <run_dir>/fabric_report.json   everything about the run: settings, baseline, every attempt record
  --output_json                  aggregated file with ONE summary entry per invocation (appended)

The functions are pure (dict in, dict out) apart from the two that write a file, so the layout of the result
files can be tested without running a loop. Field meanings are documented in the README ("Output layout").
"""

import json
import os
from datetime import datetime

from Pipeline import TRANSFORM_DIR
from FabricAnalysis import InfEncoder
import Verdict


def now_iso():
    """Local time as 2026-11-21T10:30:00 (used in the LLM interaction records)."""
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def dump_json(path, data):
    """Write `data` as indented JSON; values JSON cannot encode are stored as text."""
    with open(path, "w") as f:
        json.dump(data, f, indent=2, default=str)


def write_edit_files(attempt_dir, edit_log, patches):
    """Save the edit record of one attempt: edit_log.json and the two patch files (diff_vs_ref / diff_vs_base)."""
    dump_json(os.path.join(attempt_dir, "edit_log.json"), edit_log)
    for name, text in patches.items():
        with open(os.path.join(attempt_dir, name), "w") as f:
            f.write(text)


def transform_rows(edit_log, rec):
    """
    Rows of transforms.jsonl for one attempt: one per declared intent (with the verdict of the cross-check) and one
    per undeclared change, each carrying the outcome of the attempt so the file can be analysed on its own.
    """
    common = {"attempt": rec["attempt"], "base_attempt": edit_log["base_attempt"], "stage": rec.get("stage"),
              "sim_passed": rec["sim_passed"], "outcome": rec["outcome"], "delta_vs_ref": edit_log["delta_vs_ref"],
              "delta_vs_base": edit_log["delta_vs_base"]}
    rows = [{**common, "source": "declared", "tag": i["tag"], "tag_declared": i["tag_declared"],
             "module": i["module"], "target": i["target"], "status": i["status"], "reason": i["reason"],
             "rationale": i["rationale"], "expected_effect": i["expected_effect"], "effect_check": i["effect_check"]}
            for i in edit_log["intents"]]
    rows += [{**common, "source": "undeclared", "tag": "UNDECLARED", "module": u["module"], "kinds": u["kinds"],
              "signals": u["signals"], "new_lines": u["new_lines"], "status": "undeclared_change"}
             for u in edit_log["undeclared_changes"]]
    return rows


def append_transforms(run_dir, rows):
    """Append rows to <run_dir>/transforms.jsonl (one JSON object per line; the run directory must exist)."""
    if not rows:
        return
    with open(os.path.join(run_dir, "transforms.jsonl"), "a") as f:
        for row in rows:
            f.write(json.dumps(row, default=str) + "\n")


def compact_edit_log(edit_log):
    """
    The part of edit_log.json kept in the attempt record of fabric_report.json: counts, the checked intents without
    their code, the undeclared changes and the metric deltas. The hunks and the code are only in edit_log.json.
    """
    ref, base = edit_log["vs_reference"], edit_log["vs_base"]
    return {
        "file": "edit_log.json", "base_attempt": edit_log["base_attempt"],
        "lines_vs_reference": {"added": ref["added"], "removed": ref["removed"], "hunks": len(ref["hunks"])},
        "lines_vs_base": {"added": base["added"], "removed": base["removed"], "hunks": len(base["hunks"])},
        "modules_changed": ref["modules_changed"], "modules_added": ref["modules_added"],
        "modules_removed": ref["modules_removed"],
        "structure_delta_vs_reference": edit_log["structure"]["delta_vs_reference"],
        "intents": [{k: i[k] for k in ("id", "module", "target", "tag", "status", "reason", "effect_check")}
                    for i in edit_log["intents"]],
        "intent_parse_error": edit_log["intent_parse_error"],
        "undeclared_changes": len(edit_log["undeclared_changes"]),
        "delta_vs_ref": edit_log["delta_vs_ref"], "delta_vs_base": edit_log["delta_vs_base"],
    }


def build_report(run):
    """
    Content of fabric_report.json for the finished `run` (an OptimizeRun). `run.final_verdict` must be set.
    The attempt records are stored as they are, so every analysis script can work from this file alone.
    """
    best = run.best
    return {
        "task": "optimize", "design_id": run.args.design_id, "module": run.module, "model": run.args.model,
        "knowledge": run.args.knowledge, "feedback_detail": run.args.feedback_detail, "run_id": run.run_id,
        "final_verdict": run.final_verdict, "stop_reason": run.stop_reason, "success_attempt": run.success_attempt,
        "max_attempts": max(1, run.args.max_attempts), "stop_on_success": run.args.stop_on_success,
        "baseline_error": run.baseline_error, "stage": run.stage, "fpga_part": run.args.fpga_part,
        "clock_period_ns": run.flow.clock_period_ns, "attempts_used": len(run.history),
        "best_attempt": best["attempt"] if best else None, "total_tokens": run.total_tokens,
        "total_llm_time_s": round(run.total_llm_time, 3), "eda_time_s": round(run.eda_time, 3),
        "baseline": run.baseline, "prompt": {"system_sections": run.prompt_sections},
        "llm_interactions_file": "llm_interactions.jsonl", "attempts": run.history,
    }


def build_summary_entry(run, report_path):
    """One entry of the aggregated results file: the headline numbers of the run and a link to its report."""
    best = run.best
    last = run.history[-1] if run.history else {}
    return {
        "ID": run.args.design_id, "module": run.module, "task": "optimize",
        "knowledge": run.args.knowledge, "feedback_detail": run.args.feedback_detail, "run_id": run.run_id,
        "llm_model": run.args.model, "token_count": run.total_tokens,
        "llm_time [s]": round(run.total_llm_time, 3), "eda_time [s]": round(run.eda_time, 3),
        "attempts_used": len(run.history), "Optimization": run.final_verdict, "stop_reason": run.stop_reason,
        "success_attempt": run.success_attempt, "stage": run.stage,
        "baseline_metrics": (run.baseline or {}).get("metrics"),
        "best_attempt": best["attempt"] if best else None,
        "best_metrics": best["metrics"] if best else None,
        "best_comparison": best["comparison"] if best else None,
        "last_attempt_outcome": last.get("outcome"),
        "system_prompt_sha256": Verdict.sha256_text(run.system_prompt),
        "fabric_report": os.path.relpath(report_path, TRANSFORM_DIR),
    }


def write_report(run_dir, report):
    """Write <run_dir>/fabric_report.json (the directory must exist) and return its path."""
    path = os.path.join(run_dir, "fabric_report.json")
    with open(path, "w") as f:
        json.dump(report, f, indent=2, cls=InfEncoder)
    return path


def append_result_json(path, entry):
    """
    Append `entry` to the aggregated results file. A relative `path` is relative to FPGA-RTL-Transform/; its
    folder is created when needed (the default lives in Outputs/). An unreadable or malformed file is replaced.
    """
    if not os.path.isabs(path):
        path = os.path.join(TRANSFORM_DIR, path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    existing = []
    if os.path.exists(path):
        try:
            with open(path, "r") as f:
                existing = json.load(f)
            if not isinstance(existing, list):
                existing = [existing]
        except (json.JSONDecodeError, OSError):
            existing = []
    existing.append(entry)
    with open(path, "w") as f:
        json.dump(existing, f, indent=2, cls=InfEncoder)
