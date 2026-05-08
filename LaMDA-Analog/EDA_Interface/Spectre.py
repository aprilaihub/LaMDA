"""Spectre interface for analog design automation."""

import os
import shutil
import subprocess
from pathlib import Path

from EDA_Interface.psf_parser import parse_psf_results


class SpectreInterface:
    """Unified interface for managing Spectre operations."""

    def __init__(self):
        self.spectre_path = os.getenv("SPECTRE_PATH") or shutil.which("spectre")
        self._validate_spectre_path()

    def _validate_spectre_path(self):
        """Validate that Spectre path is available and executable."""
        if not self.spectre_path:
            raise ValueError(
                "SPECTRE_PATH is not set and 'spectre' was not found in PATH.\n"
                "Set SPECTRE_PATH, e.g.: export SPECTRE_PATH=/path/to/spectre"
            )

        if not os.path.exists(self.spectre_path):
            raise ValueError(f"Spectre binary '{self.spectre_path}' does not exist.")

        if not os.access(self.spectre_path, os.X_OK):
            raise ValueError(f"Spectre binary '{self.spectre_path}' is not executable.")

    def run_spectre(self, netlist_path, log_path, raw_dir, fmt="psfascii", allow_fail=False):
        """Run Spectre on a netlist and write logs/raw output."""
        netlist = Path(netlist_path)
        log_file = Path(log_path)
        raw_path = Path(raw_dir)

        log_file.parent.mkdir(parents=True, exist_ok=True)
        raw_path.mkdir(parents=True, exist_ok=True)

        cmd = [
            self.spectre_path,
            str(netlist),
            "+log",
            str(log_file),
            "-raw",
            str(raw_path),
            "-format",
            fmt,
        ]

        result = subprocess.run(cmd, text=True, capture_output=True)
        if result.returncode != 0 and not allow_fail:
            raise RuntimeError(
                f"Spectre failed ({result.returncode}).\n"
                f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
            )
        return result

    def tech_bind(self, raw_scs_path, bound_scs_path, tech_cfg_path):
        """Run technology binder to inject include/model mappings."""
        script_path = Path(__file__).with_name("tech_binder.py")
        cmd = [
            "python3",
            str(script_path),
            "--config",
            str(tech_cfg_path),
            str(raw_scs_path),
            str(bound_scs_path),
        ]
        result = subprocess.run(cmd, text=True, capture_output=True)
        if result.returncode != 0:
            raise RuntimeError(
                f"tech_binder failed ({result.returncode}).\n"
                f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
            )
        return Path(bound_scs_path)

    def sanitize_inverter(self, in_scs_path, out_scs_path):
        """Apply inverter-specific netlist cleanup and standard analyses."""
        script_path = Path(__file__).with_name("sanitize_inverter_netlist.py")
        cmd = ["python3", str(script_path), str(in_scs_path), str(out_scs_path)]
        result = subprocess.run(cmd, text=True, capture_output=True)
        if result.returncode != 0:
            raise RuntimeError(
                f"sanitize_inverter_netlist failed ({result.returncode}).\n"
                f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
            )
        return Path(out_scs_path)

    def parse_results(self, raw_dir):
        """Parse PSF outputs and return extracted metrics."""
        return parse_psf_results(raw_dir)
