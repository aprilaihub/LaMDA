import os
from openai import OpenAI


def select_libraries(user_request, client, model="gpt-3.5-turbo"):
    """
    Ask the LLM which ADS libraries are relevant for the design request.

    Args:
        user_request (str): User's design request.
        client (LLMClient): Initialized LLMClient instance.
        model (str): Model name to use for selection.

    Returns:
        list: Sorted list of selected library names.
    """
    available_libraries = [
        "ads_tlines", "ads_sources", "ads_simulation", "ads_rflib",
        "ads_bondwires", "ads_behavioral", "ads_common_cmps",
        "ads_datacmps", "ads_designs", "ads_pelib", "ads_quantum",
    ]

    selection_prompt = (
        f"User design request: {user_request}\n\n"
        f"Available ADS libraries: {', '.join(available_libraries)}\n\n"
        "Based on the design request, which libraries are needed?\n"
        "Respond with ONLY the library names, comma-separated, no explanation.\n"
        "Example: ads_tlines, ads_sources, ads_simulation"
    )


    try:
        result = client.generate_content(
            messages=[{"role": "user", "content": selection_prompt}]
        )
        selected_text = result["content"]
        selected = [lib.strip() for lib in selected_text.split(",")]
        valid_selected = [lib for lib in selected if lib in available_libraries]
        essential = {"ads_sources", "ads_simulation"}
        final_selection = list(set(valid_selected) | essential)
        return sorted(final_selection)

    except Exception as e:
        print(f"Warning: Library selection failed ({e}), using default set")
        return ["ads_tlines", "ads_sources", "ads_simulation", "ads_rflib"]


def load_selected_libraries(library_names, data_dir):
    """
    Load and concatenate content from selected ADS library files.

    Args:
        library_names (list): List of library names to load.
        data_dir (str): Path to the Data/ directory containing ADS-Book/libraries/.

    Returns:
        str: Combined library content string.
    """
    libraries_dir = os.path.join(data_dir, "ADS-Book", "libraries")
    content = ""
    for lib_name in library_names:
        lib_file = os.path.join(libraries_dir, f"{lib_name}.txt")
        if os.path.exists(lib_file):
            with open(lib_file, "r", encoding="utf-8") as f:
                content += f.read() + "\n"
        else:
            print(f"Warning: Library file not found: {lib_file}")
    return content