"""
Per-edit attribution of one attempt of a finished run.

An attempt holds several edits; the metrics only show what all of them did together. This script splits the
attempt's changes into groups, runs the reference with each group alone and the attempt without each group
through the Vivado flow, and tells which group gained or cost what (method: Analysis/Attribution.py,
files and cost: Pipeline/attribution_run.py; see the README, section "Per-edit attribution").

    python3 Analysis/Attribute.py --report Outputs/.../run_<id>/fabric_report.json     # the best attempt
    python3 Analysis/Attribute.py --report <fabric_report.json> --attempt 3 --group hunk
    python3 Analysis/Attribute.py --report <fabric_report.json> --plan_only             # no Vivado: check the groups

Run it from the FPGA-RTL-Transform folder (or use `make attribute`). Uses the part, stage (synthesis or
implementation) and clock period stored in the report, so the numbers are comparable with the report's baseline
and attempt. Needs Vivado (VIVADO_PATH) unless --plan_only.
"""

import argparse
import json
import os
import sys

# This script lives in Analysis/; Pipeline/ and Run.py are one level up.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from Pipeline.flow import DesignFlow
from Pipeline.attribution_run import AttributionRun
from Run import OPT_DATASET_FILE, load_design_entry
from FabricAnalysis import FabricReportParser
import Attribution


def parse_arguments():
    parser = argparse.ArgumentParser(description="Per-edit attribution of one attempt of a finished run")
    parser.add_argument("--report", required=True, help="fabric_report.json of the run")
    parser.add_argument("--attempt", type=int, default=None, help="attempt to attribute (default: the best attempt)")
    parser.add_argument("--group", choices=Attribution.GROUP_MODES, default="signal",
                        help="how hunks are grouped: hunk | signal (hunks sharing a signal) | module")
    parser.add_argument("--max_groups", type=int, default=8,
                        help="budget of groups; a finer grouping is made coarser to fit (2 Vivado flows per group)")
    parser.add_argument("--no_leave_one_out", action="store_true",
                        help="only run 'reference + one group' (half the Vivado flows; no in-context effect)")
    parser.add_argument("--plan_only", action="store_true", help="write the plan and the variants, do not run Vivado")
    parser.add_argument("--rerun", action="store_true", help="redo variants that already have a result.json")
    parser.add_argument("--dataset_file", default=None, help="Override of Dataset/fabric_opt_problems.json")
    parser.add_argument("--verbose", action="store_true")
    return parser.parse_args()


def format_table(table):
    """Short terminal view of the attribution: one line per group."""
    lines = [f"Attempt {table['attempt']} ({table['stage']}), grouping '{table['grouping']}'",
             f"  total delta vs reference : {table['total_delta']}", f"  interaction              : {table['interaction']}"]
    for g in table["groups"]:
        tags = ",".join(t["tag"] for t in g["intent_tags"]) or "-"
        lines.append(f"  {g['id']}  modules={','.join(g['modules']) or '-'}  hunks={g['hunks']}  role={g['role']}  "
                     f"labels={tags}  alone[{g['only']['status']}]={g['only']['metrics_delta']}  "
                     f"in attempt[{g['without']['status']}]={g['without']['metrics_delta']}")
    return "\n".join(lines)


def main():
    args = parse_arguments()
    with open(args.report, "r") as f:
        report = json.load(f)
    entry = load_design_entry(args.dataset_file or OPT_DATASET_FILE, report["design_id"])
    if entry is None:
        print(f"Design ID {report['design_id']} not found in the dataset.")
        return 0
    period = FabricReportParser.parse_clock_period_ns(entry["Clock Constraint"])
    flow = DesignFlow(report["fpga_part"], report["stage"], period, verbose=args.verbose)
    runner = AttributionRun(args.report, entry, flow, mode=args.group, max_groups=args.max_groups,
                            leave_one_out=not args.no_leave_one_out, plan_only=args.plan_only,
                            rerun=args.rerun, verbose=args.verbose)
    try:
        table = runner.run(args.attempt)
    except ValueError as exc:
        print(f"Attribution not possible: {exc}")
        return 0
    if table is None:
        print(f"Plan and variants written to {runner.work_dir(args.attempt or report.get('best_attempt'))}")
    else:
        print(format_table(table))
    return 1


if __name__ == "__main__":
    exit(0 if main() else 1)
