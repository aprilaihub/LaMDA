#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
psf_parser_ota.py

OTA-specific PSF/CSV parser.

Returns:
  - Gain (dB) : low-frequency gain from AC analysis (open-loop TF)
  - UGB (Hz)  : unity-gain bandwidth
  - PM (deg)  : phase margin (180 + phase(TF) at UGB), wrapped to [0, 180]
  - Power (W) : DC power from supply current

AC transfer function is computed as:
    TF = V(OUT) / ( V(INP) - V(INN) )

This avoids dependence on how the stimulus was specified (acmag vs sine/etc.)
as long as the AC run produced non-zero differential excitation.
"""

from pathlib import Path
import re
import numpy as np
import pandas as pd

_FLOAT = re.compile(r'[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?')


def _read_lines(p: Path):
    with open(p, "r", errors="ignore") as f:
        for line in f:
            yield line.rstrip("\n")


def _norm(s: str) -> str:
    return re.sub(r'[^a-z0-9]', '', s.lower())


# ---------------- AC: PSF-ASCII parser ----------------

def _parse_ac_psfascii_generic(ac_file: Path):
    """
    Parse Spectre PSF-ASCII AC file:
        "label"  re  im
    Returns:
        f      : np.ndarray frequency
        curves : dict[label_lower -> np.ndarray(complex)]
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
        nums = _FLOAT.findall(t[q2 + 1:])

        if len(nums) >= 2:
            complexv.setdefault(lab, []).append(complex(float(nums[0]), float(nums[1])))
        elif len(nums) == 1:
            scalars.setdefault(lab, []).append(float(nums[0]))

    f = np.array(scalars.get("frequency", scalars.get("freq", [])), dtype=float)

    # fallback: find scalar vector that looks like frequency
    if f.size == 0:
        clen = max((len(v) for v in complexv.values()), default=0)
        for k, arr in scalars.items():
            arr_np = np.array(arr, dtype=float)
            if len(arr_np) == clen and np.all(np.diff(arr_np) > 0):
                f = arr_np
                break

    curves = {k: np.array(v, dtype=complex) for k, v in complexv.items()}
    return f, curves


def _parse_ac_nutascii(ac_file: Path):
    """Nutmeg-ascii fallback."""
    vars_order, curves_raw = [], {}
    reading_vars = reading_vals = False
    pending = []

    cmplx = re.compile(
        r'[\(\s]*([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)\s*,\s*'
        r'([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)\s*\)?'
    )

    for s in _read_lines(ac_file):
        t = s.strip()
        lt = t.lower()
        if not t:
            continue

        if lt.startswith("variables:"):
            reading_vars, reading_vals = True, False
            continue
        if lt.startswith("values:"):
            reading_vars, reading_vals = False, True
            continue

        if reading_vars:
            parts = t.split()
            if len(parts) >= 2 and parts[0].isdigit():
                name = parts[1].lower()
                vars_order.append(name)
                curves_raw.setdefault(name, [])
            continue

        if reading_vals:
            if t.isdigit():
                pending = []
                continue
            m = cmplx.search(t)
            if m:
                val = complex(float(m.group(1)), float(m.group(2)))
            else:
                nums = _FLOAT.findall(t)
                if nums:
                    val = float(nums[0])
                else:
                    continue

            pending.append(val)
            if len(pending) == len(vars_order):
                for n, v in zip(vars_order, pending):
                    curves_raw[n].append(v)
                pending = []

    f = np.array(curves_raw.get("frequency", curves_raw.get("freq", [])), dtype=float)

    curves = {}
    for name, vec in curves_raw.items():
        if not vec:
            continue
        m = re.match(r'^v\(([^)]+)\)$', name)
        key = m.group(1).lower() if m else name.lower()
        if isinstance(vec[0], complex):
            curves[key] = np.array(vec, dtype=complex)

    return f, curves


def _get_curve(curves: dict, candidates):
    """Try several possible keys for a signal."""
    for k in candidates:
        if k in curves:
            return curves[k]
    return None


def _metrics_from_tf(f: np.ndarray, tf: np.ndarray):
    if f.size == 0 or tf.size == 0:
        return {}

    mag_db = 20.0 * np.log10(np.abs(tf) + 1e-30)
    phase_deg = np.angle(tf, deg=True)

    gain_db = float(mag_db[0])

    # UGB: first 0 dB crossing (high-to-low)
    ugb = None
    for i in range(1, len(f)):
        if (mag_db[i - 1] >= 0.0) and (mag_db[i] < 0.0):
            f1, f2 = f[i - 1], f[i]
            m1, m2 = mag_db[i - 1], mag_db[i]
            frac = (0.0 - m1) / (m2 - m1 + 1e-30)
            ugb = float(f1 + frac * (f2 - f1))
            break

    pm = None
    if ugb is not None:
        idx = int(np.argmin(np.abs(f - ugb)))
        ph = float(phase_deg[idx])
        pm_raw = (180.0 + ph) % 360.0
        pm = pm_raw if pm_raw <= 180.0 else 360.0 - pm_raw

    out = {"Gain (dB)": gain_db}
    if ugb is not None:
        out["UGB (Hz)"] = ugb
    if pm is not None:
        out["PM (deg)"] = pm
    return out


def _parse_ac_from_csv(raw_dir: Path):
    """
    Use ac_parsed.csv only if it exists and has valid columns.
    Expected: freq_Hz, gain_dB, phase_deg
    """
    ac_csv = raw_dir / "ac_parsed.csv"
    if not ac_csv.exists():
        return {}

    try:
        df = pd.read_csv(ac_csv)
    except Exception:
        return {}

    required = {"freq_Hz", "gain_dB", "phase_deg"}
    if df.empty or not required.issubset(df.columns):
        return {}

    freq = df["freq_Hz"].to_numpy(dtype=float)
    gain = df["gain_dB"].to_numpy(dtype=float)
    phase = df["phase_deg"].to_numpy(dtype=float)
    if freq.size == 0:
        return {}

    gain_db = float(gain[0])

    ugb = None
    pm = None
    for i in range(1, len(gain)):
        g1, g2 = gain[i - 1], gain[i]
        if (g1 >= 0.0 and g2 < 0.0) or (g1 < 0.0 and g2 >= 0.0):
            f1, f2 = freq[i - 1], freq[i]
            if f2 != f1 and g2 != g1:
                ugb = float(f1 + (f2 - f1) * (0.0 - g1) / (g2 - g1))
            else:
                ugb = float(f2)

            ph1, ph2 = phase[i - 1], phase[i]
            if f2 != f1:
                ph_ugb = float(ph1 + (ph2 - ph1) * (ugb - f1) / (f2 - f1))
            else:
                ph_ugb = float(ph2)

            pm_raw = (180.0 + ph_ugb) % 360.0
            pm = pm_raw if pm_raw <= 180.0 else 360.0 - pm_raw
            break

    out = {"Gain (dB)": gain_db}
    if ugb is not None:
        out["UGB (Hz)"] = ugb
    if pm is not None:
        out["PM (deg)"] = pm
    return out


def _parse_ac_auto(raw_dir: Path):
    ac_psf = raw_dir / "ac.ac"
    if not ac_psf.exists():
        return {}

    # Try PSF-ASCII first, then nutascii fallback
    f, curves = _parse_ac_psfascii_generic(ac_psf)
    if f.size == 0 or not curves:
        f, curves = _parse_ac_nutascii(ac_psf)
        if f.size == 0 or not curves:
            return {}

    # normalize keys
    curves = {k.lower(): v for k, v in curves.items()}

    # Prefer node voltage keys like 'v(out)' (psfascii) and 'out' (nutascii)
    vout = _get_curve(curves, ["v(out)", "out", "v(vout)", "vout", "vo"])
    vinp = _get_curve(curves, ["v(inp)", "inp", "v(vinp)", "vinp", "vi", "v(in_p)"])
    vinn = _get_curve(curves, ["v(inn)", "inn", "v(vinn)", "vinn", "v(in_n)"])

    if vout is None:
        return {}

    # Differential input
    vdiff = None
    if (vinp is not None) and (vinn is not None) and (vinp.size == vout.size) and (vinn.size == vout.size):
        vdiff = vinp - vinn
    elif (vinp is not None) and (vinp.size == vout.size):
        # fallback: assume VINN is AC-grounded to VCM
        vdiff = vinp
    else:
        # last resort: assume 1V differential excitation
        vdiff = np.ones_like(vout, dtype=complex)

    # Avoid divide-by-zero
    tf = vout / (vdiff + 1e-30)
    return _metrics_from_tf(f, tf)


# ---------------- DC power (from dcOp.dc) ----------------

def _parse_scalar_psfascii(p: Path):
    """Scalar PSF-ASCII: "label" value"""
    data = {}
    if not p.exists():
        return data

    for s in _read_lines(p):
        t = s.strip()
        if not t:
            continue

        if t.startswith('"'):
            q2 = t.find('"', 1)
            if q2 <= 0:
                continue
            label = t[1:q2].strip()
            rest = t[q2 + 1:]
        else:
            parts = t.split()
            if len(parts) < 2:
                continue
            label = parts[0]
            rest = " ".join(parts[1:])

        nums = _FLOAT.findall(rest or "")
        if not nums:
            continue

        try:
            val = float(nums[0])
        except Exception:
            continue

        key = _norm(label)
        data.setdefault(key, []).append(val)

    return {k: np.array(v, dtype=float) for k, v in data.items()}


def _find_dc_file(raw_dir: Path):
    candidates = [
        raw_dir / "dcOp.dc",
        raw_dir / "dc.dc",
        raw_dir / "dcOp",
        raw_dir / "dc",
    ]
    psf_sub = raw_dir / "psf"
    if psf_sub.exists() and psf_sub.is_dir():
        candidates += [
            psf_sub / "dcOp.dc",
            psf_sub / "dc.dc",
            psf_sub / "dcOp",
            psf_sub / "dc",
        ]

    for c in candidates:
        if c.exists():
            return c
    return None


def _parse_dc_power(raw_dir: Path):
    """
    DC power: |I(VDD)| * VDD.
    Expects VDD supply current label becomes normalized as 'vddp' for 'VDD:p'.
    """
    dc_file = _find_dc_file(raw_dir)
    if dc_file is None:
        return {}

    data = _parse_scalar_psfascii(dc_file)
    if not data:
        return {}

    ids = None
    for cand in ("vddp", "v1p", "ivdd"):
        if cand in data:
            ids = data[cand]
            break
    if ids is None or ids.size == 0:
        return {}

    # try to read VDD value if present, else default 5V
    vdd_val = 5.0
    if "vdd" in data and data["vdd"].size > 0:
        vdd_val = float(np.mean(data["vdd"]))

    i_avg = float(np.mean(ids))
    p = abs(i_avg) * vdd_val
    return {"Power (W)": p}


# ---------------- public API ----------------

def parse_psf_results(raw_dir):
    raw = Path(raw_dir)
    out = {}

    # Prefer ac_parsed.csv if valid, else parse ac.ac directly
    try:
        out.update(_parse_ac_from_csv(raw))
    except Exception:
        pass

    if "Gain (dB)" not in out:
        try:
            out.update(_parse_ac_auto(raw))
        except Exception as e:
            print(f"[parse_psf_results] AC parsing failed: {e}")

    # DC power
    try:
        out.update(_parse_dc_power(raw))
    except Exception as e:
        print(f"[parse_psf_results] DC power parsing failed: {e}")

    return {k: v for k, v in out.items() if v is not None}
