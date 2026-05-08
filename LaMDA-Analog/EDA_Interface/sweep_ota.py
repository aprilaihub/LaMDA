#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sweep_ota.py

OTA-specific sweep script (LLM netlist only).

- Uses latest ota_netlist_*.scs from gen_runs/*.
- Sweeps tail bias VBIAS over 15 points between 0.6 V and 2.5 V.
- Runs Spectre AC (-format psfascii).
- Uses psf_parser_ota.parse_psf_results() for scalar metrics, but also
  uses Bode curve (ac.ac) to robustly infer UGB/PM behavior:
    - If 0 dB crossing exists -> compute UGB and PM at crossing
    - If NO 0 dB crossing and gain at stop > 0 dB -> interpret as UGB > f_stop
      (do NOT reject UGB constraints like >= 1 MHz)

Prompt sensitivity:
- If user prompt contains explicit numeric requirements (>=, at least, etc.)
  for 2+ metrics (gain/pm/ugb), OR strong words + 1 numeric requirement,
  then hard_enforce=True.
- Targets (gain_db, pm_deg, ugb_hz) are parsed from the user_effective_ota.txt
  if possible; otherwise defaults are used (40 dB, 60 deg, 1e6 Hz).
"""

import csv
import datetime
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

from psf_parser_ota import parse_psf_results


# ---------------- prompt flags + target parsing ----------------

def _candidate_gen_runs_dirs() -> list[Path]:
    """Return candidate gen_runs directories in priority order.

    Supports execution from arbitrary CWD by checking env override, current
    directory, and the repository-relative OTA output location.
    """
    env_dir = os.getenv("GEN_RUNS_DIR", "gen_runs")
    script_dir = Path(__file__).resolve().parent
    repo_root = script_dir.parent
    default_ota_gen_runs = repo_root / "Evaluation" / "OTA" / "output" / "gen_runs"
    repo_root_gen_runs = repo_root / "gen_runs"
    legacy_analog_gen_runs = repo_root / "LLM_Analog_Automation" / "gen_runs"

    candidates = [
        Path(env_dir),
        Path.cwd() / "gen_runs",
        repo_root_gen_runs,
        default_ota_gen_runs,
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
    """Resolve explicit template override when provided."""
    env_path = os.getenv("TEMPLATE_NETLIST")
    if not env_path:
        return None
    p = Path(env_path).expanduser()
    if not p.is_absolute():
        p = (Path.cwd() / p).resolve()
    return p if p.exists() else None


def _find_latest_effective_prompt() -> Path | None:
    return _find_latest_in_dirs("*/user_effective_ota*.txt")


def _parse_ugb_to_hz(num: float, unit: str | None) -> float:
    if not unit:
        return float(num)  # assume Hz
    u = unit.lower()
    if u == "hz":
        return float(num)
    if u == "khz":
        return float(num) * 1e3
    if u == "mhz":
        return float(num) * 1e6
    if u == "ghz":
        return float(num) * 1e9
    return float(num)


def load_prompt_flags_and_targets():
    """
    Returns:
      flags = dict(hard_enforce, require_gain, require_pm, require_ugb, use_power)
      targets = dict(gain_db_min, pm_deg_min, ugb_hz_min)
    """
    flags = {
        "hard_enforce": False,
        "require_gain": True,   # gain is almost always relevant
        "require_pm": False,
        "require_ugb": False,
        "use_power": True,
    }
    targets = {
        "gain_db_min": 40.0,
        "pm_deg_min": 60.0,
        "ugb_hz_min": 1e6,
    }

    p = _find_latest_effective_prompt()
    if p is None:
        return flags, targets

    txt = p.read_text(errors="ignore").lower()

    # Mention-based requirements (vague prompts still "mention" these sometimes)
    flags["require_pm"] = ("phase margin" in txt) or (re.search(r"\bpm\b", txt) is not None)
    flags["require_ugb"] = ("ugb" in txt) or ("unity-gain bandwidth" in txt) or ("unity gain bandwidth" in txt)

    # Numeric requirement detection (this drives hard_enforce)
    def has_numeric_req(pattern: str) -> bool:
        return re.search(pattern, txt) is not None

    has_gain_req = has_numeric_req(r"gain\s*(?:>=|>|≥|at\s+least)\s*[0-9]*\.?[0-9]+\s*db")
    has_pm_req   = has_numeric_req(r"(?:phase margin|\bpm\b)\s*(?:>=|>|≥|at\s+least)\s*[0-9]*\.?[0-9]+")
    has_ugb_req  = has_numeric_req(r"(?:ugb|unity[- ]gain bandwidth)\s*(?:>=|>|≥|at\s+least)\s*[0-9]*\.?[0-9]+\s*(?:hz|khz|mhz|ghz)?")

    strong_words = any(w in txt for w in ["require", "requirements", "must", "ensure", "non-negotiable", "hard"])

    n_req = sum([has_gain_req, has_pm_req, has_ugb_req])
    flags["hard_enforce"] = (n_req >= 2) or (strong_words and n_req >= 1)

    # Parse numeric targets if present
    m = re.search(r"gain\s*(?:>=|>|≥|at\s+least)\s*([0-9]*\.?[0-9]+)\s*db", txt)
    if m:
        targets["gain_db_min"] = float(m.group(1))

    m = re.search(r"(?:phase margin|\bpm\b)\s*(?:>=|>|≥|at\s+least)\s*([0-9]*\.?[0-9]+)", txt)
    if m:
        targets["pm_deg_min"] = float(m.group(1))

    m = re.search(r"(?:ugb|unity[- ]gain bandwidth)\s*(?:>=|>|≥|at\s+least)\s*([0-9]*\.?[0-9]+)\s*(hz|khz|mhz|ghz)?", txt)
    if m:
        num = float(m.group(1))
        unit = m.group(2)
        targets["ugb_hz_min"] = _parse_ugb_to_hz(num, unit)

    return flags, targets


PROMPT_FLAGS, PROMPT_TARGETS = load_prompt_flags_and_targets()


# ---------------- helpers ----------------

_FLOAT = re.compile(r'[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?')


def _latest_ota_deck():
    """Pick the most recent LLM+tech_binder OTA deck:
       gen_runs/*/ota_netlist_*.scs
    """
    explicit = _resolve_template_netlist()
    if explicit is not None:
        return explicit.resolve()

    # Support both one-level run folders and occasional nested layouts.
    latest = _find_latest_in_dirs("*/ota_netlist_*.scs")
    if latest is None:
        latest = _find_latest_in_dirs("**/ota_netlist_*.scs")
    return latest.resolve() if latest else None


def _deck_search_hint() -> str:
    roots = [str(p) for p in _candidate_gen_runs_dirs()]
    return (
        "Searched patterns: '*/ota_netlist_*.scs', '**/ota_netlist_*.scs' under: "
        + ", ".join(roots)
        + ". You can also set TEMPLATE_NETLIST=/absolute/path/to/ota_netlist_xxx.scs"
    )


def patch_parameters_line(txt: str, vbias: float) -> str:
    """Update 'parameters ... VBIAS=...' or append VBIAS if missing,
    AND force the VB bias source to use this vbias value.
    """

    # ---- 1) Update or insert VBIAS in 'parameters' line ----
    if re.search(r"\bVBIAS\s*=", txt):
        txt = re.sub(
            r"\bVBIAS\s*=\s*[\w\.\+\-eE]+",
            f"VBIAS={vbias}",
            txt
        )
    else:
        def _add_vbias(m):
            line = m.group(0)
            return line + f" VBIAS={vbias}"
        txt, n_sub = re.subn(r"(?m)^\s*parameters\b.*$", _add_vbias, txt)
        if n_sub == 0:
            txt = f"parameters VBIAS={vbias}\n" + txt

    # ---- 2) Force the VB bias source to use this numeric vbias ----
    def _set_vb_dc(m: re.Match) -> str:
        line = m.group(0)
        if "dc=" in line:
            line = re.sub(
                r"dc\s*=\s*[\w\.\+\-eE]+",
                f"dc={vbias}",
                line
            )
        else:
            line = line + f" dc={vbias}"
        return line

    txt = re.sub(
        r'(?m)^\s*V\w*\s*\(VB\b[^)]*\)\s+vsource\b[^\n]*$',
        _set_vb_dc,
        txt
    )

    return txt


def ensure_summary_header(path: Path):
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    headers = [
        "Tag",
        "Status",
        "VBIAS (V)",
        "Gain (dB)",
        "UGB (Hz)",
        "PM (deg)",
        "Power (W)",
        "Runtime (s)",
    ]
    with path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(headers)


def _read_lines(p: Path):
    with open(p, "r", errors="ignore") as f:
        for line in f:
            yield line.rstrip("\n")


def _parse_ac_psfascii_generic(ac_file: Path):
    """Parse Spectre PSF-ASCII AC file of the form:
         "label"  re  im
       Returns:
         f       : np.ndarray of frequency
         curves  : dict[label_lower -> np.ndarray(complex)]
    """
    scalars = {}
    complexv = {}

    for s in _read_lines(ac_file):
        t = s.strip()
        if not t or not t.startswith('"'):
            continue
        q2 = t.find('"', 1)
        if q2 <= 0:
            continue
        lab = t[1:q2].strip().lower()
        nums = _FLOAT.findall(t[q2 + 1 :])

        if len(nums) >= 2:
            complexv.setdefault(lab, []).append(
                complex(float(nums[0]), float(nums[1]))
            )
        elif len(nums) == 1:
            scalars.setdefault(lab, []).append(float(nums[0]))

    f = np.array(scalars.get("frequency", scalars.get("freq", [])), dtype=float)

    if f.size == 0:
        clen = max((len(v) for v in complexv.values()), default=0)
        for k, arr in scalars.items():
            arr_np = np.array(arr, dtype=float)
            if len(arr_np) == clen and np.all(np.diff(arr_np) > 0):
                f = arr_np
                break

    curves = {k: np.array(v, dtype=complex) for k, v in complexv.items()}
    return f, curves


def _pick_out_in(curves: dict):
    """For OTA we expect output OUT and input INP."""
    out = None
    inn = None

    for o in ("v(out)", "out", "vout", "v(vout)"):
        if o in curves:
            out = o
            break

    for i in ("v(inp)", "inp", "vinp", "v(vinp)"):
        if i in curves:
            inn = i
            break

    return out, inn


def extract_bode_curve(raw_dir: Path):
    """Return (freq_Hz, gain_dB, phase_deg) for this AC run.
    Returns (None, None, None) if fails.
    """
    ac_psf = raw_dir / "ac.ac"
    if not ac_psf.exists():
        return None, None, None

    try:
        f, curves = _parse_ac_psfascii_generic(ac_psf)
    except Exception:
        return None, None, None

    if f.size == 0 or not curves:
        return None, None, None

    curves = {k.lower(): v for k, v in curves.items()}

    out_lab, in_lab = _pick_out_in(curves)
    if not out_lab:
        return None, None, None

    vout = curves.get(out_lab)
    vin = curves.get(in_lab) if in_lab else None

    if vout is None or vout.size == 0:
        return None, None, None

    use_vout_only = (
        vin is None or
        vin.size != vout.size or
        np.all(np.abs(vin) < 1e-9)
    )
    tf = vout if use_vout_only else (vout / (vin + 1e-30))

    mag_db = 20 * np.log10(np.abs(tf) + 1e-30)
    phase_deg = np.angle(tf, deg=True)
    return f, mag_db, phase_deg


def bode_to_metrics(freq, mag_db, phase_deg):
    """
    Compute:
      gain0_db = mag_db[0]
      ugb_hz = interpolated 0 dB crossing if exists else None
      pm_deg = 180 + phase_at_ugb (wrapped to 0..180) if ugb exists else None
    Also returns:
      ugb_is_lower_bound = True if no crossing but mag_db[-1] > 0 (UGB > stop)
    """
    if freq is None or mag_db is None or phase_deg is None:
        return None, None, None, False

    if len(freq) < 2:
        return None, None, None, False

    gain0 = float(mag_db[0])

    # find 0 dB crossing
    ugb = None
    phase_at_ugb = None
    for i in range(1, len(mag_db)):
        g1, g2 = mag_db[i - 1], mag_db[i]
        if (g1 >= 0 and g2 < 0) or (g1 > 0 and g2 <= 0):
            f1, f2 = float(freq[i - 1]), float(freq[i])
            if (f2 != f1) and (g2 != g1):
                ugb = f1 + (f2 - f1) * (0.0 - g1) / (g2 - g1)
            else:
                ugb = f2

            ph1, ph2 = float(phase_deg[i - 1]), float(phase_deg[i])
            if f2 != f1:
                phase_at_ugb = ph1 + (ph2 - ph1) * (ugb - f1) / (f2 - f1)
            else:
                phase_at_ugb = ph2
            break

    ugb_is_lower_bound = False
    pm = None

    if ugb is None:
        # If still above 0 dB at stop, UGB is beyond sweep range
        if float(mag_db[-1]) > 0.0:
            ugb_is_lower_bound = True
        return gain0, None, None, ugb_is_lower_bound

    # PM
    pm_raw = 180.0 + float(phase_at_ugb if phase_at_ugb is not None else 0.0)
    pm_wrap = pm_raw % 360.0
    pm = pm_wrap if pm_wrap <= 180.0 else 360.0 - pm_wrap

    return gain0, float(ugb), float(pm), ugb_is_lower_bound


# ---------------- run one point ----------------

def run_one_point(tag: str,
                  vbias: float,
                  template_netlist: Path,
                  run_netlist: Path,
                  log_file: Path,
                  raw_dir: Path) -> dict:

    res = {
        "Tag": tag,
        "Status": "simulated",
        "VBIAS (V)": vbias,
        "Gain (dB)": "",
        "UGB (Hz)": "",
        "PM (deg)": "",
        "Power (W)": "",
    }

    try:
        txt = template_netlist.read_text()
    except Exception as e:
        print(f"❌ Cannot read OTA template netlist: {e}")
        res["Status"] = "template_missing"
        return res

    txt = patch_parameters_line(txt, vbias)
    run_netlist.write_text(txt)

    raw_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        "spectre",
        str(run_netlist),
        "+log", str(log_file),
        "-raw", str(raw_dir),
        "-format", "psfascii",
    ]

    try:
        cp = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True
        )
    except FileNotFoundError:
        print("❌ spectre binary not found in PATH.")
        res["Status"] = "spectre_missing"
        return res

    if cp.returncode != 0:
        print(f"[{tag}] Spectre failed with return code {cp.returncode}")
        res["Status"] = f"failed({cp.returncode})"
        return res

    # Parse scalar metrics (may give None for UGB/PM depending on crossing)
    try:
        metrics = parse_psf_results(str(raw_dir)) or {}
    except Exception as e:
        print(f"[{tag}] Error in parse_psf_results: {e}")
        res["Status"] = "parse_error"
        return res

    gain_db = metrics.get("Gain (dB)", None)
    ugb_hz = metrics.get("UGB (Hz)", None)
    pm_deg = metrics.get("PM (deg)", None)
    p_w = metrics.get("Power (W)", None)

    # Bode-based robustness (especially for "UGB beyond stop")
    f_ac, mag_db, ph_deg = extract_bode_curve(raw_dir)
    bode_gain0, bode_ugb, bode_pm, ugb_lb = bode_to_metrics(f_ac, mag_db, ph_deg)

    # Prefer bode gain0 if available (usually consistent)
    if bode_gain0 is not None:
        gain_db = bode_gain0 if gain_db is None else gain_db

    # If parser produced None UGB/PM but bode can compute -> fill
    if ugb_hz is None and bode_ugb is not None:
        ugb_hz = bode_ugb
    if pm_deg is None and bode_pm is not None:
        pm_deg = bode_pm

    # If still no UGB because no crossing but mag_db[-1] > 0 => UGB > f_stop
    # For CSV we store ugb as f_stop (lower bound marker), but do NOT reject.
    if ugb_hz is None and ugb_lb and f_ac is not None and len(f_ac) > 0:
        ugb_hz = float(f_ac[-1])  # lower bound UGB >= this
        # PM not well-defined without a real crossing; keep as blank/None

    # Write outputs
    if gain_db is not None:
        res["Gain (dB)"] = gain_db
    if ugb_hz is not None:
        res["UGB (Hz)"] = ugb_hz
    if pm_deg is not None:
        res["PM (deg)"] = pm_deg
    if p_w is not None:
        res["Power (W)"] = p_w

    # ---------------- prompt-sensitive enforcement ----------------
    if PROMPT_FLAGS["hard_enforce"]:
        # Gain enforcement
        if PROMPT_FLAGS["require_gain"] and (gain_db is None or float(gain_db) < PROMPT_TARGETS["gain_db_min"]):
            res["Status"] = "rejected_gain"

        # UGB enforcement:
        # - If ugb was None but we inferred ">= f_stop", we filled with f_stop, so it can pass.
        if res["Status"] == "simulated" and PROMPT_FLAGS["require_ugb"]:
            if ugb_hz is None:
                res["Status"] = "rejected_ugb"
            else:
                if float(ugb_hz) < PROMPT_TARGETS["ugb_hz_min"]:
                    res["Status"] = "rejected_ugb"

        # PM enforcement (only if PM is actually defined)
        if res["Status"] == "simulated" and PROMPT_FLAGS["require_pm"]:
            if pm_deg is None:
                res["Status"] = "rejected_pm"
            else:
                if float(pm_deg) < PROMPT_TARGETS["pm_deg_min"]:
                    res["Status"] = "rejected_pm"

    print(f"[{tag}] Gain={res['Gain (dB)']} dB, UGB={res['UGB (Hz)']} Hz, PM={res['PM (deg)']} deg, P={res['Power (W)']} W, Status={res['Status']}")
    return res


# ---------------- bode summary writer (unchanged) ----------------

def write_bode_summary(bias_list, bode_freq, bode_gains, run_dir: Path):
    if bode_freq is None or not bode_gains:
        print("[bode] No valid Bode data collected; skipping summary_ota_bode.csv")
        return

    out_path = run_dir / "summary_ota_bode.csv"
    n_iter = len(bode_gains)

    with out_path.open("w", newline="") as f:
        w = csv.writer(f)
        header = ["freq_Hz"] + [f"gain_iter{i+1}_dB" for i in range(n_iter)]
        w.writerow(header)

        for idx in range(len(bode_freq)):
            row = [bode_freq[idx]] + [float(g[idx]) for g in bode_gains]
            w.writerow(row)

    print(f"[bode] Wrote Bode summary → {out_path}")


# ---------------- main ----------------

def main():
    template = _latest_ota_deck()
    if template is None or not template.exists():
        print("❌ No OTA deck found for sweep input.")
        print(_deck_search_hint())
        sys.exit(1)

    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    out_root = Path(os.getenv("SWEEP_RESULTS_DIR", "sweep_results"))
    out_root.mkdir(exist_ok=True)
    run_dir = out_root / ts
    run_dir.mkdir(exist_ok=True)

    summary_path = run_dir / "summary_ota.csv"
    ensure_summary_header(summary_path)

    # 15 bias points between 0.6 and 2.5
    VBIAS_MIN = 0.6
    VBIAS_MAX = 2.5
    N_BIAS = 15
    bias_list = list(np.linspace(VBIAS_MIN, VBIAS_MAX, N_BIAS))

    print(f"[ota_sweep] Using template: {template}")
    print(f"[ota_sweep] Bias points: {bias_list}")
    print(f"[ota_sweep] Prompt flags: {PROMPT_FLAGS}")
    print(f"[ota_sweep] Prompt targets: {PROMPT_TARGETS}")

    sweep_start = time.time()
    bode_freq_ref = None
    bode_gains = []

    with summary_path.open("a", newline="") as f:
        w = csv.writer(f)
        for idx, vb in enumerate(bias_list, start=1):
            tag = f"VBIAS_{vb:.3f}V"
            print(f"[{idx}/{len(bias_list)}] ▶ {tag}")
            run_netlist = run_dir / f"{tag}.scs"
            log_file    = run_dir / f"{tag}.log"
            raw_dir     = run_dir / f"{tag}_psf"

            point_start = time.time()
            res = run_one_point(tag, vb, template, run_netlist, log_file, raw_dir)
            point_runtime = time.time() - point_start
            print(f"[{tag}] Runtime={point_runtime:.2f} s")

            # bode summary collection
            f_ac, mag_db, _phase = extract_bode_curve(raw_dir)
            if f_ac is not None and mag_db is not None:
                if bode_freq_ref is None:
                    bode_freq_ref = f_ac
                else:
                    if len(f_ac) != len(bode_freq_ref) or not np.allclose(
                        f_ac, bode_freq_ref, rtol=1e-6, atol=0
                    ):
                        print(f"[bode] WARNING: frequency grid mismatch for {tag}; skipping in Bode summary.")
                        f_ac = None
                if f_ac is not None:
                    bode_gains.append(mag_db)

            row = [
                res.get("Tag", tag),
                res.get("Status", ""),
                res.get("VBIAS (V)", vb),
                res.get("Gain (dB)", ""),
                res.get("UGB (Hz)", ""),
                res.get("PM (deg)", ""),
                res.get("Power (W)", ""),
                f"{point_runtime:.3f}",
            ]
            w.writerow(row)

    total_runtime = time.time() - sweep_start
    print(f"✅ OTA sweep done. See {summary_path}")
    print(f"⏱ Total sweep time: {total_runtime:.2f} s")

    write_bode_summary(bias_list, bode_freq_ref, bode_gains, run_dir)


if __name__ == "__main__":
    main()
