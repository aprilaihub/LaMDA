#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
psf_parser.py  (combined inverter + OTA version)

Extracts from Spectre PSF-ASCII:

  From AC (OTA / generic amplifiers):
    - Gain (dB)
    - UGB (Hz)
    - PM (deg)

  From TRAN (inverter):
    - tpHL (s), tpLH (s)
    - Delay (s), Delay_worst (s)
    - Power (W), Energy (J), PDP (J)

  From DC (VTC, inverter):
    - NMH (V), NML (V)

  From .info:
    - SubthOK (True if no 'subth' appears in *.info)
"""

from pathlib import Path
import re
import numpy as np

# ---------------- basic helpers ----------------

_FLOAT = re.compile(r'[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?')

def _read_lines(p: Path):
    with open(p, "r", errors="ignore") as f:
        for line in f:
            yield line.rstrip("\n")

def _norm(s: str) -> str:
    """Lowercase + strip non-alphanumeric to make matching tolerant."""
    return re.sub(r'[^a-z0-9]', '', s.lower())

def _parse_scalar_psfascii(p: Path):
    """
    Generic parser for Spectre PSF-ASCII (DC / TRAN).

    Supports both:
      "label"  1.23 4.56 ...
    and:
      label  1.23 4.56 ...
    """
    data = {}
    if not p.exists():
        return data

    for s in _read_lines(p):
        t = s.strip()
        if not t:
            continue

        label = None
        rest = None

        if t.startswith('"'):
            # style: "label"  nums...
            q2 = t.find('"', 1)
            if q2 <= 0:
                continue
            label = t[1:q2].strip()
            rest = t[q2 + 1 :]
        else:
            # fallback: first token is label
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

def _find_key(data: dict, *candidates):
    """Pick the first candidate key present (after normalisation)."""
    for name in candidates:
        k = _norm(name)
        if k in data:
            return data[k]
    return None

# ---------------- AC PARSING (for OTA / generic amplifiers) ----------------

def _parse_ac_psfascii_generic(ac_file: Path):
    """
    Generic Spectre AC PSF-ASCII parser.

    Handles lines like:
      "V(OUT)"   1.23  -0.45
    or label without quotes. Interprets first two numbers as (re, im).
    """
    scalars, complexv = {}, {}
    if not ac_file.exists():
        return np.array([]), {}

    for s in _read_lines(ac_file):
        t = s.strip()
        if not t:
            continue
        # We only care about lines that look like they start with a label
        # (quoted or unquoted) followed by numbers.
        if not t.startswith('"') and '"' not in t and not t[0].isalpha():
            continue

        label = None
        rest = None

        if t.startswith('"'):
            q2 = t.find('"', 1)
            if q2 <= 0:
                continue
            label = t[1:q2].strip()
            rest = t[q2 + 1 :]
        else:
            parts = t.split(None, 1)
            if len(parts) < 2:
                continue
            label, rest = parts[0], parts[1]

        nums = _FLOAT.findall(rest or "")
        if len(nums) >= 2:
            # treat as complex (re,im)
            cval = complex(float(nums[0]), float(nums[1]))
            key = _norm(label)
            complexv.setdefault(key, []).append(cval)
        elif len(nums) == 1:
            sval = float(nums[0])
            key = _norm(label)
            scalars.setdefault(key, []).append(sval)

    # frequency vector: "frequency" or "freq"
    f = np.array(scalars.get("frequency", scalars.get("freq", [])), dtype=float)

    # Heuristic: if missing, look for a scalar array with strictly increasing entries
    if f.size == 0 and scalars:
        clen = max((len(v) for v in complexv.values()), default=0)
        for k, arr in scalars.items():
            arr_np = np.array(arr, dtype=float)
            if len(arr_np) == clen and np.all(np.diff(arr_np) > 0):
                f = arr_np
                break

    curves = {k: np.array(v, dtype=complex) for k, v in complexv.items()}
    return f, curves

def _parse_ac_nutascii(ac_file: Path):
    """
    Nutmeg-ascii fallback (rare, but included for robustness).
    """
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
                    curves_raw.setdefault(n, []).append(v)
                pending = []

    f = np.array(curves_raw.get("frequency", curves_raw.get("freq", [])), dtype=float)

    curves = {}
    for name, vec in curves_raw.items():
        if not vec:
            continue
        key = _norm(name)
        if isinstance(vec[0], complex):
            curves[key] = np.array(vec, dtype=complex)
    return f, curves

def _pick_out_in(curves: dict):
    """
    Heuristic mapping of OUT / IN nodes for AC.

    Covers:
      - inverter: IN, OUT
      - OTA: INP / IN, OUT
    """
    out_lab = None
    in_lab = None

    # OUT candidates
    for o in ("out", "v(out)", "vout", "vo"):
        ko = _norm(o)
        for key in curves.keys():
            if key == ko:
                out_lab = key
                break
        if out_lab:
            break

    # IN candidates (single-ended or positive differential node)
    for i in ("inp", "in", "vin", "vip"):
        ki = _norm(i)
        for key in curves.keys():
            if key == ki:
                in_lab = key
                break
        if in_lab:
            break

    return out_lab, in_lab

def _metrics_from_tf(f: np.ndarray, tf: np.ndarray):
    if f.size == 0 or tf.size == 0:
        return {}
    mag_db = 20 * np.log10(np.abs(tf) + 1e-30)
    phase = np.angle(tf, deg=True)

    gain_db = float(mag_db[0])

    # UGB: 0 dB crossing
    ugb = None
    for i in range(1, len(f)):
        if mag_db[i - 1] >= 0 and mag_db[i] < 0:
            f1, f2 = f[i - 1], f[i]
            m1, m2 = mag_db[i - 1], mag_db[i]
            frac = (0 - m1) / (m2 - m1 + 1e-30)
            ugb = float(f1 + frac * (f2 - f1))
            break

    pm = None
    if ugb is not None:
        idx = int(np.argmin(np.abs(f - ugb)))
        pm_raw = float(180.0 + phase[idx])   # conventional: 180 + ∠loop @ UGB
        pm_wrap = pm_raw % 360.0
        pm = pm_wrap if pm_wrap <= 180.0 else 360.0 - pm_wrap

    out = {"Gain (dB)": gain_db}
    if ugb is not None:
        out["UGB (Hz)"] = ugb
    if pm is not None:
        out["PM (deg)"] = pm
    return out

def _find_ac_file(raw_dir: Path) -> Path | None:
    candidates = [
        raw_dir / "ac.ac",
        raw_dir / "ac",
    ]
    candidates += sorted(raw_dir.glob("*.ac"))

    psf_sub = raw_dir / "psf"
    if psf_sub.exists() and psf_sub.is_dir():
        candidates += [psf_sub / "ac.ac", psf_sub / "ac"]
        candidates += sorted(psf_sub.glob("*.ac"))

    for c in candidates:
        if c.exists():
            return c
    return None

def _parse_ac_metrics(raw_dir: Path):
    ac_file = _find_ac_file(raw_dir)
    if ac_file is None:
        return {}

    f, curves = _parse_ac_psfascii_generic(ac_file)
    if f.size == 0 or not curves:
        f, curves = _parse_ac_nutascii(ac_file)
        if f.size == 0 or not curves:
            return {}

    # normalise keys
    curves = {k.lower(): v for k, v in curves.items()}

    out_lab, in_lab = _pick_out_in(curves)
    vout = curves.get(out_lab) if out_lab else None
    vin = curves.get(in_lab) if in_lab else None

    if vout is None:
        return {}

    # If VIN missing or ~0, fall back to |Vout| (still lets pipeline return metrics)
    use_vout_only = vin is None or vin.size != vout.size or np.all(np.abs(vin) < 1e-9)
    tf = vout if use_vout_only else (vout / (vin + 1e-30))

    return _metrics_from_tf(f, tf)

# ---------------- TRAN: delay & power (inverter) ----------------

def _crossing_time(t, v, level, edge="rising"):
    if t is None or v is None:
        return None
    if t.size < 2 or v.size < 2:
        return None

    for i in range(1, len(t)):
        v1, v2 = v[i - 1], v[i]
        t1, t2 = t[i - 1], t[i]

        if edge == "rising":
            if v1 < level <= v2:
                if abs(v2 - v1) < 1e-30:
                    return float(t1)
                frac = (level - v1) / (v2 - v1)
                return float(t1 + frac * (t2 - t1))
        else:
            if v1 > level >= v2:
                if abs(v2 - v1) < 1e-30:
                    return float(t1)
                frac = (level - v1) / (v2 - v1)
                return float(t1 + frac * (t2 - t1))

    idx = int(np.argmin(np.abs(v - level)))
    return float(t[idx])

def compute_delay_and_power(t, vin, vout, ivdd):
    if t is None or vin is None or vout is None:
        return None, None, None, None, None, None
    if t.size < 4 or vin.size < 4 or vout.size < 4:
        return None, None, None, None, None, None

    VDD = float(np.max(vin))
    if VDD <= 0:
        return None, None, None, None, None, None
    v_mid = 0.5 * VDD

    t_in_r = _crossing_time(t, vin, v_mid, edge="rising")
    t_in_f = _crossing_time(t, vin, v_mid, edge="falling")
    t_out_f = _crossing_time(t, vout, v_mid, edge="falling")
    t_out_r = _crossing_time(t, vout, v_mid, edge="rising")

    tpHL = (t_out_f - t_in_r) if (t_out_f is not None and t_in_r is not None) else None
    tpLH = (t_out_r - t_in_f) if (t_out_r is not None and t_in_f is not None) else None

    if tpHL is not None and tpHL < 0:
        tpHL = None
    if tpLH is not None and tpLH < 0:
        tpLH = None

    delays = [d for d in (tpHL, tpLH) if d is not None]
    delay_avg = float(np.mean(delays)) if delays else None
    delay_worst = float(np.max(delays)) if delays else None

    energy = None
    power = None
    if ivdd is not None and ivdd.size == t.size:
        dt = np.diff(t)
        i_mid = 0.5 * (ivdd[:-1] + ivdd[1:])
        inst_power = np.abs(i_mid) * VDD
        energy = float(np.sum(inst_power * dt))
        total_time = float(t[-1] - t[0]) if t[-1] > t[0] else None
        if total_time and total_time > 0:
            power = energy / total_time

    return tpHL, tpLH, delay_avg, delay_worst, power, energy

def _find_tran_file(raw_dir: Path) -> Path | None:
    candidates = [
        raw_dir / "tran.tran",
        raw_dir / "tran",
    ]
    candidates += sorted(raw_dir.glob("*.tran"))

    psf_sub = raw_dir / "psf"
    if psf_sub.exists() and psf_sub.is_dir():
        candidates += [psf_sub / "tran.tran", psf_sub / "tran"]
        candidates += sorted(psf_sub.glob("*.tran"))

    for c in candidates:
        if c.exists():
            return c
    return None

def _parse_tran_delay_power(raw_dir: Path):
    tran_file = _find_tran_file(raw_dir)
    if tran_file is None:
        return {}

    data = _parse_scalar_psfascii(tran_file)
    if not data:
        return {}

    t    = _find_key(data, "time")
    vin  = _find_key(data, "IN", "vin", "V(IN)")
    vout = _find_key(data, "OUT", "vout", "V(OUT)")
    ivdd = _find_key(data, "V1:p", "I(V1)", "v1p", "iv1")

    tpHL, tpLH, d_avg, d_worst, p_avg, e_tot = compute_delay_and_power(t, vin, vout, ivdd)

    out = {}
    if tpHL is not None:
        out["tpHL (s)"] = tpHL
    if tpLH is not None:
        out["tpLH (s)"] = tpLH
    if d_avg is not None:
        out["Delay (s)"] = d_avg
    if d_worst is not None:
        out["Delay_worst (s)"] = d_worst
    if p_avg is not None:
        out["Power (W)"] = p_avg
    if e_tot is not None:
        out["Energy (J)"] = e_tot
    if e_tot is not None and d_worst is not None:
        out["PDP (J)"] = e_tot * d_worst

    return out

# ---------------- DC: noise margins (inverter) ----------------

def _compute_noise_margins(vin, vout):
    if vin is None or vout is None:
        return None, None
    if vin.size < 4 or vout.size < 4:
        return None, None

    order = np.argsort(vin)
    vin = vin[order]
    vout = vout[order]

    dv = np.diff(vout)
    dx = np.diff(vin)
    dx[dx == 0] = 1e-15
    slope = dv / dx

    region = np.where(slope < -0.5)[0]
    if region.size == 0:
        return None, None

    i_left = region[0]
    i_right = region[-1]

    vil = float(vin[i_left])
    vih = float(vin[i_right + 1] if i_right + 1 < vin.size else vin[i_right])

    voh = float(np.max(vout[: i_left + 1])) if i_left + 1 <= vout.size else float(vout[0])
    vol = float(np.min(vout[i_right + 1 :])) if i_right + 1 < vout.size else float(vout[-1])

    nmh = voh - vih
    nml = vil - vol
    return nmh, nml

def _find_dc_file(raw_dir: Path) -> Path | None:
    candidates = [
        raw_dir / "dc.dc",
        raw_dir / "dc",
    ]
    candidates += sorted(raw_dir.glob("*.dc"))

    psf_sub = raw_dir / "psf"
    if psf_sub.exists() and psf_sub.is_dir():
        candidates += [psf_sub / "dc.dc", psf_sub / "dc"]
        candidates += sorted(psf_sub.glob("*.dc"))

    for c in candidates:
        if c.exists():
            return c
    return None

def _parse_dc_noise_margins(raw_dir: Path):
    dc_file = _find_dc_file(raw_dir)
    if dc_file is None:
        return {}

    data = _parse_scalar_psfascii(dc_file)
    if not data:
        return {}

    vin  = _find_key(data, "VIN_DC", "vin_dc", "IN", "vin")
    vout = _find_key(data, "OUT", "vout", "V(OUT)")
    if vin is None or vout is None:
        return {}

    nmh, nml = _compute_noise_margins(vin, vout)
    if nmh is None or nml is None:
        return {}

    return {
        "NMH (V)": float(nmh),
        "NML (V)": float(nml),
    }

# ---------------- Subthreshold flag ----------------

def _parse_subth_flag(raw_dir: Path):
    infos = list(Path(raw_dir).glob("*.info"))
    psf_sub = raw_dir / "psf"
    if psf_sub.exists():
        infos += list(psf_sub.glob("*.info"))

    if not infos:
        return {}

    for info in infos:
        for line in _read_lines(info):
            if "subth" in line.lower():
                return {"SubthOK": False}
    return {"SubthOK": True}

# ---------------- public API ----------------

def parse_psf_results(raw_dir: str | Path):
    raw = Path(raw_dir)
    out = {}

    # 1) AC (OTA / amplifiers)
    try:
        out.update(_parse_ac_metrics(raw))
    except Exception:
        pass

    # 2) DC VTC (inverter)
    try:
        out.update(_parse_dc_noise_margins(raw))
    except Exception:
        pass

    # 3) TRAN delay / power (inverter)
    try:
        out.update(_parse_tran_delay_power(raw))
    except Exception:
        pass

    # 4) Subthreshold flag
    try:
        out.update(_parse_subth_flag(raw))
    except Exception:
        pass

    return out

