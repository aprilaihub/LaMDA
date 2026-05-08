#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
psf_ac_parser.py

Usage:
    python3 psf_ac_parser.py <input_ac_ascii> <output_csv>

Parses a generic Spectre AC ASCII file and writes:
    freq_Hz, gain_dB, phase_deg
"""

import sys
import csv
import re
import math
from pathlib import Path


FLOAT_RE = re.compile(
    r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?"
)

def parse_numeric_line(line: str):
    """
    Try to extract (freq_Hz, gain_dB, phase_deg) from one line.
    We don't assume any particular Spectre column format;
    we just look for floats and infer.

    Strategy:
      - If 5+ numbers: assume freq, real, imag, mag, phase
      - Else if 3+ numbers: assume freq, something, phase
    """
    nums = FLOAT_RE.findall(line)
    if len(nums) < 3:
        return None

    vals = [float(x) for x in nums]

    # Case 1: freq, real, imag, mag, phase
    if len(vals) >= 5:
        f_hz, real, imag, mag, phase_deg = vals[:5]
        # If magnitude <= 0, can't convert to dB
        if mag <= 0:
            return None
        gain_db = 20.0 * math.log10(mag)
        return f_hz, gain_db, phase_deg

    # Case 2: generic 3-number line: freq, col2, col3
    f_hz, col2, col3 = vals[:3]

    # Heuristic:
    # If col2 is in "reasonable" dB range, treat it as dB directly.
    # Otherwise treat col2 as linear magnitude and convert.
    if -300.0 < col2 < 300.0:
        # Most AC dB values are in this range
        gain_db = col2
    else:
        mag = abs(col2)
        if mag <= 0:
            return None
        gain_db = 20.0 * math.log10(mag)

    phase_deg = col3
    return f_hz, gain_db, phase_deg


def main():
    if len(sys.argv) != 3:
        print("Usage: python3 psf_ac_parser.py <input_ac_ascii> <output_csv>")
        sys.exit(1)

    in_path = Path(sys.argv[1])
    out_path = Path(sys.argv[2])

    if not in_path.exists():
        print(f"❌ Input AC file not found: {in_path}")
        sys.exit(1)

    rows = []

    with in_path.open("r") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            # Skip comment-like lines
            if line[0] in ("#", "!", "*", "/"):
                continue

            parsed = parse_numeric_line(line)
            if parsed is None:
                continue

            rows.append(parsed)

    # Write CSV
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["freq_Hz", "gain_dB", "phase_deg"])
        for f_hz, g_db, ph_deg in rows:
            w.writerow([f_hz, g_db, ph_deg])

    print(f"✅ psf_ac_parser: wrote {len(rows)} rows to {out_path}")
    if len(rows) == 0:
        # Non-zero exit so caller knows it's empty
        sys.exit(1)


if __name__ == "__main__":
    main()

