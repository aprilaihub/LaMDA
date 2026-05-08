#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import sys, yaml
from pathlib import Path

"""
build_prompt.py <system.md> <base_user.txt> <constraints.yml> <out_user.txt> <chat_log.jsonl>

- Reads base user prompt and constraints
- Appends a 'DESIGN CONSTRAINTS' section to the user prompt
- Logs both prompts to chat_log.jsonl (system/user/effective_user)
"""

TEMPLATE = """\
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

def main():
    if len(sys.argv) != 6:
        print("usage: build_prompt.py <system.md> <base_user.txt> <constraints.yml> <out_user.txt> <chat_log.jsonl>")
        sys.exit(2)

    system_md = Path(sys.argv[1]).read_text()
    base_user = Path(sys.argv[2]).read_text()
    c = yaml.safe_load(Path(sys.argv[3]).read_text())

    hr = c.get("hard_rules", {})
    an = hr.get("analyses", {})
    ac = hr.get("ac_source", {})
    nodes = hr.get("enforce_nodes", {})
    sz = c.get("sizing", {})
    b  = c.get("bounds", {})
    lg = c.get("llm_guidance", {})

    goals = "\n".join([f"  - {g}" for g in lg.get("goals", [])])

    constraints_block = TEMPLATE.format(
        design=c.get("design","unknown"),
        supply_v=hr.get("supply_v", 5.0),
        L=hr.get("default_L_text", "0.6u"),
        vdd=nodes.get("vdd","VDD"),
        gnd=nodes.get("gnd","GND"),
        vin=nodes.get("in","IN"),
        vout=nodes.get("out","OUT"),
        tran_stop=an.get("transient", {}).get("stop","50n"),
        ac_start=an.get("ac", {}).get("start","1"),
        ac_stop=an.get("ac", {}).get("stop","10G"),
        ac_dec=an.get("ac", {}).get("dec",60),
        ac_pos=ac.get("pos","NIN"),
        ac_neg=ac.get("neg","VB"),
        ac_mag=ac.get("mag",1.0),
        ratios=sz.get("prefer_wp_over_wn_ratio",[2.0,3.0]),
        wn_min=b.get("wn_um",{}).get("min",1.0),
        wn_max=b.get("wn_um",{}).get("max",10.0),
        wp_min=b.get("wp_um",{}).get("min",1.0),
        wp_max=b.get("wp_um",{}).get("max",20.0),
        vb_min=b.get("vbias_v",{}).get("min",1.5),
        vb_max=b.get("vbias_v",{}).get("max",3.5),
        goals=goals or "  - produce clean Spectre deck"
    )

    effective_user = base_user.rstrip() + "\n\n" + constraints_block + "\n"
    Path(sys.argv[4]).write_text(effective_user)

    # minimal chat log
    import json
    log_path = Path(sys.argv[5])
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a") as f:
        f.write(json.dumps({"role":"system","content":system_md})+"\n")
        f.write(json.dumps({"role":"user","content":base_user})+"\n")
        f.write(json.dumps({"role":"user_effective","content":effective_user})+"\n")

    print(f"Wrote effective user prompt: {sys.argv[4]}")
    print(f"Appended to chat log:        {sys.argv[5]}")

if __name__ == "__main__":
    main()

