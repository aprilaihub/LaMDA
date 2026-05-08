"""
ResBench Evaluation Pipeline.
Orchestrates the complete LaMDA-FPGA workflow for FPGA design generation and evaluation.
"""

import os
import sys
import time
import argparse
import tempfile

# Add parent directories to path for imports
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from LLM_Interface.LLMClient import LLMClient
from EDA_Interface.Vivado import VivadoInterface
from Evaluation.ResBench.Dataset.Preprocessed.ResBenchPreprocessing import get_design_from_dataset
from Evaluation.ResBench.Analysis.ResBenchAnalysis import log_results
from utils import (
    SYSTEM_PROMPT,
    DESIGN_PROMPT,
    create_outputs_folder,
    extract_script,
    extract_design_info,
    check_simulation_passed
)


# Directory paths - use absolute paths to avoid issues
RESBENCH_DIR = os.path.dirname(os.path.abspath(__file__))
DATASET_DIR = os.path.join(RESBENCH_DIR, "Dataset", "Preprocessed")
ANALYSIS_DIR = os.path.join(RESBENCH_DIR, "Analysis")
OUTPUTS_DIR = os.path.join(RESBENCH_DIR, "Outputs")
DESIGN_FILES_DIR = os.path.join(OUTPUTS_DIR, "Design_Files")
VIVADO_PROJECT_DIR = os.path.join(OUTPUTS_DIR, "vivado_project")
VIVADO_LOGS_DIR = os.path.join(OUTPUTS_DIR, "vivado_logs")
VIVADO_REPORTS_DIR = os.path.join(VIVADO_PROJECT_DIR, "reports")
TCL_DIR = os.path.join(RESBENCH_DIR, "..", "..", "EDA_Interface", "Vivado")
TCL_DIR = os.path.abspath(TCL_DIR)


def create_output_directories():
    """Create necessary output directories for ResBench evaluation."""
    create_outputs_folder(
        DESIGN_FILES_DIR,
        VIVADO_PROJECT_DIR,
        VIVADO_LOGS_DIR,
        os.path.join(VIVADO_REPORTS_DIR, "Synthesis"),
        os.path.join(VIVADO_REPORTS_DIR, "Implementation")
    )


class ResBenchPipeline:
    """Main pipeline orchestrator for ResBench evaluation."""
    
    def __init__(self, args):
        self.args = args
        self.llm_client = LLMClient(args.model)
        self.vivado = VivadoInterface()
        self.design_name = None
        self.total_tokens = 0
        self.total_llm_time = 0.0
    
    def run(self):
        """Execute the complete ResBench evaluation pipeline."""
        try:
            create_output_directories()
            
            dataset_path = os.path.join(DATASET_DIR, "problems_preprocessed.json")
            prompt_file = get_design_from_dataset(
                dataset_path, 
                DESIGN_FILES_DIR, 
                self.args.design_id, 
                self.args.verbose
            )
            
            if not prompt_file:
                print(f"Failed to load design ID {self.args.design_id}")
                return 0
            
            if self.args.verbose:
                print("\n=== Generating Verilog Design ===")
            self._generate_design(prompt_file)
            
            if self.args.verbose:
                print("\n=== Running Simulation ===")
            sim_passed = self._run_simulation()
            
            eda_start_time = time.time()
            if sim_passed:
                if self.args.verbose:
                    print("\n=== Running Synthesis ===")
                self._run_synthesis()
                
                if self.args.verbose:
                    print("\n=== Running Implementation ===")
                self._run_implementation()
            else:
                if self.args.verbose:
                    print("\nSimulation failed. Skipping synthesis and implementation.")
            
            eda_time = time.time() - eda_start_time
            
            if self.args.verbose:
                print("\n=== Logging Results ===")
            self._log_results(sim_passed, eda_time)
            
            if self.args.verbose:
                print("\n=== Pipeline Completed Successfully ===")
            
            return 1
            
        except Exception as e:
            print(f"Pipeline failed with error: {e}")
            import traceback
            traceback.print_exc()
            return 0
    
    def _generate_design(self, prompt_file):
        """Generate Verilog design from dataset prompt."""
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
    
    def _log_results(self, sim_passed, eda_time):
        """Log experimental results."""
        power_report = os.path.join(VIVADO_REPORTS_DIR, "implementation", "power_report.txt")
        utilization_report = os.path.join(VIVADO_REPORTS_DIR, "implementation", "utilization_report.txt")
        timing_report = os.path.join(VIVADO_REPORTS_DIR, "implementation", "timing_report.txt")
        synthesis_log = os.path.join(VIVADO_LOGS_DIR, f"{self.design_name}_synth.log")
        implementation_log = os.path.join(VIVADO_LOGS_DIR, f"{self.design_name}_impl.log")
        verilog_path = os.path.join(DESIGN_FILES_DIR, f"{self.design_name}.v")
        
        if not os.path.exists(power_report):
            power_report = None
        if not os.path.exists(utilization_report):
            utilization_report = None
        if not os.path.exists(timing_report):
            timing_report = None
        if not os.path.exists(synthesis_log):
            synthesis_log = None
        if not os.path.exists(implementation_log):
            implementation_log = None
        
        dataset_path = os.path.join(DATASET_DIR, "problems_preprocessed.json")
        output_json = os.path.join(ANALYSIS_DIR, self.args.output_json)
        
        log_results(
            llm_model=self.args.model,
            token_count=self.total_tokens,
            llm_time=self.total_llm_time,
            eda_time=eda_time,
            design_id=self.args.design_id,
            problems_json_path=dataset_path,
            output_json_path=output_json,
            power_report_path=power_report,
            utilization_report_path=utilization_report,
            timing_report_path=timing_report,
            synthesis_log_path=synthesis_log,
            implementation_log_path=implementation_log,
            sim_passed=sim_passed,
            verilog_path=verilog_path
        )
        
        if self.args.verbose:
            print(f"Results logged to {output_json}")


def parse_arguments():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Run ResBench evaluation pipeline for FPGA design with LLMs",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    
    parser.add_argument("--design_id", type=int, required=True,
                       help="Design ID from ResBench dataset (1-56)")
    
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
    
    parser.add_argument("--output_json", type=str, default="exp_results.json",
                       help="Output JSON filename for results")
    parser.add_argument("--verbose", action="store_true",
                       help="Enable verbose logging")
    
    return parser.parse_args()


def main():
    """Main entry point for the ResBench evaluation pipeline."""
    args = parse_arguments()
    
    print("=" * 60)
    print("ResBench Evaluation Pipeline")
    print("=" * 60)
    print(f"Design ID: {args.design_id}")
    print(f"Model: {args.model}")
    print(f"FPGA Part: {args.fpga_part}")
    print("=" * 60)
    
    pipeline = ResBenchPipeline(args)
    result = pipeline.run()
    
    return result


if __name__ == "__main__":
    exit(0 if main() else 1)