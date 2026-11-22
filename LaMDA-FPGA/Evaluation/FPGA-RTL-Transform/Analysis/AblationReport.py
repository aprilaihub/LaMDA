"""
Ablation report: what does each kind of evidence (and each knowledge level) buy?

Reads the fabric_report.json of finished runs (Outputs/<knowledge>/<feedback_detail>/design_*/run_*/) and compares the
configurations on the same designs. A configuration is (stage, knowledge, feedback_detail[, model]); the stage is a
field of the report because a run that stopped at synthesis must not be mixed with a run that went to implementation.

Per configuration:
  runs            finished runs with a verdict (BASELINE_ONLY and BASELINE_FAILED runs are not counted)
  pass            share of runs that ended PASS (a correct design better than the baseline in at least one metric)
  first success   mean attempt of the first success, over the runs that had one
  better          mean number of metrics (LUT, FF, DSP, BRAM, Fmax) better than the baseline in the best attempt
  sim ok          share of attempts that passed simulation (how often the edit kept the behavior)
  tool fail       share of attempts that failed in synthesis / implementation
  tokens          mean tokens per run

The report also lists, per configuration, PASS / runs for every design, so a difference caused by one hard design is
visible. With few runs per configuration these are descriptions, not significance tests: repeat the runs
(Makefile: SWEEP_REPEATS) before concluding.

    python3 Analysis/AblationReport.py [--outputs Outputs] [--model MODEL] [--by-model] [--markdown FILE] [--json FILE]

Pure functions (`load_reports`, `summarize`, `format_markdown`) so that the table can be tested without a run.
"""

import argparse
import glob
import json
import os
import sys

TRANSFORM_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Verdicts of a finished optimization (the others, BASELINE_ONLY and BASELINE_FAILED, have no attempts to compare).
COUNTED_VERDICTS = ("PASS", "FAIL")

# Cumulative evidence order, used to sort the configurations (the same order as Pipeline/levels.py).
LEVEL_ORDER = ("none", "metrics", "reports", "reports+logs", "reports+logs+edits")
KNOWLEDGE_ORDER = ("none", "primers", "primers+patterns")


def load_reports(outputs_dir):
    """All fabric_report.json files below `outputs_dir`, as a list of dicts (unreadable files are skipped)."""
    reports = []
    for path in sorted(glob.glob(os.path.join(outputs_dir, "**", "fabric_report.json"), recursive=True)):
        try:
            with open(path, "r") as f:
                report = json.load(f)
        except (OSError, ValueError):
            continue
        if isinstance(report, dict) and report.get("task") == "optimize":
            report["_path"] = os.path.relpath(path, outputs_dir)
            reports.append(report)
    return reports


def count_better(report):
    """Number of metrics better than the baseline in the best attempt of the run (0 when there is none)."""
    best = report.get("best_attempt")
    for rec in report.get("attempts") or []:
        if rec.get("attempt") == best:
            return list((rec.get("comparison") or {}).values()).count("better")
    return 0


def run_facts(report):
    """The numbers of one run that the summary averages."""
    attempts = report.get("attempts") or []
    return {
        "design": report.get("design_id"), "passed": report.get("final_verdict") == "PASS",
        "success_attempt": report.get("success_attempt"), "better": count_better(report),
        "attempts": len(attempts), "sim_ok": sum(1 for a in attempts if a.get("sim_passed")),
        "tool_fail": sum(1 for a in attempts if a.get("sim_passed") and not a.get("tool_ok")),
        "tokens": report.get("total_tokens") or 0,
    }


def _mean(values):
    values = [v for v in values if v is not None]
    return sum(values) / len(values) if values else None


def config_key(report, by_model):
    key = (report.get("stage") or "?", report.get("knowledge") or "?", report.get("feedback_detail") or "?")
    return key + ((report.get("model") or "?",) if by_model else ())


def _sort_key(key):
    knowledge = KNOWLEDGE_ORDER.index(key[1]) if key[1] in KNOWLEDGE_ORDER else len(KNOWLEDGE_ORDER)
    level = LEVEL_ORDER.index(key[2]) if key[2] in LEVEL_ORDER else len(LEVEL_ORDER)
    return (key[0], knowledge, level) + key[3:]


def summarize(reports, by_model=False, model=None):
    """
    {configuration: summary} for the runs of `reports`. `model` keeps only the runs of that model. Each summary has
    the keys runs, pass_rate, first_success, better, sim_ok, tool_fail, tokens and designs ({design: [passed, runs]}).
    """
    groups = {}
    for report in reports:
        if report.get("final_verdict") not in COUNTED_VERDICTS:
            continue
        if model and report.get("model") != model:
            continue
        groups.setdefault(config_key(report, by_model), []).append(run_facts(report))

    summary = {}
    for key in sorted(groups, key=_sort_key):
        runs = groups[key]
        attempts = sum(r["attempts"] for r in runs)
        designs = {}
        for r in runs:
            counts = designs.setdefault(r["design"], [0, 0])
            counts[0] += 1 if r["passed"] else 0
            counts[1] += 1
        summary[key] = {
            "runs": len(runs), "pass_rate": sum(1 for r in runs if r["passed"]) / len(runs),
            "first_success": _mean([r["success_attempt"] for r in runs if r["passed"]]),
            "better": _mean([r["better"] for r in runs]),
            "sim_ok": sum(r["sim_ok"] for r in runs) / attempts if attempts else None,
            "tool_fail": sum(r["tool_fail"] for r in runs) / attempts if attempts else None,
            "tokens": _mean([r["tokens"] for r in runs]), "designs": dict(sorted(designs.items())),
        }
    return summary


def _fmt(value, spec):
    return "n/a" if value is None else spec.format(value)


def format_markdown(summary, by_model=False):
    """The summary as two markdown tables: the configurations, and PASS / runs per design."""
    if not summary:
        return "No finished optimization runs found.\n"
    head = ["stage", "knowledge", "feedback"] + (["model"] if by_model else [])
    columns = head + ["runs", "pass", "first success", "better", "sim ok", "tool fail", "tokens"]
    lines = ["| " + " | ".join(columns) + " |", "|" + "---|" * len(columns)]
    design_ids = sorted({d for s in summary.values() for d in s["designs"]}, key=lambda d: (d is None, d))
    for key, s in summary.items():
        lines.append("| " + " | ".join(list(key) + [
            str(s["runs"]), _fmt(s["pass_rate"], "{:.0%}"), _fmt(s["first_success"], "{:.1f}"),
            _fmt(s["better"], "{:.2f}"), _fmt(s["sim_ok"], "{:.0%}"), _fmt(s["tool_fail"], "{:.0%}"),
            _fmt(s["tokens"], "{:,.0f}")]) + " |")

    lines += ["", "PASS / runs per design:", "",
              "| " + " | ".join(head + [f"design {d}" for d in design_ids]) + " |",
              "|" + "---|" * (len(head) + len(design_ids))]
    for key, s in summary.items():
        cells = [f"{s['designs'][d][0]}/{s['designs'][d][1]}" if d in s["designs"] else "-" for d in design_ids]
        lines.append("| " + " | ".join(list(key) + cells) + " |")
    return "\n".join(lines) + "\n"


def to_json(summary):
    """JSON-able form of the summary (the configuration tuple becomes a '|'-joined string)."""
    return {"|".join(key): value for key, value in summary.items()}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Compare the knowledge / feedback levels over the finished runs.")
    parser.add_argument("--outputs", default=os.path.join(TRANSFORM_DIR, "Outputs"),
                        help="folder searched for fabric_report.json (default: Outputs/)")
    parser.add_argument("--model", default=None, help="only the runs of this model")
    parser.add_argument("--by-model", action="store_true", help="one row per model as well")
    parser.add_argument("--markdown", default=None, help="also write the tables to this file")
    parser.add_argument("--json", default=None, help="also write the summary to this JSON file")
    args = parser.parse_args(argv)

    summary = summarize(load_reports(args.outputs), by_model=args.by_model, model=args.model)
    text = format_markdown(summary, by_model=args.by_model)
    print(text)
    if args.markdown:
        with open(args.markdown, "w") as f:
            f.write(text)
    if args.json:
        with open(args.json, "w") as f:
            json.dump(to_json(summary), f, indent=2)
    return 0


if __name__ == "__main__":
    sys.exit(main())
