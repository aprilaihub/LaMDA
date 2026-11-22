#!/usr/bin/env python3
"""
Check every design of Dataset/src/ with Vivado xsim (no synthesis):

  reference  the reference RTL must pass its own testbench ("All tests passed"; this also means the
             reference matches the software matrix product computed in the testbench)
  mutants    the testbench must FAIL on the mutants of the reference: the 'mutants' list of design.json when it
             exists (timing and function changes specific to the design), else two timing mutants of the
             systolic arrays ('done' one cycle late / early), i.e. the cycle-exact comparison really detects a
             changed latency or a changed result

Usage:
  python3 Dataset/check_designs.py            # all designs
  python3 Dataset/check_designs.py 1 3        # selected design IDs

Needs VIVADO_PATH (path of the vivado binary, as for Run.py) or Vivado 2024.1 in /opt/Xilinx.
This script reads Dataset/src/ directly (not the JSON).
"""

import json
import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from build_dataset import SRC_DIR, testbench_text  # noqa: E402

# Default mutants {name: (text to find in the reference, replacement)} for designs without a 'mutants' list in
# design.json: every systolic array raises 'done' at 'cnt == K + 7'.
DEFAULT_MUTANTS = {
    "done_late": ("K + 7", "K + 8"),
    "done_early": ("K + 7", "K + 6"),
}


def mutants_of(meta):
    """{name: (old, new)} of a design: its own 'mutants' list, or DEFAULT_MUTANTS."""
    if "mutants" in meta:
        return {m["name"]: (m["old"], m["new"]) for m in meta["mutants"]}
    return DEFAULT_MUTANTS


def vivado_bin_dir():
    path = os.environ.get("VIVADO_PATH")
    if path and os.path.exists(path):
        return os.path.dirname(os.path.abspath(path))
    return "/opt/Xilinx/Vivado/2024.1/bin"


def run_xsim(module, rtl, tb):
    """Compile and run one design + testbench in a temporary folder; returns the log."""
    bin_dir = vivado_bin_dir()
    settings = os.path.join(os.path.dirname(bin_dir), "settings64.sh")
    with tempfile.TemporaryDirectory() as workdir:
        with open(os.path.join(workdir, f"{module}.v"), "w") as f:
            f.write(rtl)
        with open(os.path.join(workdir, f"{module}_tb.v"), "w") as f:
            f.write(tb)
        script = (
            f"source '{settings}' >/dev/null 2>&1; cd '{workdir}' && "
            f"xvlog {module}.v {module}_tb.v && xelab {module}_tb -s snap -debug off --timescale 1ns/1ps "
            f"&& xsim snap -R"
        )
        proc = subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=900)
    return proc.stdout + proc.stderr


def main(argv):
    wanted = {int(a) for a in argv if a.isdigit()}
    folders = sorted(d for d in os.listdir(SRC_DIR) if re.match(r"^\d+_", d))
    failures = 0
    for folder in folders:
        design_id = int(folder.split("_", 1)[0])
        if wanted and design_id not in wanted:
            continue
        base = os.path.join(SRC_DIR, folder)
        meta = json.load(open(os.path.join(base, "design.json")))
        module = meta["module"]
        reference = open(os.path.join(base, "reference.v")).read()
        tb = testbench_text(open(os.path.join(base, "tb.v")).read(), reference)

        log = run_xsim(module, reference, tb)
        ok_ref = "All tests passed" in log
        done_cycles = re.findall(r"run \d+: done raised (\d+) cycles", log)
        line = f"[{design_id}] {module:12s} reference: {'ok' if ok_ref else 'FAIL'}"
        if done_cycles:                      # only the start/done designs print their latency
            line += f" (done after {done_cycles} cycles)"
        if not ok_ref:
            failures += 1
            print(line)
            print("\n".join(log.splitlines()[-25:]))
            continue
        for name, (old, new) in mutants_of(meta).items():
            if old not in reference:
                print(f"{line} | mutant {name}: pattern '{old}' not found")
                failures += 1
                continue
            mutated = run_xsim(module, reference.replace(old, new, 1), tb)
            caught = "All tests passed" not in mutated and "FAIL" in mutated
            line += f" | {name}: {'caught' if caught else 'NOT CAUGHT'}"
            failures += 0 if caught else 1
        print(line)
    print("ALL CHECKS PASSED" if failures == 0 else f"{failures} CHECK(S) FAILED")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
