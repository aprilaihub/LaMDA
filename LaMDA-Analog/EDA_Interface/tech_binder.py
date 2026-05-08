#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
tech_binder.py
Binds PDK include lines, remaps model names, and sets numeric params.
Supports BOTH:
  - old schema:
      include_lines: [ 'include "...usage.scs" section=pre_simu', ... ]
      model_map: { 'nch_5':'nch_5', 'pch_5':'pch_5' }
  - your schema:
      includes:
        pre: /path/to/usage.scs::pre_simu
        tt:  /path/to/usage.scs::tt_lib
      models:
        nmos: nch_5
        pmos: pch_5
      numerics:
        vdd: 5
        vbias_default: 2.5
"""

import sys, argparse, re
from pathlib import Path

# ---------------- YAML loader ----------------
def load_yaml(path: Path) -> dict:
    try:
        import yaml  # pip install pyyaml
    except Exception:
        return {}
    if not path.exists():
        return {}
    with open(path, "r") as f:
        return yaml.safe_load(f) or {}

# ---------------- helpers ----------------
def strip_clean(s: str) -> str:
    s = s.replace("\r", "")
    s = re.sub(r"[^\x09\x0a\x20-\x7e]", "", s)   # non-printables
    s = re.sub(r"\\\s*\n", "\n", s)              # remove line continuations
    return s

def ensure_header(t: str) -> str:
    if "simulator lang=spectre" not in t.lower():
        t = "simulator lang=spectre\nglobal 0\n\n" + t
    elif "global 0" not in t.lower():
        t = re.sub(r"(?i)^\s*simulator\s+lang=spectre\s*\n",
                   "simulator lang=spectre\nglobal 0\n", t, count=1)
    return t

def norm_space_lower(x: str) -> str:
    return re.sub(r"\s+", " ", x.strip()).lower()

def has_line(text: str, needle: str) -> bool:
    return norm_space_lower(needle) in norm_space_lower(text)

def inject_after_header(text: str, payload: str) -> str:
    m = re.search(r"(?i)^\s*simulator\s+lang=spectre\s*(?:\n\s*global\s+0\s*)?\n", text)
    pos = m.end() if m else 0
    return text[:pos] + payload + text[pos:]

def parse_include_lines_from_cfg(cfg: dict) -> list[str]:
    """Build spectre 'include \"<path>\" section=<sec>' lines from either schema."""
    # old schema
    if "include_lines" in cfg and isinstance(cfg["include_lines"], list):
        return list(cfg["include_lines"])

    # your schema: includes: {pre: "path::section", tt: "path::section"}
    incs = []
    incd = cfg.get("includes", {})
    for key in ("pre", "tt"):
        val = incd.get(key)
        if not val:
            continue
        if "::" in val:
            path, section = val.split("::", 1)
            incs.append(f'include "{path}" section={section}')
        else:
            # if section omitted
            incs.append(f'include "{val}"')
    return incs

def build_model_map_from_cfg(cfg: dict) -> dict:
    # old schema
    if "model_map" in cfg and isinstance(cfg["model_map"], dict):
        return dict(cfg["model_map"])
    # your schema
    mm = {}
    models = cfg.get("models", {})
    if "nmos" in models:
        mm["nmos"] = models["nmos"]
        mm["NMOS_MODEL"] = models["nmos"]
    if "pmos" in models:
        mm["pmos"] = models["pmos"]
        mm["PMOS_MODEL"] = models["pmos"]
    return mm

def remap_models(text: str, model_map: dict) -> str:
    """Replace MOS model token after pin list if it matches a placeholder key."""
    if not model_map:
        return text
    t = text
    # Matches lines like: M0 (D G S B) MODEL ...
    for old, new in model_map.items():
        t = re.sub(rf"(^\s*M\w+\s*\([^)]*\)\s+){re.escape(old)}\b",
                   rf"\1{new}", t, flags=re.M)
    return t

def ensure_parameters(text: str, cfg: dict) -> str:
    """Ensure 'parameters VDD=... VBIAS=...' exist if numerics provided."""
    nums = cfg.get("numerics", {})
    vdd  = nums.get("vdd")
    vb   = nums.get("vbias_default") or nums.get("vbias")
    if vdd is None and vb is None:
        return text

    # Build/merge a parameters line
    has_params = re.search(r"(?i)^\s*parameters\s", text, re.M)
    if has_params:
        def repl(m):
            line = m.group(0)
            # Update or add tokens
            def upsert(tok, val, s):
                if val is None: return s
                if re.search(rf"(?<!\S){tok}\s*=", s):
                    return re.sub(rf"(?<!\S){tok}\s*=\s*\S+",
                                  f"{tok}={val}", s)
                return s.strip() + f" {tok}={val}"
            line = upsert("VDD", vdd, line)
            line = upsert("VBIAS", vb, line)
            return line
        text = re.sub(r"(?im)^\s*parameters.*$", repl, text, count=1)
    else:
        toks = []
        if vdd is not None: toks.append(f"VDD={vdd}")
        if vb  is not None: toks.append(f"VBIAS={vb}")
        if toks:
            text = re.sub(r"(?i)^\s*simulator\s+lang=spectre.*\n", lambda m: m.group(0), text, count=1)
            # Insert after header
            payload = "parameters " + " ".join(toks) + "\n"
            text = inject_after_header(text, payload)
    return text

def ensure_saves_allpub(text: str) -> str:
    # Remove explicit 'save ...' (to avoid syntax glitches) and keep allpub
    t = re.sub(r"(?im)^\s*save\s+.*$", "", text)
    if "saveoptions options save=allpub" not in t.lower():
        t += "\nsaveOptions options save=allpub\n"
    return t

# ---------------- main ----------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("input",  help="input Spectre deck (sanitized)")
    ap.add_argument("output", help="output Spectre deck (tech-bound)")
    ap.add_argument("--config", default="tech_config.yml")
    args = ap.parse_args()

    src = Path(args.input)
    dst = Path(args.output)
    cfg = load_yaml(Path(args.config))

    txt = src.read_text(errors="ignore")
    txt = strip_clean(txt)
    txt = ensure_header(txt)

    # includes
    inc_lines = parse_include_lines_from_cfg(cfg)
    to_add = [ln for ln in inc_lines if not has_line(txt, ln)]
    if to_add:
        txt = inject_after_header(txt, "\n".join(to_add) + "\n")

    # parameters (VDD/VBIAS)
    txt = ensure_parameters(txt, cfg)

    # model remap (e.g., 'nmos'/'pmos' -> 'nch_5'/'pch_5')
    model_map = build_model_map_from_cfg(cfg)
    txt = remap_models(txt, model_map)

    # keep saveOptions only
    txt = ensure_saves_allpub(txt)

    # tidy extra blank lines
    txt = re.sub(r"\n{3,}", "\n\n", txt).strip() + "\n"

    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(txt)
    print(f"[tech_binder] wrote: {dst}")
    print(f"[tech_binder] includes added: {len(to_add)}  | model_map entries: {len(model_map)}")

if __name__ == "__main__":
    main()

