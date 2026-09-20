"""
ADS Runner — consolidated subprocess entry point for LaMDA-RF.
Executed inside the ADS Python virtual environment (ads2026_venv).

Usage (called by ADSInterface.run_ads via subprocess):
    python main.py <netlist_path> <design_type> <target_freq_hz> [target_freq_hz_2]

    design_type: 1=antenna, 2=coupler, 3=filter, 4=matching_network, 0=unknown
"""

import glob
import os
import re
import sys
import time
from pathlib import Path

# ---------------------------------------------------------------------------
# create_workspace_and_library
# ---------------------------------------------------------------------------

def create_workspace_and_library(workspace_name: str, cell_name: str):
    """Create an ADS workspace and library, replacing any existing one."""
    import shutil
    import keysight.ads.de as de

    home_dir = os.environ["HOME"]
    workspace_path = os.path.join(home_dir, workspace_name)
    output_dir = os.path.join(workspace_path, "data")

    if de.workspace_is_open():
        de.close_workspace()

    if Path(workspace_path).exists():
        shutil.rmtree(workspace_path)

    workspace = de.create_workspace(workspace_path)
    workspace.open()

    lib_name = f"{workspace_name}_lib"
    lib_path = os.path.join(workspace.path, lib_name)
    library = de.create_new_library(lib_name, lib_path)
    workspace.add_library(lib_name, lib_path, de.LibraryMode.SHARED)

    return workspace, lib_name, output_dir, library


# ---------------------------------------------------------------------------
# create_schematic
# ---------------------------------------------------------------------------

def create_schematic(lib_name: str, cell_name: str):
    """Create a schematic cell in the given library."""
    from keysight.ads.de import db_uu as db

    design = db.create_schematic(f"{lib_name}:{cell_name}:schematic")
    design.save_design()
    return design


# ---------------------------------------------------------------------------
# simulate_netlist
# ---------------------------------------------------------------------------

def simulate_netlist(netlist: str, output_dir: str, cell_name: str):
    """Run ADS simulation and return a DataFrame with S-parameter data in dB."""
    import numpy as np
    import keysight.ads.dataset as dataset
    from keysight.edatoolbox import ads

    simulator = ads.CircuitSimulator()
    simulator.run_netlist(netlist, output_dir=output_dir)

    dataset_path = Path(output_dir) / f"{cell_name}.ds"
    output_data = dataset.open(dataset_path)
    simulation_data = output_data[output_data.varblock_names[0]].to_dataframe().reset_index()

    for col in simulation_data.columns:
        if np.iscomplexobj(simulation_data[col]):
            simulation_data[col] = 20 * np.log10(np.abs(simulation_data[col]))

    csv_file_path = Path(output_dir) / f"{cell_name}_simulation_data.csv"
    simulation_data.to_csv(csv_file_path, index=False)

    return simulation_data


# ---------------------------------------------------------------------------
# Shared helper
# ---------------------------------------------------------------------------

def _format_frequency(freq_hz):
    """Format a frequency value to a human-readable string."""
    if freq_hz >= 1e9:
        return f"{freq_hz / 1e9:.2f} GHz"
    if freq_hz >= 1e6:
        return f"{freq_hz / 1e6:.2f} MHz"
    if freq_hz >= 1e3:
        return f"{freq_hz / 1e3:.2f} kHz"
    return f"{freq_hz:.2f} Hz"


# ---------------------------------------------------------------------------
# antenna_helper
# ---------------------------------------------------------------------------

def antenna_helper(simulation_data, target_freq_hz):
    """Extract antenna-focused metrics (S11) from simulation data."""
    if simulation_data is None or simulation_data.empty or simulation_data.shape[1] < 2:
        return None

    idx = (simulation_data.iloc[:, 0] - target_freq_hz).abs().idxmin()
    freq_at_target = float(simulation_data.iloc[idx, 0])
    s11_at_target_db = round(float(simulation_data.iloc[idx, 1]), 1)

    min_idx = simulation_data.iloc[:, 1].idxmin()
    resonance_freq_hz = float(simulation_data.iloc[min_idx, 0])
    min_s11_db = round(float(simulation_data.iloc[min_idx, 1]), 1)

    summary = (
        f"S11 at {_format_frequency(freq_at_target)} is {s11_at_target_db} dB. "
        f"Resonant frequency is {_format_frequency(resonance_freq_hz)} "
        f"with S11 = {min_s11_db} dB."
    )

    return {
        "freq_at_target": freq_at_target,
        "s11_at_target_db": s11_at_target_db,
        "resonance_freq_hz": resonance_freq_hz,
        "min_s11_db": min_s11_db,
        "summary": summary,
    }


# ---------------------------------------------------------------------------
# coupler_helper
# ---------------------------------------------------------------------------

def _find_frequency_column(columns):
    for column_name in columns:
        if "freq" in str(column_name).lower():
            return column_name
    return columns[0]


def _find_sparam_column(columns, row, col):
    pattern = re.compile(rf"^S\[\s*{row}\s*,\s*{col}\s*\]$", re.IGNORECASE)
    for column_name in columns:
        if pattern.match(str(column_name)):
            return column_name
    return None


def coupler_helper(simulation_data, target_freq_hz):
    """Extract coupler metrics (S11/S21/S31/S41) at the target frequency."""
    if simulation_data is None or simulation_data.empty or simulation_data.shape[1] < 2:
        return None

    freq_column = _find_frequency_column(simulation_data.columns)
    idx = (simulation_data[freq_column] - target_freq_hz).abs().idxmin()
    freq_at_target = float(simulation_data.loc[idx, freq_column])

    s11_col = _find_sparam_column(simulation_data.columns, 1, 1)
    s21_col = _find_sparam_column(simulation_data.columns, 2, 1)
    s31_col = _find_sparam_column(simulation_data.columns, 3, 1)
    s41_col = _find_sparam_column(simulation_data.columns, 4, 1)

    def _get(col):
        return round(float(simulation_data.loc[idx, col]), 1) if col is not None else None

    s11 = _get(s11_col)
    s21 = _get(s21_col)
    s31 = _get(s31_col)
    s41 = _get(s41_col)

    parts = [f"Coupler metrics at {_format_frequency(freq_at_target)}:"]
    for label, val in [("S11", s11), ("S21", s21), ("S31", s31), ("S41", s41)]:
        if val is not None:
            parts.append(f"{label} = {val} dB")
    if len(parts) == 1:
        parts.append("No matching S-parameter columns found")

    return {
        "freq_at_target": freq_at_target,
        "s11_at_target_db": s11,
        "s21_at_target_db": s21,
        "s31_at_target_db": s31,
        "s41_at_target_db": s41,
        "s11_column": s11_col,
        "s21_column": s21_col,
        "s31_column": s31_col,
        "s41_column": s41_col,
        "summary": ", ".join(parts),
    }


# ---------------------------------------------------------------------------
# filter_helper
# ---------------------------------------------------------------------------

def filter_helper(simulation_data, target_freq_hz, target_freq_hz_2=None, is_bandpass=False):
    """Extract filter metrics (S11/S21) at one or two target frequencies.

    When is_bandpass=True and target_freq_hz_2 is provided, the two frequencies
    are treated as passband edges: band-wide worst-case S11/S21 in the passband
    and worst-case S21 outside the passband are reported.
    Otherwise, the original single/dual spot-check behaviour is preserved.
    """
    if simulation_data is None or simulation_data.empty or simulation_data.shape[1] < 2:
        return None

    has_s21 = simulation_data.shape[1] > 3
    freq_col = simulation_data.iloc[:, 0]

    # --- Band-pass filter mode ---
    if is_bandpass and target_freq_hz_2 is not None:
        pb_low_hz  = min(target_freq_hz, target_freq_hz_2)
        pb_high_hz = max(target_freq_hz, target_freq_hz_2)
        center_hz  = (pb_low_hz + pb_high_hz) / 2

        idx_center   = (freq_col - center_hz).abs().idxmin()
        freq_center  = float(freq_col.iloc[idx_center])
        s11_center   = round(float(simulation_data.iloc[idx_center, 1]), 1)

        band_mask    = (freq_col >= pb_low_hz) & (freq_col <= pb_high_hz)
        sb_mask      = ~band_mask
        s11_worst_pb = round(float(simulation_data.iloc[:, 1][band_mask].max()), 1)

        if has_s21:
            s21_col      = simulation_data.iloc[:, 3]
            s21_center   = round(float(s21_col.iloc[idx_center]), 1)
            s21_worst_pb = round(float(s21_col[band_mask].max()), 1)
            s21_worst_sb = round(float(s21_col[sb_mask].max()), 1) if sb_mask.any() else None
            sb_str = f", S21 worst outside passband = {s21_worst_sb} dB" if s21_worst_sb is not None else ""
            summary = (
                f"BPF metrics — passband {_format_frequency(pb_low_hz)} to {_format_frequency(pb_high_hz)}: "
                f"S11 at centre = {s11_center} dB (worst in band = {s11_worst_pb} dB), "
                f"S21 at centre = {s21_center} dB (worst in band = {s21_worst_pb} dB)"
                f"{sb_str}"
            )
        else:
            s21_center = s21_worst_pb = s21_worst_sb = None
            summary = (
                f"BPF metrics — passband {_format_frequency(pb_low_hz)} to {_format_frequency(pb_high_hz)}: "
                f"S11 at centre = {s11_center} dB (worst in band = {s11_worst_pb} dB)"
            )

        return {
            "freq_at_target": freq_center,
            "s11_at_target_db": s11_center,
            "s11_worst_pb_db": s11_worst_pb,
            "s21_at_target_db": s21_center,
            "s21_worst_pb_db": s21_worst_pb,
            "s21_worst_sb_db": s21_worst_sb,
            "freq_2_at_target": None,
            "s21_at_target_2_db": None,
            "summary": summary,
        }

    # --- Original single/dual spot-check mode (LPF / HPF) ---
    idx = (freq_col - target_freq_hz).abs().idxmin()
    freq_at_target   = float(freq_col.iloc[idx])
    s11_at_target_db = round(float(simulation_data.iloc[idx, 1]), 1)

    s21_at_target_db = None
    freq_2_at_target = None
    s21_at_target_2_db = None

    if has_s21:
        s21_at_target_db = round(float(simulation_data.iloc[idx, 3]), 1)
        if target_freq_hz_2 is not None:
            idx_2 = (freq_col - target_freq_hz_2).abs().idxmin()
            freq_2_at_target = float(freq_col.iloc[idx_2])
            s21_at_target_2_db = round(float(simulation_data.iloc[idx_2, 3]), 1)

    if s21_at_target_db is not None and s21_at_target_2_db is not None:
        summary = (
            f"Filter metrics at {_format_frequency(freq_at_target)}: "
            f"S11 = {s11_at_target_db} dB, S21 = {s21_at_target_db} dB. "
            f"S21 at {_format_frequency(freq_2_at_target)} = {s21_at_target_2_db} dB"
        )
    elif s21_at_target_db is not None:
        summary = (
            f"Filter metrics at {_format_frequency(freq_at_target)}: "
            f"S11 = {s11_at_target_db} dB, S21 = {s21_at_target_db} dB"
        )
    else:
        summary = (
            f"Filter metrics at {_format_frequency(freq_at_target)}: "
            f"S11 = {s11_at_target_db} dB"
        )

    return {
        "freq_at_target": freq_at_target,
        "s11_at_target_db": s11_at_target_db,
        "s21_at_target_db": s21_at_target_db,
        "freq_2_at_target": freq_2_at_target,
        "s21_at_target_2_db": s21_at_target_2_db,
        "summary": summary,
    }


# ---------------------------------------------------------------------------
# matching_network_helper
# ---------------------------------------------------------------------------

def matching_network_helper(simulation_data, target_freq_hz):
    """Extract matching network metrics (S11 and S21) at the target frequency."""
    if simulation_data is None or simulation_data.empty or simulation_data.shape[1] < 2:
        return None

    freq_col = simulation_data.iloc[:, 0]
    idx = (freq_col - target_freq_hz).abs().idxmin()
    freq_at_target = float(freq_col.iloc[idx])
    s11_at_target_db = round(float(simulation_data.iloc[idx, 1]), 1)

    has_s21 = simulation_data.shape[1] > 3
    s21_at_target_db = round(float(simulation_data.iloc[idx, 3]), 1) if has_s21 else None

    if s21_at_target_db is not None:
        summary = (
            f"Matching network at {_format_frequency(freq_at_target)}: "
            f"S11 = {s11_at_target_db} dB, S21 = {s21_at_target_db} dB."
        )
    else:
        summary = (
            f"Matching network at {_format_frequency(freq_at_target)}: "
            f"S11 = {s11_at_target_db} dB."
        )

    return {
        "freq_at_target": freq_at_target,
        "s11_at_target_db": s11_at_target_db,
        "s21_at_target_db": s21_at_target_db,
        "summary": summary,
    }


# ---------------------------------------------------------------------------
# plot_simulation_data
# ---------------------------------------------------------------------------

def _normalize_sparam_name(name: str) -> str:
    return re.sub(r"\s+", "", str(name)).upper()


def plot_simulation_data(simulation_data, output_path=None, title=None, selected_sparams=None):
    """Create a PNG plot of frequency vs S-parameter columns."""
    import matplotlib.pyplot as plt

    if simulation_data is None or simulation_data.empty or simulation_data.shape[1] < 2:
        return None

    x_axis = simulation_data.columns[0]
    for column_name in simulation_data.columns:
        if "freq" in str(column_name).lower():
            x_axis = column_name
            break

    sparam_pattern = re.compile(r"^S\[\s*\d+\s*,\s*\d+\s*\]$", re.IGNORECASE)
    available_sparam_cols = [
        c for c in simulation_data.columns
        if c != x_axis and sparam_pattern.match(str(c))
    ]

    if selected_sparams:
        selected_set = {_normalize_sparam_name(p) for p in selected_sparams}
        y_cols = [c for c in available_sparam_cols if _normalize_sparam_name(c) in selected_set]
    else:
        y_cols = available_sparam_cols

    if not y_cols:
        return None

    plot_data = simulation_data.copy()
    plot_data[x_axis] = plot_data[x_axis].astype(float) / 1e9

    fig, ax = plt.subplots(figsize=(10, 6))
    for y_col in y_cols:
        ax.plot(plot_data[x_axis], plot_data[y_col], label=str(y_col))

    ax.set_title(title or "Simulation Results")
    ax.set_xlabel("Frequency (GHz)")
    ax.set_ylabel("Magnitude (dB)")
    ax.grid(True)
    ax.legend()
    plt.tight_layout()

    if output_path is None:
        output_path = os.path.join(os.getcwd(), "simulation_results.png")

    fig.savefig(output_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    return output_path


# ---------------------------------------------------------------------------
# ads_netlist_to_graph  (optional utility)
# ---------------------------------------------------------------------------

def ads_netlist_to_graph(file_path, include_net_nodes=True):
    """
    Parse an ADS netlist file and return a NetworkX graph.

    Nodes:
      - component nodes (type='component', label=instance_name)
      - net nodes      (type='net',       label=net_name)  if include_net_nodes
      - port nodes     (type='port',      label=port_name)

    Edges connect components to their nets.
    """
    import networkx as nx

    graph = nx.Graph()

    with open(file_path, "r", encoding="utf-8") as fh:
        lines = fh.readlines()

    component_pattern = re.compile(
        r"^(?P<type>\w+):(?P<name>\w+)\s+(?P<nets>[\w\s]+?)(?:\s+\w+=.*)?$"
    )

    for line in lines:
        line = line.strip()
        if not line or line.startswith(";"):
            continue

        match = component_pattern.match(line)
        if not match:
            continue

        comp_type = match.group("type")
        comp_name = match.group("name")
        nets_str = match.group("nets").split()

        node_type = "port" if comp_type.lower() == "port" else "component"
        graph.add_node(comp_name, type=node_type, component_type=comp_type, label=comp_name)

        for net in nets_str:
            if net == "0":
                net = "GND"
            if include_net_nodes:
                if not graph.has_node(net):
                    graph.add_node(net, type="net", label=net)
                graph.add_edge(comp_name, net)

    return graph


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    start_time = time.time()

    # Resolve project root (two levels up from this script: ads_runner/ → EDA_Interface/ → LaMDA-RF/)
    script_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(os.path.dirname(script_dir))

    # --- Command-line arguments ---
    if len(sys.argv) > 1:
        netlist_path = sys.argv[1]
    else:
        datafiles_dir = os.path.join(project_root, "NetlistFiles")
        netlist_files = glob.glob(os.path.join(datafiles_dir, "netlist_*.txt"))
        netlist_path = (
            max(netlist_files, key=os.path.getmtime)
            if netlist_files
            else os.path.join(datafiles_dir, "netlist_1.txt")
        )

    print(f"Using netlist: {netlist_path}")

    try:
        design_type = int(sys.argv[2]) if len(sys.argv) > 2 else 0
    except ValueError:
        design_type = 0

    try:
        target_freq_hz = float(sys.argv[3]) if len(sys.argv) > 3 else 2.4e9
    except ValueError:
        target_freq_hz = 2.4e9

    try:
        target_freq_hz_2 = float(sys.argv[4]) if len(sys.argv) > 4 and sys.argv[4] else None
    except ValueError:
        target_freq_hz_2 = None

    is_bandpass = len(sys.argv) > 5 and sys.argv[5].lower() in ("1", "true", "yes")

    # --- Workspace / cell naming ---
    workspace_name = "my_workspace_1"
    cell_name = "first_cell"
    iteration_number = "1"
    netlist_match = re.search(r"netlist_(\d+)\.txt$", os.path.basename(netlist_path))
    if netlist_match:
        iteration_number = netlist_match.group(1)
        workspace_name = f"my_workspace_{iteration_number}"

    # --- Run pipeline ---
    workspace, lib_name, output_dir, library = create_workspace_and_library(workspace_name, cell_name)
    design = create_schematic(lib_name, cell_name)

    with open(netlist_path, "r", encoding="utf-8") as fh:
        netlist = fh.read()

    match = re.search(r'TopDesignName="[^:]*:([^:]*):[^"]*"', netlist)
    extracted_name = match.group(1) if match else cell_name

    simulation_data = simulate_netlist(netlist, output_dir, extracted_name)

    # --- Plot ---
    logs_dir = os.path.join(project_root, "Outputs")
    os.makedirs(logs_dir, exist_ok=True)
    png_results_dir = os.path.join(logs_dir, "Simulation_PNG_Results")
    os.makedirs(png_results_dir, exist_ok=True)
    plot_path = os.path.join(png_results_dir, f"iteration_{iteration_number}_results.png")

    plot_sparams_by_design_type = {
        1: ["S[1,1]"],
        2: ["S[1,1]", "S[2,1]", "S[3,1]", "S[4,1]"],
        3: ["S[1,1]", "S[2,1]"],
        4: ["S[1,1]", "S[2,1]"],
    }
    selected_sparams = plot_sparams_by_design_type.get(design_type)

    plot_simulation_data(
        simulation_data,
        output_path=plot_path,
        title=f"{extracted_name} Simulation Results",
        selected_sparams=selected_sparams,
    )

    # --- Post-processing ---
    antenna_results = coupler_results = filter_results = matching_network_results = None
    if design_type == 1:
        antenna_results = antenna_helper(simulation_data, target_freq_hz)
    elif design_type == 2:
        coupler_results = coupler_helper(simulation_data, target_freq_hz)
    elif design_type == 3:
        filter_results = filter_helper(simulation_data, target_freq_hz, target_freq_hz_2, is_bandpass=is_bandpass)
    elif design_type == 4:
        matching_network_results = matching_network_helper(simulation_data, target_freq_hz)

    elapsed_time = time.time() - start_time
    print("\n--- Simulation Time ---")
    print(f"Time taken: {elapsed_time:.2f} seconds")
    print("-----------------------")
    print("\n--- Simulation Results ---")
    if antenna_results:
        print(antenna_results["summary"])
    if coupler_results:
        print(coupler_results["summary"])
    if filter_results:
        print(filter_results["summary"])
    if matching_network_results:
        print(matching_network_results["summary"])
    print("-----------------------\n")
