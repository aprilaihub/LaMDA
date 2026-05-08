"""
Testing Pipeline for LaMDA-FPGA Tool.
Orchestrates the complete LaMDA-FPGA workflow for FPGA design generation and evaluation
using example prompts.
"""

import os
import sys
import time
import argparse
import tempfile
import json

# Add parent directories to path for imports
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from LLM_Interface.LLMClient import LLMClient
from EDA_Interface.Vivado import VivadoInterface
from Evaluation.ResBench.Analysis.ResBenchAnalysis import ReportParser
from utils import (
    SYSTEM_PROMPT, 
    DESIGN_PROMPT,
    create_outputs_folder,
    extract_script,
    extract_design_info,
    check_simulation_passed
)

# Directory paths - use absolute paths to avoid issues
TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUTS_DIR = os.path.join(TESTS_DIR, "Outputs")
DESIGN_FILES_DIR = os.path.join(OUTPUTS_DIR, "Design_Files")
VIVADO_PROJECT_DIR = os.path.join(OUTPUTS_DIR, "vivado_project")
VIVADO_LOGS_DIR = os.path.join(OUTPUTS_DIR, "vivado_logs")
VIVADO_REPORTS_DIR = os.path.join(VIVADO_PROJECT_DIR, "reports")

# Prompt templates
TESTBENCH_PROMPT = \
    "Considering the input design, provide these outputs, using these markers in the response: " \
    "Verilog testbench: start marker '// Start Verilog Testbench', end marker '// End Verilog Testbench'. " \
    "Please add timescale 1ns/1ps at the beginning of the testbench. " \
    "Top module name must be the same as the design name + '_tb'. " \
    "For each test case, save inputs and outputs in a text file named 'logic_sim.txt' as hex values. " \
    "Stop the simulation after the last test case. " \
    "Define the integer file variable outside the process. " \
    "Do not use dump .vcd file generation. " \
    "Define variables outside processes. Run clock only during the stimuli process, not forever " \
    "The syntax of the top module is module module_name_tb();"

CHECKER_PROMPT = \
    "Considering the input testbench, provide these outputs, using these markers in the response: " \
    "Python checker: start marker '// Start Python Checker', end marker '// End Python Checker'. " \
    "Python has to manage any delay from registers (for example, if there is a pipeline stage, the results " \
    "associated to the i-th input appear at i+1-th clock cycle). " \
    "Data from the simulator are in hex format. " \
    "Please manage overflow and underflow of the data. " \
    "Python has to manage X states from the simulator properly to avoid data conflicts: " \
    "If an 'X' state is found in the output, replace it with 0. " \
    "Do the checks and report the results in the console if 'verbose' is set to True. " \
    "If a computing error is found, do not interrupt the execution of the checker. " \
    "The function name must be the same as the design name + '_checker'. " \
    "This function take two arguments: first is the text file and second is the argument 'verbose'. " \
    "Please do not include any main in this python file."

CONSTRAINTS_PROMPT = \
    "Considering the input design, provide these outputs, using these markers in the response: " \
    "Constraints file (.xdc): start marker '// Start Constraints', end marker '// End Constraints'. " \
    "Only clock information. No comments in the constraints file. " \
    "For now, constraint the clock signal only as indicated by the user."

RECOMMENDATION_PROMPT = \
    "Considering the implementation reports, summarize key insights and provide possible optimization suggestions. " \
    "start marker '// Start Recommendation', end marker '// End Recommendation'. " \
    "Please summarize the timing, utilization, power and log status. " \
    "If any problems are found (e.g. negative slack, critical warnings), suggest fixes."


def create_output_directories():
    """Create necessary output directories for testing pipeline."""
    create_outputs_folder(
        DESIGN_FILES_DIR,
        VIVADO_PROJECT_DIR,
        VIVADO_LOGS_DIR,
        os.path.join(VIVADO_REPORTS_DIR, "Synthesis"),
        os.path.join(VIVADO_REPORTS_DIR, "Implementation")
    )


class TestingPipeline:
    """Main pipeline orchestrator for testing the LaMDA-FPGA tool."""
    
    def __init__(self, args):
        self.args = args
        self.llm_client = LLMClient(args.model)
        self.vivado = VivadoInterface()
        self.design_name = None
        self.total_tokens = 0
        self.total_llm_time = 0.0
        self.parser = ReportParser()
    
    def run(self):
        """Execute the complete testing pipeline."""
        try:
            create_output_directories()
            
            # Load the example prompt
            prompt_file = os.path.join(TESTS_DIR, "example_prompt.txt")
            if not os.path.exists(prompt_file):
                print(f"Prompt file not found: {prompt_file}")
                return 0
            
            if self.args.verbose:
                print("\n=== Generating Verilog Design ===")
            self._generate_design(prompt_file)
            
            if self.args.verbose:
                print("\n=== Generating Testbench ===")
            self._generate_testbench()
            
            if self.args.verbose:
                print("\n=== Generating Python Checker ===")
            self._generate_checker()
            
            if self.args.verbose:
                print("\n=== Generating Constraints ===")
            self._generate_constraints()
            
            if self.args.verbose:
                print("\n=== Running Simulation ===")
            sim_passed = self._run_simulation()
            
            eda_start_time = time.time()
            
            if self.args.verbose:
                print("\n=== Running Synthesis ===")
            self._run_synthesis()
            
            if self.args.verbose:
                print("\n=== Running Implementation ===")
            self._run_implementation()
            
            if self.args.verbose:
                print("\n=== Parsing Reports ===")
            self._parse_reports()
            
            if self.args.verbose:
                print("\n=== Generating Recommendations ===")
            self._generate_recommendations()
            
            eda_time = time.time() - eda_start_time
            
            if self.args.verbose:
                print("\n=== Summary ===")
                print(f"Design: {self.design_name}")
                print(f"Total LLM Time: {self.total_llm_time:.2f}s")
                print(f"Total Tokens: {self.total_tokens}")
                print(f"EDA Time: {eda_time:.2f}s")
                print(f"Simulation: {'PASSED' if sim_passed else 'FAILED'}")
                print("\n=== Pipeline Completed Successfully ===")
            
            return 1
            
        except Exception as e:
            print(f"Pipeline failed with error: {e}")
            import traceback
            traceback.print_exc()
            return 0
    
    def _generate_design(self, prompt_file):
        """Generate Verilog design from prompt file."""
        with open(prompt_file, 'r') as f:
            prompt_content = f.read()
        
        full_prompt = f"{prompt_content}\n\n{DESIGN_PROMPT}"
        start_time = time.time()
        content, tokens = self.llm_client.generate_content(
            prompt=full_prompt,
            system_prompt=SYSTEM_PROMPT,
            max_tokens=self.args.max_tokens,
            temperature=self.args.temperature,
            top_p=self.args.top_p
        )
        exec_time = time.time() - start_time
        
        self.total_tokens += tokens
        self.total_llm_time += exec_time
        
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.txt') as tmp:
            tmp.write(content)
            tmp_path = tmp.name
        
        try:
            self.design_name = extract_design_info(tmp_path)
            design_file = os.path.join(DESIGN_FILES_DIR, f"{self.design_name}.v")
            extract_script(tmp_path, design_file, "// Start Verilog Design\n", "\n// End Verilog Design", verbose=False)
        finally:
            os.unlink(tmp_path)
        
        if self.args.verbose:
            print(f"Design '{self.design_name}' generated ({tokens} tokens, {exec_time:.2f}s)")
    
    def _generate_testbench(self):
        """Generate Verilog testbench."""
        design_file = os.path.join(DESIGN_FILES_DIR, f"{self.design_name}.v")
        with open(design_file, 'r') as f:
            design_content = f.read()
        
        full_prompt = f"{design_content}\n\n{TESTBENCH_PROMPT}"
        start_time = time.time()
        content, tokens = self.llm_client.generate_content(
            prompt=full_prompt,
            system_prompt=SYSTEM_PROMPT,
            max_tokens=self.args.max_tokens,
            temperature=self.args.temperature,
            top_p=self.args.top_p
        )
        exec_time = time.time() - start_time
        
        self.total_tokens += tokens
        self.total_llm_time += exec_time
        
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.txt') as tmp:
            tmp.write(content)
            tmp_path = tmp.name
        
        try:
            testbench_file = os.path.join(DESIGN_FILES_DIR, f"{self.design_name}_tb.v")
            extract_script(tmp_path, testbench_file, "// Start Verilog Testbench\n", "\n// End Verilog Testbench", verbose=False)
        finally:
            os.unlink(tmp_path)
        
        if self.args.verbose:
            print(f"Testbench generated ({tokens} tokens, {exec_time:.2f}s)")
    
    def _generate_checker(self):
        """Generate Python checker."""
        testbench_file = os.path.join(DESIGN_FILES_DIR, f"{self.design_name}_tb.v")
        with open(testbench_file, 'r') as f:
            testbench_content = f.read()
        
        full_prompt = f"{testbench_content}\n\n{CHECKER_PROMPT}"
        start_time = time.time()
        content, tokens = self.llm_client.generate_content(
            prompt=full_prompt,
            system_prompt=SYSTEM_PROMPT,
            max_tokens=self.args.max_tokens,
            temperature=self.args.temperature,
            top_p=self.args.top_p
        )
        exec_time = time.time() - start_time
        
        self.total_tokens += tokens
        self.total_llm_time += exec_time
        
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.txt') as tmp:
            tmp.write(content)
            tmp_path = tmp.name
        
        try:
            checker_file = os.path.join(DESIGN_FILES_DIR, f"{self.design_name}_checker.py")
            extract_script(tmp_path, checker_file, "// Start Python Checker\n", "\n// End Python Checker", verbose=False)
        finally:
            os.unlink(tmp_path)
        
        if self.args.verbose:
            print(f"Checker generated ({tokens} tokens, {exec_time:.2f}s)")
    
    def _generate_constraints(self):
        """Generate constraints file."""
        design_file = os.path.join(DESIGN_FILES_DIR, f"{self.design_name}.v")
        with open(design_file, 'r') as f:
            design_content = f.read()
        
        full_prompt = f"{design_content}\n\n{CONSTRAINTS_PROMPT}"
        start_time = time.time()
        content, tokens = self.llm_client.generate_content(
            prompt=full_prompt,
            system_prompt=SYSTEM_PROMPT,
            max_tokens=self.args.max_tokens,
            temperature=self.args.temperature,
            top_p=self.args.top_p
        )
        exec_time = time.time() - start_time
        
        self.total_tokens += tokens
        self.total_llm_time += exec_time
        
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.txt') as tmp:
            tmp.write(content)
            tmp_path = tmp.name
        
        try:
            constraints_file = os.path.join(DESIGN_FILES_DIR, f"{self.design_name}.xdc")
            extract_script(tmp_path, constraints_file, "// Start Constraints\n", "\n// End Constraints", verbose=False)
        finally:
            os.unlink(tmp_path)
        
        if self.args.verbose:
            print(f"Constraints generated ({tokens} tokens, {exec_time:.2f}s)")
    
    def _run_simulation(self):
        """Run Vivado simulation."""
        self.vivado.run_vivado(
            project_name=self.design_name,
            project_dir=VIVADO_PROJECT_DIR,
            log_path=VIVADO_LOGS_DIR,
            design_files_dir=DESIGN_FILES_DIR,
            fpga_part=self.args.fpga_part,
            mode='sim',
            verbose=self.args.verbose
        )
        
        sim_log = os.path.join(VIVADO_LOGS_DIR, f"{self.design_name}_sim.log")
        return check_simulation_passed(sim_log, verbose=self.args.verbose)
    
    def _run_synthesis(self):
        """Run Vivado synthesis."""
        self.vivado.run_vivado(
            project_name=self.design_name,
            project_dir=VIVADO_PROJECT_DIR,
            log_path=VIVADO_LOGS_DIR,
            design_files_dir=DESIGN_FILES_DIR,
            fpga_part=self.args.fpga_part,
            mode='synth',
            verbose=self.args.verbose
        )
    
    def _run_implementation(self):
        """Run Vivado implementation."""
        self.vivado.run_vivado(
            project_name=self.design_name,
            project_dir=VIVADO_PROJECT_DIR,
            log_path=VIVADO_LOGS_DIR,
            design_files_dir=DESIGN_FILES_DIR,
            fpga_part=self.args.fpga_part,
            mode='impl',
            verbose=self.args.verbose
        )
    
    def _parse_reports(self):
        """Parse implementation reports and logs."""
        impl_reports_dir = os.path.join(VIVADO_REPORTS_DIR, "implementation")
        parsed_reports = self.parser.batch_parse(impl_reports_dir)
        parsed_logs = self.parser.batch_parse(VIVADO_LOGS_DIR)
        parsed_all = {**parsed_reports, **parsed_logs}
        
        output_file = os.path.join(OUTPUTS_DIR, "parsed_reports.json")
        with open(output_file, "w") as f:
            json.dump(parsed_all, f, indent=4)
        
        if self.args.verbose:
            print(f"Parsed reports saved to {output_file}")
    
    def _generate_recommendations(self):
        """Generate optimization recommendations based on reports."""
        parsed_reports_file = os.path.join(OUTPUTS_DIR, "parsed_reports.json")
        if not os.path.exists(parsed_reports_file):
            if self.args.verbose:
                print("No parsed reports found. Skipping recommendations.")
            return
        
        with open(parsed_reports_file, 'r') as f:
            parsed_content = json.dumps(json.load(f), indent=2)
        
        full_prompt = f"Here are the parsed implementation reports:\n\n{parsed_content}\n\n{RECOMMENDATION_PROMPT}"
        start_time = time.time()
        content, tokens = self.llm_client.generate_content(
            prompt=full_prompt,
            system_prompt=SYSTEM_PROMPT,
            max_tokens=self.args.max_tokens,
            temperature=self.args.temperature,
            top_p=self.args.top_p
        )
        exec_time = time.time() - start_time
        
        self.total_tokens += tokens
        self.total_llm_time += exec_time
        
        # Extract recommendations
        start_marker = "// Start Recommendation"
        end_marker = "// End Recommendation"
        start_index = content.find(start_marker)
        end_index = content.find(end_marker, start_index)
        
        if start_index != -1 and end_index != -1:
            start_index += len(start_marker)
            recommendations = content[start_index:end_index].strip()
            
            recommendations_file = os.path.join(OUTPUTS_DIR, "recommendations.txt")
            with open(recommendations_file, "w") as f:
                f.write(recommendations)
            
            if self.args.verbose:
                print(f"Recommendations saved to {recommendations_file}")
                print(f"\n{recommendations}")
        else:
            if self.args.verbose:
                print("Could not extract recommendations from LLM response.")


def parse_arguments():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Run LaMDA-FPGA testing pipeline for FPGA design",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    
    parser.add_argument("--model", type=str, default="gpt-4o",
                       help="LLM model name (e.g., gpt-4o, gemini-2.0-flash-exp)")
    parser.add_argument("--max_tokens", type=int, default=3000,
                       help="Maximum tokens for LLM generation")
    parser.add_argument("--temperature", type=float, default=1.0,
                       help="Temperature for LLM sampling")
    parser.add_argument("--top_p", type=float, default=1.0,
                       help="Top-p for LLM sampling")
    
    parser.add_argument("--fpga_part", type=str, default="xc7z020clg400-1",
                       help="FPGA part number for Vivado")
    
    parser.add_argument("--verbose", action="store_true",
                       help="Enable verbose logging")
    
    return parser.parse_args()


def main():
    """Main entry point for the testing pipeline."""
    args = parse_arguments()
    
    print("=" * 60)
    print("LaMDA-FPGA Testing Pipeline")
    print("=" * 60)
    print(f"Model: {args.model}")
    print(f"FPGA Part: {args.fpga_part}")
    print("=" * 60)
    
    pipeline = TestingPipeline(args)
    result = pipeline.run()
    
    return result


if __name__ == "__main__":
    exit(0 if main() else 1)