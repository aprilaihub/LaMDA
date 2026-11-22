"""
Verdict of an attempt (pure Python, no Vivado / LLM dependency).

- compare_to_reference: per-metric better / same / worse of an attempt against a set of metrics (the measured
  reference RTL, or the best attempt so far): LUT, FF, DSP, BRAM (lower is better) and Fmax (higher is better).
- classify_outcome: outcome class of one attempt (functional_fail, tool_fail, improved, mixed, regression, no_gain).
- parse_reasoning / extract_between: the free-text reasoning of the LLM (between the Reasoning markers).
- sha256_text: hashes for prompt provenance.

Fmax is judged on the worst register-to-register slack (WNS) at the same clock period, and only when both sides
have one.
"""

import hashlib
import math

REASONING_START = "// Start Reasoning"
REASONING_END = "// End Reasoning"

# Metric keys that are "lower is better" (area) in baseline/attempt metrics.
AREA_KEYS = ("LUT_USED", "FF_TOTAL", "DSP48E1", "BRAM")

# Tolerance (ns) below which a WNS change is not considered a gain/regression
# (place & route run-to-run noise).
WNS_TOLERANCE_NS = 0.05


def sha256_text(text):
    """Return the hex SHA-256 of a string (used for prompt provenance)."""
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def _finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def extract_between(text, start_marker, end_marker):
    """Return the text between two markers (stripped), or None if either is missing."""
    if not text:
        return None
    start = text.find(start_marker)
    if start < 0:
        return None
    start += len(start_marker)
    end = text.find(end_marker, start)
    if end < 0:
        return None
    return text[start:end].strip()


def parse_reasoning(response_text):
    """Full LLM reasoning (text between the Reasoning markers), or None if the block is absent."""
    return extract_between(response_text, REASONING_START, REASONING_END)


def fmax_comparable(base_metrics, new_metrics):
    """
    True when Fmax of the two metric sets can be compared: both have a register-to-register WNS. Without a WNS
    (ports are unconstrained, so a design whose registers all sit inside DSP48E1/BRAM has no timed path) Fmax
    falls back to the primitive minimum period (a clock-buffer / DSP / RAMB pulse-width bound), which says
    nothing about the logic.
    """
    base_metrics, new_metrics = base_metrics or {}, new_metrics or {}
    return _finite(base_metrics.get("wns_ns")) and _finite(new_metrics.get("wns_ns"))


def compare_to_reference(base_metrics, new_metrics):
    """
    Compare an attempt with the reference: {metric: "better" | "same" | "worse" | None} for LUT_USED, FF_TOTAL,
    DSP48E1, BRAM (lower is better) and Fmax (higher is better; None when not comparable, see fmax_comparable).
    Fmax is judged on the WNS at the same clock period (higher WNS = higher Fmax), with WNS_TOLERANCE_NS as noise margin.
    """
    base_metrics, new_metrics = base_metrics or {}, new_metrics or {}
    result = {}
    for key in AREA_KEYS:
        b, n = base_metrics.get(key), new_metrics.get(key)
        result[key] = None if not (_finite(b) and _finite(n)) else ("better" if n < b else "worse" if n > b else "same")
    if fmax_comparable(base_metrics, new_metrics):
        d = new_metrics["wns_ns"] - base_metrics["wns_ns"]
        result["Fmax"] = "better" if d > WNS_TOLERANCE_NS else "worse" if d < -WNS_TOLERANCE_NS else "same"
    else:
        result["Fmax"] = None
    return result


def classify_outcome(sim_passed, tool_ok, comparison):
    """
    Outcome of one attempt relative to the reference:
    functional_fail | tool_fail | improved | mixed | regression | no_gain.
    `comparison` is the result of compare_to_reference. "improved" and "mixed" (something better, something
    worse) both count as a pass; the loop only requires at least one better metric.
    """
    if not sim_passed:
        return "functional_fail"
    if not tool_ok:
        return "tool_fail"
    values = list((comparison or {}).values())
    better, worse = "better" in values, "worse" in values
    if better and worse:
        return "mixed"
    if better:
        return "improved"
    if worse:
        return "regression"
    return "no_gain"
