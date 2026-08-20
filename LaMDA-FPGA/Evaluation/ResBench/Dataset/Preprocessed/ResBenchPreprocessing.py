"""
Dataset preprocessing utilities for ResBench benchmark.
Handles ID assignment, LUT minimization, timing constraints, and prompt enrichment.
"""

import json
import os
from collections import OrderedDict
import csv
import random
import argparse


class DatasetPreprocessor:
    """Handles preprocessing operations for the ResBench dataset."""
    
    def __init__(self, input_json_path, output_json_path):
        self.input_json_path = input_json_path
        self.output_json_path = output_json_path
        self.data = None
    
    def _load_data(self):
        """Load JSON data from input path."""
        with open(self.input_json_path, 'r', encoding='utf-8') as f:
            self.data = json.load(f, object_pairs_hook=OrderedDict)
    
    def _save_data(self):
        """Save JSON data to output path."""
        with open(self.output_json_path, 'w', encoding='utf-8') as f:
            json.dump(self.data, f, indent=4)
    
    def add_ids(self):
        """Add unique ID field to each design, numbered in order of appearance."""
        self._load_data()
        current_id = 1
        
        if isinstance(self.data, dict):
            for category in self.data:
                designs_list = self.data[category]
                if isinstance(designs_list, list):
                    for i, design in enumerate(designs_list):
                        new_design = OrderedDict([("ID", current_id)])
                        new_design.update(design)
                        designs_list[i] = new_design
                        current_id += 1
        
        self._save_data()
    
    def add_lutmin(self, csv_path):
        """Add minimum LUT count from CSV analysis file."""
        self._load_data()
        lutmin_dict = self._load_lutmin_from_csv(csv_path)
        
        if isinstance(self.data, dict):
            for category in self.data:
                designs_list = self.data[category]
                if isinstance(designs_list, list):
                    for design in designs_list:
                        module_name = design.get("module", "").replace(' ', '_')
                        if module_name in lutmin_dict:
                            design["LUTmin"] = lutmin_dict[module_name]
        
        self._save_data()
    
    def _load_lutmin_from_csv(self, csv_path):
        """Parse CSV file to extract minimum LUT values per module."""
        lutmin_dict = {}
        
        with open(csv_path, newline='', encoding='utf-8') as csvfile:
            reader = csv.reader(csvfile)
            for row in reader:
                if not row or len(row) < 2:
                    continue
                
                module_name = row[0].strip().replace(' ', '_')
                values = [x.strip() for x in row[1:] if x.strip() != '']
                
                if all(v.lower() == 'inf' for v in values) and values:
                    lutmin_dict[module_name] = 'inf'
                    continue
                
                try:
                    min_val = min(float(x) for x in values if x.lower() != 'inf')
                    lutmin_dict[module_name] = int(min_val)
                except ValueError:
                    continue
        
        return lutmin_dict
    
    def add_delay_constraints(self, delay_min_ns, delay_max_ns):
        """Add randomized delay constraints to designs."""
        self._load_data()
        
        if isinstance(self.data, dict):
            for category in self.data:
                designs_list = self.data[category]
                if isinstance(designs_list, list):
                    for design in designs_list:
                        delay = round(random.uniform(delay_min_ns, delay_max_ns), 1)
                        design["DelayMax [ns]"] = delay
        
        self._save_data()
    
    def add_clock_constraints(self, freq_min_mhz, freq_max_mhz, freq_step_mhz=50):
        """Add clock constraints for designs with clock ports."""
        self._load_data()
        freq_values = list(range(int(freq_min_mhz), int(freq_max_mhz) + 1, int(freq_step_mhz)))
        
        if isinstance(self.data, dict):
            for category in self.data:
                designs_list = self.data[category]
                if isinstance(designs_list, list):
                    for design in designs_list:
                        module_header = design.get("Module header", "")
                        
                        if "clk" in module_header:
                            freq = random.choice(freq_values)
                            period = 1000 / freq  # Convert MHz to ns
                            constraint_str = f"create_clock -period {period:.3f} [get_ports clk]"
                            design["Clock Constraint"] = constraint_str
                            design["Clock Frequency [MHz]"] = freq
                            design["DelayMax [ns]"] = None
                        else:
                            design["Clock Constraint"] = ""
                            design["Clock Frequency [MHz]"] = None
        
        self._save_data()

    def remove_timing_constraints(self, keep_clock_frequency=False):
        """Remove delay/clock constraints while optionally keeping clock frequency metadata."""
        self._load_data()

        if isinstance(self.data, dict):
            for category in self.data:
                designs_list = self.data[category]
                if isinstance(designs_list, list):
                    for design in designs_list:
                        design.pop("DelayMax [ns]", None)
                        design.pop("Clock Constraint", None)
                        if not keep_clock_frequency:
                            design.pop("Clock Frequency [MHz]", None)

        self._save_data()
    
    def enrich_prompts_with_objectives(self):
        """Enrich problem descriptions with objective constraints in natural language."""
        self._load_data()
        
        if isinstance(self.data, dict):
            for category in self.data:
                designs_list = self.data[category]
                if isinstance(designs_list, list):
                    for design in designs_list:
                        objectives = self._build_objectives(design)
                        
                        if objectives:
                            enriched_problem = design.get("Problem", "").strip()
                            if not enriched_problem.endswith('.'):
                                enriched_problem += '.'
                            enriched_problem += " " + " ".join(objectives)
                            design["Problem"] = enriched_problem
        
        self._save_data()
    
    def _build_objectives(self, design):
        """Build list of objective strings for a design."""
        objectives = []
        
        # LUT objective
        if "LUTmin" in design:
            objectives.append(f"Your solution should use at most {design['LUTmin']} LUTs.")
        
        # Delay objective (only if no clock constraint)
        if (
            "DelayMax [ns]" in design
            and design.get("Clock Constraint", "") == ""
            and design.get("DelayMax [ns]") not in [None, "NA"]
        ):
            objectives.append(f"The maximum delay must not exceed {design['DelayMax [ns]']} ns.")
        
        # Clock frequency objective (only if clock constraint is set)
        if (
            design.get("Clock Constraint", "") != ""
            and (design.get("DelayMax [ns]") in [None, "NA"] or "DelayMax [ns]" not in design)
            and design.get("Clock Frequency [MHz]") not in [None, "NA"]
        ):
            freq = design["Clock Frequency [MHz]"]
            objectives.append(f"Your solution should operate correctly at a clock frequency of {freq} MHz.")
        
        return objectives


class DatasetExporter:
    """Handles exporting specific designs from the dataset."""
    
    def __init__(self, json_path):
        self.json_path = json_path
        self.data = None
    
    def _load_data(self):
        """Load JSON data."""
        with open(self.json_path, 'r', encoding='utf-8') as f:
            self.data = json.load(f)
    
    def export_design(self, design_id, output_txt_path, testbench_v_path, constraint_xdc_path):
        """Export specific design fields to separate files."""
        self._load_data()
        design = self._find_design_by_id(design_id)
        
        if not design:
            raise ValueError(f"Design with ID {design_id} not found.")
        
        self._write_prompt_file(design, output_txt_path)
        self._write_testbench_file(design, testbench_v_path)
        self._write_constraint_file(design, constraint_xdc_path)
    
    def _find_design_by_id(self, design_id):
        """Find and return design with specified ID."""
        if isinstance(self.data, dict):
            for designs_list in self.data.values():
                if isinstance(designs_list, list):
                    for item in designs_list:
                        if str(item.get("ID")) == str(design_id):
                            return item
        return None
    
    def _write_prompt_file(self, design, output_path):
        """Write module, Problem, and Module header to text file."""
        with open(output_path, 'w', encoding='utf-8') as txt_file:
            for key in ["module", "Problem", "Module header"]:
                if key in design:
                    txt_file.write(f"{key}: {design[key]}\n")
    
    def _write_testbench_file(self, design, output_path):
        """Write testbench to Verilog file."""
        if "Testbench" in design:
            with open(output_path, 'w', encoding='utf-8') as v_file:
                v_file.write(design["Testbench"])
    
    def _write_constraint_file(self, design, output_path):
        """Write timing constraints to XDC file."""
        if "Clock Constraint" in design:
            with open(output_path, 'w', encoding='utf-8') as xdc_file:
                xdc_file.write(design["Clock Constraint"])
    
    def get_design_info(self, design_id, scripts_dir, verbose=False):
        """Extract design from dataset and export to files. Returns prompt file path."""
        if not self.json_path or design_id is None:
            if verbose:
                print("Dataset path or design_id not provided.")
            return None
        
        if verbose:
            print("\nExtracting design from dataset...")
        
        self._load_data()
        design = self._find_design_by_id(design_id)
        
        if design is None:
            if verbose:
                print(f"Design ID {design_id} not found in dataset.")
            return None
        
        module_id = design.get("ID")
        module_name = design.get("module")
        
        output_txt_path = os.path.join(scripts_dir, f"{module_id}_{module_name}_prompt.txt")
        testbench_v_path = os.path.join(scripts_dir, f"{module_name}_tb.v")
        constraint_xdc_path = os.path.join(scripts_dir, f"{module_name}.xdc")
        
        self.export_design(design_id, output_txt_path, testbench_v_path, constraint_xdc_path)
        
        return output_txt_path


class SimulationLogParser:
    """Parses simulation log files to check test results."""
    
    @staticmethod
    def parse(log_file_path):
        """Parse simulation log and return 1 if all tests passed, else 0."""
        try:
            with open(log_file_path, 'r', encoding='utf-8') as log_file:
                for line in log_file:
                    if 'All tests passed' in line:
                        print("Simulation successful: All tests passed.")
                        return 1
            return 0
        except FileNotFoundError:
            return 0


def run_full_preprocessing(
    delay_min_ns=6.0,
    delay_max_ns=9.0,
    freq_min_mhz=100.0,
    freq_max_mhz=200.0,
    include_constraints_in_problem=True,
    include_timing_constraints=True,
    output_json_name="problems_preprocessed.json",
    seed=None
):
    """Execute complete preprocessing pipeline on ResBench dataset."""
    resbench_dir = os.path.dirname(os.path.abspath(__file__))
    dataset_dir = os.path.dirname(resbench_dir)
    original_dir = os.path.join(dataset_dir, 'Original')
    
    input_json = os.path.join(original_dir, "problems.json")
    output_json = os.path.join(resbench_dir, output_json_name)
    csv_path = os.path.join(original_dir, "solution_resource_analysis.csv")

    if seed is not None:
        random.seed(seed)
    
    try:
        preprocessor = DatasetPreprocessor(input_json, output_json)
        
        # Step 1: Add IDs
        preprocessor.add_ids()
        
        # Step 2: Add LUT minimums
        preprocessor.input_json_path = output_json  # Update to work on preprocessed file
        preprocessor.add_lutmin(csv_path)
        
        # Step 3/4: Add or remove timing constraints
        if include_timing_constraints:
            preprocessor.add_delay_constraints(delay_min_ns, delay_max_ns)
            preprocessor.add_clock_constraints(freq_min_mhz, freq_max_mhz)
        else:
            # Keep sampled clock frequency targets for post-run checks, but do not
            # emit explicit clock/delay constraints into the dataset.
            preprocessor.add_clock_constraints(freq_min_mhz, freq_max_mhz)
            preprocessor.remove_timing_constraints(keep_clock_frequency=True)
        
        # Step 5: Optionally enrich prompts with objectives
        if include_constraints_in_problem:
            preprocessor.enrich_prompts_with_objectives()
        
        return 1
    except Exception as e:
        print(f"An error occurred: {e}")
        return 0


# Legacy function wrappers for backward compatibility
def export_json_fields(json_dir, design_id, output_txt_dir, testbench_v_dir, constraint_xdc_dir):
    """Legacy wrapper for DatasetExporter.export_design."""
    exporter = DatasetExporter(json_dir)
    exporter.export_design(design_id, output_txt_dir, testbench_v_dir, constraint_xdc_dir)


def get_design_from_dataset(dataset, scripts_dir, design_id, verbose=False):
    """Legacy wrapper for DatasetExporter.get_design_info."""
    exporter = DatasetExporter(dataset)
    return exporter.get_design_info(design_id, scripts_dir, verbose)


def parse_simulation_log(log_file_path):
    """Legacy wrapper for SimulationLogParser.parse."""
    return SimulationLogParser.parse(log_file_path)


def dataset_preprocess():
    """Main entry point for command-line preprocessing."""
    parser = argparse.ArgumentParser(description="Preprocess ResBench Dataset")
    parser.add_argument("--delay_min_ns", type=float, default=6.0, help="Minimum delay constraint in nanoseconds")
    parser.add_argument("--delay_max_ns", type=float, default=9.0, help="Maximum delay constraint in nanoseconds")
    parser.add_argument("--freq_min_mhz", type=float, default=100.0, help="Minimum clock frequency in MHz")
    parser.add_argument("--freq_max_mhz", type=float, default=200.0, help="Maximum clock frequency in MHz")
    parser.add_argument(
        "--output_json_name",
        type=str,
        default="problems_preprocessed.json",
        help="Output JSON filename written into Dataset/Preprocessed"
    )
    parser.add_argument(
        "--include_constraints_in_problem",
        dest="include_constraints_in_problem",
        action="store_true",
        default=True,
        help="Include LUT/timing constraints in each Problem field"
    )
    parser.add_argument(
        "--exclude_constraints_in_problem",
        dest="include_constraints_in_problem",
        action="store_false",
        help="Do not append LUT/timing constraints to each Problem field"
    )
    parser.add_argument(
        "--include_timing_constraints",
        dest="include_timing_constraints",
        action="store_true",
        default=True,
        help="Generate delay and clock constraints in dataset fields"
    )
    parser.add_argument(
        "--exclude_timing_constraints",
        dest="include_timing_constraints",
        action="store_false",
        help="Do not generate DelayMax/Clock Constraint/Clock Frequency fields"
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help="Optional random seed for deterministic delay/clock sampling"
    )
    args = parser.parse_args()
    
    return run_full_preprocessing(
        delay_min_ns=args.delay_min_ns,
        delay_max_ns=args.delay_max_ns,
        freq_min_mhz=args.freq_min_mhz,
        freq_max_mhz=args.freq_max_mhz,
        include_constraints_in_problem=args.include_constraints_in_problem,
        include_timing_constraints=args.include_timing_constraints,
        output_json_name=args.output_json_name,
        seed=args.seed
    )


if __name__ == "__main__":
    dataset_preprocess()