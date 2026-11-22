#!/usr/bin/env python3
"""
Assemble Dataset/fabric_opt_problems.json from the per-design sources in Dataset/src/.

Each design lives in Dataset/src/<NN>_<module>/ :
  design.json   metadata and prompt texts (keys: see REQUIRED_KEYS and OPTIONAL_KEYS below and the README)
  reference.v   the REFERENCE RTL shown to the LLM: a complete multi-module design (top module, memories,
                controller, PEs, ...), correct but not optimised
  tb.v          self-checking testbench of the top module. It instantiates the design under test as 'uut' and
                an untouched copy of the reference as 'refm' (ref_* modules, appended below by this script),
                gives both the same stimulus and compares the outputs cycle by cycle.
  measured.json (optional) written by Dataset/apply_measurements.py from the Vivado baseline run:
                baseline_metrics, measured_on, run_id.

The design ID is the numeric prefix of the folder name.

Usage:  python3 Dataset/build_dataset.py
"""

import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SRC_DIR = os.path.join(HERE, "src")
OUT_FILE = os.path.join(HERE, "fabric_opt_problems.json")

DATASET_VERSION = "7"
DATASET_DATE = "2026-11-17"

# problem text (what the LLM reads) + annotations (known_inefficiency, levers: never shown to the LLM)
REQUIRED_KEYS = ("module", "clock_period_ns", "dataflow", "tags", "Problem", "latency_contract",
                 "known_inefficiency", "levers")

# Optional design.json keys:
#   family    free-text name of the design family (for grouping results)
#   mutants   [{name, old, new}] text replacements of the reference that change its timing or function;
#             used only by check_designs.py (the testbench must fail on every one). Not part of the JSON output.
OPTIONAL_KEYS = ("family", "mutants")

TAXONOMY_FILE = os.path.join(HERE, "..", "Analysis", "transform_taxonomy.json")


def read(path):
    with open(path, "r") as f:
        return f.read()


def validate_meta(folder, meta, reference):
    """Check the annotation keys of a design.json (the ones never shown to the LLM).

    levers   must be non-empty taxonomy tags (Analysis/transform_taxonomy.json): the edits that could help
    mutants  each {name, old, new}; 'old' must occur in the reference and differ from 'new'
    Raises ValueError with the folder name on the first problem."""
    unexpected = [k for k in meta if k not in REQUIRED_KEYS + OPTIONAL_KEYS]
    if unexpected:
        raise ValueError(f"{folder}/design.json: unknown keys {unexpected}")
    tags = json.loads(read(TAXONOMY_FILE))["tags"]
    unknown = [t for t in meta["levers"] if t not in tags]
    if not meta["levers"] or unknown:
        raise ValueError(f"{folder}/design.json: 'levers' must be a non-empty list of taxonomy tags, got {meta['levers']}")
    for mutant in meta.get("mutants", []):
        if set(mutant) != {"name", "old", "new"}:
            raise ValueError(f"{folder}/design.json: a mutant needs exactly name, old, new: {mutant}")
        if mutant["old"] not in reference or mutant["old"] == mutant["new"]:
            raise ValueError(f"{folder}/design.json: mutant {mutant['name']} does not apply to reference.v")


def module_names(rtl):
    """Names of all modules declared in a Verilog source."""
    return re.findall(r"^\s*module\s+(\w+)", rtl, re.MULTILINE)


def module_header(rtl, module):
    """Text from 'module <name>' up to the first ');' (the port list), as given to the LLM."""
    match = re.search(r"\bmodule\s+" + re.escape(module) + r"\b.*?\);", rtl, re.S)
    if not match:
        raise ValueError(f"cannot find the module header of {module}")
    return match.group(0)


def reference_copy(rtl):
    """The reference with every module renamed ref_<name>; used by the testbench as the golden behaviour.

    Module names must be distinctive words (they are renamed everywhere they appear as a whole word)."""
    names = module_names(rtl)
    pattern = re.compile(r"\b(" + "|".join(re.escape(n) for n in names) + r")\b")
    return pattern.sub(lambda m: "ref_" + m.group(1), rtl)


def testbench_text(tb, reference):
    """Testbench followed by the renamed reference copy (one file, as the Vivado flow expects)."""
    return tb.rstrip("\n") + "\n\n// ---- untouched copy of the reference (modules renamed ref_*) ----\n" + reference_copy(reference)


def build_design(folder):
    design_id = int(folder.split("_", 1)[0])
    base = os.path.join(SRC_DIR, folder)
    meta = json.loads(read(os.path.join(base, "design.json")))
    missing = [k for k in REQUIRED_KEYS if k not in meta]
    if missing:
        raise ValueError(f"{folder}/design.json is missing keys: {missing}")

    measured_path = os.path.join(base, "measured.json")
    measured = json.loads(read(measured_path)) if os.path.exists(measured_path) else {}

    reference = read(os.path.join(base, "reference.v"))
    tb = read(os.path.join(base, "tb.v"))
    if meta["module"] not in module_names(reference):
        raise ValueError(f"{folder}/reference.v has no top module {meta['module']}")
    validate_meta(folder, meta, reference)

    period = float(meta["clock_period_ns"])
    return {
        "ID": design_id,
        "module": meta["module"],
        "dataflow": meta["dataflow"],
        "family": meta.get("family", meta["dataflow"]),
        "tags": meta["tags"],
        "levers": meta["levers"],
        "Problem": meta["Problem"],
        "Module header": module_header(reference, meta["module"]),
        "reference_rtl": reference,
        "Testbench": testbench_text(tb, reference),
        "Clock Constraint": f"create_clock -period {period:.3f} -name clk [get_ports clk]\n",
        "latency_contract": meta["latency_contract"],
        "modules": module_names(reference),
        "baseline_metrics": measured.get("baseline_metrics"),
        "measurement": ({"measured_on": measured.get("measured_on"), "run_id": measured.get("run_id")}
                        if measured else None),
        "known_inefficiency": meta["known_inefficiency"],
        "provenance": {
            "date": DATASET_DATE,
            "scope": "problem text, reference RTL, testbench",
            "checks": "python3 Dataset/check_designs.py (xsim: reference passes, mutants fail)",
        },
    }


def main():
    folders = sorted(d for d in os.listdir(SRC_DIR)
                     if os.path.isdir(os.path.join(SRC_DIR, d)) and re.match(r"^\d+_", d))
    designs = [build_design(f) for f in folders]
    ids = [d["ID"] for d in designs]
    if len(set(ids)) != len(ids):
        raise ValueError(f"duplicate design IDs: {ids}")
    data = {
        "dataset_version": DATASET_VERSION,
        "provenance": {
            "date": DATASET_DATE,
            "note": "Systolic arrays (output, weight and input stationary; 4x4 and 8x8) and streaming datapaths "
                    "(FIR filter, 3x3 convolution with line buffer, saturating dot-product engine). "
                    "known_inefficiency and levers are annotations and are never shown to the LLM. "
                    "Baselines are measured with Vivado (Dataset/apply_measurements.py).",
        },
        "designs": designs,
    }
    with open(OUT_FILE, "w") as f:
        json.dump(data, f, indent=2)
        f.write("\n")
    print(f"Wrote {OUT_FILE}: {len(designs)} designs {ids}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
