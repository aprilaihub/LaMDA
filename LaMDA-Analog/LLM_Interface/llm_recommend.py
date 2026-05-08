#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os, json, re, argparse
from pathlib import Path

import pandas as pd
from openai import OpenAI

# -------------------------------------------------
# DESIGN-SPACE LIMITS (match sweep_generic.py)
# -------------------------------------------------
WN_MIN, WN_MAX = 1.0, 30.0   # µm
WP_MIN, WP_MAX = 2.0, 90.0   # µm
RATIO_MIN, RATIO_MAX = 2.0, 3.0
MAX_POINTS = 30              # how many LLM points we want to sweep


# -------------------------------------------------
# System prompt
# -------------------------------------------------
SYS_PROMPT = f"""You are an analog design co-pilot for a static CMOS inverter design.

You will receive a JSON object with:
- objective: high-level goal (e.g., "min_delay", "min_pdp", "balanced").
- power_cap_w: optional power limit in watts.
- records: an array of inverter sweep results, each with fields like
  wn_um, wp_um, l_m, status,
  tpHL_s, tpLH_s, delay_s, delay_worst_s,
  power_w, energy_j,
  nmh_v, nml_v (optional, may be null).

Design context:
- Static CMOS inverter in Spectre at VDD = 5 V.
- Main trade-off: worst-case delay vs power / energy per toggle.
- DESIGN SPACE is DISCRETE on device width:
  * Allowed Wn_um values: any INTEGER from {int(WN_MIN)} to {int(WN_MAX)} inclusive.
  * Allowed Wp_um values: any INTEGER from {int(WP_MIN)} to {int(WP_MAX)} inclusive.
  * For every proposed point define Ratio = Wp_um / Wn_um.
  * Ratio MUST satisfy {RATIO_MIN:.1f} ≤ Ratio ≤ {RATIO_MAX:.1f}.
- Wn_um and Wp_um MUST be integers, NOT decimals.

HARD CONSTRAINTS (MUST be respected):
- Every proposed point MUST satisfy ALL of:
    - Wn_um is an integer in [{int(WN_MIN)}, {int(WN_MAX)}].
    - Wp_um is an integer in [{int(WP_MIN)}, {int(WP_MAX)}].
    - {RATIO_MIN:.1f} ≤ (Wp_um / Wn_um) ≤ {RATIO_MAX:.1f}.
- Wn_um and Wp_um MUST be printed as plain integers with NO decimal point.
  VALID examples:
    (1, 2, 2.00)
    (5, 12, 2.40)
  INVALID examples (DO NOT OUTPUT):
    (1.5, 3.75, 2.50)
    (1.0, 3.0, 3.0)   <- 1.0 has a decimal point
    (1, 3.5, 3.50)    <- 3.5 is not an integer
- You MUST NOT output any point that violates these constraints.
- DO NOT include “invalid” points in the numbered list.
- All proposed points MUST be distinct: no repeated (Wn_um, Wp_um) pairs.

Your tasks:
1) Briefly summarize the Pareto trade-offs you observe between worst-case delay and power/energy.
   - Focus on how Wn, Wp, and the Wp/Wn ratio impact delay and power.
   - If noise margins (nmh_v, nml_v) are present, mention whether most points appear acceptable.

2) Propose up to {MAX_POINTS} next sweep points as triples:
     (Wn_um, Wp_um, Ratio)
   obeying ALL HARD CONSTRAINTS above.
   - Compute Ratio = Wp_um / Wn_um and print it with at most 2 decimal places.
   - Print Wn_um and Wp_um as integers (no decimal point).
   - Prefer points that are likely to improve the objective and respect any power_cap_w if provided.
   - If power_cap_w is strict, favor lower-power candidates even if delay is slightly worse.

3) For each proposed point, justify in ONE short sentence why it may improve the objective.

4) OUTPUT FORMAT (a parser depends on this EXACT shape):
   - Start with a short paragraph titled exactly: "Pareto Trade-offs Summary:".
   - Then a header line exactly:
       "Proposed Next Sweep Points (Wn_um, Wp_um, Ratio):"
   - Then a numbered list, one point per line, like:
       1. (1, 3, 3.00) - short justification...
       2. (2, 5, 2.50) - short justification...
   - Each numbered line MUST start with: "<index>. (" and then the tuple.
   - For EVERY line, Wn_um and Wp_um must be integers and {RATIO_MIN:.1f} ≤ (Wp_um / Wn_um) ≤ {RATIO_MAX:.1f}.
   - Do NOT add any other numbered lists or bullet lists.
   - Keep everything concise and actionable.
"""


# -------------------------------------------------
# Helpers
# -------------------------------------------------

def latest_llm_json(results_dir: Path) -> Path:
    runs = sorted(
        results_dir.glob("*/summary.llm.json"),
        key=lambda p: p.stat().st_mtime,
    )
    if not runs:
        raise SystemExit(f"No summary.llm.json found under {results_dir}. Run EDA_Interface/report_parser.py first.")
    return runs[-1]


def extract_points_from_text(text: str):
    """
    Parse lines like:
      1. (2, 5, 2.50) - ...
    We only care about Wn, Wp. Third field may be Ratio or something else.
    Apply bounds + ratio filtering + integer rounding, return list of (wn, wp).
    """
    rows = []
    for line in text.splitlines():
        m = re.search(r"\(([^)]+)\)", line)
        if not m:
            continue
        parts = [p.strip() for p in m.group(1).split(",")]
        if len(parts) < 2:
            continue
        try:
            wn = float(parts[0])
            wp = float(parts[1])
        except ValueError:
            continue

        # round to integers (as we want integer widths)
        wn_i = int(round(wn))
        wp_i = int(round(wp))

        # basic bounds
        if not (WN_MIN <= wn_i <= WN_MAX):
            continue
        if not (WP_MIN <= wp_i <= WP_MAX):
            continue
        if wn_i <= 0:
            continue

        ratio = wp_i / wn_i
        if ratio < RATIO_MIN or ratio > RATIO_MAX:
            continue

        rows.append((wn_i, wp_i))

    # deduplicate
    seen = set()
    uniq = []
    for wn_i, wp_i in rows:
        k = (wn_i, wp_i)
        if k in seen:
            continue
        seen.add(k)
        uniq.append((wn_i, wp_i))

    # cap
    return uniq[:MAX_POINTS]


# -------------------------------------------------
# Main
# -------------------------------------------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--objective",
        default="min_pdp",
        choices=["min_delay", "min_pdp", "balanced"],
        help="High-level optimisation goal for the LLM.",
    )
    ap.add_argument(
        "--power_cap",
        type=float,
        default=None,
        help="Optional power limit in watts (e.g., 4e-4).",
    )
    args = ap.parse_args()

    results_dir = Path(os.getenv("SWEEP_RESULTS_DIR", "sweep_results"))
    next_sweep_csv = Path(os.getenv("NEXT_SWEEP_CSV", "next_sweep.csv"))

    jpath = latest_llm_json(results_dir)
    data = json.loads(jpath.read_text())

    user_msg = {
        "objective": args.objective,
        "power_cap_w": args.power_cap,
        "records": data.get("records", []),
        "suggestions_seen": data.get("suggestions", {}),
    }

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise SystemExit("OPENAI_API_KEY not set")

    client = OpenAI(api_key=api_key)

    completion = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": SYS_PROMPT},
            {"role": "user", "content": json.dumps(user_msg)},
        ],
        temperature=0.2,
    )

    text = completion.choices[0].message.content

    # 1) Save human-readable recommendation text
    rec_txt_path = jpath.with_suffix(".recommendation.txt")
    rec_txt_path.write_text(text)
    print(f"Recommendation written: {rec_txt_path}")

    # 2) Parse points and write next_sweep.csv
    points = extract_points_from_text(text)
    if not points:
        print(f"⚠️ No valid sweep points parsed from LLM output; {next_sweep_csv} will NOT be updated.")
    else:
        df = pd.DataFrame(points, columns=["Wn_um", "Wp_um"])
        next_sweep_csv.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(next_sweep_csv, index=False)
        print(f"[ok] wrote {len(df)} points to {next_sweep_csv}")

    print("\n--- Preview ---\n")
    print(text)


if __name__ == "__main__":
    main()

