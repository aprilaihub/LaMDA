#!/usr/bin/env python3
"""
Use an LLM to propose (Wn_um, Wp_um) sweep points for a 5 V CMOS inverter,
based on Evaluation/Inverter/user_prompt.txt.

Outputs: next_sweep.csv with columns: Wn_um, Wp_um, Ratio

Behavior:
- Infers an exploration density from prompt specificity (guidance only, not exact)
- If a ratio sweep range is requested (e.g., 2.0 to 3.0), enforces ratio diversity
- Reproducible by default (temperature=0), but can be overridden via env var
"""

import os
import json
import re
from pathlib import Path

import pandas as pd
from openai import OpenAI

# ------------------------------
# BOUNDS / SETTINGS
# ------------------------------
WN_MIN, WN_MAX = 1.0, 20.0     # µm
WP_MIN, WP_MAX = 2.0, 60.0     # µm

# Soft caps for sanity (NOT exact counts)
MIN_POINTS_IF_RATIO_SWEEP = 20
MAX_POINTS_WRITE = 120


def infer_target_points(user_text: str) -> int:
    """
    Guidance only (not enforced):
    - More constraints/objectives -> fewer, more focused points
    - Vague objective -> broader exploration
    """
    t = user_text.lower()
    detailed_signals = [
        "noise margin", "nmh", "nml",
        "pdp", "power-delay product",
        "worst-case", "max(tp", "tphl", "tplh",
        "constraint", "must", "reject", "filter"
    ]
    score = sum(1 for s in detailed_signals if s in t)
    return 16 if score >= 2 else 60


def detect_ratio_range(user_text: str):
    """
    Detect a ratio range like:
      "Wp/Wn between 2.0 and 3.0" / "ratio from 2 to 3"
    """
    t = user_text.lower()

    m = re.search(
        r"(?:wp\s*/\s*wn|wp\/wn|ratio)\s*(?:between|from)\s*([0-9]*\.?[0-9]+)\s*(?:and|to)\s*([0-9]*\.?[0-9]+)",
        t
    )
    if m:
        a = float(m.group(1))
        b = float(m.group(2))
        return (a, b) if a <= b else (b, a)

    m2 = re.search(r"(?:between|from)\s*([0-9]*\.?[0-9]+)\s*(?:and|to)\s*([0-9]*\.?[0-9]+)", t)
    if m2 and ("wp/wn" in t or "ratio" in t):
        a = float(m2.group(1))
        b = float(m2.group(2))
        return (a, b) if a <= b else (b, a)

    return None


def build_ratio_sweep_points(ratio_range=(2.0, 3.0)) -> pd.DataFrame:
    """
    Deterministic ratio sweep builder used if the LLM output collapses to a single ratio
    or yields too few points under a ratio sweep request.
    """
    lo, hi = ratio_range

    # 5 ratios spanning the range
    ratios = [lo, lo + 0.25*(hi-lo), lo + 0.5*(hi-lo), lo + 0.75*(hi-lo), hi]
    ratios = [round(r, 2) for r in ratios]

    # Enough Wn values to easily exceed MIN_POINTS_IF_RATIO_SWEEP
    wn_vals = [1.0, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0, 6.0, 8.0, 10.0, 12.0, 15.0, 18.0, 20.0]

    synth = []
    for wn in wn_vals:
        for r in ratios:
            wp = wn * r
            if WN_MIN <= wn <= WN_MAX and WP_MIN <= wp <= WP_MAX:
                # Round like main path (0.5um grid)
                wn_r = round(2 * wn) / 2.0
                wp_r = round(2 * wp) / 2.0
                ratio = wp_r / wn_r if wn_r != 0 else None
                synth.append({"Wn_um": wn_r, "Wp_um": wp_r, "Ratio": round(ratio, 2) if ratio else None})

    return pd.DataFrame(synth).drop_duplicates()


def main():
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise SystemExit("OPENAI_API_KEY not set")

    client = OpenAI(api_key=api_key)

    user_prompt_path = Path("Evaluation/Inverter/user_prompt.txt")
    user_text = user_prompt_path.read_text(errors="ignore") if user_prompt_path.exists() else "Design a static CMOS inverter at 5 V."

    n_target = infer_target_points(user_text)
    ratio_range = detect_ratio_range(user_text) or (2.0, 3.0)

    # Reproducible by default; user can override
    temperature = float(os.getenv("LLM_TEMPERATURE", "0.0"))
    model = os.getenv("OPENAI_MODEL_SWEEP", "gpt-4o-mini")

    sys_prompt = f"""
You are an analog design co-pilot for a 5 V CMOS inverter.

Task:
- Propose sizing points (Wn_um, Wp_um) in micrometers.
- Return approximately {n_target} points (exact count is not required).

Hard constraints:
- {WN_MIN} <= Wn_um <= {WN_MAX}
- {WP_MIN} <= Wp_um <= {WP_MAX}

Ratio guidance:
- If the user specifies a Wp/Wn ratio range (e.g., 2.0 to 3.0),
  include multiple DISTINCT ratios spanning that range.
- Do NOT return all points at the same ratio.
- Include at least 5 distinct ratio values across the range.

Output format:
- Return ONLY JSON: {{"points":[{{"Wn_um":...,"Wp_um":...}}, ...]}}
"""

    completion = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": user_text},
        ],
        temperature=temperature,
        top_p=1.0,
    )

    raw = completion.choices[0].message.content.strip()

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise SystemExit(f"Failed to parse JSON response: {e}\nRaw:\n{raw}")

    pts = data.get("points", [])
    if not pts:
        raise SystemExit("LLM returned no points")

    rows = []
    for p in pts:
        try:
            wn = float(p["Wn_um"])
            wp = float(p["Wp_um"])
        except Exception:
            continue

        if not (WN_MIN <= wn <= WN_MAX):
            continue
        if not (WP_MIN <= wp <= WP_MAX):
            continue

        # Round to 0.5 µm grid
        wn_r = round(2 * wn) / 2.0
        wp_r = round(2 * wp) / 2.0
        ratio = wp_r / wn_r if wn_r != 0 else None

        rows.append({"Wn_um": wn_r, "Wp_um": wp_r, "Ratio": round(ratio, 2) if ratio else None})

    if not rows:
        raise SystemExit("No valid points after filtering")

    df = pd.DataFrame(rows).drop_duplicates()

    # Detect if user requested ratio sweep
    t = user_text.lower()
    asked_ratio_sweep = ("sweep" in t and ("wp/wn" in t or "ratio" in t)) or ("prefer_wp_over_wn_ratio" in t)

    # Enforce ratio diversity if ratio sweep requested
    if asked_ratio_sweep:
        unique_ratios = df["Ratio"].dropna().unique() if not df.empty else []
        if len(unique_ratios) < 5 or len(df) < MIN_POINTS_IF_RATIO_SWEEP:
            df = build_ratio_sweep_points(ratio_range)

    # Soft cap to keep files manageable (not a "target")
    if len(df) > MAX_POINTS_WRITE:
        df = df.head(MAX_POINTS_WRITE)

    df.to_csv("next_sweep.csv", index=False)
    print(f"[ok] wrote {len(df)} points to next_sweep.csv (guidance ~{n_target}, temp={temperature}, model={model})")


if __name__ == "__main__":
    main()
