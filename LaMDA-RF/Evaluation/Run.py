"""
Custom Evaluation Pipeline for LaMDA-RF.
Runs the complete LLM-driven RF design loop on a user-provided prompt.
"""

import argparse
import os
import sys

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from EDA_Interface.ADS import ADSInterface
from LLM_Interface.LLMClient import LLMClient
from LLM_Interface.requirements_utils import (
    extract_design_type,
    extract_frequency_hz,
    extract_second_frequency_hz,
    extract_requirements,
    generate_and_queue_feedback,
)
from LLM_Interface.workspace_data_utils import copy_workspace_csvs, combine_csv_columns
from utils import (
    create_outputs_folder,
    clear_previous_run_folders,
    extract_netlist_block,
    save_stats_csv,
    check_simulation_passed,
)

CUSTOM_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(CUSTOM_DIR)
DATA_DIR = os.path.join(PROJECT_ROOT, "Data")


def _int_to_bool(value):
    return bool(int(value))


def _resolve_prompt_path(prompt_file):
    if os.path.isabs(prompt_file):
        return prompt_file

    # Try Evaluation-relative first, then project-root-relative for convenience.
    eval_relative = os.path.abspath(os.path.join(CUSTOM_DIR, prompt_file))
    if os.path.exists(eval_relative):
        return eval_relative

    project_relative = os.path.abspath(os.path.join(PROJECT_ROOT, prompt_file))
    if os.path.exists(project_relative):
        return project_relative

    # Fall back to Evaluation-relative path for a clear error message upstream.
    return eval_relative


def _build_system_prompt(system_prompt_enabled=True, ads_book_enabled=True):
    if not system_prompt_enabled:
        return (
            "You are an expert in Python coding and Keysight ADS. "
            "Always be precise on syntax and semantics."
        )

    prompt_parts = [
        "You are an expert in Python coding and Keysight ADS.",
        "Always be precise on syntax and semantics.",
        "You are an expert on Python script using Keysight's ADS Design Environment (DE) Python API.",
        "Do not use new line characters in netlist blocks.",
        "Keep the netlist elements on a single line.",
        "For FR-4 substrate, use the following: model Sub1 MSUB H=1.6 mm Er=4.4 Mur=1 Cond=5.8e7 Hu=1e+33 mm T=0 mm TanD=0.02 Rough=0 Name=Sub1.",
        "For an antenna patch, use MLOC component.",
        "Consider manufacturing tolerances and practical implementation aspects in your designs.",
    ]

    if not ads_book_enabled:
        return " ".join(prompt_parts)

    ads_book_dir = os.path.join(DATA_DIR, "ADS-Book")

    with open(os.path.join(ads_book_dir, "keysight-ads-de.txt"), "r", encoding="utf-8") as f:
        keysight_ads_de = f.read()

    with open(os.path.join(ads_book_dir, "libraries_and_components.txt"), "r", encoding="utf-8") as f:
        libraries_and_components = f.read()

    with open(os.path.join(ads_book_dir, "Netlist.txt"), "r", encoding="utf-8") as f:
        netlist = f.read()

    prompt_parts.append(f"This is information regarding ADS Design Environment scripting:\n\n{keysight_ads_de}")
    prompt_parts.append(f"This is information regarding libraries, components and simulators:\n\n{libraries_and_components}")
    prompt_parts.append(f"It is expected that you generate a netlist like this:\n\n{netlist}")
    return " ".join(prompt_parts)


class CustomPipeline:
    """Orchestrates the iterative LLM + ADS design loop for a single prompt."""

    def __init__(self, args):
        self.args = args
        self.llm = LLMClient(args.model)
        self.ads = ADSInterface()
        self.project_root = PROJECT_ROOT

    def run(self):
        clear_previous_run_folders(self.project_root)

        prompt_file = _resolve_prompt_path(self.args.prompt_file)
        if not os.path.exists(prompt_file):
            print(f"Prompt file not found: {prompt_file}")
            return

        with open(prompt_file, "r", encoding="utf-8") as f:
            user_input = f.read().strip()

        print(f"\nDesign request:\n{user_input}\n")

        # Parse request
        design_type = extract_design_type(user_input)
        target_freq_hz = extract_frequency_hz(user_input)
        target_freq_hz_2 = extract_second_frequency_hz(user_input)
        is_bandpass = design_type == 3 and any(kw in user_input.lower() for kw in ("band-pass", "bandpass", "bpf"))
        requirements = extract_requirements(user_input)

        print(f"Design type: {design_type}  (0=unknown, 1=antenna, 2=coupler, 3=filter, 4=matching_network)")
        print(f"Target frequency: {target_freq_hz} Hz")
        print(f"Second target frequency: {target_freq_hz_2} Hz\n")

        system_prompt = _build_system_prompt(
            system_prompt_enabled=_int_to_bool(self.args.system_prompt_enabled),
            ads_book_enabled=_int_to_bool(self.args.ads_book_enabled),
        )

        # Persist the exact prompts used so archived runs are reproducible.
        outputs_dir = os.path.join(self.project_root, "Outputs")
        os.makedirs(outputs_dir, exist_ok=True)
        with open(os.path.join(outputs_dir, "effective_user_prompt.txt"), "w", encoding="utf-8") as f:
            f.write(user_input)
        with open(os.path.join(outputs_dir, "effective_system_prompt.txt"), "w", encoding="utf-8") as f:
            f.write(system_prompt)
        with open(os.path.join(outputs_dir, "run_config.txt"), "w", encoding="utf-8") as f:
            f.write(f"prompt_file={prompt_file}\n")
            f.write(f"system_prompt_enabled={int(_int_to_bool(self.args.system_prompt_enabled))}\n")
            f.write(f"ads_book_enabled={int(_int_to_bool(self.args.ads_book_enabled))}\n")
            f.write(f"model={self.args.model}\n")
            f.write(f"temperature={self.args.temperature}\n")
            f.write(f"top_p={self.args.top_p}\n")
            f.write(f"iterations={self.args.iterations}\n")

        # Initialise message history
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_input},
        ]

        # Output directories
        data_dir = os.path.join(self.project_root, "NetlistFiles")
        response_dir = os.path.join(self.project_root, "LLM_Responses")
        create_outputs_folder(data_dir, response_dir)

        all_stats = []
        successful_simulations = 0
        netlist_index = 1

        for iteration in range(self.args.iterations):
            print(f"\n--- Iteration {iteration + 1}/{self.args.iterations} ---")

            result = self.llm.generate_content(
                messages=messages,
                temperature=self.args.temperature,
                top_p=self.args.top_p,
            )

            if self.args.verbose:
                print("Bot:", result["content"])

            # Save response
            response_file = os.path.join(response_dir, f"response_{netlist_index}.txt")
            with open(response_file, "w", encoding="utf-8") as f:
                f.write(result["content"])

            # Extract and save netlist
            netlist_content = extract_netlist_block(result["content"])
            netlist_file = os.path.join(data_dir, f"netlist_{netlist_index}.txt")
            with open(netlist_file, "w", encoding="utf-8") as f:
                f.write(netlist_content)

            print(f"Response time: {result['time_seconds']:.2f}s  "
                  f"Tokens: {result['total_tokens']}")

            all_stats.append({
                "iteration": iteration + 1,
                "time_seconds": result["time_seconds"],
                "prompt_tokens": result["prompt_tokens"],
                "completion_tokens": result["completion_tokens"],
                "total_tokens": result["total_tokens"],
            })

            messages.append({"role": "assistant", "content": result["content"]})

            # Run ADS simulation
            print("Running ADS simulation...")
            main_py_result = self.ads.run_ads(
                netlist_path=netlist_file,
                netlist_index=netlist_index,
                design_type=design_type,
                target_freq_hz=target_freq_hz,
                target_freq_hz_2=target_freq_hz_2,
                is_bandpass=is_bandpass,
                project_root=self.project_root,
                verbose=self.args.verbose,
            )

            if check_simulation_passed(main_py_result):
                successful_simulations += 1

            netlist_index += 1

            # Generate feedback for next iteration
            feedback_result = generate_and_queue_feedback(
                iteration=iteration,
                num_iterations=self.args.iterations,
                requirements=requirements,
                main_py_result=main_py_result,
                messages=messages,
            )
            messages = feedback_result["messages"]

            if feedback_result["should_print_final"]:
                print("Simulation Results:", main_py_result["summary"])
                print(f"\n{'='*60}\nFINAL ITERATION COMPLETE\n{'='*60}")
            elif self.args.verbose:
                print("\nFeedback for next iteration:")
                print(feedback_result["feedback_text"])

        # --- Summary ---
        total_time = sum(s["time_seconds"] for s in all_stats)
        total_tokens = sum(s["total_tokens"] for s in all_stats)
        print(f"\n{'='*60}")
        print(f"SUMMARY: {self.args.iterations} iterations")
        print(f"Total time: {total_time / 60:.2f} min  Total tokens: {total_tokens}")
        print(f"Successful simulations: {successful_simulations}/{self.args.iterations}")
        print(f"{'='*60}")

        # Save stats CSV
        stats_path = os.path.join(self.project_root, "Outputs", "LLM_stats.csv")
        save_stats_csv(all_stats, stats_path)
        print(f"Stats saved to: {stats_path}")

        # Copy and combine simulation CSVs
        sim_csv_dir = os.path.join(self.project_root, "Outputs", "Simulation_CSV_Results")
        ads_ws_dir = os.path.join(self.project_root, "ADS_Workspaces")
        copied = copy_workspace_csvs(base_dir=ads_ws_dir, logs_dir=sim_csv_dir)
        print(f"Copied {len(copied)} workspace CSV(s) to: {sim_csv_dir}")

        combined = combine_csv_columns(
            source_dir=sim_csv_dir,
            output_path=os.path.join(sim_csv_dir, "Combined_Results.csv"),
        )
        print(f"Combined CSV: {combined['output_path']}")


def _parse_args():
    parser = argparse.ArgumentParser(description="LaMDA-RF Custom Evaluation Pipeline")
    parser.add_argument("--model", default="o3", help="LLM model name")
    parser.add_argument("--temperature", type=float, default=1.0)
    parser.add_argument("--top_p", type=float, default=1.0)
    parser.add_argument("--iterations", type=int, default=5)
    parser.add_argument("--prompt_file", default="prompt.txt", help="Prompt file path (absolute or relative to Evaluation)")
    parser.add_argument("--system_prompt_enabled", type=int, choices=[0, 1], default=1)
    parser.add_argument("--ads_book_enabled", type=int, choices=[0, 1], default=1)
    parser.add_argument("--verbose", action="store_true")
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    CustomPipeline(args).run()
