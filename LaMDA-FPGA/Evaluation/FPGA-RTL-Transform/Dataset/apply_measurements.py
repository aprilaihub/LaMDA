#!/usr/bin/env python3
"""
Fold the Vivado baseline measurements of `make measure_baselines` into the dataset sources.

Reads the summary file written by Run.py (default ../Outputs/fabric_opt_results.json), takes for every design the
LATEST BASELINE_ONLY entry and writes Dataset/src/<NN>_<module>/measured.json:
    baseline_metrics  LUT / FF / DSP / BRAM / WNS / Fmax of the reference RTL
    measured_on       date of the run
    run_id            id of the run in fabric_opt_results.json
build_dataset.py merges that file into the dataset entry. The baseline is shown to the LLM as the
reference result and is the target of the judging rule (see the README, "Judging rule").

Usage:  python3 Dataset/apply_measurements.py [--results ../Outputs/fabric_opt_results.json] [--table]
"""

import argparse
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SRC_DIR = os.path.join(HERE, "src")
GEN_DIR = os.path.dirname(HERE)

METRIC_KEYS = ("LUT_USED", "FF_TOTAL", "DSP48E1", "BRAM", "wns_ns", "fmax_mhz", "timing_basis",
               "min_period_ns", "clock_period_ns", "stage")


def load_latest(results_path):
    """{design_id: entry} with the latest BASELINE_ONLY entry of each design."""
    with open(results_path, "r") as f:
        entries = json.load(f)
    latest = {}
    for entry in entries:
        if entry.get("Optimization") == "BASELINE_ONLY" and entry.get("baseline_metrics"):
            latest[int(entry["ID"])] = entry
    return latest


def run_date(run_id):
    match = re.match(r"(\d{4})(\d{2})(\d{2})_", run_id or "")
    return "-".join(match.groups()) if match else None


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--results", default=os.path.join(GEN_DIR, "Outputs", "fabric_opt_results.json"))
    parser.add_argument("--table", action="store_true", help="also print a markdown table of the measurements")
    args = parser.parse_args()

    latest = load_latest(args.results)
    rows = []
    for folder in sorted(d for d in os.listdir(SRC_DIR) if re.match(r"^\d+_", d)):
        design_id = int(folder.split("_", 1)[0])
        entry = latest.get(design_id)
        if entry is None:
            print(f"[{design_id}] {folder}: no BASELINE_ONLY entry in {args.results}, skipped")
            continue
        metrics = {k: entry["baseline_metrics"].get(k) for k in METRIC_KEYS}
        measured = {"measured_on": run_date(entry.get("run_id")), "run_id": entry.get("run_id"),
                    "baseline_metrics": metrics}
        with open(os.path.join(SRC_DIR, folder, "measured.json"), "w") as f:
            json.dump(measured, f, indent=2)
            f.write("\n")
        rows.append((design_id, folder, metrics))
        print(f"[{design_id}] {folder}: wrote measured.json (run {entry.get('run_id')})")

    if args.table and rows:
        print("\n| ID | Design | LUT | FF | DSP | BRAM | WNS (ns) |\n|---|---|---|---|---|---|---|")
        for design_id, folder, m in rows:
            wns = "n/a" if m["wns_ns"] is None else f"{m['wns_ns']:+.3f}"
            print(f"| {design_id} | {folder} | {m['LUT_USED']} | {m['FF_TOTAL']} | {m['DSP48E1']} | {m['BRAM']} | {wns} |")
    print("Now run: python3 Dataset/build_dataset.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
