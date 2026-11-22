"""
Feedback levels (--feedback_detail): how much evidence the LLM gets back after an attempt.

The levels are cumulative, so every level adds exactly one kind of evidence to the previous one. That is what makes
the comparison between two levels a measurement of that evidence:

  none                  only the verdict: in which metrics the attempt is better / worse than the baseline.
                        No numbers, no reports (the lower bound: does any detail help at all?)
  metrics               + LUT / FF / DSP / BRAM / Fmax against the baseline, the timing basis, the primitive histogram
  reports               + the digest of the Vivado reports (area per module, memories, DSP mapping, worst path)
  reports+logs          + what the tools did, mined from the logs (Analysis/LogMining.py): opt_design changes,
                          phys_opt replications, the WNS trajectory of place and route, log message groups
  reports+logs+edits    + a record of the edits made so far: for every previous attempt the transformation labels
                          that were declared, whether the reports support them, and what the metrics did

The stage rule is not part of the level: with --stop_stage synth the facts passed in (digest, log facts) already
contain synthesis information only, so no level can show implementation evidence of a run that stopped at synthesis.

Used by Pipeline/prompts.py (first prompt, history) and Pipeline/feedback.py (result note).
"""

FEEDBACK_DETAILS = ("none", "metrics", "reports", "reports+logs", "reports+logs+edits")

# The kinds of evidence a level carries, from the cheapest to the richest.
FEATURES = ("metrics", "digest", "logs", "edits")

# Level -> number of FEATURES it carries (a level carries the first n features).
_FEATURE_COUNT = {"none": 0, "metrics": 1, "reports": 2, "reports+logs": 3, "reports+logs+edits": 4}


def has(feedback_detail, feature):
    """True when `feedback_detail` carries `feature` ("metrics", "digest", "logs" or "edits")."""
    if feature not in FEATURES:
        raise ValueError(f"unknown feedback feature '{feature}' (expected one of {FEATURES})")
    return FEATURES.index(feature) < _FEATURE_COUNT[feedback_detail]
