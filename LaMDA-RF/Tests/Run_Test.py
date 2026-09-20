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
from utils import (
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

    ads_book_dir = os.path.join(DATA_DIR, "ADS-Book")

    with open(os.path.join(ads_book_dir, "keysight-ads-de.txt"), "r", encoding="utf-8") as f:
        keysight_ads_de = f.read()

    with open(os.path.join(ads_book_dir, "libraries_and_components.txt"), "r", encoding="utf-8") as f:
        libraries_and_components = f.read()

    with open(os.path.join(ads_book_dir, "Netlist.txt"), "r", encoding="utf-8") as f:
        netlist = f.read()

    system_prompt = (
        "You are an expert in Python coding and Keysight ADS."
        "Always be precise on syntax and semantics."
        "You are an expert on Python script using Keysight's ADS Design Environment (DE) Python API"
        f"This is information regarding ADS Design Environment scripting:\n\n{keysight_ads_de}"
        f"This is information regarding libraries, components and simulators:\n\n{libraries_and_components}"
        f"It is expected that you generate a netlist like this:\n\n{netlist}"
        "Do not use new line characters in netlist blocks."
        "Keep the netlist elements on a single line."
        "For FR-4 substrate, use the following: model Sub1 MSUB H=1.6 mm Er=4.4 Mur=1 Cond=5.8e7 Hu=1e+33 mm T=0 mm TanD=0.02 Rough=0 Name=Sub1"
        "For an antenna patch, use MLOC component."
        "Consider manufacturing tolerances and practical implementation aspects in your designs."
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
