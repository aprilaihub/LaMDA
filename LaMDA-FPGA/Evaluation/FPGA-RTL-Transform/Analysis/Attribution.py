"""
Per-edit attribution: which part of an attempt's changes caused which change in the metrics?

An attempt usually holds several edits, and the metrics only say what all of them did together. This module splits
the difference between the reference RTL and an attempt into GROUPS of hunks and builds two kinds of variants, to be
run through the same Vivado flow as the attempt (Pipeline/attribution.py does that):

  only_<g>     the reference with ONLY group g applied     -> the effect of g alone
  without_<g>  the attempt with group g REVERTED           -> the effect of g inside the attempt

Both are needed: an edit can look neutral alone and still matter in combination (for example a memory change that only
pays off together with a read-register change). The difference between the whole effect and the sum of the effects
alone is reported as the INTERACTION. A variant that no longer simulates is reported as such (the groups depend on each
other); that is information about the dependency, not an error.

How hunks are grouped (`mode`):
  hunk     every hunk is its own group
  signal   hunks of one module that touch a common signal are one group (the declaration of a new memory and the
           statements that use it travel together) -- the default
  module   all hunks of one module are one group

Everything here is pure (strings and dicts in, strings and dicts out): the variants and the attribution table can be
tested without Vivado. Heuristic limits: the grouping is by signal name and module, not by data flow; a coupling the
names do not show (a signal renamed in one hunk) produces a variant that fails to compile, and says so.
"""

from EditLog import DELTA_KEYS, diff_hunks, metric_deltas
import Verdict

GROUP_MODES = ("hunk", "signal", "module")

# Coarser grouping tried when a mode gives more groups than the budget of Vivado runs allows.
_COARSER = {"hunk": "signal", "signal": "module"}


# ---------------------------------------------------------------------- grouping

def _find(parent, i):
    while parent[i] != i:
        parent[i] = parent[parent[i]]
        i = parent[i]
    return i


def group_hunks(hunks, mode="signal"):
    """
    Group the hunks of diff_hunks(): a list of groups, each {"id": "g1", "hunks": [indexes into `hunks`], "modules",
    "signals", "kinds", "added", "removed"} in order of the first hunk of the group.
    """
    if mode not in GROUP_MODES:
        raise ValueError(f"unknown grouping '{mode}' (expected one of {GROUP_MODES})")
    parent = list(range(len(hunks)))
    first_with = {}   # (module, signal) or module -> first hunk index that had it
    for i, hunk in enumerate(hunks):
        keys = []
        if mode == "module":
            keys = [hunk["module"]]
        elif mode == "signal":
            keys = [(hunk["module"], s) for s in hunk["signals"]]
        for key in keys:
            if key in first_with:
                parent[_find(parent, i)] = _find(parent, first_with[key])
            else:
                first_with[key] = i

    members = {}
    for i in range(len(hunks)):
        members.setdefault(_find(parent, i), []).append(i)
    groups = []
    for number, indexes in enumerate(sorted(members.values(), key=lambda m: m[0]), 1):
        chosen = [hunks[i] for i in indexes]
        groups.append({
            "id": f"g{number}", "hunks": indexes,
            "modules": sorted({h["module"] for h in chosen if h["module"]}),
            "signals": sorted({s for h in chosen for s in h["signals"]}),
            "kinds": sorted({k for h in chosen for k in h["kinds"]}),
            "added": sum(h["added"]["total"] for h in chosen), "removed": sum(h["removed"]["total"] for h in chosen),
        })
    return groups


def fit_groups(hunks, mode, max_groups):
    """
    (groups, mode used): the grouping of `mode`, made coarser (hunk -> signal -> module) while it has more groups than
    `max_groups`. Raises ValueError when even module grouping exceeds the budget, so that no run is silently cut.
    """
    while True:
        groups = group_hunks(hunks, mode)
        if len(groups) <= max_groups:
            return groups, mode
        if mode not in _COARSER:
            raise ValueError(f"{len(groups)} groups with grouping '{mode}' exceed max_groups={max_groups}; "
                             f"raise --max_groups (each group costs two Vivado flows)")
        mode = _COARSER[mode]


# ---------------------------------------------------------------------- variants

def compose(reference, attempt, hunks, taken):
    """
    The design made from the reference by taking the attempt's version of the hunks in `taken` (a set of indexes into
    `hunks`) and the reference's version of every other hunk. All of `hunks` taken gives the attempt (up to comment
    and blank-line changes), none gives the reference.
    """
    ref_lines, att_lines = reference.splitlines(), attempt.splitlines()
    out, position = [], 0
    for index in sorted(range(len(hunks)), key=lambda i: hunks[i]["old_span"][0]):
        hunk = hunks[index]
        (i1, i2), (j1, j2) = hunk["old_span"], hunk["new_span"]
        out += ref_lines[position:i1]
        out += att_lines[j1:j2] if index in taken else ref_lines[i1:i2]
        position = i2
    out += ref_lines[position:]
    return "\n".join(out) + "\n"


def plan_variants(reference, attempt, mode="signal", max_groups=8, leave_one_out=True):
    """
    {"mode", "groups", "variants"}. `variants` is [{"name", "kind": "only" | "without", "group", "code"}]. With fewer
    than two groups there is nothing to separate and no variant is planned.
    """
    hunks = diff_hunks(reference, attempt)["hunks"]
    groups, used = fit_groups(hunks, mode, max_groups)
    variants = []
    if len(groups) >= 2:
        everything = set(range(len(hunks)))
        for group in groups:
            members = set(group["hunks"])
            variants.append({"name": f"only_{group['id']}", "kind": "only", "group": group["id"],
                             "code": compose(reference, attempt, hunks, members)})
            if leave_one_out:
                variants.append({"name": f"without_{group['id']}", "kind": "without", "group": group["id"],
                                 "code": compose(reference, attempt, hunks, everything - members)})
    return {"mode": used, "groups": groups, "hunks": hunks, "variants": variants}


# ---------------------------------------------------------------------- the attribution table

def variant_status(flow_result):
    """'ok' when the variant has metrics; else where it failed: sim_failed, tool_failed or not_run."""
    if flow_result is None:
        return "not_run"
    if not flow_result.get("sim_passed"):
        return "sim_failed"
    if not flow_result.get("tool_ok") or not flow_result.get("metrics"):
        return "tool_failed"
    return "ok"


def _role(comparison):
    """gain / cost / mixed / neutral from a per-metric better / same / worse comparison."""
    values = list((comparison or {}).values())
    better, worse = values.count("better"), values.count("worse")
    if better and worse:
        return "mixed"
    return "gain" if better else ("cost" if worse else "neutral")


def intent_tags(group, intents):
    """
    Labels the LLM declared that point at this group: the intents naming one of the group's modules, marked
    'target' when the intent's target is one of the group's signals, else 'module'.
    """
    tags = []
    for intent in intents or []:
        if intent.get("module") in group["modules"]:
            match = "target" if intent.get("target") in group["signals"] else "module"
            tags.append({"tag": intent["tag"], "match": match})
    return tags


def build_attribution(groups, results, reference_metrics, attempt_metrics, intents=None):
    """
    The attribution of one attempt.

      groups              from plan_variants
      results             {variant name: flow result} (a missing name = not run)
      reference_metrics   metrics of the reference, attempt_metrics those of the attempt (same flow and stage)
      intents             the compact intents of the attempt (record of Analysis/EditLog.py), for the labels

    Returns {"total_delta", "sum_of_solo_deltas", "interaction", "groups": [...]}. Per group: the status and the metric
    change of its `only` and `without` variants, and a `role` (gain / cost / mixed / neutral / unknown) judged on the
    `only` variant, else on `without`. `interaction` = total minus the sum of the solo deltas; None unless every group's
    `only` variant has metrics.
    """
    total = metric_deltas(reference_metrics, attempt_metrics)
    rows, solo_sum, solo_complete = [], {k: 0.0 for k in DELTA_KEYS}, True
    for group in groups:
        row = {k: group[k] for k in ("id", "modules", "signals", "kinds", "added", "removed")}
        row["hunks"] = len(group["hunks"])
        row["intent_tags"] = intent_tags(group, intents)
        for kind in ("only", "without"):
            result = results.get(f"{kind}_{group['id']}")
            status = variant_status(result)
            entry = {"status": status, "metrics_delta": None, "comparison": None}
            if status == "ok":
                if kind == "only":
                    entry["metrics_delta"] = metric_deltas(reference_metrics, result["metrics"])
                    entry["comparison"] = Verdict.compare_to_reference(reference_metrics, result["metrics"])
                else:   # the attempt against the attempt without the group = what the group adds
                    entry["metrics_delta"] = metric_deltas(result["metrics"], attempt_metrics)
                    entry["comparison"] = Verdict.compare_to_reference(result["metrics"], attempt_metrics)
            row[kind] = entry
        solo = row["only"]
        if solo["status"] == "ok":
            for key in DELTA_KEYS:
                solo_sum[key] += solo["metrics_delta"][key] or 0.0
        else:
            solo_complete = False
        evidence = solo if solo["status"] == "ok" else row["without"]
        row["role"] = _role(evidence["comparison"]) if evidence["status"] == "ok" else "unknown"
        rows.append(row)

    interaction = None
    if solo_complete and groups and total:
        interaction = {k: None if total[k] is None else round(total[k] - solo_sum[k], 3) for k in DELTA_KEYS}
    return {"total_delta": total, "sum_of_solo_deltas": {k: round(v, 3) for k, v in solo_sum.items()} if groups else None,
            "interaction": interaction, "groups": rows}


def attribution_rows(attribution, context):
    """
    One flat row per group for attribution.jsonl: the group, its labels, its two effects and the context of the
    attempt ({"design", "stage", "attempt", ...}), so the file can be analysed without the report.
    """
    return [{**context, "group": g["id"], "modules": g["modules"], "signals": g["signals"], "kinds": g["kinds"],
             "intent_tags": [t["tag"] for t in g["intent_tags"]], "role": g["role"],
             "only_status": g["only"]["status"], "only_delta": g["only"]["metrics_delta"],
             "without_status": g["without"]["status"], "without_delta": g["without"]["metrics_delta"],
             "interaction": attribution["interaction"]} for g in attribution["groups"]]
