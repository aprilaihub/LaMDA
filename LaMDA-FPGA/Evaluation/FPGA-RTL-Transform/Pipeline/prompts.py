"""
Prompts of the optimization task.

System prompt = base Verilog rules + task description + (--knowledge primers) one section per resource primer.
User prompt   = the design (problem, header, clock, latency contract), the REFERENCE RTL, its measured baseline
                (+ the evidence of the --feedback_detail level, see Pipeline/levels.py: report digest, log facts),
                the attempt history (+ the record of the earlier edits with --feedback_detail reports+logs+edits)
                and the feedback note.

--knowledge none     the LLM relies on what it knows about the 7-Series fabric.
--knowledge primers  Primers/{lut,dsp,bram}.md are appended: what a LUT, DSP48E1 and block RAM can do, and how
                     the Verilog is written so that Vivado infers them. Resources only; no design-specific
                     advice and no transformation recipe.
--knowledge primers+patterns
                     the resource primers plus Primers/transforms.md (generic RTL transformation patterns, one
                     per label of the answer format, with their latency rules) and Primers/reports.md (how to read
                     the Vivado results, per stage). The only difference to `primers` is the added knowledge, so
                     the two levels measure what the transformation patterns are worth.
"""

import os
import re

from . import TRANSFORM_DIR
from .levels import has
from utils import append_prompt_section
from ReportDigest import format_digest
from LogMining import LogMiner
from Verdict import sha256_text
from EditLog import taxonomy_prompt_text

PRIMERS_DIR = os.path.join(TRANSFORM_DIR, "Primers")
PRIMER_MODULES = ("lut", "dsp", "bram")           # resource primers, in prompt order
KNOWLEDGE_LEVELS = ("none", "primers", "primers+patterns")

# Extra primers of the level `primers+patterns`: (file, section title). Resource primers keep the title
# "RESOURCE KNOWLEDGE: <NAME> (Xilinx 7-Series)".
PATTERN_PRIMERS = (("transforms", "TRANSFORMATION PATTERNS (Xilinx 7-Series, Vivado)"),
                   ("reports", "HOW TO READ THE VIVADO RESULTS"))

# Rows of the comparison tables: (metric key, label). Fmax is judged on the WNS (see Analysis/Verdict.py).
METRIC_ROWS = (("LUT_USED", "LUT"), ("FF_TOTAL", "FF"), ("DSP48E1", "DSP"), ("BRAM", "BRAM"), ("Fmax", "Fmax"))

# Base rules of this pipeline (deliberately not the shared utils.SYSTEM_PROMPT used by the other pipelines).
SYSTEM_PROMPT = """# Role
You are an expert in FPGA design and Verilog (IEEE 1364) RTL coding, targeting AMD/Xilinx 7-Series devices (Artix-7, Kintex-7, Virtex-7, Zynq-7000) synthesized and simulated with Vivado.

# Precision
Be precise and consistent with Verilog syntax and semantics so the generated code compiles and elaborates cleanly under Vivado. Target Verilog-2001 (IEEE 1364-2001) constructs within Vivado's synthesizable subset. Avoid non-synthesizable constructs inside the design (e.g. delay controls such as #10, `real` variables, or unsynthesizable `initial` blocks) and avoid SystemVerilog-only syntax unless explicitly instructed to use it.

# Correctness Rules
- Use non-blocking assignments (<=) exclusively inside sequential always @(posedge clk...) (or similar clocked) blocks, and blocking assignments (=) exclusively inside combinational always @(*) (or equivalent) blocks. Never mix the two assignment styles within the same always block.
- Never rename, reorder, add, remove, or change the bit width of any port of the top module. Reproduce the exact top-module header given in the prompt verbatim. All inputs and outputs must be declared in the top module.
- When using begin/end blocks, follow this style:
  ```verilog
  if (condition) begin
      // code
  end else begin
      // code
  end
  ```"""

TASK_TEMPLATE = """# Task: RTL Optimization of an Accelerator
You receive a REFERENCE Verilog design of a small accelerator (for example a systolic array, a streaming filter or a dot-product engine, with its memories and controller, made of several modules). It is functionally correct: it passes a fixed, cycle-exact testbench. Improve it by RTL transformations (better Fmax, fewer LUT/FF/DSP/BRAM) when synthesized with Vivado for the target 7-Series device. The work is iterative: after every attempt you get the measured result and feedback, and you refine the best design so far, a few changes at a time.

# Design Contract (what must not change)
- The top module name and its port list (see the base rules).
- The behavior seen at the ports: reset semantics, the memory write interface, the valid or start / done handshake, the read-out interface, output widths and signed arithmetic. The testbench compares every output with the reference in every clock cycle.
- The timing contract stated as 'Latency contract' in the user prompt: `done` (or the output valid signal) and the read-out data appear on exactly the same clock edges as in the reference. Never add or remove a pipeline stage on a path that ends at an output, not even to meet timing. Inside the design you are free: you may change how registers are described, move logic between registers, absorb registers into a DSP48E1 or block RAM, restructure the arithmetic, the memories, the controller and the modules.

# How Your Result Is Judged
- Your design is measured with the same Vivado flow, part and clock constraint as the baseline in the user prompt (the stage, post-synthesis or post-implementation, is stated there).
- Every attempt is compared with the baseline metric by metric (LUT, FF, DSP, BRAM: fewer is better; Fmax: higher is better), and each metric is marked better, same or worse. An attempt succeeds when it is functionally correct and at least one metric is better than the baseline; metrics that got worse are reported, so avoid them. The correct design with the most better metrics (then the fewest worse) is kept and is the starting point of your next attempt.
- Make one or two deliberate changes per attempt, so the measured effect can be attributed to them. A change that does not show in the measurements is a wasted attempt.
- Fmax comes from the worst register-to-register slack (WNS). When no constrained register-to-register path remains (for example every register is absorbed into a DSP48E1 or block RAM), Fmax comes from the minimum clock period of the primitive, and each result states which basis was used.
- Area is counted as LUTs, flip-flops (FF_TOTAL), DSP48E1 and block RAM after Vivado optimization; logic that synthesis removes costs nothing.

# Coding Rules
- Write behavioral RTL and let Vivado infer the primitives (DSP48E1, block RAM, LUT RAM, SRL, CARRY4). Do not hand-instantiate library primitives: an incompletely wired primitive simulates as 'x'. Steer inference with synthesis attributes (for example ram_style, use_dsp) when useful.
- The design has several modules. Return ALL modules of your design in the Verilog section, top module included; you may add, remove, merge or rename the modules other than the top module.
- Signed arithmetic: declare every operand of a signed expression `signed` (one unsigned operand makes the whole expression unsigned). Never sign-extend with a concatenation for later arithmetic, because a concatenation is unsigned; let a signed-to-signed assignment extend the value, or use $signed(...).
- Before answering, count the latency of your design: for every output, trace the paths from the inputs that matter (start, the write ports, the read address) and count only clocked register crossings (`reg <= ...` inside a clocked always block, and the registered read of a block RAM); combinational logic adds zero cycles. The counts must equal the reference exactly. Deleting a register and folding it into combinational logic lowers a count by one, which is a latency bug even if the design gets smaller.

# Output Format
Provide these outputs, using these exact markers in the response:
1. Design name: start marker '// Start Design name', end marker '// End Design name'.
2. Verilog design: start marker '// Start Verilog Design', end marker '// End Verilog Design'. All modules of the design.
3. Reasoning: start marker '// Start Reasoning', end marker '// End Reasoning'. Free text explaining the RTL transformations you applied relative to the REFERENCE RTL (cumulative, not relative to your previous attempt): for each change, what you changed, where (modules, signals), why it should reduce LUT/FF/DSP/BRAM usage or improve timing on the 7-Series fabric, and how the latency contract is kept.
4. Transformations: start marker '// Start Transformations', end marker '// End Transformations'. A JSON list with one object per change relative to the REFERENCE RTL (cumulative), so that your edits can be recorded. Fields of each object: "module" (the module you edited), "target" (the signal, memory or block), "tag" (one label from the list below), "rationale" (one sentence), "expected_effect" (an object that maps LUT, FF, DSP, BRAM or Fmax to "up" or "down"; only the metrics you expect to change). Example: [{"module": "top", "target": "acc_reg", "tag": "OTHER", "rationale": "one sentence", "expected_effect": {"FF": "down"}}]. The labels only name what an edit is; they are not a list of edits to make:
@TAXONOMY@
The top module name must be the same as the design name.

# Behavior
Follow what is asked and do not ask clarifying questions; make the most reasonable assumption and proceed. Respond with ONLY the four marker-delimited sections above and no additional prose outside those markers. Do not wrap the Verilog design section itself in markdown code fences."""

# The label list comes from Analysis/transform_taxonomy.json, so the prompt and the cross-check share one source.
TASK_SECTION = TASK_TEMPLATE.replace("@TAXONOMY@", taxonomy_prompt_text())


# ---------------------------------------------------------------------- system prompt

def load_primer(name):
    """Primers/<name>.md -> (meta, body). A leading <!-- key: value --> comment holds the header (family, module,
    references, scope); it is parsed here and never sent to the LLM."""
    with open(os.path.join(PRIMERS_DIR, f"{name}.md"), "r") as f:
        text = f.read()
    meta = {}
    match = re.match(r"\s*<!--(.*?)-->", text, re.S)
    if match:
        for line in match.group(1).splitlines():
            if ":" in line:
                key, value = line.split(":", 1)
                meta[key.strip()] = value.strip()
        text = text[match.end():]
    return meta, text.strip()


def build_system_prompt(knowledge):
    """
    (system_prompt, sections) for --knowledge none|primers|primers+patterns. `sections` lists name, source, size and
    hash of every part, recorded with every LLM call so that a result can be traced to the exact prompt text.
    """
    sections = []

    def note(name, source, text, **extra):
        digest = sha256_text(text)
        sections.append({"name": name, "source": source, "chars": len(text), "sha256": digest,
                         "version": digest[:12], **extra})

    note("base_system_prompt", "Pipeline/prompts.py:SYSTEM_PROMPT", SYSTEM_PROMPT)
    note("optimize_rules", "Pipeline/prompts.py:TASK_SECTION", TASK_SECTION)
    prompt = f"{SYSTEM_PROMPT}\n\n{TASK_SECTION}"
    if knowledge in ("primers", "primers+patterns"):
        for name in PRIMER_MODULES:
            _, body = load_primer(name)
            prompt = append_prompt_section(prompt, f"RESOURCE KNOWLEDGE: {name.upper()} (Xilinx 7-Series)", body)
            note(f"primer_{name}", f"Primers/{name}.md", body)
    if knowledge == "primers+patterns":
        for name, title in PATTERN_PRIMERS:
            _, body = load_primer(name)
            prompt = append_prompt_section(prompt, title, body)
            note(f"primer_{name}", f"Primers/{name}.md", body)
    return prompt, sections


# ---------------------------------------------------------------------- shared text helpers

def fmt(value, spec="{}", none="n/a"):
    return none if value is None else spec.format(value)


def histogram_text(primitives):
    return ", ".join(f"{k} x{v}" for k, v in sorted((primitives or {}).items())) or "n/a"


def timing_text(m):
    """One sentence stating Fmax and what it is based on (the 'timing_basis' metric)."""
    period, fmax = fmt(m.get("clock_period_ns"), "{:.3f} ns"), fmt(m.get("fmax_mhz"), "{:.1f} MHz")
    basis = m.get("timing_basis")
    if basis == "reg2reg_wns":
        return (f"Timing: worst register-to-register slack (WNS) {fmt(m.get('wns_ns'), '{:.3f} ns')} at a "
                f"clock period of {period}; Fmax about {fmax}.")
    if basis == "min_period":
        lead = (f"worst register-to-register slack (WNS) {m['wns_ns']:.3f} ns" if m.get("wns_ns") is not None
                else "no constrained register-to-register path (WNS not reported)")
        return (f"Timing: {lead}; Fmax about {fmax} at a clock period of {period}, bounded by the minimum clock "
                f"period of a primitive ({m.get('min_period_ns'):.3f} ns, pulse-width check of a DSP48E1/RAMB/BUFG pin).")
    return "Timing: no timing evidence (WNS not reported and no minimum-period check); Fmax is unknown."


# ---------------------------------------------------------------------- user prompt

def baseline_text(baseline, fpga_part, feedback_detail):
    """The measured reference: metrics and histogram (metrics), the report digest (reports), the log facts (logs)."""
    m = baseline["metrics"]
    lines = [
        f"Baseline measurements of the REFERENCE RTL (Vivado {m['stage']} results, part {fpga_part}):",
        f"- LUT_USED: {m['LUT_USED']}",
        f"- FF_TOTAL: {m['FF_TOTAL']}",
        f"- DSP48E1: {m['DSP48E1']}",
        f"- BRAM: {m['BRAM']} (RAMB36 equivalents; a RAMB18 counts 0.5)",
        "- " + timing_text(m),
        "- Primitive histogram: " + histogram_text(baseline.get("primitives")),
    ]
    if m.get("wns_ns") is None:
        lines.append("- Fmax note: the reference has no timed register-to-register path, so its Fmax is only a "
                     "primitive limit and is NOT compared; only LUT, FF, DSP and BRAM are.")
    else:
        lines.append("- Fmax note: Fmax is compared only when your design also reports a register-to-register WNS; "
                     "if every register is absorbed into a DSP48E1/BRAM, Fmax is not compared.")
    if has(feedback_detail, "digest"):
        digest = format_digest(baseline.get("digest"))
        if digest:
            lines.append(digest)
    if has(feedback_detail, "logs"):
        facts = LogMiner.format_log_facts(baseline.get("log_facts"))
        if facts:
            lines.append(f"What the Vivado tools did for the REFERENCE (from the {m['stage']} logs):\n" + facts)
    return "\n".join(lines)


GOAL_TEXT = ("Goal: be better than the baseline in at least one of LUT, FF, DSP, BRAM or Fmax, with the same "
             "port-level behavior and timing. Metrics that get worse are reported, so avoid them.")

# Labels of the metric deltas in the edit history (keys of Analysis/EditLog.py DELTA_KEYS).
DELTA_LABELS = (("LUT_USED", "LUT", "{:+g}"), ("FF_TOTAL", "FF", "{:+g}"), ("DSP48E1", "DSP", "{:+g}"),
                ("BRAM", "BRAM", "{:+g}"), ("fmax_mhz", "Fmax", "{:+.1f} MHz"))


def history_text(history, best, feedback_detail="reports"):
    """
    Table of the previous attempts. Without the metrics evidence (level `none`) the numbers are left out: the LLM
    only sees which attempts passed and how each one was judged.
    """
    if not history:
        return ""
    show_metrics = has(feedback_detail, "metrics")
    lines = ["Results of your previous attempts (same flow and part as the baseline):"]
    if show_metrics:
        lines += ["| attempt | simulation | LUT | FF | DSP | BRAM | Fmax (MHz) | outcome |",
                  "|---|---|---|---|---|---|---|---|"]
    else:
        lines += ["| attempt | simulation | outcome |", "|---|---|---|"]
    for rec in history:
        m = rec.get("metrics") or {}
        marker = " (best)" if best and rec["attempt"] == best["attempt"] else ""
        sim = "pass" if rec["sim_passed"] else "FAIL"
        if show_metrics:
            lines.append(
                f"| {rec['attempt']}{marker} | {sim} | {fmt(m.get('LUT_USED'))} | "
                f"{fmt(m.get('FF_TOTAL'))} | {fmt(m.get('DSP48E1'))} | {fmt(m.get('BRAM'))} | "
                f"{fmt(m.get('fmax_mhz'), '{:.1f}')} | {rec['outcome']} |")
        else:
            lines.append(f"| {rec['attempt']}{marker} | {sim} | {rec['outcome']} |")
    return "\n".join(lines)


def delta_text(delta):
    """'LUT -40, BRAM +1' for the non-zero metric changes of an attempt; says so when nothing changed or is missing."""
    if delta is None:
        return "no metrics (the attempt failed before the reports)"
    parts = [f"{label} {spec.format(delta[key])}" for key, label, spec in DELTA_LABELS
             if delta.get(key) not in (None, 0)]
    return ", ".join(parts) or "no change in any metric"


def edit_history_text(history):
    """
    The record of the earlier edits (level `reports+logs+edits`): for every previous attempt that has an edit log,
    the transformation labels the LLM declared, whether the Vivado reports support each one, and what the metrics did
    against the attempt it started from. The deltas belong to all edits of the attempt together; the text says so,
    because one attempt can hold several edits and the effect of a single edit is not known from it.
    """
    rows = []
    for rec in history:
        log = rec.get("edit_log")
        if not log:
            continue
        origin = "the reference" if not log.get("base_attempt") else f"attempt {log['base_attempt']}"
        lines = log["lines_vs_base"]
        edits = []
        for intent in log["intents"]:
            where = f" on {intent['target']}" if intent.get("target") else (
                f" in module {intent['module']}" if intent.get("module") else "")
            verdict = {"confirmed": "supported by the reports", "unsupported": "NOT supported by the reports",
                       "unverifiable": "cannot be checked from the reports"}.get(intent["status"], intent["status"])
            edits.append(f"{intent['tag']}{where} ({verdict})")
        if log.get("undeclared_changes"):
            edits.append(f"{log['undeclared_changes']} change(s) in modules you did not name")
        result = "simulation FAILED" if not rec["sim_passed"] else delta_text(log["delta_vs_base"])
        rows.append(f"- attempt {rec['attempt']} (edited {origin}, +{lines['added']}/-{lines['removed']} lines): "
                    + ("; ".join(edits) or "no declared transformation") + f". Change vs {origin}: {result}.")
    if not rows:
        return ""
    return ("Record of the edits of your previous attempts (the metric change belongs to all edits of that attempt "
            "together, not to one edit alone):\n" + "\n".join(rows))


def build_user_prompt(entry, reference_code, baseline, fpga_part, feedback_detail,
                      history, best, base_attempt, attempt_code, previous_code, correction_note):
    """
    User prompt of one attempt.

      entry            dataset entry of the design
      baseline         measured reference ({"metrics", "primitives", "digest"})
      history, best    records of the previous attempts and the best correct one so far
      base_attempt     number of the attempt whose code is `previous_code` (the base of this attempt)
      attempt_code     {attempt number: Verilog} of every attempt
      correction_note  feedback on the last attempt (None for the first attempt)
    """
    header = [
        f"top module: {entry['module']}",
        f"Problem: {entry['Problem']}",
        f"Top module header: {entry['Module header']}",
        f"Clock constraint (XDC):\n{entry['Clock Constraint'].strip()}",
        f"Latency contract: {entry['latency_contract']} The testbench is cycle-exact.",
    ]
    parts = [
        "\n".join(header),
        "REFERENCE RTL (functionally correct, passes the testbench; this is the design to optimize):\n"
        "```verilog\n" + reference_code.strip() + "\n```",
        baseline_text(baseline, fpga_part, feedback_detail),
        GOAL_TEXT,
    ]
    table = history_text(history, best, feedback_detail)
    if table:
        parts.append(table)
    if has(feedback_detail, "edits"):
        edit_record = edit_history_text(history)
        if edit_record:
            parts.append(edit_record)

    last_no = history[-1]["attempt"] if history else None
    if previous_code and correction_note:
        if base_attempt is not None and base_attempt != last_no:
            # The last attempt did not beat the best design: refine the best one.
            parts.append(
                f"CURRENT BEST design (attempt {base_attempt}); refine THIS design:\n```verilog\n"
                f"{previous_code.strip()}\n```\n\nFeedback on your last attempt (attempt {last_no}, a different "
                f"design that is not shown again):\n{correction_note}")
        else:
            label = "CURRENT BEST design" if best and best["attempt"] == last_no else "Your previous attempt"
            parts.append(f"{label} (attempt {last_no}) was:\n```verilog\n{previous_code.strip()}\n```\n\n"
                         f"{correction_note}")
    elif correction_note:
        parts.append(f"Feedback on your previous attempt (attempt {last_no}):\n{correction_note}")
    if (best and best["attempt"] not in (base_attempt, last_no) and best["attempt"] in attempt_code):
        parts.append(f"The best functionally correct attempt so far is attempt {best['attempt']}; fall back to it "
                     f"if the current fix does not work:\n```verilog\n{attempt_code[best['attempt']].strip()}\n```")
    parts.append("Provide the optimized design (all modules), the Reasoning section and the Transformations list: "
                 "the RTL transformations you applied relative to the REFERENCE RTL. Change one or two things per "
                 "attempt.")
    return "\n\n".join(parts)
