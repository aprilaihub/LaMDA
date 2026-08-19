"""
ResBench Analysis utilities for experiment results processing and visualization.
Handles report parsing, results logging, CSV conversion, and plotting.
"""

import json
import csv
import os
import re
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt


class InfEncoder(json.JSONEncoder):
    """Custom JSON encoder that converts float('inf') to 'inf' string."""
    def encode(self, obj):
        if isinstance(obj, float):
            if obj == float('inf'):
                return '"inf"'
            elif obj == float('-inf'):
                return '"-inf"'
            elif obj != obj:  # NaN check
                return '"NaN"'
        return super().encode(obj)
    
    def iterencode(self, obj, _one_shot=False):
        """Encode object, converting infinity values to strings."""
        for chunk in super().iterencode(obj, _one_shot):
            chunk = chunk.replace('Infinity', '"inf"')
            chunk = chunk.replace('-Infinity', '"-inf"')
            chunk = chunk.replace('NaN', '"NaN"')
            yield chunk


class ReportParser:
    """Parses various types of EDA tool reports."""
    
    @staticmethod
    def detect_report_type(content):
        """Detect the type of report from its content."""
        if "Total On-Chip Power" in content:
            return "power"
        elif "Slice LUTs" in content or "LUT as Logic" in content:
            return "utilization"
        elif "Slack" in content and "Data Path Delay" in content:
            return "timing"
        elif "Vivado" in content and "INFO:" in content:
            return "log"
        else:
            return "unknown"
    
    @staticmethod
    def parse_power_report(content):
        """Parse power report and extract power metrics."""
        power_data = {"type": "power"}
        total_match = re.search(r'Total On-Chip Power.*?([\d\.]+)', content)
        dynamic_match = re.search(r'Dynamic.*?([\d\.]+)', content)
        static_match = re.search(r'Static Power.*?([\d\.]+)', content)
        power_data['Total'] = float(total_match.group(1)) if total_match else None
        power_data['Dynamic'] = float(dynamic_match.group(1)) if dynamic_match else None
        power_data['Static'] = float(static_match.group(1)) if static_match else None
        return power_data
    
    @staticmethod
    def parse_utilization_report(content):
        """Parse utilization report and extract resource usage."""
        result = {"type": "utilization"}
        
        def extract_resource(content, name):
            pattern = rf'{name}\s*\|\s*(\d+)\s*\|\s*\d+\s*\|\s*\d+\s*\|\s*(\d+)\s*\|\s*([<>]?\d+\.\d+)\s*\|'
            match = re.search(pattern, content)
            if match:
                return {
                    "used": int(match.group(1)),
                    "available": int(match.group(2)),
                    "utilization_percentage": float(match.group(3).replace("<", "0"))
                }
            else:
                return {"used": 0, "available": 0, "utilization_percentage": 0.0}
        
        resources = {
            "luts": extract_resource(content, "Slice LUTs"),
            "registers": extract_resource(content, "Slice Registers"),
            "bram": extract_resource(content, "Block RAM Tile"),
            "dsp": extract_resource(content, "DSPs"),
            "io": extract_resource(content, "Bonded IOB")
        }
        result["resources"] = resources
        return result
    
    @staticmethod
    def parse_timing_report(content):
        """Parse timing report and extract timing metrics."""
        result = {"type": "timing"}
        
        # Extract Slack
        slack_match = re.search(r'Slack\s*\((?:MET|VIOLATED)\)\s*:\s*(inf|[-\d.]+)', content, re.IGNORECASE)
        if not slack_match:
            slack_match = re.search(r'Slack\s*:\s*(inf|[-\d.]+)', content, re.IGNORECASE)
        if slack_match:
            slack_str = slack_match.group(1)
            if slack_str.lower() == "inf":
                result["slack"] = float("inf")
            else:
                result["slack"] = float(slack_str)
        else:
            result["slack"] = None
        
        # Extract Data Path Delay
        data_path_match = re.search(
            r'Data Path Delay:\s*([-\d.]+)ns\s*\(logic\s*([-\d.]+)ns\s*\(([\d.]+)%\)\s*route\s*([-\d.]+)ns\s*\(([\d.]+)%\)\)',
            content
        )
        if data_path_match:
            result["data_path_delay_ns"] = float(data_path_match.group(1))
            result["logic_delay_ns"] = float(data_path_match.group(2))
            result["logic_delay_percent"] = float(data_path_match.group(3))
            result["route_delay_ns"] = float(data_path_match.group(4))
            result["route_delay_percent"] = float(data_path_match.group(5))
        else:
            data_path_simple = re.search(r'Data Path Delay:\s*([-\d.]+)ns', content)
            if data_path_simple:
                result["data_path_delay_ns"] = float(data_path_simple.group(1))
            else:
                result["data_path_delay_ns"] = None
            result["logic_delay_ns"] = None
            result["logic_delay_percent"] = None
            result["route_delay_ns"] = None
            result["route_delay_percent"] = None
        
        return result
    
    @staticmethod
    def parse_log_file(content, severities=None):
        """Parse log file and extract errors and stage completion status."""
        result = {"type": "log"}
        if severities is None:
            severities = ["ERROR"]
        
        for severity in severities:
            key = severity.lower() + "s"
            lines = re.findall(rf'^.*?{severity}.*$', content, re.MULTILINE)
            extracted = []
            for line in lines:
                match = re.search(rf'{severity}\s*[:\-]?\s*(.*)', line)
                if match:
                    extracted.append(match.group(1).strip())
                else:
                    extracted.append(line.strip())
            result[key] = extracted
        
        stage_status = {
            "synthesis_completed": False,
            "implementation_completed": False,
            "bitstream_generated": False
        }
        if "synth_design completed successfully" in content or "Finished Synth" in content:
            stage_status["synthesis_completed"] = True
        if "place_design completed successfully" in content and "route_design completed successfully" in content:
            stage_status["implementation_completed"] = True
        if "write_bitstream completed successfully" in content:
            stage_status["bitstream_generated"] = True
        result["stage_status"] = {stage: True for stage, done in stage_status.items() if done}
        
        return result
    
    def batch_parse(self, directory, log_severities=None):
        """Parse all report files in a directory."""
        results = {}
        for file in os.listdir(directory):
            if file.endswith(".txt") or file.endswith(".log"):
                path = os.path.join(directory, file)
                with open(path, 'r', errors="ignore") as f:
                    content = f.read()
                    if file.endswith(".log"):
                        results[file] = self.parse_log_file(content, severities=log_severities)
                        continue
                    report_type = self.detect_report_type(content)
                    if report_type == "power":
                        results[file] = self.parse_power_report(content)
                    elif report_type == "utilization":
                        results[file] = self.parse_utilization_report(content)
                    elif report_type == "timing":
                        results[file] = self.parse_timing_report(content)
                    else:
                        continue
        return results


class ResultsLogger:
    """Handles logging of experiment results to JSON format."""
    
    def __init__(self, problems_json_path, output_json_path):
        self.problems_json_path = problems_json_path
        self.output_json_path = output_json_path
        self.parser = ReportParser()
    
    def log_results(self, llm_model, token_count, llm_time, eda_time, design_id,
                    power_report_path=None, utilization_report_path=None, 
                    timing_report_path=None, synthesis_log_path=None, 
                    implementation_log_path=None, sim_passed=False, verilog_path=None):
        """Log module ID, verification status, and design metrics to JSON."""
        # Load problems.json
        with open(self.problems_json_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        # Find the design and create module_info
        module_info = self._extract_module_info(data, design_id, llm_model, token_count, 
                                                  llm_time, eda_time, sim_passed, verilog_path)
        
        if not module_info or design_id is None:
            raise ValueError(f"No design found in {self.problems_json_path}.")
        
        # Process metrics based on simulation result
        if not sim_passed:
            self._handle_simulation_failure(module_info)
        else:
            self._process_reports(module_info, power_report_path, utilization_report_path,
                                  timing_report_path, synthesis_log_path, implementation_log_path)
        
        # Check constraints
        self._check_constraints(module_info, data)
        
        # Add error descriptions
        self._add_error_descriptions(module_info, sim_passed, synthesis_log_path, implementation_log_path)
        
        # Append to existing results
        self._append_and_save(module_info)

        return module_info
    
    def _extract_module_info(self, data, design_id, llm_model, token_count, 
                             llm_time, eda_time, sim_passed, verilog_path):
        """Extract module information from problems.json."""
        module_info = None
        if isinstance(data, dict):
            for designs_list in data.values():
                if isinstance(designs_list, list):
                    for item in designs_list:
                        if isinstance(item, dict) and item.get("ID") == design_id:
                            module_info = {
                                "ID": item.get("ID"),
                                "module": item.get("module"),
                                "Clock Constraint": item.get("Clock Constraint"),
                            }
                            if verilog_path and os.path.exists(verilog_path):
                                with open(verilog_path, 'r', encoding='utf-8', errors='ignore') as vf:
                                    verilog_code = vf.read()
                                module_info["verilog_code"] = verilog_code
                            module_info.update({
                                "llm_model": llm_model,
                                "token_count": token_count,
                                "llm_time [s]": llm_time,
                                "eda_time [s]": eda_time,
                                "Functional Verification": "PASS" if sim_passed else "FAIL"
                            })
                            break
                if module_info:
                    break
        return module_info
    
    def _handle_simulation_failure(self, module_info):
        """Set all metrics to inf/fail when simulation fails."""
        module_info["Synthesis"] = "FAIL"
        module_info["Implementation"] = "FAIL"
        module_info["LUTs"] = float("inf")
        module_info["Registers"] = float("inf")
        module_info["BRAMs"] = float("inf")
        module_info["DSPs"] = float("inf")
        module_info["IO"] = float("inf")
        module_info["Slack [ns]"] = float("inf")
        module_info["Data Path Delay [ns]"] = float("inf")
        module_info["Logic Delay [ns]"] = float("inf")
        module_info["Route Delay [ns]"] = float("inf")
        module_info["Total Power [W]"] = float("inf")
        module_info["Dynamic Power [W]"] = float("inf")
        module_info["Static Power [W]"] = float("inf")
        module_info["LUTConstraint"] = "FAIL"
        module_info["DelayConstraint"] = "FAIL"
    
    def _process_reports(self, module_info, power_report_path, utilization_report_path,
                         timing_report_path, synthesis_log_path, implementation_log_path):
        """Process all report files and extract metrics."""
        # Process synthesis log
        if synthesis_log_path and os.path.exists(synthesis_log_path):
            with open(synthesis_log_path, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read()
                errors = re.findall(r'^.*?ERROR.*$', content, re.MULTILINE)
                module_info["Synthesis"] = "FAIL" if errors else "PASS"
        
        # Process implementation log
        if implementation_log_path and os.path.exists(implementation_log_path):
            with open(implementation_log_path, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read()
                errors = re.findall(r'^.*?ERROR.*$', content, re.MULTILINE)
                module_info["Implementation"] = "FAIL" if errors else "PASS"
        
        # Process utilization report
        if utilization_report_path and os.path.exists(utilization_report_path):
            with open(utilization_report_path, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read()
                util_data = self.parser.parse_utilization_report(content).get("resources", {})
                module_info["LUTs"] = util_data.get("luts", {}).get("used")
                module_info["Registers"] = util_data.get("registers", {}).get("used")
                module_info["BRAMs"] = util_data.get("bram", {}).get("used")
                module_info["DSPs"] = util_data.get("dsp", {}).get("used")
                module_info["IO"] = util_data.get("io", {}).get("used")
        
        # Process timing report
        if timing_report_path and os.path.exists(timing_report_path):
            with open(timing_report_path, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read()
                timing_data = self.parser.parse_timing_report(content)
                module_info["Slack [ns]"] = timing_data.get("slack")
                module_info["Data Path Delay [ns]"] = timing_data.get("data_path_delay_ns")
                module_info["Logic Delay [ns]"] = timing_data.get("logic_delay_ns")
                module_info["Route Delay [ns]"] = timing_data.get("route_delay_ns")
        
        # Process power report
        if power_report_path and os.path.exists(power_report_path):
            with open(power_report_path, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read()
                power_data = self.parser.parse_power_report(content)
                module_info["Total Power [W]"] = power_data.get("Total")
                module_info["Dynamic Power [W]"] = power_data.get("Dynamic")
                module_info["Static Power [W]"] = power_data.get("Static")
    
    def _check_constraints(self, module_info, data):
        """Check LUT and delay/clock constraints."""
        # Constraints are only meaningful when all evaluation stages pass.
        fv_ok = str(module_info.get("Functional Verification", "")).upper() == "PASS"
        syn_ok = str(module_info.get("Synthesis", "")).upper() == "PASS"
        impl_ok = str(module_info.get("Implementation", "")).upper() == "PASS"

        if not (fv_ok and syn_ok and impl_ok):
            module_info["LUTConstraint"] = "FAIL"
            module_info["DelayConstraint"] = "FAIL"
            return

        lut_min = None
        delay_max = None
        
        # Find constraints for this design
        if isinstance(data, dict):
            for designs_list in data.values():
                if isinstance(designs_list, list):
                    for item in designs_list:
                        if isinstance(item, dict) and item.get("ID") == module_info.get("ID"):
                            lut_min = item.get("LUTmin")
                            delay_max = item.get("DelayMax [ns]")
                            break
                if lut_min is not None:
                    break
        
        # Check LUT constraint
        luts_used = module_info.get("LUTs")
        try:
            if lut_min is not None and luts_used is not None and float(luts_used) <= float(lut_min):
                module_info["LUTConstraint"] = "PASS"
            else:
                module_info["LUTConstraint"] = "FAIL"
        except Exception:
            module_info["LUTConstraint"] = "FAIL"
        
        # Check delay or clock constraint
        delay_val = module_info.get("Data Path Delay [ns]")
        slack_val = module_info.get("Slack [ns]")
        clock_constraint = module_info.get("Clock Constraint")
        
        try:
            if clock_constraint == "" or clock_constraint is None:
                if delay_max is not None and delay_val is not None and float(delay_val) <= float(delay_max):
                    module_info["DelayConstraint"] = "PASS"
                else:
                    module_info["DelayConstraint"] = "FAIL"
            else:
                if slack_val is not None and float(slack_val) >= 0:
                    module_info["DelayConstraint"] = "PASS"
                else:
                    module_info["DelayConstraint"] = "FAIL"
        except Exception:
            module_info["DelayConstraint"] = "FAIL"
    
    def _add_error_descriptions(self, module_info, sim_passed, synthesis_log_path, implementation_log_path):
        """Add error descriptions for failed stages."""
        if not sim_passed:
            verification_errors = []
            if synthesis_log_path and os.path.exists(synthesis_log_path):
                with open(synthesis_log_path, 'r', encoding='utf-8', errors='ignore') as f:
                    content = f.read()
                    verification_errors += re.findall(r'^.*?ERROR.*$', content, re.MULTILINE)
            if implementation_log_path and os.path.exists(implementation_log_path):
                with open(implementation_log_path, 'r', encoding='utf-8', errors='ignore') as f:
                    content = f.read()
                    verification_errors += re.findall(r'^.*?ERROR.*$', content, re.MULTILINE)
            if verification_errors:
                module_info["VerificationError"] = verification_errors
            else:
                module_info["VerificationError"] = "Functional verification failed."
        else:
            if synthesis_log_path and os.path.exists(synthesis_log_path):
                with open(synthesis_log_path, 'r', encoding='utf-8', errors='ignore') as f:
                    content = f.read()
                    errors = re.findall(r'^.*?ERROR.*$', content, re.MULTILINE)
                    if errors:
                        module_info["SynthesisError"] = errors
            if implementation_log_path and os.path.exists(implementation_log_path):
                with open(implementation_log_path, 'r', encoding='utf-8', errors='ignore') as f:
                    content = f.read()
                    errors = re.findall(r'^.*?ERROR.*$', content, re.MULTILINE)
                    if errors:
                        module_info["ImplementationError"] = errors
    
    def _append_and_save(self, module_info):
        """Append results to existing JSON file or create new one."""
        if os.path.exists(self.output_json_path):
            with open(self.output_json_path, 'r', encoding='utf-8') as f:
                try:
                    existing_results = json.load(f)
                    if not isinstance(existing_results, list):
                        existing_results = [existing_results]
                except json.JSONDecodeError:
                    existing_results = []
        else:
            existing_results = []
        
        existing_results.append(module_info)
        
        with open(self.output_json_path, 'w', encoding='utf-8') as f:
            json.dump(existing_results, f, indent=4, cls=InfEncoder)


class PostProcessor:
    """Handles conversion and compression of experiment results."""
    
    @staticmethod
    def json_to_csv(json_path, csv_path):
        """Convert JSON results to CSV format, excluding verilog_code field."""
        with open(json_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        if isinstance(data, dict):
            data = [data]
        
        if data:
            fieldnames = [k for k in data[0].keys() if k != "verilog_code"]
            for item in data[1:]:
                for k in item.keys():
                    if k != "verilog_code" and k not in fieldnames:
                        fieldnames.append(k)
        else:
            fieldnames = []
        
        with open(csv_path, 'w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for item in data:
                filtered_item = {k: v for k, v in item.items() if k != "verilog_code"}
                writer.writerow(filtered_item)
    
    @staticmethod
    def compress_csv(input_csv_path, output_csv_path):
        """Compress CSV by selecting best result per design ID and adding category."""
        df = pd.read_csv(input_csv_path)
        
        step_fields = ["Functional Verification", "Synthesis", "Implementation"]
        fieldnames = [col for col in df.columns if col != "Slack [ns]" and col not in step_fields]
        
        required_columns = ['ID', 'llm_time [s]', 'eda_time [s]', 'LUTs']
        for col in required_columns:
            if col not in df.columns:
                raise ValueError(f"Required column '{col}' not found in CSV.")
        
        if "category" not in fieldnames:
            id_index = fieldnames.index("ID") + 1
            fieldnames.insert(id_index, "category")
        
        pass_fields = [
            "Functional_Verification_Passed",
            "Synthesis_Passed",
            "Implementation_Passed"
        ]
        for pf in pass_fields:
            if pf not in fieldnames:
                fieldnames.append(pf)
        
        grouped = df.groupby('ID')
        summary_rows = []
        
        for id_val, group in grouped:
            lut_values = pd.to_numeric(group['LUTs'], errors='coerce')
            if lut_values.isnull().all():
                min_lut_idx = group.index[0]
            else:
                min_lut_idx = lut_values.idxmin()
            best_row = group.loc[min_lut_idx].to_dict()
            
            for field in ["Slack [ns]"] + step_fields:
                if field in best_row:
                    del best_row[field]
            
            best_row["category"] = PostProcessor._get_category(id_val)
            
            fv_passed = (group["Functional Verification"].astype(str).str.upper() == "PASS").sum() if "Functional Verification" in group else 0
            syn_passed = (group["Synthesis"].astype(str).str.upper() == "PASS").sum() if "Synthesis" in group else 0
            impl_passed = (group["Implementation"].astype(str).str.upper() == "PASS").sum() if "Implementation" in group else 0
            
            best_row["Functional_Verification_Passed"] = fv_passed
            best_row["Synthesis_Passed"] = syn_passed
            best_row["Implementation_Passed"] = impl_passed
            
            ordered_row = {}
            for key in fieldnames:
                ordered_row[key] = best_row.get(key, "")
            summary_rows.append(ordered_row)
        
        with open(output_csv_path, 'w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for row in summary_rows:
                writer.writerow(row)
    
    @staticmethod
    def reorganize_experiment_results(analysis_dir, json_filename, full_csv_filename="full_results.csv", 
                                       summary_csv_filename="summary_results.csv", notebook_filename="ResBench_Plots.ipynb",
                                       plots_folder="Plots"):
        """Reorganize experiment results into a subfolder named after the experiment.
        
        Creates a subfolder with the same name as the JSON file (without extension) and moves
        the JSON file, CSV files, Jupyter notebook, and Plots folder into it.
        
        Args:
            analysis_dir: Path to the Analysis directory containing the files
            json_filename: Name of the JSON file (e.g., 'exp_gpt-4o_20260113_113910.json')
            full_csv_filename: Name of the full results CSV file (default: 'full_results.csv')
            summary_csv_filename: Name of the summary results CSV file (default: 'summary_results.csv')
            notebook_filename: Name of the Jupyter notebook (default: 'ResBench_Plots.ipynb')
            plots_folder: Name of the plots folder (default: 'Plots')
        
        Returns:
            str: Path to the created experiment folder
        """
        import shutil
        from pathlib import Path
        
        analysis_path = Path(analysis_dir)
        
        # Extract experiment name from JSON filename (without extension)
        experiment_name = Path(json_filename).stem
        
        # Create experiment subfolder
        experiment_folder = analysis_path / experiment_name
        experiment_folder.mkdir(exist_ok=True)
        
        # Define source and destination paths for files
        files_to_move = [
            (analysis_path / json_filename, experiment_folder / json_filename),
            (analysis_path / full_csv_filename, experiment_folder / full_csv_filename),
            (analysis_path / summary_csv_filename, experiment_folder / summary_csv_filename),
            (analysis_path / notebook_filename, experiment_folder / notebook_filename)
        ]
        
        # Move files if they exist
        moved_files = []
        for src, dst in files_to_move:
            if src.exists():
                shutil.move(str(src), str(dst))
                moved_files.append(src.name)
                print(f"✅ Moved {src.name} to {experiment_name}/")
            else:
                print(f"⚠️  File not found: {src.name}")
        
        # Move Plots folder if it exists
        plots_src = analysis_path / plots_folder
        plots_dst = experiment_folder / plots_folder
        if plots_src.exists() and plots_src.is_dir():
            shutil.move(str(plots_src), str(plots_dst))
            print(f"✅ Moved {plots_folder}/ folder to {experiment_name}/")
            moved_files.append(plots_folder)
        else:
            print(f"⚠️  Folder not found: {plots_folder}/")
        
        if moved_files:
            print(f"\n✅ Reorganization complete! Files moved to: {experiment_folder}")
        else:
            print(f"\n❌ No files were moved. Please check the file names and paths.")
        
        return str(experiment_folder)
    
    @staticmethod
    def _get_category(id_val):
        """Map design ID to category."""
        id_int = int(id_val)
        if 1 <= id_int <= 8:
            return "Combinatorial_Logic"
        elif 9 <= id_int <= 12:
            return "Finite_State_Machines"
        elif 13 <= id_int <= 17:
            return "Mathematical_Functions"
        elif 18 <= id_int <= 22:
            return "Basic_Arithmetic_Operations"
        elif 23 <= id_int <= 26:
            return "Bitwise_and_Logical_Operations"
        elif 27 <= id_int <= 31:
            return "Pipelining"
        elif 32 <= id_int <= 36:
            return "Polynomial_Evaluation"
        elif 37 <= id_int <= 41:
            return "Machine_Learning"
        elif 42 <= id_int <= 45:
            return "Financial_Computing"
        elif 46 <= id_int <= 48:
            return "Encryption"
        elif 49 <= id_int <= 52:
            return "Physics"
        elif 53 <= id_int <= 56:
            return "Climate"
        else:
            return "Unknown"


class DataPlotter:
    """Handles visualization of experiment results with publication-quality formatting."""
    
    def __init__(self, csv_path):
        self.csv_path = csv_path
        self.df = None
        self.category_id_mapping = {
            'Combinatorial_Logic': 'Comb. Logic',
            'Finite_State_Machines': 'FSMs',
            'Mathematical_Functions': 'Math. Func.',
            'Basic_Arithmetic_Operations': 'Arith. Ops.',
            'Bitwise_and_Logical_Operations': 'Bitwise Logic',
            'Pipelining': 'Pipe',
            'Polynomial_Evaluation': 'Poly. Eval.',
            'Machine_Learning': 'ML',
            'Financial_Computing': 'Finance',
            'Encryption': 'Encrypt.',
            'Physics': 'Phys.',
            'Climate': 'Clim.'
        }
        self.category_ranges = [
            ("Combinatorial Logic", range(1, 9)),
            ("Finite State Machines", range(9, 13)),
            ("Mathematical Functions", range(13, 18)),
            ("Basic Arithmetic Operations", range(18, 23)),
            ("Bitwise and Logical Operations", range(23, 27)),
            ("Pipelining", range(27, 32)),
            ("Polynomial Evaluation", range(32, 37)),
            ("Machine Learning", range(37, 42)),
            ("Financial Computing", range(42, 46)),
            ("Encryption", range(46, 49)),
            ("Physics", range(49, 53)),
            ("Climate", range(53, 57)),
        ]
    
    def load_data(self, data_dir=None):
        """Load summary and full results data.
        
        Args:
            data_dir: Directory containing summary_results.csv and full_results.csv.
                     If None, returns only the summary DataFrame from self.csv_path.
                     If provided (including "." or ""), returns both summary and full DataFrames.
        
        Returns:
            If data_dir is None: single DataFrame (summary data)
            Otherwise: tuple of (summary_df, full_df)
        """
        if data_dir is not None:
            from pathlib import Path
            # Handle empty string or "." as current directory
            data_path = Path(data_dir) if data_dir else Path(".")
            summary_df = pd.read_csv(data_path / "summary_results.csv")
            full_df = pd.read_csv(data_path / "full_results.csv")
            return summary_df, full_df
        else:
            return pd.read_csv(self.csv_path)
    
    def get_category_stats(self, df):
        """Get category statistics including problem counts with category ID mapping."""
        df['category_id'] = df['category'].map(self.category_id_mapping)
        
        category_stats = df.groupby('category').agg({
            'llm_time [s]': 'mean',
            'eda_time [s]': 'mean',
            'token_count': 'mean',
            'module': 'count'
        }).round(2)
        
        category_stats['total_time [s]'] = (
            category_stats['llm_time [s]'] + category_stats['eda_time [s]']
        ).round(2)
        
        category_stats = category_stats.rename(columns={'module': 'problem_count'})
        category_stats['category_id'] = category_stats.index.map(self.category_id_mapping)
        
        return category_stats
    
    def get_id_count_table(self, full_df):
        """Generate a table showing the count of each ID in the full_df, sorted by ID."""
        id_count_table = full_df['ID'].value_counts().reset_index()
        id_count_table.columns = ['ID', 'count']
        id_count_table = id_count_table.sort_values(by='ID').reset_index(drop=True)
        return id_count_table
    
    def set_plot_style(self, ax=None, fontsize=7, plot_area_inches=2.3, margin=0.3):
        """Apply publication-quality style to matplotlib axes with uniform fontsize."""
        import matplotlib as mpl
        figsize = (plot_area_inches + margin, plot_area_inches + margin)
        mpl.rcParams.update({
            'font.family': 'serif',
            'font.serif': ['Times New Roman', 'Times', 'DejaVu Serif', 'serif'],
            'axes.labelsize': fontsize,
            'axes.titlesize': fontsize,
            'xtick.labelsize': fontsize-1,
            'ytick.labelsize': fontsize-1,
            'axes.linewidth': 1.2,
            'xtick.direction': 'out',
            'ytick.direction': 'out',
            'xtick.color': 'black',
            'ytick.color': 'black',
            'axes.edgecolor': 'black',
            'figure.dpi': 300,
            'savefig.dpi': 300,
            'legend.fontsize': fontsize-2,
            'legend.frameon': False,
            'axes.grid': False,
            'grid.alpha': 0.0,
            'axes.facecolor': 'white',
            'figure.facecolor': 'white',
            'figure.subplot.left': 0.13,
            'figure.subplot.right': 0.97,
            'figure.subplot.bottom': 0.13,
            'figure.subplot.top': 0.97,
        })
        if ax is not None:
            ax.spines['top'].set_visible(False)
            ax.spines['right'].set_visible(False)
            ax.spines['left'].set_linewidth(1.2)
            ax.spines['bottom'].set_linewidth(1.2)
            ax.spines['left'].set_color('black')
            ax.spines['bottom'].set_color('black')
            ax.tick_params(axis='both', which='both', color='black', direction='out', length=5, width=1.2)
        return figsize
    
    def create_category_barplot(self, data, column, ylabel, fontsize=7, plot_area_inches=None, margin=None, figsize=None):
        """Create a formatted bar plot for a specific metric by category."""
        import textwrap
        if plot_area_inches is not None and margin is not None:
            width = plot_area_inches[0] + margin[0] + margin[1]
            height = plot_area_inches[1] + margin[2] + margin[3]
            figsize = (width, height)
        if figsize is None:
            figsize = self.set_plot_style(fontsize=fontsize)
        else:
            self.set_plot_style(fontsize=fontsize)
        
        sorted_data = data.sort_values(by=column, ascending=True)
        category_ids = sorted_data['category_id'].tolist()
        wrapped_labels = ["\n".join(textwrap.wrap(str(label), width=9)) for label in category_ids]
        values = sorted_data[column].tolist()
        palette = ['skyblue'] * len(category_ids)
        
        plt.figure(figsize=figsize)
        bars = plt.bar(category_ids, values, color=palette, alpha=0.85)
        ax = plt.gca()
        self.set_plot_style(ax, fontsize)
        plt.xlabel('Category', fontsize=fontsize-1)
        plt.ylabel(ylabel, fontsize=fontsize)
        plt.xticks(category_ids, wrapped_labels, rotation=0, ha='center', fontsize=fontsize-2)
        
        for i, bar in enumerate(bars):
            height = bar.get_height()
            plt.text(bar.get_x() + bar.get_width()/2., height, f'{height:.1f}', 
                    ha='center', va='bottom', fontsize=fontsize)
        
        plt.tight_layout()
        if margin is not None:
            left = margin[0] / figsize[0]
            right = 1 - margin[1] / figsize[0]
            bottom = margin[3] / figsize[1]
            top = 1 - margin[2] / figsize[1]
            plt.subplots_adjust(left=left, right=right, top=top, bottom=bottom)
        return plt.gcf()
    
    def create_category_boxplot(self, data, column, ylabel, fontsize=7, plot_area_inches=None, margin=None, figsize=None):
        """Create a formatted box plot for a specific metric by category."""
        import textwrap
        import seaborn as sns
        if plot_area_inches is not None and margin is not None:
            width = plot_area_inches[0] + margin[0] + margin[1]
            height = plot_area_inches[1] + margin[2] + margin[3]
            figsize = (width, height)
        if figsize is None:
            figsize = self.set_plot_style(fontsize=fontsize)
        else:
            self.set_plot_style(fontsize=fontsize)
        
        category_means = data.groupby('category')[column].mean().sort_values(ascending=True)
        categories = category_means.index.tolist()
        category_ids = [data[data['category'] == cat]['category_id'].iloc[0] for cat in categories]
        wrapped_labels = ["\n".join(textwrap.wrap(str(label), width=9)) for label in category_ids]
        palette = ['skyblue'] * len(category_ids)
        
        plt.figure(figsize=figsize)
        ax = sns.boxplot(
            data=data, x='category', y=column, order=categories, palette=palette, fliersize=3, linewidth=1.0,
            boxprops=dict(edgecolor='black'), whiskerprops=dict(color='black', linewidth=1.0),
            flierprops=dict(markeredgecolor='black'), medianprops=dict(color='black', linewidth=1.0),
            capprops=dict(color='black', linewidth=1.0)
        )
        self.set_plot_style(ax, fontsize)
        plt.xlabel('Category', fontsize=fontsize-1)
        plt.ylabel(ylabel, fontsize=fontsize)
        plt.xticks(range(len(categories)), wrapped_labels, rotation=0, ha='center', fontsize=fontsize-2)
        plt.tight_layout()
        if margin is not None:
            left = margin[0] / figsize[0]
            right = 1 - margin[1] / figsize[0]
            bottom = margin[3] / figsize[1]
            top = 1 - margin[2] / figsize[1]
            plt.subplots_adjust(left=left, right=right, top=top, bottom=bottom)
        return plt.gcf()
    
    def print_summary_stats(self, stats):
        """Print a summary of the category statistics with category IDs and names."""
        print("=== Category Summary Statistics ===")
        print(f"{'Category ID':<12} {'Category Name':<25} {'Problems':<10} {'LLM Time':<12} {'EDA Time':<12} {'Total Time':<12} {'Tokens':<10}")
        print("-" * 95)
        
        for category in stats.index:
            row = stats.loc[category]
            print(f"{row['category_id']:<12} {category:<25} {int(row['problem_count']):<10} "
                  f"{row['llm_time [s]']:<12.1f} {row['eda_time [s]']:<12.1f} "
                  f"{row['total_time [s]']:<12.1f} {row['token_count']:<10.0f}")
    
    def create_pass_rate_heatmap(self, summary_df, full_df, k=5, column_name='Functional Verification', 
                                 max_columns=8, fontsize=7, plot_area_inches=None, margin=None, figsize=None):
        """Create a formatted heatmap showing verification performance across categories and problems."""
        from matplotlib.colors import BoundaryNorm
        from mpl_toolkits.axes_grid1 import make_axes_locatable
        
        if plot_area_inches is not None and margin is not None:
            width = plot_area_inches[0] + margin[0] + margin[1]
            height = plot_area_inches[1] + margin[2] + margin[3]
            figsize = (width, height)
        if figsize is None:
            figsize = self.set_plot_style(fontsize=fontsize)
        else:
            self.set_plot_style(fontsize=fontsize)
        
        if column_name not in full_df.columns:
            available_columns = list(full_df.columns)
            raise ValueError(f"Column '{column_name}' not found in full_df. Available columns: {available_columns}")
        
        max_results = full_df['module'].value_counts().max()
        if k > max_results or k < 1:
            raise ValueError(f"k must be between 1 and {max_results}, got {k}")
        
        verification_counts = {}
        for module in full_df['module'].unique():
            passes = (full_df[full_df['module'] == module].head(k)[column_name] == 'PASS').sum()
            verification_counts[module] = passes
        
        category_id_map = summary_df.drop_duplicates('category')[['category', 'category_id']].set_index('category')['category_id']
        categories = summary_df['category'].unique()
        category_ids = [category_id_map[cat] for cat in categories]
        max_problems = min(summary_df['category'].value_counts().max(), max_columns)
        
        heatmap_data = pd.DataFrame(index=category_ids, columns=range(1, max_problems + 1), dtype=float)
        
        for cat, cat_id in zip(categories, category_ids):
            category_problems = summary_df[summary_df['category'] == cat].sort_values('ID')
            for i, (_, row) in enumerate(category_problems.iterrows()):
                if i >= max_problems:
                    break
                heatmap_data.loc[cat_id, i + 1] = verification_counts.get(row['module'], 0)
        
        plt.figure(figsize=figsize)
        
        cmap = plt.cm.Blues
        boundaries = np.arange(-0.5, k + 1.5, 1)
        norm = BoundaryNorm(boundaries, cmap.N)
        
        ax = plt.gca()
        im = ax.imshow(heatmap_data.values, cmap=cmap, norm=norm, aspect='auto')
        
        for i in range(len(heatmap_data.index)+1):
            ax.axhline(i-0.5, color='black', linewidth=0.7, zorder=3)
        for j in range(len(heatmap_data.columns)+1):
            ax.axvline(j-0.5, color='black', linewidth=0.7, zorder=3)
        
        divider = make_axes_locatable(ax)
        cax = divider.append_axes('right', size='10%', pad=0.15)
        cbar = plt.colorbar(im, cax=cax, boundaries=boundaries, ticks=np.arange(0, k + 1))
        cbar.set_label(f'Number of PASSED attempts out of {k}', fontsize=fontsize)
        cbar.ax.tick_params(labelsize=fontsize - 2, length=0)
        
        nan_mask = heatmap_data.isna()
        for i in range(len(heatmap_data.index)):
            for j in range(len(heatmap_data.columns)):
                if nan_mask.iloc[i, j]:
                    ax.add_patch(plt.Rectangle((j-0.5, i-0.5), 1, 1, facecolor='lightgray', 
                                              edgecolor='black', linewidth=0.5, zorder=4))
        
        ax.set_xlabel('Problem IDs', fontsize=fontsize)
        ax.set_ylabel('Category', fontsize=fontsize)
        ax.set_yticks(np.arange(len(category_ids)))
        ax.set_yticklabels(category_ids, fontsize=fontsize)
        ax.set_xticks(np.arange(max_problems))
        ax.set_xticklabels([str(i+1) for i in range(max_problems)], fontsize=fontsize)
        
        plt.tight_layout()
        if margin is not None:
            left = margin[0] / figsize[0]
            right = 1 - margin[1] / figsize[0]
            bottom = margin[3] / figsize[1]
            top = 1 - margin[2] / figsize[1]
            plt.subplots_adjust(left=left, right=right, top=top, bottom=bottom)
        return plt.gcf(), heatmap_data
    
    def plot_experiment_data(self):
        """Generate all plots for experiment data (legacy method for backward compatibility)."""
        self.df = pd.read_csv(self.csv_path)
        self.df['Category'] = self.df['ID'].apply(self._get_category)
        
        x_labels = self.df['ID'].astype(str).tolist()
        x = np.arange(len(self.df))
        
        categories, category_positions, delimiter_positions = self._calculate_category_positions()
        
        self._plot_llm_time(x, x_labels, categories, category_positions, delimiter_positions)
        self._plot_token_count(x, x_labels, categories, category_positions, delimiter_positions)
        self._plot_verification_results(x, x_labels, categories, category_positions, delimiter_positions)
        
        plt.show()
    
    def _get_category(self, id_val):
        """Map ID to category."""
        for cat, id_range in self.category_ranges:
            if int(id_val) in id_range:
                return cat
        return "Unknown"
    
    def _calculate_category_positions(self):
        """Calculate category positions and delimiter positions for plots."""
        categories = [cat for cat, _ in self.category_ranges]
        category_positions = []
        delimiter_positions = []
        
        for cat, _ in self.category_ranges:
            idx = self.df[self.df['Category'] == cat].index
            if len(idx) > 0:
                category_positions.append(idx[0])
                delimiter_positions.append(idx[-1] + 0.5)
            else:
                category_positions.append(None)
                delimiter_positions.append(None)
        
        return categories, category_positions, delimiter_positions
    
    def _add_category_labels_and_delimiters(self, ax, categories, category_positions, 
                                            delimiter_positions, y_offset_factor=0.12):
        """Add category labels and vertical delimiters to a plot."""
        ylim = ax.get_ylim()
        class_fontsize = 13
        
        for cat, pos in zip(categories, category_positions):
            if pos is not None:
                ax.text(pos, ylim[0] - (ylim[1] * y_offset_factor), cat,
                        ha='left', va='top', fontsize=class_fontsize, color='blue', rotation=30,
                        fontweight='bold', bbox=dict(facecolor='white', alpha=0.5, edgecolor='none'))
        
        for delim in delimiter_positions:
            if delim is not None and delim < len(self.df):
                ax.axvline(x=delim, color='red', linestyle='--', linewidth=1)
    
    def _plot_llm_time(self, x, x_labels, categories, category_positions, delimiter_positions):
        """Generate LLM time plot."""
        fig, ax = plt.subplots(figsize=(max(8, len(self.df) * 0.6), 6))
        ax.bar(x, self.df['llm_time [s]'], width=0.6, label='LLM Time [s]', color='orange')
        ax.set_xlabel('ID')
        ax.set_ylabel('LLM Time [s]')
        ax.set_title('LLM Time per ID')
        ax.set_xticks(x)
        ax.set_xticklabels(x_labels, rotation=45, ha='right')
        self._add_category_labels_and_delimiters(ax, categories, category_positions, delimiter_positions)
        plt.tight_layout()
    
    def _plot_token_count(self, x, x_labels, categories, category_positions, delimiter_positions):
        """Generate token count plot."""
        fig, ax = plt.subplots(figsize=(max(8, len(self.df) * 0.6), 6))
        ax.bar(x, self.df['token_count'], width=0.6, label='Token Count', color='green')
        ax.set_xlabel('ID')
        ax.set_ylabel('Token Count')
        ax.set_title('Token Count per ID')
        ax.set_xticks(x)
        ax.set_xticklabels(x_labels, rotation=45, ha='right')
        self._add_category_labels_and_delimiters(ax, categories, category_positions, delimiter_positions)
        plt.tight_layout()
    
    def _plot_verification_results(self, x, x_labels, categories, category_positions, delimiter_positions):
        """Generate verification results plot."""
        fig, ax = plt.subplots(figsize=(max(8, len(self.df) * 0.6), 6))
        width = 0.2
        ax.bar(x - width, self.df['Functional_Verification_Passed'], width, label='Functional Verification')
        ax.bar(x, self.df['Synthesis_Passed'], width, label='Synthesis')
        ax.bar(x + width, self.df['Implementation_Passed'], width, label='Implementation')
        ax.set_xlabel('ID')
        ax.set_ylabel('Score (0 to 5)')
        ax.set_title('Verification Results per ID')
        ax.set_xticks(x)
        ax.set_xticklabels(x_labels, rotation=45, ha='right')
        ax.set_ylim(-0.5, 5.5)
        self._add_category_labels_and_delimiters(ax, categories, category_positions, 
                                                  delimiter_positions, y_offset_factor=0.4/5.5)
        ax.legend()
        plt.tight_layout()


# Legacy function wrappers for backward compatibility
def detect_report_type(content):
    """Legacy wrapper for ReportParser.detect_report_type."""
    return ReportParser.detect_report_type(content)


def parse_power_report(content):
    """Legacy wrapper for ReportParser.parse_power_report."""
    return ReportParser.parse_power_report(content)


def parse_utilization_report(content):
    """Legacy wrapper for ReportParser.parse_utilization_report."""
    return ReportParser.parse_utilization_report(content)


def parse_timing_report(content):
    """Legacy wrapper for ReportParser.parse_timing_report."""
    return ReportParser.parse_timing_report(content)


def parse_log_file(content, severities=None):
    """Legacy wrapper for ReportParser.parse_log_file."""
    return ReportParser.parse_log_file(content, severities)


def batch_parse(directory, log_severities=None):
    """Legacy wrapper for ReportParser.batch_parse."""
    parser = ReportParser()
    return parser.batch_parse(directory, log_severities)


def log_results(llm_model, token_count, llm_time, eda_time, design_id, 
                problems_json_path, output_json_path, power_report_path=None, 
                utilization_report_path=None, timing_report_path=None,
                synthesis_log_path=None, implementation_log_path=None, sim_passed=False,
                verilog_path=None):
    """Legacy wrapper for ResultsLogger.log_results."""
    logger = ResultsLogger(problems_json_path, output_json_path)
    return logger.log_results(llm_model, token_count, llm_time, eda_time, design_id,
                              power_report_path, utilization_report_path, timing_report_path,
                              synthesis_log_path, implementation_log_path, sim_passed, verilog_path)


def json_to_csv(json_path, csv_path):
    """Legacy wrapper for PostProcessor.json_to_csv."""
    PostProcessor.json_to_csv(json_path, csv_path)


def compress_csv(input_csv_path, output_csv_path):
    """Legacy wrapper for PostProcessor.compress_csv."""
    PostProcessor.compress_csv(input_csv_path, output_csv_path)


def plot_experiment_data(csv_path):
    """Legacy wrapper for DataPlotter.plot_experiment_data."""
    plotter = DataPlotter(csv_path)
    plotter.plot_experiment_data()
