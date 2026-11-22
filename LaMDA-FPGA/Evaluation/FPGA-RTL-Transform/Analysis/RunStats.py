"""
Run statistics: what did a set of finished runs cost, and what did they produce?

Reads the files a run leaves in Outputs/ (fabric_report.json, llm_interactions.jsonl, attempt_N/llm_interaction.json and
the aggregated fabric_opt_results.json) and writes one Markdown report. No Vivado, no LLM.

Which runs are in the tables: the LATEST run of every design (the highest run_id). The tables assume one
configuration (model, knowledge, feedback level): for a sweep over several configurations use --model and a separate
Outputs/ (or Analysis/AblationReport.py, which compares configurations). Earlier runs of the same design are only
listed in the last section, including runs whose folder was deleted but whose summary is still in
fabric_opt_results.json.

Sections of the report:
  1  Overview                 settings of the runs, PASS count
  2  Per design               verdict, attempts, LLM time, Vivado time, output tokens, truncated answers
  3  Totals                   sum / mean / median / max over the designs, time split, tokens per second
  4  Quality                  baseline -> best attempt for LUT, FF, DSP, BRAM, Fmax and WNS, timing closure
  5  Attempts                 one row per attempt: outcome, failure reason, feedback sent back, tokens, time
  6  Transformations          the declared edits by tag and by cross-check result (confirmed / unsupported / ...)
  7  Edit size                lines and hunks changed against the reference, edits per design
  8  Prompt size              characters of the prompts and an estimate of the input tokens
  9  Cost (optional)          only with --price_in / --price_out (USD per million tokens)
  10 Earlier runs             runs of the same designs that are not in the tables

Notes on the numbers:
  * Tokens are OUTPUT tokens (what the API reports as completion tokens). The prompt tokens are not recorded; section
    8 estimates them as characters / 4.
  * An answer is "truncated" when its token count reaches the max_tokens of the request: the model was cut off, so the
    RTL block is usually incomplete ("no_design_extracted") and the attempt is wasted.
  * Vivado time (`eda_time_s`) is the baseline measurement plus every attempt, as recorded by the run.

    python3 Analysis/RunStats.py [--outputs Outputs] [--model MODEL] [--markdown FILE] [--json FILE]
                                 [--price_in USD] [--price_out USD]

Pure functions (`load_run`, `run_facts`, `select_runs`, `format_markdown`) so that the report can be tested without a run.
"""

import argparse
import json
import os
import statistics
import sys
from collections import Counter

from AblationReport import COUNTED_VERDICTS, load_reports

TRANSFORM_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# The metrics shown in the quality table: (column label, key in the metrics dict, relative change?).
# Counts and Fmax are compared in percent; DSP / BRAM start at 0 in many designs and WNS can be negative or zero, so
# those are compared in absolute difference.
QUALITY_METRICS = (
    ("LUT", "LUT_USED", True),
    ("FF", "FF_TOTAL", True),
    ("DSP", "DSP48E1", False),
    ("BRAM", "BRAM", False),
    ("Fmax MHz", "fmax_mhz", True),
    ("WNS ns", "wns_ns", False),
)

# Keys of the `comparison` dict of an attempt (the five metrics the verdict looks at).
COMPARED_METRICS = ("LUT_USED", "FF_TOTAL", "DSP48E1", "BRAM", "Fmax")

# Cross-check results of a declared edit that get their own column (anything else is counted as "other").
EDIT_STATUSES = ("confirmed", "unverifiable", "unsupported")

# Rough size of a token in characters, used only to estimate the (unrecorded) input tokens.
CHARS_PER_TOKEN = 4


# ----------------------------------------------------------------------------------------------------------------
# Loading
# ----------------------------------------------------------------------------------------------------------------

def read_json(path):
    """The JSON content of `path`, or None when the file is missing or unreadable."""
    try:
        with open(path, "r") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def read_jsonl(path):
    """The objects of a JSON-lines file (a missing file gives []; unreadable lines are skipped)."""
    rows = []
    try:
        with open(path, "r") as f:
            for line in f:
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    continue
    except OSError:
        pass
    return [row for row in rows if isinstance(row, dict)]


def load_run(report, outputs_dir):
    """
    Everything one run left on disk, as a dict:
      report   the fabric_report.json content
      calls    {attempt: line of llm_interactions.jsonl} for the attempts that called the LLM
      prompts  {attempt: request settings and prompt sizes}, read from attempt_N/llm_interaction.json
    Missing files give empty entries, so an incomplete run folder still produces a row.
    """
    run_dir = os.path.dirname(os.path.join(outputs_dir, report["_path"]))
    calls = {}
    for row in read_jsonl(os.path.join(run_dir, "llm_interactions.jsonl")):
        if row.get("llm_called") and row.get("attempt") is not None:
            calls[row["attempt"]] = row
    prompts = {}
    for number in calls:
        data = read_json(os.path.join(run_dir, "attempt_%s" % number, "llm_interaction.json")) or {}
        params = data.get("generation_params") or {}
        prompts[number] = {
            "max_tokens": params.get("max_tokens"), "temperature": params.get("temperature"),
            "top_p": params.get("top_p"),
            "system_chars": len(data.get("system_prompt") or ""), "user_chars": len(data.get("user_prompt") or ""),
            "response_chars": len(data.get("llm_response_raw") or ""),
        }
    return {"report": report, "run_dir": run_dir, "calls": calls, "prompts": prompts}


def load_results_file(outputs_dir):
    """The entries of Outputs/fabric_opt_results.json that carry a verdict (a missing or broken file gives [])."""
    entries = read_json(os.path.join(outputs_dir, "fabric_opt_results.json"))
    if not isinstance(entries, list):
        return []
    return [e for e in entries if isinstance(e, dict) and e.get("Optimization") in COUNTED_VERDICTS]


# ----------------------------------------------------------------------------------------------------------------
# Facts of one run
# ----------------------------------------------------------------------------------------------------------------

def attempt_facts(run):
    """One dict per attempt of the run (the baseline, attempt 0, is not an attempt of the loop)."""
    rows = []
    for rec in run["report"].get("attempts") or []:
        number = rec.get("attempt")
        call = run["calls"].get(number, {})
        prompt = run["prompts"].get(number, {})
        tokens, limit = call.get("tokens"), prompt.get("max_tokens")
        edit = rec.get("edit_log") or {}
        lines = edit.get("lines_vs_reference") or {}
        rows.append({
            "attempt": number, "outcome": rec.get("outcome"), "success": bool(rec.get("success")),
            "sim_passed": rec.get("sim_passed"), "synthesis_ok": rec.get("synthesis_ok"),
            "implementation_ok": rec.get("implementation_ok"), "failure": rec.get("failure"),
            "feedback": (rec.get("feedback_info") or {}).get("type"),
            "tokens": tokens, "llm_time_s": call.get("llm_time_s"), "max_tokens": limit,
            "truncated": bool(tokens is not None and limit and tokens >= limit),
            "system_chars": prompt.get("system_chars"), "user_chars": prompt.get("user_chars"),
            "response_chars": prompt.get("response_chars"),
            "added": lines.get("added"), "removed": lines.get("removed"), "hunks": lines.get("hunks"),
            "intents": edit.get("intents") or [], "undeclared": edit.get("undeclared_changes") or 0,
            "has_edit_log": bool(edit), "metrics": rec.get("metrics"), "comparison": rec.get("comparison") or {},
        })
    return rows


def run_facts(run):
    """The numbers of one run that the report needs (a flat dict, JSON-able)."""
    report = run["report"]
    attempts = attempt_facts(run)
    best = next((a for a in attempts if a["attempt"] == report.get("best_attempt")), None)
    requests = sorted(c["request_time"] for c in run["calls"].values() if c.get("request_time"))
    responses = sorted(c["response_time"] for c in run["calls"].values() if c.get("response_time"))
    prompt_values = list(run["prompts"].values())
    return {
        "design": report.get("design_id"), "module": report.get("module"), "run_id": report.get("run_id"),
        "model": report.get("model"), "knowledge": report.get("knowledge"),
        "feedback_detail": report.get("feedback_detail"), "stage": report.get("stage"),
        "fpga_part": report.get("fpga_part"), "clock_period_ns": report.get("clock_period_ns"),
        "vivado_version": (report.get("baseline") or {}).get("vivado_version"),
        "verdict": report.get("final_verdict"), "stop_reason": report.get("stop_reason"),
        "attempts_used": report.get("attempts_used"), "max_attempts": report.get("max_attempts"),
        "stop_on_success": report.get("stop_on_success"), "success_attempt": report.get("success_attempt"),
        "best_attempt": report.get("best_attempt"),
        "tokens": report.get("total_tokens") or 0, "llm_time_s": report.get("total_llm_time_s") or 0.0,
        "eda_time_s": report.get("eda_time_s") or 0.0,
        "truncated": sum(1 for a in attempts if a["truncated"]),
        "baseline": (report.get("baseline") or {}).get("metrics") or {},
        "best_metrics": (best or {}).get("metrics") or {}, "best_comparison": (best or {}).get("comparison") or {},
        "attempts": attempts,
        "temperatures": sorted({p["temperature"] for p in prompt_values if p.get("temperature") is not None}),
        "max_tokens": sorted({p["max_tokens"] for p in prompt_values if p.get("max_tokens") is not None}),
        "first_request": requests[0] if requests else None, "last_response": responses[-1] if responses else None,
    }


def select_runs(runs):
    """
    (latest, earlier): the latest run of every design (highest run_id, which starts with the timestamp) sorted by
    design ID, and all the other runs sorted by design and run_id.
    """
    by_design = {}
    for run in runs:
        by_design.setdefault(run["report"].get("design_id"), []).append(run)
    latest, earlier = [], []
    for design in sorted(by_design, key=lambda d: (d is None, d or 0)):
        ordered = sorted(by_design[design], key=lambda r: str(r["report"].get("run_id") or ""))
        latest.append(ordered[-1])
        earlier.extend(ordered[:-1])
    return latest, earlier


def common_max_tokens(facts):
    """The most frequent max_tokens of the requests in `facts` (None when no request was recorded)."""
    counts = Counter(a["max_tokens"] for f in facts for a in f["attempts"] if a["max_tokens"])
    return counts.most_common(1)[0][0] if counts else None


def earlier_rows(earlier_runs, results, loaded_run_ids, limit):
    """
    Rows of the "earlier runs" table: the runs with a folder, then the entries of fabric_opt_results.json whose folder
    is gone. For the latter the truncation is INFERRED: if tokens == attempts x max_tokens, every answer hit the limit.
    """
    rows = []
    for run in earlier_runs:
        f = run_facts(run)
        rows.append({"design": f["design"], "module": f["module"], "run_id": f["run_id"], "verdict": f["verdict"],
                     "attempts": f["attempts_used"], "tokens": f["tokens"], "llm_time_s": f["llm_time_s"],
                     "eda_time_s": f["eda_time_s"], "stop_reason": f["stop_reason"], "truncated": f["truncated"],
                     "inferred": False, "source": "run folder"})
    for entry in results:
        if entry.get("run_id") in loaded_run_ids:
            continue
        attempts, tokens = entry.get("attempts_used") or 0, entry.get("token_count") or 0
        truncated = attempts if (limit and attempts and tokens == attempts * limit) else None
        rows.append({"design": entry.get("ID"), "module": entry.get("module"), "run_id": entry.get("run_id"),
                     "verdict": entry.get("Optimization"), "attempts": attempts, "tokens": tokens,
                     "llm_time_s": entry.get("llm_time [s]"), "eda_time_s": entry.get("eda_time [s]"),
                     "stop_reason": entry.get("stop_reason"), "truncated": truncated, "inferred": True,
                     "source": "results file only"})
    return sorted(rows, key=lambda r: (r["design"] is None, r["design"] or 0, str(r["run_id"])))


# ----------------------------------------------------------------------------------------------------------------
# Small formatting helpers
# ----------------------------------------------------------------------------------------------------------------

def _fmt(value, spec="{:.1f}"):
    """`value` formatted with `spec`, or '-' when it is None."""
    return "-" if value is None else spec.format(value)


def _num(value):
    """A metric value without trailing zeros (2169, 179.02, -0.048)."""
    return "-" if value is None else "{:g}".format(value)


def _mean(values):
    values = [v for v in values if v is not None]
    return sum(values) / len(values) if values else None


def _median(values):
    values = [v for v in values if v is not None]
    return statistics.median(values) if values else None


def _duration(seconds):
    """Seconds as '1h 02m 03s' / '4m 05s' / '12s'."""
    total = int(round(seconds))
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return "%dh %02dm %02ds" % (hours, minutes, secs)
    if minutes:
        return "%dm %02ds" % (minutes, secs)
    return "%ds" % secs


def _joined(values):
    """Distinct values as 'a, b' (None entries dropped)."""
    return ", ".join(str(v) for v in sorted({v for v in values if v is not None})) or "-"


def table(header, rows):
    """A Markdown table (header row, separator, rows) as a list of lines."""
    lines = ["| " + " | ".join(header) + " |", "|" + "|".join("---" for _ in header) + "|"]
    lines += ["| " + " | ".join(str(cell) for cell in row) + " |" for row in rows]
    return lines


def change(base, new, relative):
    """'base -> new (delta)' with a relative delta in percent, or an absolute one when `relative` is False."""
    if base is None or new is None:
        return "-"
    if relative and base:
        delta = "%+.1f%%" % ((new - base) / abs(base) * 100.0)
    else:
        delta = "%+g" % round(new - base, 6)
    return "%s -> %s (%s)" % (_num(base), _num(new), delta)


def metric_delta(base, new, relative):
    """The number behind `change` (percent or absolute), None when it is not defined."""
    if base is None or new is None:
        return None
    if relative:
        return (new - base) / abs(base) * 100.0 if base else None
    return new - base


def reference_attempt(facts):
    """The attempt shown in the edit tables: the best one, else the last attempt that has an edit record."""
    by_number = {a["attempt"]: a for a in facts["attempts"]}
    best = by_number.get(facts["best_attempt"])
    if best and best["has_edit_log"]:
        return best
    return next((a for a in reversed(facts["attempts"]) if a["has_edit_log"]), None)


# ----------------------------------------------------------------------------------------------------------------
# Report sections (each returns a list of Markdown lines)
# ----------------------------------------------------------------------------------------------------------------

def overview_section(facts):
    passed = sum(1 for f in facts if f["verdict"] == "PASS")
    total_time = sum(f["llm_time_s"] + f["eda_time_s"] for f in facts)
    rows = [
        ("Designs (latest run of each)", ", ".join("%s `%s`" % (f["design"], f["module"]) for f in facts)),
        ("Result", "%d of %d designs PASS" % (passed, len(facts))),
        ("Model", _joined(f["model"] for f in facts)),
        ("Knowledge / feedback detail", "%s / %s" % (_joined(f["knowledge"] for f in facts),
                                                      _joined(f["feedback_detail"] for f in facts))),
        ("Stage / FPGA part", "%s / %s" % (_joined(f["stage"] for f in facts), _joined(f["fpga_part"] for f in facts))),
        ("Vivado", _joined(f["vivado_version"] for f in facts)),
        ("Attempts allowed / stop on success", "%s / %s" % (_joined(f["max_attempts"] for f in facts),
                                                           _joined(f["stop_on_success"] for f in facts))),
        ("max_tokens / temperature", "%s / %s" % (_joined(m for f in facts for m in f["max_tokens"]),
                                                   _joined(t for f in facts for t in f["temperatures"]))),
        ("Run ids", "%s ... %s" % (min(str(f["run_id"]) for f in facts), max(str(f["run_id"]) for f in facts))),
        ("LLM + Vivado time, all designs", _duration(total_time)),
    ]
    return ["## 1. Overview", ""] + table(["Item", "Value"], rows)


def design_section(facts):
    rows = []
    for f in facts:
        rows.append((f["design"], "`%s`" % f["module"], f["verdict"], "%s / %s" % (f["attempts_used"], f["max_attempts"]),
                     _fmt(f["success_attempt"], "{}"), _fmt(f["llm_time_s"]), _fmt(f["eda_time_s"]), f["tokens"],
                     f["truncated"] or "-", f["stop_reason"] or "-"))
    lines = ["## 2. Per design", ""]
    lines += table(["ID", "Module", "Verdict", "Attempts", "First success", "LLM time [s]", "Vivado time [s]",
                    "Output tokens", "Truncated answers", "Stop reason"], rows)
    return lines


def totals_section(facts):
    def stat_row(label, values, spec="{:.1f}"):
        return (label, _fmt(sum(values), spec), _fmt(_mean(values), spec), _fmt(_median(values), spec),
                _fmt(max(values), spec))

    llm, eda = [f["llm_time_s"] for f in facts], [f["eda_time_s"] for f in facts]
    tokens, attempts = [f["tokens"] for f in facts], [f["attempts_used"] or 0 for f in facts]
    lines = ["## 3. Totals", ""]
    lines += table(["Quantity", "Sum", "Mean per design", "Median per design", "Max per design"], [
        stat_row("LLM time [s]", llm), stat_row("Vivado time [s]", eda),
        stat_row("LLM + Vivado time [s]", [a + b for a, b in zip(llm, eda)]),
        stat_row("Output tokens", tokens, "{:.0f}"), stat_row("Attempts", attempts, "{:.1f}")])
    total_attempts, passed = sum(attempts), [f for f in facts if f["verdict"] == "PASS"]
    lines += ["",
              "- Time split: %.1f%% Vivado, %.1f%% LLM." % (100.0 * sum(eda) / max(sum(eda) + sum(llm), 1e-9),
                                                           100.0 * sum(llm) / max(sum(eda) + sum(llm), 1e-9)),
              "- Per attempt: %s s of LLM time and %s output tokens on average." % (
                  _fmt(sum(llm) / total_attempts if total_attempts else None),
                  _fmt(sum(tokens) / total_attempts if total_attempts else None, "{:.0f}")),
              "- Generation speed: %s output tokens per second." % _fmt(sum(tokens) / sum(llm) if sum(llm) else None),
              "- Output tokens per PASS design: %s (all tokens of the PASS runs divided by their number)." % _fmt(
                  sum(f["tokens"] for f in passed) / len(passed) if passed else None, "{:.0f}")]
    return lines


def quality_section(facts):
    rows, deltas = [], {key: [] for _, key, _ in QUALITY_METRICS}
    for f in facts:
        base, best = f["baseline"], f["best_metrics"]
        cells = []
        for _, key, relative in QUALITY_METRICS:
            cells.append(change(base.get(key), best.get(key), relative) if best else "-")
            if best:
                deltas[key].append(metric_delta(base.get(key), best.get(key), relative))
        wns_base, wns_best = base.get("wns_ns"), best.get("wns_ns")
        met = "-" if not best or wns_base is None or wns_best is None else "%s -> %s" % (
            "yes" if wns_base >= 0 else "no", "yes" if wns_best >= 0 else "no")
        better = sum(1 for k in COMPARED_METRICS if f["best_comparison"].get(k) == "better")
        worse = sum(1 for k in COMPARED_METRICS if f["best_comparison"].get(k) == "worse")
        rows.append([f["design"], "`%s`" % f["module"], f["verdict"]] + cells +
                    [met, "%d / %d" % (better, worse) if best else "-"])
    mean_cells = []
    for _, key, relative in QUALITY_METRICS:
        mean = _mean(deltas[key])
        mean_cells.append("-" if mean is None else ("%+.1f%%" if relative else "%+.3g") % mean)
    rows.append(["", "**Mean change**", ""] + mean_cells + ["", ""])
    lines = ["## 4. Quality: baseline -> best attempt", ""]
    lines += table(["ID", "Module", "Verdict"] + [label for label, _, _ in QUALITY_METRICS] +
                   ["Timing met (WNS >= 0)", "Better / worse metrics"], rows)
    lines += ["",
              "A run passes when the design is correct and better than the baseline in at least one metric; other "
              "metrics may be worse (for example DSP up for LUT down, or a slightly lower WNS). The change is relative "
              "for LUT, FF and Fmax and absolute for DSP, BRAM and WNS. The mean ignores designs without a best "
              "attempt and metrics with a zero baseline."]
    return lines


def attempts_section(facts):
    rows = []
    for f in facts:
        for a in f["attempts"]:
            reason = a["failure"] or ("simulation failed" if a["sim_passed"] is False else
                                      "synthesis failed" if a["synthesis_ok"] is False else
                                      "implementation failed" if a["implementation_ok"] is False else "-")
            rows.append((f["design"], a["attempt"], a["outcome"] or "-", reason, a["feedback"] or "-",
                         _fmt(a["tokens"], "{}"), "yes" if a["truncated"] else "no", _fmt(a["llm_time_s"])))
    outcomes = Counter(a["outcome"] or "-" for f in facts for a in f["attempts"])
    lines = ["## 5. Attempts", ""]
    lines += table(["ID", "Attempt", "Outcome", "Failure", "Feedback sent after it", "Output tokens", "Truncated",
                    "LLM time [s]"], rows)
    lines += ["", "Outcomes of all attempts: " + ", ".join("%s %d" % (k, v) for k, v in sorted(outcomes.items())) + "."]
    return lines


def tag_counts(facts):
    """(per tag: Counter of edits, statuses and effect checks, number of undeclared changes) over all attempts."""
    tags, undeclared = {}, 0
    for f in facts:
        for a in f["attempts"]:
            undeclared += a["undeclared"]
            for intent in a["intents"]:
                row = tags.setdefault(intent.get("tag") or "OTHER", Counter())
                row["edits"] += 1
                status = intent.get("status")
                row[status if status in EDIT_STATUSES else "other"] += 1
                checks = intent.get("effect_check")
                for result in (checks.values() if isinstance(checks, dict) else []):
                    if result in ("met", "not_met"):
                        row[result] += 1
    return tags, undeclared


def transform_section(facts):
    tags, undeclared = tag_counts(facts)
    rows = []
    for tag, c in sorted(tags.items(), key=lambda item: (-item[1]["edits"], item[0])):
        checked = c["met"] + c["not_met"]
        rows.append(["`%s`" % tag, c["edits"]] + [c[s] or "-" for s in EDIT_STATUSES] + [
            c["other"] or "-", "%d / %d" % (c["met"], checked) if checked else "-"])
    lines = ["## 6. Transformations", ""]
    if rows:
        lines += table(["Tag", "Declared edits"] + list(EDIT_STATUSES) + ["other", "Expected effects met"], rows)
    else:
        lines += ["No declared edits were recorded."]
    lines += ["",
              "Declared edits of all attempts of the listed runs; undeclared changes (hunks the LLM did not announce): "
              "%d. `confirmed` means the reports show the expected structural change, `unsupported` that they do not, "
              "`unverifiable` that the tag cannot be checked from the reports or the attempt failed before them. "
              "\"Expected effects met\" counts the metrics the LLM said would improve and did. These are labels of "
              "the whole attempt: which single edit moved a metric is answered by `make attribute`." % undeclared]
    return lines


def edit_section(facts):
    rows = []
    for f in facts:
        a = reference_attempt(f)
        if a is None:
            rows.append((f["design"], "`%s`" % f["module"], "-", "-", "-", "-", "-", "-"))
            continue
        tag_text = ", ".join("%s x%d" % (t, n) for t, n in sorted(Counter(
            i.get("tag") or "OTHER" for i in a["intents"]).items())) or "-"
        rows.append((f["design"], "`%s`" % f["module"], a["attempt"], _fmt(a["added"], "{}"), _fmt(a["removed"], "{}"),
                     _fmt(a["hunks"], "{}"), "%d + %d" % (len(a["intents"]), a["undeclared"]), tag_text))
    lines = ["## 7. Edit size", ""]
    lines += table(["ID", "Module", "Attempt", "Lines added", "Lines removed", "Hunks", "Declared + undeclared edits",
                    "Tags"], rows)
    lines += ["", "Against the reference RTL, for the best attempt (else the last attempt with an edit record)."]
    return lines


def prompt_section(facts):
    rows = []
    for f in facts:
        called = [a for a in f["attempts"] if a["user_chars"] is not None]
        if not called:
            rows.append((f["design"], "`%s`" % f["module"], "-", "-", "-", "-", f["tokens"]))
            continue
        approx_in = sum((a["system_chars"] or 0) + (a["user_chars"] or 0) for a in called) // CHARS_PER_TOKEN
        rows.append((f["design"], "`%s`" % f["module"], called[0]["system_chars"], called[0]["user_chars"],
                     called[-1]["user_chars"], approx_in, f["tokens"]))
    lines = ["## 8. Prompt size", ""]
    lines += table(["ID", "Module", "System prompt [chars]", "First user prompt [chars]", "Last user prompt [chars]",
                    "Input tokens (estimate)", "Output tokens"], rows)
    lines += ["", "The input estimate is (system + user characters of every attempt) / %d; the API usage of the "
              "prompt is not stored in the run folder. The user prompt grows with the attempt history, so the last "
              "prompt is the largest." % CHARS_PER_TOKEN]
    return lines


def cost_section(facts, price_in, price_out):
    """Cost estimate for the given prices in USD per million tokens (input tokens are the estimate of section 8)."""
    rows, total = [], 0.0
    for f in facts:
        approx_in = sum(((a["system_chars"] or 0) + (a["user_chars"] or 0)) for a in f["attempts"]) // CHARS_PER_TOKEN
        cost = approx_in * (price_in or 0.0) / 1e6 + f["tokens"] * (price_out or 0.0) / 1e6
        total += cost
        rows.append((f["design"], "`%s`" % f["module"], approx_in, f["tokens"], "%.4f" % cost))
    lines = ["## 9. Cost estimate", ""]
    lines += table(["ID", "Module", "Input tokens (estimate)", "Output tokens", "Cost [USD]"], rows)
    lines += ["", "Total: %.4f USD (prices: %s input, %s output, USD per million tokens)." % (
        total, _num(price_in), _num(price_out))]
    return lines


def earlier_section(rows, facts):
    lines = ["## 10. Earlier runs of the same designs (not in the tables above)", ""]
    if not rows:
        return lines + ["None: every design has a single run."]
    shown = []
    for r in rows:
        truncated = "-" if r["truncated"] is None and not r["inferred"] else (
            "n/a" if r["truncated"] is None else "%d%s" % (r["truncated"], " (inferred)" if r["inferred"] else ""))
        shown.append((r["design"], "`%s`" % r["module"], r["run_id"], r["verdict"], r["attempts"], r["tokens"],
                      _fmt(r["llm_time_s"]), _fmt(r["eda_time_s"]), truncated, r["stop_reason"] or "-", r["source"]))
    lines += table(["ID", "Module", "Run", "Verdict", "Attempts", "Output tokens", "LLM time [s]", "Vivado time [s]",
                    "Truncated answers", "Stop reason", "Source"], shown)
    lines += ["", "\"inferred\": the run folder is gone, but the tokens equal attempts x max_tokens, so every answer "
              "reached the limit. A run with truncated answers wasted attempts: raise `--max_tokens`."]
    all_tokens = sum(f["tokens"] for f in facts) + sum(r["tokens"] or 0 for r in rows)
    all_llm = sum(f["llm_time_s"] for f in facts) + sum(r["llm_time_s"] or 0.0 for r in rows)
    all_eda = sum(f["eda_time_s"] for f in facts) + sum(r["eda_time_s"] or 0.0 for r in rows)
    lines += ["", "Everything spent, earlier runs included: %d output tokens, %s of LLM time, %s of Vivado time." % (
        all_tokens, _duration(all_llm), _duration(all_eda))]
    return lines


def format_markdown(facts, earlier, price_in=None, price_out=None):
    """The whole report. `facts` are the `run_facts` of the latest runs, `earlier` the rows of `earlier_rows`."""
    if not facts:
        return "# Run statistics\n\nNo finished run (PASS or FAIL) found.\n"
    sections = [overview_section(facts), design_section(facts), totals_section(facts), quality_section(facts),
                attempts_section(facts), transform_section(facts), edit_section(facts), prompt_section(facts)]
    if price_in is not None or price_out is not None:
        sections.append(cost_section(facts, price_in, price_out))
    sections.append(earlier_section(earlier, facts))
    header = ["# Run statistics", "",
              "Latest run of each design; the numbers come from `fabric_report.json`, `llm_interactions.jsonl` and "
              "`llm_interaction.json` of the run folders. Tokens are output tokens.", ""]
    body = "\n\n".join("\n".join(section) for section in sections)
    return "\n".join(header) + "\n" + body + "\n"


# ----------------------------------------------------------------------------------------------------------------
# Command line
# ----------------------------------------------------------------------------------------------------------------

def collect(outputs_dir, model=None):
    """(facts of the latest runs, rows of the earlier runs) for the finished runs below `outputs_dir`."""
    reports = [r for r in load_reports(outputs_dir) if r.get("final_verdict") in COUNTED_VERDICTS
               and (model is None or r.get("model") == model)]
    runs = [load_run(r, outputs_dir) for r in reports]
    latest, earlier = select_runs(runs)
    facts = [run_facts(run) for run in latest]
    results = [e for e in load_results_file(outputs_dir) if model is None or e.get("llm_model") == model]
    rows = earlier_rows(earlier, results, {r.get("run_id") for r in reports} | {f["run_id"] for f in facts},
                        common_max_tokens(facts))
    return facts, rows


def main(argv=None):
    parser = argparse.ArgumentParser(description="Time, tokens, attempts and transformations of the finished runs.")
    parser.add_argument("--outputs", default=os.path.join(TRANSFORM_DIR, "Outputs"),
                        help="folder searched for fabric_report.json (default: Outputs/)")
    parser.add_argument("--model", default=None, help="only the runs of this model")
    parser.add_argument("--markdown", default=None,
                        help="report file (default: <outputs>/run_statistics.md)")
    parser.add_argument("--json", default=None, help="also write the numbers to this JSON file")
    parser.add_argument("--price_in", type=float, default=None, help="USD per million input tokens (adds a cost section)")
    parser.add_argument("--price_out", type=float, default=None,
                        help="USD per million output tokens (adds a cost section)")
    args = parser.parse_args(argv)

    facts, rows = collect(args.outputs, args.model)
    text = format_markdown(facts, rows, args.price_in, args.price_out)
    path = args.markdown or os.path.join(args.outputs, "run_statistics.md")
    if os.path.dirname(path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(text)
    if args.json:
        with open(args.json, "w") as f:
            json.dump({"latest": facts, "earlier": rows}, f, indent=2, default=str)
    print("%d design(s), %d earlier run(s); report written to %s" % (len(facts), len(rows), path))
    return 0


if __name__ == "__main__":
    sys.exit(main())
