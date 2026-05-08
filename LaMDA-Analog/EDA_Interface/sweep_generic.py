#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Prompt-driven inverter sweep runner.

Features:
- Delay computed as worst-case max(tpHL, tpLH); optionally uses average if requested by prompt
- Optional PDP computation if requested by prompt
- Optional noise margin extraction + enforcement if requested by prompt
- Writes a stable summary.csv schema for downstream plotting/analysis
"""

import os
import re
import datetime
import subprocess
import sys
from pathlib import Path

import pandas as pd

try:
    from psf_parser import parse_psf_results
except Exception:
    parse_psf_results = None


# ============================================================
# PROMPT FLAGS (derived from latest user_effective*.txt)
# ============================================================

def load_prompt_flags():
    flags = {
        "use_avg_delay": False,
        "require_noise_margin": False,
        "use_pdp": False,
    }

    try:
        latest = sorted(
            Path(os.getenv("GEN_RUNS_DIR", "gen_runs")).glob("*/user_effective*.txt"),
            key=lambda p: p.stat().st_mtime
        )[-1]
        txt = latest.read_text(errors="ignore").lower()
    except Exception:
        return flags

    if "average delay" in txt or "avg delay" in txt:
        flags["use_avg_delay"] = True

    if ("noise margin" in txt) or ("nmh" in txt) or ("nml" in txt):
        flags["require_noise_margin"] = True

    if "pdp" in txt or "power-delay product" in txt:
        flags["use_pdp"] = True

    return flags


PROMPT_FLAGS = load_prompt_flags()


# ============================================================
# TEMPLATE DISCOVERY
# ============================================================

def _candidate_gen_runs_dirs() -> list[Path]:
    """Return candidate gen_runs directories in priority order."""
    env_dir = os.getenv("GEN_RUNS_DIR", "gen_runs")
    script_dir = Path(__file__).resolve().parent
    repo_root = script_dir.parent
    default_inv_gen_runs = repo_root / "Evaluation" / "Inverter" / "output" / "gen_runs"
    repo_root_gen_runs = repo_root / "gen_runs"
    legacy_analog_gen_runs = repo_root / "LLM_Analog_Automation" / "gen_runs"

    candidates = [
        Path(env_dir),
        Path.cwd() / "gen_runs",
        repo_root_gen_runs,
        default_inv_gen_runs,
        legacy_analog_gen_runs,
    ]

    seen = set()
    unique = []
    for p in candidates:
        rp = p.resolve()
        if rp in seen:
            continue
        seen.add(rp)
        unique.append(rp)
    return unique


def _find_latest_in_dirs(pattern: str) -> Path | None:
    found = []
    for base in _candidate_gen_runs_dirs():
        if not base.exists():
            continue
        found.extend(base.glob(pattern))
    if not found:
        return None
    return max(found, key=lambda p: p.stat().st_mtime)


def _resolve_template_netlist() -> Path | None:
    env = os.environ.get("TEMPLATE_NETLIST")
    if not env:
        return None
    p = Path(env).expanduser()
    if not p.is_absolute():
        p = (Path.cwd() / p).resolve()
    return p if p.exists() else None


def _template_search_hint() -> str:
    roots = [str(p) for p in _candidate_gen_runs_dirs()]
    return (
        "Searched patterns: '*/inv_netlist_*.scs', '**/inv_netlist_*.scs' under: "
        + ", ".join(roots)
        + ". You can also set TEMPLATE_NETLIST=/absolute/path/to/inverter_netlist_xxx.scs"
    )

def find_template():
    explicit = _resolve_template_netlist()
    if explicit is not None:
        return explicit.resolve()

    latest = _find_latest_in_dirs("*/inv_netlist_*.scs")
    if latest is None:
        latest = _find_latest_in_dirs("**/inv_netlist_*.scs")
    return latest.resolve() if latest else None


TEMPLATE_NETLIST = find_template()
VDD = 5.0


# ============================================================
# SWEEP CONFIG
# ============================================================

WN_MIN, WN_MAX = 1.0, 30.0
WP_MIN, WP_MAX = 2.0, 90.0

DEFAULT_L = "0.6u"
DEFAULT_VB = 2.5


def load_llm_sweep_points():
    path = Path("next_sweep.csv")
    if not path.exists():
        return None

    try:
        df = pd.read_csv(path)
    except Exception:
        return None

    if "Wn_um" not in df.columns or "Wp_um" not in df.columns:
        return None

    pts = []
    seen = set()

    for _, r in df.iterrows():
        try:
            wn = float(r["Wn_um"])
            wp = float(r["Wp_um"])
        except Exception:
            continue

        if not (WN_MIN <= wn <= WN_MAX):
            continue
        if not (WP_MIN <= wp <= WP_MAX):
            continue

        key = (wn, wp)
        if key in seen:
            continue
        seen.add(key)

        pts.append((wn, wp, DEFAULT_L, DEFAULT_VB))

    return pts if pts else None


def build_fallback():
    """
    Deterministic ratio sweep fallback:
    - Ensures Wp/Wn spans a range instead of staying constant
    """
    ratios = [2.0, 2.25, 2.5, 2.75, 3.0]
    wn_vals = [1, 2, 3, 4, 5, 6, 8, 10]
    pts = []
    for wn in wn_vals:
        for r in ratios:
            wp = wn * r
            if WP_MIN <= wp <= WP_MAX:
                pts.append((float(wn), float(wp), DEFAULT_L, DEFAULT_VB))
    return pts


# ============================================================
# TAG FORMATTER (avoids collisions for non-integer widths)
# ============================================================

def fmt_u(x: float) -> str:
    """Format a float for filenames/tags: 2.25 -> '2p25'."""
    s = f"{x:.3f}".rstrip("0").rstrip(".")
    return s.replace(".", "p")


# ============================================================
# PARAM PATCH
# ============================================================

def patch_parameters(txt, wn, wp, lval, vb):
    new = (
        f"parameters WN={wn}u WP={wp}u LVAL={lval} "
        f"VDD={VDD} VIN_DC=0 VBIAS={vb}"
    )

    if re.search(r"(?m)^\s*parameters\b", txt):
        txt = re.sub(r"(?m)^\s*parameters\b.*$", new, txt)
    else:
        txt = "simulator lang=spectre\nglobal 0\n" + new + "\n" + txt

    return txt


# ============================================================
# RUN ONE POINT
# ============================================================

def run_point(tag, wn, wp, lval, vb, template, run_dir):

    result = {
        "Tag": tag,
        "Status": "simulated",
        "Wn (u)": wn,
        "Wp (u)": wp,
        "Ratio": wp / wn if wn else None,
        "Delay (s)": None,
        "Delay_avg (s)": None,
        "Delay_worst (s)": None,
        "Power (W)": None,
        "PDP (J)": None,
        # Stable schema: keep these columns even if prompt does not request them
        "NMH (V)": None,
        "NML (V)": None,
    }

    run_netlist = run_dir / f"{tag}.scs"
    log_file = run_dir / f"{tag}.log"
    raw_dir = run_dir / f"{tag}_psf"

    txt = template.read_text(errors="ignore")
    txt = patch_parameters(txt, wn, wp, lval, vb)
    run_netlist.write_text(txt)

    cmd = [
        "spectre",
        str(run_netlist),
        "+log", str(log_file),
        "-raw", str(raw_dir),
        "-format", "psfascii",
    ]

    cp = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    if cp.returncode != 0:
        result["Status"] = "failed"
        return result

    if parse_psf_results is None:
        return result

    metrics = parse_psf_results(str(raw_dir))
    if not metrics:
        return result

    tpHL = metrics.get("tpHL (s)")
    tpLH = metrics.get("tpLH (s)")
    power = metrics.get("Power (W)")

    # Delay calculations
    if tpHL is not None and tpLH is not None:
        delay_avg = 0.5 * (tpHL + tpLH)
        delay_worst = max(tpHL, tpLH)

        result["Delay_avg (s)"] = delay_avg
        result["Delay_worst (s)"] = delay_worst

        # Prompt-driven delay definition
        result["Delay (s)"] = delay_avg if PROMPT_FLAGS["use_avg_delay"] else delay_worst

    result["Power (W)"] = power

    # Prompt-gated noise margin extraction
    nmh = None
    nml = None
    if PROMPT_FLAGS["require_noise_margin"]:
        nmh = metrics.get("NMH (V)")
        nml = metrics.get("NML (V)")
        result["NMH (V)"] = nmh
        result["NML (V)"] = nml

    # Optional PDP
    if PROMPT_FLAGS["use_pdp"] and power is not None and result["Delay (s)"] is not None:
        result["PDP (J)"] = power * result["Delay (s)"]

    # Optional noise margin enforcement
    if PROMPT_FLAGS["require_noise_margin"]:
        if nmh is not None and nmh < 1.25:
            result["Status"] = "rejected_nmh"
        if nml is not None and nml < 1.25:
            result["Status"] = "rejected_nml"

    print(f"{tag}: Delay={result['Delay (s)']} Power={power}")
    return result


# ============================================================
# MAIN
# ============================================================

def main():

    if TEMPLATE_NETLIST is None:
        print("No inverter deck found for sweep input.")
        print(_template_search_hint())
        sys.exit(1)

    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    out_root = Path(os.getenv("SWEEP_RESULTS_DIR", "sweep_results"))
    out_root.mkdir(exist_ok=True)
    run_dir = out_root / ts
    run_dir.mkdir(exist_ok=True)

    pts = load_llm_sweep_points()
    if pts:
        print(f"[sweep] Using {len(pts)} points from next_sweep.csv")
    else:
        pts = build_fallback()
        print(f"[sweep] Using fallback ratio sweep ({len(pts)} points)")

    summary = []
    for i, (wn, wp, lval, vb) in enumerate(pts, 1):
        tag = f"Wn{fmt_u(wn)}_Wp{fmt_u(wp)}"
        print(f"[{i}/{len(pts)}] ▶ {tag}")
        res = run_point(tag, wn, wp, lval, vb, TEMPLATE_NETLIST, run_dir)
        summary.append(res)

    df = pd.DataFrame(summary)
    df.to_csv(run_dir / "summary.csv", index=False)

    print(f"✅ Sweep complete → {run_dir}/summary.csv")


if __name__ == "__main__":
    main()
