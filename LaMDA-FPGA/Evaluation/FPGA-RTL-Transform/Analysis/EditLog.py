"""
Objective record of the RTL edits of one attempt (pure Python: no Vivado, no LLM, no file access except the taxonomy).

What the LLM says about its edits is not taken as a fact. For every attempt this module computes:

  1. the diff against the REFERENCE RTL and against the BASE attempt (the design the attempt started from), as
     unified patches and as hunks: where (module), what kind of line (declaration, clocked assignment, attribute...),
     which signals, and the removed / added code;
  2. structural facts of the Verilog (clocked always blocks, non-blocking targets, `*` operators, memories,
     synthesis attributes) and their change;
  3. the edit INTENTS the LLM declared (Transformations section of its answer, labels from
     Analysis/transform_taxonomy.json), each one cross-checked against the diff and against the Vivado facts of
     the stage that was run (metrics, primitive histogram): confirmed / unsupported / unverifiable;
  4. the hunks that no intent explains (undeclared changes).

Limits, stated so the data is read correctly:
  - The Verilog handling is a line-based heuristic (comments stripped, modules found by `module`/`endmodule`),
    not a parser. It is exact for the diffs and good enough for the structural counts.
  - Evidence is design-level: a confirmed label means "the design moved the way the label says", not "this edit
    alone caused it". Isolating the effect of one edit is the job of the attribution step (Analysis/Attribution.py).
  - With --stop_stage synth the metrics and primitives are the synthesis ones, so only synthesis facts are used.

Field meanings: the "Output layout" section of the README.
"""

import difflib
import json
import os
import re

from Verdict import WNS_TOLERANCE_NS, extract_between

ANALYSIS_DIR = os.path.dirname(os.path.abspath(__file__))
TAXONOMY_FILE = os.path.join(ANALYSIS_DIR, "transform_taxonomy.json")

TRANSFORMS_START = "// Start Transformations"
TRANSFORMS_END = "// End Transformations"

# Lines of code kept per side in one hunk record (the full change is in the patch files).
MAX_HUNK_LINES = 30

# Metric names the LLM may use in `expected_effect` -> key in the metrics dictionary. Fmax follows the WNS.
EFFECT_METRICS = {"LUT": "LUT_USED", "FF": "FF_TOTAL", "DSP": "DSP48E1", "BRAM": "BRAM", "Fmax": "wns_ns"}

# Metrics whose change against the reference / base is stored with every attempt.
DELTA_KEYS = ("LUT_USED", "FF_TOTAL", "DSP48E1", "BRAM", "wns_ns", "fmax_mhz")

_TAXONOMY = None


# ---------------------------------------------------------------------- taxonomy

def load_taxonomy():
    """{tag: {"description", "strength", "evidence"}} from transform_taxonomy.json (read once)."""
    global _TAXONOMY
    if _TAXONOMY is None:
        with open(TAXONOMY_FILE, "r") as f:
            _TAXONOMY = json.load(f)["tags"]
    return _TAXONOMY


def taxonomy_prompt_text():
    """The label list shown to the LLM: one line per tag, the meaning only (no advice on when to use it)."""
    return "\n".join(f"  - {tag}: {info['description']}" for tag, info in load_taxonomy().items())


# ---------------------------------------------------------------------- Verilog text helpers

def strip_comments(text):
    """
    Verilog without comments. Line structure is kept (a block comment becomes as many empty lines), so line
    numbers of the stripped text are valid in the original. A `//` inside a string literal is not handled.
    """
    text = re.sub(r"/\*.*?\*/", lambda m: "\n" * m.group(0).count("\n"), text, flags=re.S)
    return re.sub(r"//[^\n]*", "", text)


def normalize_line(line):
    """Whitespace-normalized code line, used to ignore formatting changes in the diff."""
    return re.sub(r"\s+", " ", line).strip()


def module_spans(code_lines):
    """[(module name, first line, last line)], 1-based and inclusive, from comment-free lines."""
    spans, current = [], None
    for number, line in enumerate(code_lines, 1):
        match = re.match(r"\s*module\s+([A-Za-z_]\w*)", line)
        if match and current is None:
            current = [match.group(1), number]
        if current is not None and re.match(r"\s*endmodule\b", line):
            spans.append((current[0], current[1], number))
            current = None
    if current is not None:   # unterminated module: runs to the end of the text
        spans.append((current[0], current[1], len(code_lines)))
    return spans


def module_at(spans, line_number):
    """Name of the module containing a 1-based line, None outside every module."""
    for name, first, last in spans:
        if first <= line_number <= last:
            return name
    return None


def declared_names(line):
    """Names declared on a declaration line (`reg [7:0] a, b;` -> a, b); [] when it is not a declaration."""
    match = re.match(r"\s*(?:input|output|inout|wire|reg|integer|genvar|parameter|localparam)\b(.*)", line)
    if not match:
        return []
    rest = re.sub(r"\[[^\]]*\]", " ", match.group(1))          # widths and array dimensions
    rest = re.sub(r"=.*?(?=,|;|$)", "", rest)                   # initial values
    names = re.findall(r"[A-Za-z_]\w*", rest.split(";")[0])
    return [n for n in names if n not in ("signed", "unsigned", "reg", "wire", "integer")]


def assigned_name(line):
    """Left-hand side signal of an assignment line (`assign x = ...`, `x[3] <= ...`), None otherwise."""
    match = re.match(r"\s*(?:assign\s+)?([A-Za-z_]\w*)\s*(?:\[[^\]]*\]\s*)*(?:<=|=)(?!=)", line)
    return match.group(1) if match else None


# `(* ram_style = "block" *)` anywhere in the line (a sensitivity list `@(*)` has no closing `*)`).
_ATTRIBUTE_PATTERN = re.compile(r"\(\*[^)]*\*\)")

# Kinds of a changed line, tested in this order (after the attribute test).
_LINE_KINDS = (
    ("declaration", re.compile(r"\s*(?:input|output|inout|wire|reg|integer|genvar|parameter|localparam)\b")),
    ("assign", re.compile(r"\s*assign\b")),
    ("always", re.compile(r"\s*always\b")),
    ("generate", re.compile(r"\s*(?:generate|endgenerate|genvar)\b")),
    ("clocked_assign", re.compile(r"[^;]*<=")),
    ("comb_assign", re.compile(r"\s*[A-Za-z_]\w*\s*(?:\[[^\]]*\]\s*)*=(?!=)")),
    ("control", re.compile(r"\s*(?:if|else|case|casez|casex|endcase|begin|end|for)\b")),
)


def line_kind(line):
    """Kind of one code line: attribute, declaration, assign, always, generate, clocked_assign, comb_assign, control, other."""
    if _ATTRIBUTE_PATTERN.search(line):
        return "attribute"
    for kind, pattern in _LINE_KINDS:
        if pattern.match(line):
            return kind
    return "other"


# ---------------------------------------------------------------------- diff and hunks

def unified_patch(old_text, new_text, old_name, new_name):
    """The unified diff of two sources as one string ('' when they are identical)."""
    lines = difflib.unified_diff(old_text.splitlines(keepends=True), new_text.splitlines(keepends=True),
                                 fromfile=old_name, tofile=new_name)
    return "".join(lines)


def _excerpt(lines):
    """First MAX_HUNK_LINES lines of one side of a hunk, with a flag telling when lines were cut."""
    return {"lines": [l.rstrip() for l in lines[:MAX_HUNK_LINES]], "total": len(lines),
            "truncated": len(lines) > MAX_HUNK_LINES}


def diff_hunks(old_text, new_text):
    """
    Compare two designs line by line (comments and whitespace ignored) and return
    {"added": n, "removed": n, "hunks": [...], "modules_added": [...], "modules_removed": [...], "modules_changed": [...]}.
    A hunk is {"module", "old_lines": [first, last], "new_lines": [first, last], "old_span", "new_span", "kinds",
    "signals", "removed", "added"}
    with 1-based line numbers (None for an empty side) and the code of both sides; the spans are 0-based half-open.
    """
    old_lines, new_lines = old_text.splitlines(), new_text.splitlines()
    old_code, new_code = strip_comments(old_text).splitlines(), strip_comments(new_text).splitlines()
    old_spans, new_spans = module_spans(old_code), module_spans(new_code)
    old_norm, new_norm = [normalize_line(l) for l in old_code], [normalize_line(l) for l in new_code]

    hunks, added, removed = [], 0, 0
    matcher = difflib.SequenceMatcher(None, old_norm, new_norm, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        # Blank-only changes (a comment became an empty line, a blank line was added) are not edits.
        if not any(old_norm[i1:i2]) and not any(new_norm[j1:j2]):
            continue
        # A hunk belongs to the module of its new code; a pure deletion belongs to the module it was removed from.
        module = module_at(new_spans, j1 + 1) if j2 > j1 else module_at(old_spans, i1 + 1)
        changed = [l for l in old_code[i1:i2] + new_code[j1:j2] if l.strip()]
        signals = []
        for line in changed:
            for name in declared_names(line) + [assigned_name(line)]:
                if name and name not in signals:
                    signals.append(name)
        hunks.append({
            "module": module,
            "old_lines": [i1 + 1, i2] if i2 > i1 else None,
            "new_lines": [j1 + 1, j2] if j2 > j1 else None,
            # 0-based half-open line spans, kept for Analysis/Attribution.py (it rebuilds designs from them);
            # unlike old_lines / new_lines they also locate a pure insertion or deletion.
            "old_span": [i1, i2], "new_span": [j1, j2],
            "kinds": sorted({line_kind(l) for l in changed}),
            "signals": signals,
            "removed": _excerpt(old_lines[i1:i2]),
            "added": _excerpt(new_lines[j1:j2]),
        })
        removed += sum(1 for l in old_norm[i1:i2] if l)
        added += sum(1 for l in new_norm[j1:j2] if l)

    old_modules, new_modules = [s[0] for s in old_spans], [s[0] for s in new_spans]
    changed_modules = []
    for hunk in hunks:
        if hunk["module"] and hunk["module"] not in changed_modules:
            changed_modules.append(hunk["module"])
    return {
        "added": added, "removed": removed, "hunks": hunks,
        "modules_added": [m for m in new_modules if m not in old_modules],
        "modules_removed": [m for m in old_modules if m not in new_modules],
        "modules_changed": changed_modules,
    }


# ---------------------------------------------------------------------- structural facts

def structure_facts(text):
    """
    Counts that describe how the design is written (not how it was synthesized):
    modules, clocked / combinational always blocks, continuous assigns, distinct non-blocking targets (registers),
    `*` operators, memory declarations (`reg [w] name [depth]`), synthesis attributes and generate blocks.
    """
    code = strip_comments(text)
    spans = module_spans(code.splitlines())
    # `@(*)` and `@*` are sensitivity lists, not multiplications or attributes.
    flat = re.sub(r"@\s*\(\s*\*\s*\)|@\s*\*", "@ALL", code)

    attributes = []
    for match in re.finditer(r"\(\*(.*?)\*\)", flat, flags=re.S):
        module = module_at(spans, flat.count("\n", 0, match.start()) + 1)
        for item in match.group(1).split(","):
            name, _, value = item.partition("=")
            if name.strip():
                attributes.append({"module": module, "name": name.strip(), "value": value.strip().strip('"')})

    no_attr = re.sub(r"\(\*.*?\*\)", " ", flat, flags=re.S)
    memories = []
    for match in re.finditer(r"\breg\b\s*(?:signed\s*)?(?:\[[^\]]+\]\s*)?([A-Za-z_]\w*)\s*(\[[^\]]+\])\s*[;,]", no_attr):
        memories.append({"module": module_at(spans, no_attr.count("\n", 0, match.start()) + 1),
                         "name": match.group(1), "dimension": match.group(2)})

    # A non-blocking assignment starts a statement; `<=` inside a condition (`if (a <= b)`) is a comparison.
    nonblocking = {m.group(1) for m in re.finditer(
        r"(?:^|[;:)]|\bbegin\b|\belse\b)\s*([A-Za-z_]\w*)\s*(?:\[[^\]]*\]\s*)*<=", no_attr, flags=re.M)}
    always_total = len(re.findall(r"\balways\b", no_attr))
    clocked = len(re.findall(r"\balways\s*@\s*\(\s*(?:posedge|negedge)", no_attr))
    return {
        "modules": [s[0] for s in spans],
        "clocked_always": clocked,
        "comb_always": always_total - clocked,
        "continuous_assigns": len(re.findall(r"\bassign\b", no_attr)),
        "nonblocking_targets": len(nonblocking),
        "mult_ops": len(re.findall(r"(?<!\*)\*(?!\*)", no_attr)),
        "generate_blocks": len(re.findall(r"\bgenerate\b", no_attr)),
        "memories": memories,
        "attributes": attributes,
    }


def structure_delta(before, after):
    """Change of the structural facts: numeric differences plus the memories and attributes added / removed."""
    delta = {key: after[key] - before[key] for key in
             ("clocked_always", "comb_always", "continuous_assigns", "nonblocking_targets", "mult_ops", "generate_blocks")}

    def keys(items, fields):
        return {tuple(item[f] for f in fields) for item in items}

    for label, fields in (("memories", ("module", "name", "dimension")), ("attributes", ("module", "name", "value"))):
        old, new = keys(before[label], fields), keys(after[label], fields)
        delta[f"{label}_added"] = sorted("/".join(str(p) for p in k) for k in new - old)
        delta[f"{label}_removed"] = sorted("/".join(str(p) for p in k) for k in old - new)
    return delta


# ---------------------------------------------------------------------- declared intents

def _effect_dict(value):
    """The `expected_effect` of an intent as {"LUT": "down", ...}; anything malformed is dropped."""
    if not isinstance(value, dict):
        return {}
    return {k: str(v).strip().lower() for k, v in value.items()
            if k in EFFECT_METRICS and str(v).strip().lower() in ("up", "down")}


def parse_intents(response):
    """
    The edit intents of an answer: (intents, error). `error` is None when the Transformations section was found
    and held a JSON list; an unknown tag is stored as OTHER and keeps the declared text in `tag_declared`.
    """
    block = extract_between(response, TRANSFORMS_START, TRANSFORMS_END)
    if block is None:
        return [], "the answer has no Transformations section"
    block = re.sub(r"^```\w*\s*|\s*```$", "", block.strip())
    try:
        data = json.loads(block)
    except ValueError as exc:
        return [], f"the Transformations section is not valid JSON: {exc}"
    if isinstance(data, dict):
        data = data.get("transformations", [data])
    if not isinstance(data, list):
        return [], "the Transformations section is not a JSON list"

    taxonomy, intents = load_taxonomy(), []
    for number, item in enumerate(data, 1):
        if not isinstance(item, dict):
            continue
        declared = str(item.get("tag", "")).strip().upper()
        intents.append({
            "id": number, "module": str(item.get("module") or "").strip(),
            "target": str(item.get("target") or "").strip(),
            "tag": declared if declared in taxonomy else "OTHER", "tag_declared": declared,
            "rationale": str(item.get("rationale") or "").strip(),
            "expected_effect": _effect_dict(item.get("expected_effect")),
        })
    return intents, None


# ---------------------------------------------------------------------- evidence from the Vivado facts

def change_of(before, after, tolerance=0.0):
    """'up' | 'down' | 'same' for two numbers; None when either is missing."""
    if before is None or after is None:
        return None
    if after > before + tolerance:
        return "up"
    if after < before - tolerance:
        return "down"
    return "same"


def _tolerance(metric):
    return WNS_TOLERANCE_NS if metric == "wns_ns" else 0.0


def primitive_count(primitives, prefixes):
    """Number of primitives whose name starts with one of the prefixes (RAMB18 -> RAMB18E1, ...)."""
    return sum(int(n) for name, n in (primitives or {}).items() if any(name.startswith(p) for p in prefixes))


def condition_result(condition, ref, new):
    """(met, text) of one evidence condition between the reference and the attempt ({"metrics", "primitives"})."""
    wanted = condition["change"]
    if "metric" in condition:
        key = condition["metric"]
        before, after = (ref["metrics"] or {}).get(key), (new["metrics"] or {}).get(key)
        label, tol = key, _tolerance(key)
    else:
        prefixes = condition["primitive"]
        before, after = primitive_count(ref["primitives"], prefixes), primitive_count(new["primitives"], prefixes)
        label, tol = "/".join(prefixes) + " count", 0.0
    actual = change_of(before, after, tol)
    met = {"up": actual == "up", "down": actual == "down",
           "not_up": actual in ("down", "same"), "not_down": actual in ("up", "same")}.get(wanted, False)
    return met, f"{label} {before} -> {after} (needs {wanted})"


def check_intent(intent, ref, new, changed_modules):
    """
    Cross-check one intent: (status, reason).
      unsupported   the named module was not edited, or no evidence alternative of the label holds
      confirmed     one evidence alternative holds (reason names it and says whether it is direct or indirect)
      unverifiable  the attempt has no metrics, or the label cannot be checked from the reports
    """
    taxonomy = load_taxonomy()[intent["tag"]]
    if intent["module"] and intent["module"] not in changed_modules:
        return "unsupported", f"no edit in module '{intent['module']}'"
    if new["metrics"] is None:
        return "unverifiable", "the attempt produced no metrics (it failed before the reports)"
    if not taxonomy["evidence"]:
        return "unverifiable", f"{intent['tag']} cannot be checked from the reports"
    observed = []
    for alternative in taxonomy["evidence"]:
        results = [condition_result(c, ref, new) for c in alternative]
        observed.append(" and ".join(text for _, text in results))
        if all(met for met, _ in results):
            return "confirmed", f"{taxonomy['strength']} evidence: {observed[-1]}"
    return "unsupported", "; or ".join(observed)


def check_effects(intent, ref, new):
    """{"LUT": "met" | "not_met" | "n/a"}: did each metric the LLM expected to change move that way?"""
    result = {}
    for name, direction in intent["expected_effect"].items():
        key = EFFECT_METRICS[name]
        actual = change_of((ref["metrics"] or {}).get(key), (new["metrics"] or {}).get(key), _tolerance(key))
        # For the area metrics 'down' is the improvement; for Fmax (WNS) 'up' is. The direction is the LLM's own.
        result[name] = "n/a" if actual is None else ("met" if actual == direction else "not_met")
    return result


def cross_check(intents, diff_vs_ref, ref, new):
    """
    Annotate every intent with `status`, `reason` and `effect_check`, and list the undeclared changes: hunks in
    a module that no intent names. Returns (checked intents, undeclared hunk summaries).
    """
    changed = set(diff_vs_ref["modules_changed"]) | set(diff_vs_ref["modules_added"]) | set(diff_vs_ref["modules_removed"])
    checked = []
    for intent in intents:
        status, reason = check_intent(intent, ref, new, changed)
        checked.append({**intent, "status": status, "reason": reason, "effect_check": check_effects(intent, ref, new)})

    named = {i["module"] for i in intents if i["module"]}
    undeclared = [{"module": h["module"], "new_lines": h["new_lines"], "old_lines": h["old_lines"],
                   "kinds": h["kinds"], "signals": h["signals"]}
                  for h in diff_vs_ref["hunks"] if h["module"] not in named]
    return checked, undeclared


# ---------------------------------------------------------------------- one attempt

def metric_deltas(before, after):
    """{metric: after - before} over DELTA_KEYS; None when either side has no metrics, None per missing value."""
    if before is None or after is None:
        return None
    out = {}
    for key in DELTA_KEYS:
        b, a = before.get(key), after.get(key)
        out[key] = None if b is None or a is None else round(a - b, 3)
    return out


def build_edit_log(attempt, base_attempt, reference_code, base_code, new_code, response, ref, base, new):
    """
    Everything recorded about the edits of one attempt.

      attempt, base_attempt   attempt numbers (base_attempt None = the attempt started from the reference)
      reference_code, base_code, new_code   Verilog text (base_code None = same as the reference)
      response                raw LLM answer (for the Transformations section)
      ref, base, new          {"metrics", "primitives"} of the reference, the base and the attempt
                              (base None = the reference; new["metrics"] None when the attempt has no metrics)

    Returns (edit_log, patches): `edit_log` is the JSON-able record, `patches` is {file name: unified diff}.
    """
    if base_code is None or base is None:
        base_code, base = reference_code, ref
    diff_ref = diff_hunks(reference_code, new_code)
    diff_base = diff_ref if base_code is reference_code else diff_hunks(base_code, new_code)
    intents, parse_error = parse_intents(response)
    checked, undeclared = cross_check(intents, diff_ref, ref, new)

    facts_ref, facts_base, facts_new = (structure_facts(t) for t in (reference_code, base_code, new_code))
    log = {
        "attempt": attempt, "base_attempt": base_attempt,
        "vs_reference": diff_ref, "vs_base": diff_base,
        "structure": {"reference": facts_ref, "attempt": facts_new,
                      "delta_vs_reference": structure_delta(facts_ref, facts_new),
                      "delta_vs_base": structure_delta(facts_base, facts_new)},
        "intents": checked, "intent_parse_error": parse_error, "undeclared_changes": undeclared,
        "delta_vs_ref": metric_deltas(ref["metrics"], new["metrics"]),
        "delta_vs_base": metric_deltas(base["metrics"], new["metrics"]),
    }
    patches = {
        "diff_vs_ref.patch": unified_patch(reference_code, new_code, "reference", f"attempt_{attempt}"),
        "diff_vs_base.patch": unified_patch(base_code, new_code,
                                            f"attempt_{base_attempt}" if base_attempt else "reference", f"attempt_{attempt}"),
    }
    return log, patches
