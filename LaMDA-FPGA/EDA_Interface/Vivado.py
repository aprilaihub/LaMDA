"""
Vivado Interface for FPGA design automation.
Handles TCL script generation and Vivado execution.
"""

import os
import subprocess
from pathlib import Path


class VivadoInterface:
    """Unified interface for managing Vivado operations."""
    
    def __init__(self):
        self.vivado_path = os.getenv("VIVADO_PATH")
        self._validate_vivado_path()
    
    def _validate_vivado_path(self):
        """Validate that Vivado path is set, exists, and is executable."""
        if self.vivado_path is None:
            raise ValueError(
                "VIVADO_PATH environment variable not found.\n"
                "Please set it by running:\n"
                "  export VIVADO_PATH=/path/to/vivado/bin/vivado\n"
                "Common locations:\n"
                "  - /tools/Xilinx/Vivado/<version>/bin/vivado\n"
                "  - /opt/Xilinx/Vivado/<version>/bin/vivado"
            )
        
        if not os.path.exists(self.vivado_path):
            raise ValueError(
                f"Vivado path '{self.vivado_path}' does not exist.\n"
                "Please verify your VIVADO_PATH environment variable points to the vivado executable."
            )
        
        if not os.access(self.vivado_path, os.X_OK):
            raise ValueError(
                f"Vivado path '{self.vivado_path}' is not executable.\n"
                "Please check file permissions: chmod +x {self.vivado_path}"
            )
    
    def generate_tcl(self, tcl_file, project_name, project_dir, design_files_dir, fpga_part, mode):
        """Generate a TCL script for Vivado based on the mode."""
        project_dir = Path(project_dir).as_posix()
        design_files_dir = Path(design_files_dir).as_posix()
        tcl_content = self._get_tcl_content(project_name, project_dir, design_files_dir, fpga_part, mode)
        
        with open(tcl_file, "w") as f:
            f.write(tcl_content)
    
    def _get_tcl_content(self, project_name, project_dir, design_files_dir, fpga_part, mode):
        """Return the TCL content based on the mode."""
        if mode == "sim":
            return self._get_simulation_tcl(project_name, project_dir, design_files_dir, fpga_part)
        elif mode == "synth":
            return self._get_synthesis_tcl(project_name, project_dir, fpga_part)
        elif mode == "impl":
            return self._get_implementation_tcl(project_name, project_dir, fpga_part)
        else:
            raise ValueError(f"Unsupported mode: {mode}. Use 'sim', 'synth', or 'impl'.")
    
    def _get_simulation_tcl(self, project_name, project_dir, design_files_dir, fpga_part):
        """Generate TCL script for simulation."""
        return f"""\
            # Define project variables
            set project_name "{project_name}"
            set project_dir "{project_dir}"
            set design_files_dir "{design_files_dir}"
            set report_dir "reports"
            set part "{fpga_part}"
            set top_module "{project_name}"
            set tb_module "{project_name}_tb"

            # Create a directory for the project if it doesn't exist
            file mkdir $project_dir
            file mkdir [file join $project_dir $report_dir]

            # Start Vivado in non-GUI (batch) mode
            create_project $project_name $project_dir -part $part -force

            # Add HDL source files using absolute paths
            add_files [file join $design_files_dir ${{project_name}}.v]
            add_files -fileset sim_1 [file join $design_files_dir ${{project_name}}_tb.v]

            # Add constraints if they exist
            set xdc_file [file join $design_files_dir ${{project_name}}.xdc]
            if {{[file exists $xdc_file]}} {{
                add_files -fileset constrs_1 $xdc_file
            }}

            # Set the top module for simulation
            set_property top $tb_module [get_filesets sim_1]

            # Launch the simulation (behavioral)
            launch_simulation -mode behavioral

            # Exit Vivado
            exit
            """
    
    def _get_synthesis_tcl(self, project_name, project_dir, fpga_part):
        """Generate TCL script for synthesis."""
        return f"""\
            # Define project variables
            set project_name "{project_name}"
            set project_dir "{project_dir}"
            set report_dir "reports"
            set part "{fpga_part}"
            set top_module "{project_name}"

            # Create report directories
            file mkdir [file join $project_dir $report_dir]
            file mkdir [file join $project_dir $report_dir synthesis]

            # Open the existing project
            open_project $project_dir/$project_name.xpr

            # Run synthesis
            synth_design -top $top_module

            # Save the synthesis checkpoint
            write_checkpoint -force $project_dir/synth.dcp

            # Generate reports
            report_timing -file $project_dir/reports/synthesis/timing_report.txt
            report_utilization -file $project_dir/reports/synthesis/utilization_report.txt

            # Exit Vivado
            exit
            """
    
    def _get_implementation_tcl(self, project_name, project_dir, fpga_part):
        """Generate TCL script for implementation."""
        return f"""\
            # Define project variables
            set project_name "{project_name}"
            set project_dir "{project_dir}"
            set report_dir "reports"
            set part "{fpga_part}"
            set top_module "{project_name}"

            # Create report directories
            file mkdir [file join $project_dir $report_dir]
            file mkdir [file join $project_dir $report_dir implementation]

            # Open the existing project
            open_project $project_dir/$project_name.xpr

            # Open the synthesis checkpoint
            open_checkpoint $project_dir/synth.dcp

            # Run implementation
            opt_design
            place_design
            route_design

            # Save the implementation checkpoint
            write_checkpoint -force $project_dir/impl.dcp

            # Generate reports
            report_timing -file $project_dir/reports/implementation/timing_report.txt
            report_utilization -file $project_dir/reports/implementation/utilization_report.txt
            report_power -file $project_dir/reports/implementation/power_report.txt

            # Exit Vivado
            exit
            """
    
    def run_vivado(self, project_name, project_dir, log_path, design_files_dir, fpga_part, mode, verbose=False):
        """Run Vivado with the generated TCL script."""
        # Ensure directories exist
        os.makedirs(project_dir, exist_ok=True)
        os.makedirs(log_path, exist_ok=True)
        
        tcl_file = os.path.join(log_path, f"vivado_{mode}_{project_name}.tcl")
        self.generate_tcl(tcl_file, project_name, project_dir, design_files_dir, fpga_part, mode)
        
        log_file = os.path.join(log_path, f"{project_name}_{mode}.log")
        journal_file = os.path.join(log_path, f"{project_name}_{mode}.jou")
        
        command = [
            self.vivado_path,
            "-mode", "batch",
            "-source", tcl_file,
            "-log", log_file,
            "-journal", journal_file,
        ]
        
        try:
            if verbose:
                print(f"\n{'='*60}")
                print(f"Running Vivado {mode.upper()}")
                print(f"{'='*60}")
                print(f"Command: {' '.join(command)}")
                print(f"TCL Script: {tcl_file}")
                print(f"Log File: {log_file}")
                print(f"{'='*60}\n")
            
            # Run with real-time output streaming
            process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                universal_newlines=True
            )
            
            # Stream output in real-time
            for line in process.stdout:
                print(line, end='')
            
            # Wait for process to complete
            return_code = process.wait()
            
            if return_code != 0:
                raise subprocess.CalledProcessError(return_code, command)
            
            if verbose:
                print(f"\n{'='*60}")
                self._print_success_message(mode, log_path, project_dir)
                print(f"{'='*60}\n")
                    
        except subprocess.CalledProcessError as err:
            print(f"\n{'='*60}")
            print(f"ERROR: Vivado {mode.capitalize()} failed with error code {err.returncode}")
            print(f"Check log file: {log_file}")
            print(f"{'='*60}\n")
            raise
        except FileNotFoundError:
            print(f"\nERROR: Vivado executable not found at: {self.vivado_path}")
            print("Please verify your VIVADO_PATH environment variable.")
            raise
    
    def _print_success_message(self, mode, log_path, project_dir):
        """Print success messages based on the mode."""
        print(f"Vivado {mode.capitalize()} completed successfully.\nLogs are saved in {log_path}.")
        
        if mode == "synth":
            print(f"Synthesis reports are saved in {project_dir}/reports/synthesis/")
        elif mode == "impl":
            print(f"Implementation reports are saved in {project_dir}/reports/implementation/")
