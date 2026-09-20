"""
Common utilities for LaMDA-RF project.
Shared functions, constants, and simple helpers used across the pipelines.
"""

import csv
import os
import shutil


# ---------------------------------------------------------------------------
# Folder utilities
# ---------------------------------------------------------------------------

def create_outputs_folder(*directories):
    """Create one or more output directories, ignoring existing ones."""
    for directory in directories:
        os.makedirs(directory, exist_ok=True)


def clear_previous_run_folders(project_root):
    """Remove all transient output folders from a previous run."""
    folders_to_clear = ["Outputs", "NetlistFiles", "ADS_Workspaces", "LLM_Feedback", "LLM_Responses"]
    cleared_count = 0
    for folder_name in folders_to_clear:
        folder_path = os.path.join(project_root, folder_name)
        if os.path.exists(folder_path):
            try:
                shutil.rmtree(folder_path)
                cleared_count += 1
                print(f"Cleared: {folder_name}")
            except Exception as e:
                print(f"Warning: Could not clear {folder_name}: {e}")
    if cleared_count > 0:
        print(f"\nCleared {cleared_count} folder(s) from previous runs.\n")
    else:
        print("\nNo previous run folders found to clear.\n")


# ---------------------------------------------------------------------------
# Netlist parsing
# ---------------------------------------------------------------------------

def extract_netlist_block(text):
    """
    Extract the netlist from an LLM response.

    Looks for a ```plaintext ... ``` or ``` ... ``` fenced block.
    Returns the original text if no fenced block is found.
    """
    if not text:
        return text

    start_token_plaintext = "```plaintext"
    start_token_netlist = "```netlist"
    start_token_generic = "```"
    end_token = "```"

    start_index = text.find(start_token_plaintext)
    if start_index != -1:
        start_index += len(start_token_plaintext)
    else:
        start_index = text.find(start_token_netlist)
        if start_index != -1:
            start_index += len(start_token_netlist)
        else:
            start_index = text.find(start_token_generic)
            if start_index == -1:
                return text
            start_index += len(start_token_generic)

    end_index = text.find(end_token, start_index)
    if end_index == -1:
        return text

    return text[start_index:end_index].strip()


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------

def save_stats_csv(all_stats, output_path):
    """Write per-iteration timing and token statistics to a CSV file."""
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", newline="", encoding="utf-8") as csvfile:
        writer = csv.writer(csvfile)
        writer.writerow([
            "iteration",
            "time_seconds",
            "prompt_tokens",
            "completion_tokens",
            "total_tokens",
        ])
        for stat in all_stats:
            writer.writerow([
                stat.get("iteration"),
                stat.get("time_seconds"),
                stat.get("prompt_tokens"),
                stat.get("completion_tokens"),
                stat.get("total_tokens"),
            ])


# ---------------------------------------------------------------------------
# Simulation check
# ---------------------------------------------------------------------------

def check_simulation_passed(main_py_result):
    """Return True if the ADS simulation completed successfully."""
    return main_py_result.get("success", False)
