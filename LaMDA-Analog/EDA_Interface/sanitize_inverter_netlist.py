#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
sanitize_inverter_netlist.py

Usage:
  python3 sanitize_inverter_netlist.py IN.scs OUT.scs

For the inverter deck:
- Ensure VIN_DC is declared on the parameters line (VIN_DC=0 if missing).
- Strip any existing DC / TRAN / finalTimeOP / saveOptions / save V1:p lines.
- Append a clean, standard measurement block:

    dc dc param=VIN_DC start=0 stop=VDD step=0.01
    finalTimeOP info what=oppoint where=rawfile
    tran tran stop=50n
    saveOptions options save=allpub
    save V1:p IN OUT
"""

import sys
import re
from pathlib import Path


def main():
    if len(sys.argv) != 3:
        print("Usage: sanitize_inverter_netlist.py IN.scs OUT.scs")
        sys.exit(1)

    in_path = Path(sys.argv[1])
    out_path = Path(sys.argv[2])

    txt = in_path.read_text()

    cleaned_lines = []
    for line in txt.splitlines():
        # ---- Ensure VIN_DC is present on the parameters line ----
        if re.match(r"\s*parameters\b", line):
            if "VIN_DC" not in line:
                line = line.rstrip() + " VIN_DC=0"
            cleaned_lines.append(line)
            continue

        # ---- Remove existing measurement / save blocks we will override ----
        # Remove any existing DC analysis line
        if re.match(r"\s*dc\s+dc\b", line):
            continue
        # Remove any existing TRAN analysis line
        if re.match(r"\s*tran\s+tran\b", line):
            continue
        # Remove any existing finalTimeOP line
        if "finalTimeOP info what=oppoint" in line:
            continue
        # Remove generic saveOptions line (we will re-add)
        if re.match(r"\s*saveOptions\s+options\s+save=", line, re.IGNORECASE):
            continue
        # Remove our previous save V1:p ... line(s) if any
        if re.match(r"\s*save\s+V1:p\b", line, re.IGNORECASE):
            continue

        cleaned_lines.append(line)

    base_txt = "\n".join(cleaned_lines).rstrip() + "\n\n"

    # Standard analysis + save block we want for the inverter sweep
    measure_block = """\
dc dc param=VIN_DC start=0 stop=VDD step=0.01
finalTimeOP info what=oppoint where=rawfile
tran tran stop=50n
saveOptions options save=allpub
save V1:p IN OUT
"""

    out_path.write_text(base_txt + measure_block)
    print(f"[sanitize_inverter_netlist] wrote {out_path}")


if __name__ == "__main__":
    main()

