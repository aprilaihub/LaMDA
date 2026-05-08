"""
Common utilities for LaMDA-FPGA project.
Shared functions and constants used across testing and evaluation pipelines.
"""

import os
import time
import tempfile


# Common prompt templates
SYSTEM_PROMPT = \
    "You are an expert in FPGA design, Verilog coding, Python coding. \
     Be always precise on syntax and semantics. Follow what asked and do not ask more questions."

DESIGN_PROMPT = \
    "Design the Verilog code of the provided module, following the reported information. \
     Provide these outputs, using these markers in the response: \
     1. Design name: start marker '// Start Design name', end marker  '// End Design name', \
     2. Verilog design: start marker '// Start Verilog Design', end marker '// End Verilog Design'. \
     Top module name must be the same as the design name. \
     List of syntax rules to follow: \
     - Inputs and outputs must be declared in the top module. \
     - When using blocks, please follows a style like this: \
        ```verilog \
        if (condition) begin \
            // code \
        end else begin \
            // code \
        end \
        ``` "


def create_outputs_folder(*directories):
    """
    Create necessary output directories.
    
    Args:
        *directories: Variable number of directory paths to create
    """
    for directory in directories:
        os.makedirs(directory, exist_ok=True)


def extract_script(input_filename, output_filename, start_marker, end_marker, verbose=False):
    """
    Extract code section from file between markers.
    
    Args:
        input_filename: Path to input file containing marked content
        output_filename: Path to output file where extracted content will be saved
        start_marker: String marker indicating start of content to extract
        end_marker: String marker indicating end of content to extract
        verbose: If True, print status messages
    """
    with open(input_filename, 'r') as file:
        content = file.read()
    start_index = content.find(start_marker)
    end_index = content.find(end_marker, start_index)
    if start_index != -1 and end_index != -1:
        start_index += len(start_marker)
        extracted_content = content[start_index:end_index].strip()
        with open(output_filename, 'w') as output_file:
            output_file.write(extracted_content)
            if verbose:
                print(f"Extracted content saved to {output_filename}")
    else:
        if verbose:
            print("Markers not found or invalid.")


def extract_design_info(chat_filename, design_name_marker='// Start Design name'):
    """
    Extract design name from LLM response file.
    
    Args:
        chat_filename: Path to file containing LLM response
        design_name_marker: Marker indicating start of design name
        
    Returns:
        str: The extracted design name
        
    Raises:
        ValueError: If design name could not be found
    """
    design_name = None
    with open(chat_filename, 'r') as f:
        lines = f.readlines()
    for i, line in enumerate(lines):
        if design_name_marker in line and i + 1 < len(lines):
            design_name = lines[i + 1].strip()
    if design_name is None:
        raise ValueError("Could not find design name.")
    return design_name


def generate_llm_content_and_extract(llm_client, prompt, system_prompt, design_files_dir,
                                     output_filename, start_marker, end_marker,
                                     max_tokens=3000, temperature=1.0, top_p=1.0, verbose=False):
    """
    Generate content from LLM and extract specific section.
    
    Args:
        llm_client: LLMClient instance
        prompt: User prompt for the LLM
        system_prompt: System prompt for the LLM
        design_files_dir: Directory where output file will be saved
        output_filename: Name of the output file
        start_marker: Marker for start of content to extract
        end_marker: Marker for end of content to extract
        max_tokens: Maximum tokens for LLM generation
        temperature: Temperature for LLM sampling
        top_p: Top-p for LLM sampling
        verbose: If True, print status messages
        
    Returns:
        tuple: (tokens_used, execution_time)
    """
    start_time = time.time()
    content, tokens = llm_client.generate_content(
        prompt=prompt,
        system_prompt=system_prompt,
        max_tokens=max_tokens,
        temperature=temperature,
        top_p=top_p
    )
    exec_time = time.time() - start_time
    
    with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.txt') as tmp:
        tmp.write(content)
        tmp_path = tmp.name
    
    try:
        output_path = os.path.join(design_files_dir, output_filename)
        extract_script(tmp_path, output_path, start_marker, end_marker, verbose=verbose)
    finally:
        os.unlink(tmp_path)
    
    return tokens, exec_time


def check_simulation_passed(sim_log_path, verbose=False):
    """
    Check if simulation passed by examining log file.
    
    Args:
        sim_log_path: Path to simulation log file
        verbose: If True, print status messages
        
    Returns:
        bool: True if simulation passed, False otherwise
    """
    if os.path.exists(sim_log_path):
        with open(sim_log_path, 'r') as f:
            content = f.read()
            if 'All tests passed' in content:
                if verbose:
                    print("Simulation PASSED")
                return True
            else:
                if verbose:
                    print("Simulation FAILED")
                return False
    return False
