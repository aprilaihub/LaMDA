"""Common utilities for LaMDA-Analog workflows."""

import re
from pathlib import Path

import yaml


ROOT_DIR = Path(__file__).resolve().parent
EVALUATION_DIR = ROOT_DIR / "Evaluation"


def create_outputs_folder(*directories):
    """Create output directories if they do not exist."""
    for directory in directories:
        Path(directory).mkdir(parents=True, exist_ok=True)


def read_text(path):
    """Read a UTF-8 text file."""
    return Path(path).read_text(encoding="utf-8")


def load_system_prompt(design):
    """Load system prompt by design name."""
    d = design.strip().lower()
    if d == "inverter":
        p = EVALUATION_DIR / "Inverter" / "system_prompt.md"
    elif d == "ota":
        p = EVALUATION_DIR / "OTA" / "system_prompt.md"
    else:
        raise ValueError(f"Unsupported design: {design}")
    return read_text(p)


def load_user_prompt(design):
    """Load base user prompt by design name."""
    d = design.strip().lower()
    if d == "inverter":
        p = EVALUATION_DIR / "Inverter" / "user_prompt.txt"
    elif d == "ota":
        p = EVALUATION_DIR / "OTA" / "user_prompt.txt"
    else:
        raise ValueError(f"Unsupported design: {design}")
    return read_text(p)


def load_constraints(design):
    """Load YAML constraints by design name."""
    d = design.strip().lower()
    if d == "inverter":
        p = EVALUATION_DIR / "Inverter" / "constraints.yml"
    elif d == "ota":
        p = EVALUATION_DIR / "OTA" / "constraints.yml"
    else:
        raise ValueError(f"Unsupported design: {design}")
    return yaml.safe_load(read_text(p)) or {}


CONSTRAINTS_TEMPLATE = """\
# DESIGN CONSTRAINTS (machine-readable bullets)
- design: {design}
- supply_v: {supply_v}
- default_L_text: {L}
- node_names: vdd={vdd}, gnd={gnd}, in={vin}, out={vout}
- analyses: transient.stop={tran_stop}, ac.start={ac_start}, ac.stop={ac_stop}, ac.dec={ac_dec}
- ac_source: pos={ac_pos}, neg={ac_neg}, mag={ac_mag}
- sizing: prefer_wp_over_wn_ratio={ratios}
- bounds: wn_um=[{wn_min},{wn_max}], wp_um=[{wp_min},{wp_max}], vbias_v=[{vb_min},{vb_max}]
- must_include: saveOptions options save=allpub
- goals:
{goals}
"""


def build_effective_prompt(base_user_prompt, constraints):
    """Append machine-readable constraints to the user prompt."""
    hr = constraints.get("hard_rules", {})
    an = hr.get("analyses", {})
    ac = hr.get("ac_source", {})
    nodes = hr.get("enforce_nodes", {})
    sz = constraints.get("sizing", {})
    bnd = constraints.get("bounds", {})
    lg = constraints.get("llm_guidance", {})

    goals = "\n".join([f"  - {g}" for g in lg.get("goals", [])])

    block = CONSTRAINTS_TEMPLATE.format(
        design=constraints.get("design", constraints.get("design_name", "unknown")),
        supply_v=hr.get("supply_v", 5.0),
        L=hr.get("default_L_text", "1u"),
        vdd=nodes.get("vdd", "VDD"),
        gnd=nodes.get("gnd", "0"),
        vin=nodes.get("in", "IN"),
        vout=nodes.get("out", "OUT"),
        tran_stop=an.get("transient", {}).get("stop", "50n"),
        ac_start=an.get("ac", {}).get("start", "1"),
        ac_stop=an.get("ac", {}).get("stop", "100M"),
        ac_dec=an.get("ac", {}).get("dec", 60),
        ac_pos=ac.get("pos", "INP"),
        ac_neg=ac.get("neg", "INN"),
        ac_mag=ac.get("mag", 1.0),
        ratios=sz.get("prefer_wp_over_wn_ratio", [2.0, 3.0]),
        wn_min=bnd.get("wn_um", {}).get("min", 1.0),
        wn_max=bnd.get("wn_um", {}).get("max", 20.0),
        wp_min=bnd.get("wp_um", {}).get("min", 1.0),
        wp_max=bnd.get("wp_um", {}).get("max", 60.0),
        vb_min=bnd.get("vbias_v", {}).get("min", 0.6),
        vb_max=bnd.get("vbias_v", {}).get("max", 2.5),
        goals=goals or "  - produce clean Spectre deck",
    )

    return base_user_prompt.rstrip() + "\n\n" + block + "\n"


def extract_spectre_deck(response_text):
    """Extract a fenced spectre block if present; otherwise return raw response."""
    match = re.search(r"```(?:spectre)?\s*(.*?)```", response_text, flags=re.S | re.I)
    deck = match.group(1).strip() if match else response_text.strip()

    lower = deck.lower()
    if "simulator lang=spectre" not in lower:
        deck = "simulator lang=spectre\nglobal 0\n\n" + deck
    elif "global 0" not in lower:
        deck = deck.replace("simulator lang=spectre", "simulator lang=spectre\nglobal 0", 1)
    return deck
