"""
Feedback note sent to the LLM after an attempt. Exactly one note per attempt, chosen by the stage that failed:

  no Verilog extracted   -> ask again with the required output format
  simulation failed      -> compile errors, or the failing test vectors (+ a latency-shift diagnosis)
  synthesis / impl failed-> the error lines of the Vivado log
  everything passed      -> result_note(): the comparison with the baseline, plus the evidence of the chosen
                            --feedback_detail level (cumulative, defined in Pipeline/levels.py)

--feedback_detail none                the verdict only (better / worse in which metric), no numbers.
--feedback_detail metrics             LUT / FF / DSP / BRAM / Fmax against the baseline, the timing basis,
                                      the primitive histogram.
--feedback_detail reports             the same, plus the digest: where the area is (per module), which memories
                                      became block RAM / LUT RAM / SRL, how the DSP48E1 are configured, the worst
                                      path, the highest-fanout nets; every number with its value in the best attempt
                                      so far (or in the reference), so the effect of the last change can be read off.
--feedback_detail reports+logs        the same, plus what the tools did (Analysis/LogMining.py): opt_design changes,
                                      phys_opt replications, the WNS trajectory, log message groups. A synthesis-only
                                      run carries the synthesis facts only.
--feedback_detail reports+logs+edits  the same; the record of the earlier edits is added to the attempt history by
                                      Pipeline/prompts.py (edit_history_text), not to this note.

Every function returns (note_text, info_dict); the info dict is stored in fabric_report.json.
"""

import os

from .levels import FEEDBACK_DETAILS, has   # FEEDBACK_DETAILS is re-exported for Run.py
from .prompts import METRIC_ROWS, fmt, histogram_text, timing_text
from FabricAnalysis import FabricReportParser
from LogMining import LogMiner
from ReportDigest import format_digest

# Max number of log entries (failing vectors / error lines) in one note.
MAX_ENTRIES = 10


def _bullets(items, total, what):
    text = "\n".join(f"- {item}" for item in items)
    omitted = total - len(items)
    return text + (f"\n(+{omitted} more {what} omitted)" if omitted > 0 else "")


def _read(path):
    if not os.path.exists(path):
        return None
    with open(path, "r", errors="ignore") as f:
        return f.read()


# ---------------------------------------------------------------------- failures

def extraction_note():
    return ("Your previous response did not contain an extractable Verilog design between the "
            "'// Start Verilog Design' and '// End Verilog Design' markers. Respond again using the "
            "required output format.", {"type": "extraction_failure"})


def simulation_note(sim_log, latency_contract):
    """Why the simulation failed: compile errors, or the failing vectors (with a latency-shift diagnosis)."""
    parser = FabricReportParser
    log = _read(sim_log)
    generic = ("The previous design FAILED functional simulation against the fixed testbench (the top module name "
               "and port list must match the specification, and the behavior must be functionally correct). "
               "Fix the functional/interface issue.")
    if log is None:
        return generic, {"type": "simulation_failure", "log_available": False}

    feedback = parser.extract_simulation_feedback(log, max_entries=MAX_ENTRIES)
    if feedback["compile_errors"]:
        note = ("The previous design FAILED to compile/elaborate for simulation. Compile/elaboration errors:\n"
                + _bullets(feedback["compile_errors"], feedback["compile_errors_total"], "compile error(s)")
                + "\nFix these syntax/elaboration issues, keeping the exact same top module name and port list; "
                  "every module you instantiate must be defined in your Verilog section.")
    elif feedback["failing_vectors"]:
        note = ("The previous design compiled and ran, but FAILED functional simulation against the fixed "
                "testbench. Failing checks (actual value, expected value):\n"
                + _bullets(feedback["failing_vectors"], feedback["failing_vectors_total"], "failing check(s)")
                + "\nAnalyze these mismatches and fix the functional/interface issue, keeping the exact same top "
                  "module name and port list.")
        shift = parser.detect_latency_shift(log)
        if shift["detected"]:
            direction = "later" if shift["shift_cycles"] > 0 else "earlier"
            note += (f"\n\nLatency diagnosis: output '{shift['signal']}' is consistently {direction} by "
                     f"{abs(shift['shift_cycles'])} cycle(s) than expected (matched {shift['match_ratio']:.0%} of "
                     f"{shift['samples']} failing samples). A register stage was probably added or removed on a "
                     "path to this output.")
        feedback["latency_shift_diag"] = shift
    else:
        note = generic
    note += (f"\nThe testbench is cycle-exact ({latency_contract}) If almost every check mismatches, a "
             "transformation probably added, removed or moved a register stage on an output path, changed the "
             "memory read latency, or changed the reset behavior.")
    return note, {"type": "simulation_failure", **feedback}


def synthesis_note(log_path, failed_stage):
    """Why synthesis / implementation failed (error lines of the Vivado log)."""
    log = _read(log_path)
    generic = ("The previous design failed to synthesize in Vivado. Fix any syntax or synthesis-blocking "
               "constructs while preserving functionality.")
    if log is None:
        return generic, {"type": "synthesis_failure", "log_available": False}
    feedback = FabricReportParser.extract_synthesis_feedback(log, max_entries=MAX_ENTRIES)
    if feedback["errors"]:
        note = ("The previous design failed to synthesize in Vivado. Synthesis errors:\n"
                + _bullets(feedback["errors"], feedback["errors_total"], "error(s)")
                + "\nFix these issues while preserving functionality.")
    elif feedback["critical_warnings"]:
        note = ("The previous design failed to synthesize in Vivado (no explicit ERROR line found; nearest critical "
                "warnings below):\n"
                + _bullets(feedback["critical_warnings"], feedback["critical_warnings_total"], "critical warning(s)")
                + "\nFix any syntax or synthesis-blocking constructs while preserving functionality.")
    elif failed_stage == "implementation":
        # No error line at all: Vivado most likely stopped by itself (crash, memory, timeout), which says nothing
        # about the design. Do not blame the RTL.
        note = ("The design synthesized, but the Vivado implementation (place & route) stopped without reporting "
                "any error message. This is probably a tool problem, not an RTL problem. Re-submit the design above "
                "unchanged, or with a small, careful variation.")
    else:
        note = generic
    if failed_stage == "implementation" and (feedback["errors"] or feedback["critical_warnings"]):
        note = "The design synthesized but implementation (place & route) failed. " + note
    return note, {"type": "synthesis_failure", **feedback}


# ---------------------------------------------------------------------- result

def comparison_table(metrics, baseline_metrics, comparison):
    """Markdown table baseline vs attempt for LUT, FF, DSP, BRAM, Fmax, with the better / same / worse verdict."""
    lines = ["| metric | baseline | attempt | vs baseline |", "|---|---|---|---|"]
    for key, label in METRIC_ROWS:
        key = "fmax_mhz" if key == "Fmax" else key
        verdict = comparison.get("Fmax" if key == "fmax_mhz" else key) or "not compared"
        lines.append(f"| {label} | {fmt(baseline_metrics.get(key))} | {fmt(metrics.get(key))} | {verdict} |")
    return "\n".join(lines)


def result_note(rec, baseline, best, feedback_detail):
    """
    Feedback for an attempt that passed simulation and the flow.
      rec       record of the attempt ({"metrics", "comparison", "diag", "digest", "regressions_vs_best"})
      baseline  measured reference ({"metrics", "digest"})
      best      best correct attempt so far (may be `rec` itself)
    """
    m, base_m, comparison = rec["metrics"], baseline["metrics"], rec["comparison"]
    labels = dict(METRIC_ROWS)
    better = [labels[k] for k, v in comparison.items() if v == "better"]
    worse = [labels[k] for k, v in comparison.items() if v == "worse"]
    head = (f"The previous attempt passed simulation and the Vivado {m['stage']} flow. Against the baseline it is "
            + (f"better in {', '.join(better)}" if better else "not better in any metric")
            + (f" and worse in {', '.join(worse)}" if worse else "") + ".")
    lines = [head]
    if has(feedback_detail, "metrics"):
        lines += [comparison_table(m, base_m, comparison), timing_text(m)]
        if comparison.get("Fmax") is None:
            lines.append("Fmax was not compared: it is compared only when the baseline and the attempt both report a "
                         "register-to-register WNS.")
        lines.append("Primitive histogram of this attempt: " + histogram_text(rec["diag"].get("primitives")) + ".")

    if has(feedback_detail, "digest"):
        # Compare with the best earlier attempt when there is one, else with the reference.
        if best is not None and best is not rec and best.get("digest"):
            previous, label = best["digest"], f"your best attempt so far (attempt {best['attempt']})"
        else:
            previous, label = baseline.get("digest"), "the reference"
        digest = format_digest(rec.get("digest"), previous=previous, previous_label=label)
        if digest:
            lines.append(digest)
    if has(feedback_detail, "logs"):
        # What the tools did for this attempt; the facts already follow the stage of the run (synthesis-only runs
        # have no implementation section), so nothing is requested that the run did not produce.
        facts = LogMiner.format_log_facts(rec.get("log_facts"))
        if facts:
            lines.append(f"What the Vivado tools did for this attempt (from the {m['stage']} logs):\n" + facts)
    if rec["regressions_vs_best"]:
        lines.append("Compared with your best attempt so far: " + "; ".join(rec["regressions_vs_best"]) + ".")
    lines.append("Make one or two deliberate changes to the CURRENT BEST design to improve a metric that is still "
                 "same or worse, and explain them in the Reasoning section relative to the REFERENCE RTL.")
    info = {"type": "result", "feedback_detail": feedback_detail, "comparison": comparison, "metrics": m,
            "baseline": base_m, "regressions_vs_best": rec["regressions_vs_best"]}
    return "\n".join(lines), info
