"""
Test Pipeline for LaMDA-RF.
Runs a single iteration of the LLM + ADS pipeline on prompt.txt
to verify the full stack is wired correctly.
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from EDA_Interface.ADS import ADSInterface
from LLM_Interface.LLMClient import LLMClient
from LLM_Interface.requirements_utils import (
    extract_design_type,
    extract_frequency_hz,
    extract_second_frequency_hz,
    extract_requirements,
)
from LLM_Interface.library_selector_utils import select_libraries, load_selected_libraries
from utils import (
    SYSTEM_PROMPT,
    create_outputs_folder,
    clear_previous_run_folders,
    extract_netlist_block,
    check_simulation_passed,
)

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(TESTS_DIR)
DATA_DIR = os.path.join(PROJECT_ROOT, "Data")


def _parse_args():
    parser = argparse.ArgumentParser(description="LaMDA-RF Test Pipeline")
    parser.add_argument("--model", default="o3", help="LLM model name")
    parser.add_argument("--temperature", type=float, default=1.0)
    parser.add_argument("--top_p", type=float, default=1.0)
    parser.add_argument("--verbose", action="store_true")
    return parser.parse_args()


def run_test(args):
    prompt_file = os.path.join(TESTS_DIR, "prompt.txt")
    if not os.path.exists(prompt_file):
        print(f"ERROR: Prompt file not found: {prompt_file}")
        sys.exit(1)

    with open(prompt_file, "r", encoding="utf-8") as f:
        # Use only the first non-empty line for a quick single test
        user_input = next(
            (line.strip() for line in f if line.strip()), ""
        )

    if not user_input:
        print("ERROR: prompt.txt is empty.")
        sys.exit(1)

    print(f"Test prompt: {user_input}\n")

    clear_previous_run_folders(PROJECT_ROOT)

    llm = LLMClient(args.model)
    ads = ADSInterface()

    design_type = extract_design_type(user_input)
    target_freq_hz = extract_frequency_hz(user_input)
    target_freq_hz_2 = extract_second_frequency_hz(user_input)
    requirements = extract_requirements(user_input)

    print(f"Design type: {design_type}  Freq: {target_freq_hz} Hz\n")

    # Library selection
    selected_libs = select_libraries(user_input, llm, args.model)
    print(f"Selected libraries: {', '.join(selected_libs)}")

    with open(os.path.join(DATA_DIR, "ADS-Book", "Netlist.txt"), "r", encoding="utf-8") as f:
        netlist_example = f.read()

    library_content = load_selected_libraries(selected_libs, DATA_DIR)

    system_prompt = (
        SYSTEM_PROMPT
        + f"\n\nLibraries:\n\n{library_content}"
        + f"\n\nExample netlist:\n\n{netlist_example}"
    )

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_input},
    ]

    data_dir = os.path.join(PROJECT_ROOT, "NetlistFiles")
    response_dir = os.path.join(PROJECT_ROOT, "LLM_Responses")
    create_outputs_folder(data_dir, response_dir)

    # --- Step 1: LLM generation ---
    print("\n[STEP 1] Generating netlist...")
    result = llm.generate_content(
        messages=messages,
        temperature=args.temperature,
        top_p=args.top_p,
    )

    if args.verbose:
        print("LLM response:", result["content"])

    print(f"  Tokens: {result['total_tokens']}  Time: {result['time_seconds']:.2f}s")

    response_file = os.path.join(response_dir, "response_1.txt")
    with open(response_file, "w", encoding="utf-8") as f:
        f.write(result["content"])

    netlist_content = extract_netlist_block(result["content"])
    netlist_file = os.path.join(data_dir, "netlist_1.txt")
    with open(netlist_file, "w", encoding="utf-8") as f:
        f.write(netlist_content)

    assert os.path.exists(netlist_file), "Netlist file was not created."
    assert os.path.getsize(netlist_file) > 0, "Netlist file is empty."
    print("  [PASS] Netlist file created.")

    # --- Step 2: ADS simulation ---
    print("\n[STEP 2] Running ADS simulation...")
    sim_result = ads.run_ads(
        netlist_path=netlist_file,
        netlist_index=1,
        design_type=design_type,
        target_freq_hz=target_freq_hz,
        target_freq_hz_2=target_freq_hz_2,
        project_root=PROJECT_ROOT,
        verbose=args.verbose,
    )

    print(f"  Success: {sim_result['success']}")
    print(f"  Summary: {sim_result['summary'][:200]}")

    if check_simulation_passed(sim_result):
        print("  [PASS] Simulation succeeded.")
    else:
        print("  [WARN] Simulation did not succeed — check ADS logs.")

    print("\nTest pipeline completed.")


if __name__ == "__main__":
    args = _parse_args()
    run_test(args)
