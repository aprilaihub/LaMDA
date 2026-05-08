"""
ADS Interface for RF circuit design automation.
Handles subprocess execution of the ADS Python environment.
"""

import os
import re
import subprocess
from pathlib import Path


class ADSInterface:
    """Unified interface for managing ADS simulation operations."""

    def __init__(self):
        self.hpeesof_dir = os.getenv("HPEESOF_DIR")
        self._validate_ads_path()
        self.ads_venv_python = os.getenv(
            "ADS_VENV_PYTHON",
            str(Path.home() / "ads2026_venv" / "Scripts" / "python.exe"),
        )
        self._runner_script = str(
            Path(__file__).parent / "ads_runner" / "main.py"
        )

    def _validate_ads_path(self):
        """Validate that HPEESOF_DIR is set and the directory exists."""
        if self.hpeesof_dir is None:
            raise ValueError(
                "HPEESOF_DIR environment variable not found.\n"
                "Please set it in your .env file, e.g.:\n"
                r"  HPEESOF_DIR=C:\Program Files\Keysight\ADS2026_Update1.2"
            )
        if not os.path.isdir(self.hpeesof_dir):
            raise ValueError(
                f"HPEESOF_DIR '{self.hpeesof_dir}' does not exist.\n"
                "Please verify your HPEESOF_DIR environment variable."
            )

    def run_ads(
        self,
        netlist_path,
        netlist_index,
        design_type=0,
        target_freq_hz=2.4e9,
        target_freq_hz_2=None,
        cell_name="first_cell",
        project_root=None,
        verbose=False,
    ):
        """
        Run the ADS simulation subprocess for a given netlist.

        Args:
            netlist_path (str): Full path to the netlist file.
            netlist_index (int): Index of the current netlist (used for log naming).
            design_type (int): 1=antenna, 2=coupler, 3=filter, 0=unknown.
            target_freq_hz (float): Primary target frequency in Hz.
            target_freq_hz_2 (float|None): Optional second target frequency in Hz.
            cell_name (str): ADS cell name for the schematic.
            project_root (str|None): Project root for workspace and log directories.
                                     Defaults to the LaMDA-RF root directory.
            verbose (bool): If True, stream subprocess output to stdout.

        Returns:
            dict: {'summary': str, 'success': bool}
        """
        if project_root is None:
            project_root = str(Path(__file__).parent.parent)

        ads_workspaces_dir = os.path.join(project_root, "ADS_Workspaces")
        os.makedirs(ads_workspaces_dir, exist_ok=True)

        log_dir = os.path.join(project_root, "Outputs", "ADS_Outputs")
        os.makedirs(log_dir, exist_ok=True)
        output_log = os.path.join(log_dir, f"ads_output_{netlist_index}.log")

        env = os.environ.copy()
        env["HOME"] = ads_workspaces_dir
        env["PYTHONHOME"] = ""
        env["HPEESOF_DIR"] = self.hpeesof_dir

        cmd = [
            self.ads_venv_python,
            self._runner_script,
            netlist_path,
            str(design_type),
            str(target_freq_hz),
            str(target_freq_hz_2) if target_freq_hz_2 is not None else "",
        ]

        if verbose:
            print(f"\n{'='*60}")
            print("Running ADS Simulation")
            print(f"{'='*60}")
            print(f"Netlist: {netlist_path}")
            print(f"Design type: {design_type}  Freq: {target_freq_hz} Hz")
            print(f"Log: {output_log}")
            print(f"{'='*60}\n")

        try:
            with open(output_log, "w", encoding="utf-8") as f:
                subprocess.run(
                    cmd,
                    stdout=f,
                    stderr=subprocess.STDOUT,
                    timeout=300,
                    cwd=project_root,
                    env=env,
                    check=False,
                )
            with open(output_log, "r", encoding="utf-8") as f:
                output = f.read()

        except subprocess.TimeoutExpired:
            return {"summary": "ADS simulation timed out after 300 seconds.", "success": False}
        except FileNotFoundError:
            return {
                "summary": (
                    f"ADS venv Python not found at '{self.ads_venv_python}'.\n"
                    "Set the ADS_VENV_PYTHON environment variable to the correct path."
                ),
                "success": False,
            }

        return self._parse_output(output)

    def _parse_output(self, output):
        """Parse simulation output to extract the results summary."""
        output_lines = output.strip().split("\n")
        sim_start = -1
        sim_end = -1

        for i, line in enumerate(output_lines):
            if "--- Simulation Results ---" in line:
                sim_start = i
            elif sim_start != -1 and "-----------------------" in line:
                sim_end = i
                break

        if sim_start != -1 and sim_end != -1:
            sim_lines = output_lines[sim_start + 1 : sim_end]
            summary = "\n".join(line.strip() for line in sim_lines if line.strip())
            return {"summary": summary, "success": True}

        error_message = None
        for i, line in enumerate(output_lines):
            if re.search(r"\w+Error:", line):
                if i < len(output_lines) - 1:
                    error_message = line.strip() + " " + output_lines[i + 1].strip()
                else:
                    error_message = line.strip()
        if error_message is not None:
            return {"summary": error_message, "success": False}

        return {"summary": output.strip()[-500:] if output.strip() else "No output.", "success": False}
