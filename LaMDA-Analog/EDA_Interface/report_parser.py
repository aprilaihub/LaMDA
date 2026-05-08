#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from pathlib import Path
import json
import pandas as pd

FIELD_MAP = {
    "Wn (u)":          "wn_um",
    "Wp (u)":          "wp_um",
    "Ratio":           "ratio_wp_over_wn",
    "L":               "l_text",
    "Status":          "status",
    "tpHL (s)":        "tpHL_s",
    "tpLH (s)":        "tpLH_s",
    "Delay_avg (s)":   "delay_avg_s",
    "Delay_worst (s)": "delay_worst_s",
    "Power (W)":       "power_w",
    "PDP (J)":         "pdp_j",
    "VIL (V)":         "vil_v",
    "VIH (V)":         "vih_v",
    "VOH (V)":         "voh_v",
    "VOL (V)":         "vol_v",
    "NMH (V)":         "nmh_v",
    "NML (V)":         "nml_v",
    "SubthCount":      "subth_count",
    "SubthOK":         "subth_ok",
 # OTA AC metrics (from summary_ota.csv)
    "Gain (dB)":       "gain_db",
    "UGB (Hz)":        "ugb_hz",
    "PM (deg)":        "pm_deg",
}

def latest_summary_csv() -> Path:
    root = Path("sweep_results")
    runs = sorted(root.glob("*/summary.csv"), key=lambda p: p.stat().st_mtime)
    if not runs:
        raise SystemExit("No summary.csv found under sweep_results/*")
    return runs[-1]

def parse_number(x):
    if x is None:
        return None
    s = str(x).strip()
    if s == "" or s.lower() == "nan":
        return None
    try:
        return float(s)
    except Exception:
        return None

def parse_bool(x):
    if x is None:
        return None
    s = str(x).strip().lower()
    if s in ("true","1","yes"):
        return True
    if s in ("false","0","no"):
        return False
    return None

def main():
    csv_path = latest_summary_csv()
    df = pd.read_csv(csv_path)

    recs = []
    for _, row in df.iterrows():
        r = {}
        for old, new in FIELD_MAP.items():
            if old in df.columns:
                r[new] = row.get(old, None)

        for k in ["wn_um","wp_um","ratio_wp_over_wn","tpHL_s","tpLH_s",
                  "delay_avg_s","delay_worst_s","power_w","pdp_j",
                  "vil_v","vih_v","voh_v","vol_v","nmh_v","nml_v"]:
            if k in r:
                r[k] = parse_number(r.get(k))

        if "subth_count" in r:
            sc = row.get("SubthCount", None)
            try:
                r["subth_count"] = int(sc) if str(sc).strip() != "" else None
            except Exception:
                r["subth_count"] = None
        if "subth_ok" in r:
            r["subth_ok"] = parse_bool(row.get("SubthOK", None))

        # Derived PDP if missing
        if r.get("pdp_j") is None:
            pw = r.get("power_w")
            dw = r.get("delay_worst_s")
            if pw is not None and dw is not None:
                r["pdp_j"] = pw * dw

        recs.append(r)

    out = {"meta": {"source_csv": str(csv_path), "count": len(recs)}, "records": recs}
    out_path = csv_path.with_suffix(".llm.json")
    out_path.write_text(json.dumps(out, indent=2))
    print(f"LLM JSON written: {out_path}")

if __name__ == "__main__":
    main()

