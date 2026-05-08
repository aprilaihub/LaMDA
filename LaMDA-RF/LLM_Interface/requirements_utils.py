import os
import re


TEXT_REPLACEMENTS = (
    ("\u2212", "-"),
    ("\u2013", "-"),
    ("\u2014", "-"),
    ("\u2264", "<="),
    ("\u2265", ">="),
)

OPERATOR_MAP = {
    "is below": "<=",
    "is above": ">=",
    "is less than": "<",
    "is greater than": ">",
    "is": "=",
    "below": "<=",
    "less than": "<",
    "above": ">=",
    "greater than": ">",
}

FREQ_PATTERN = r'(\d+\.?\d*)\s*(GHz|MHz|kHz|Hz)'
FIRST_FREQ_PATTERN = re.compile(FREQ_PATTERN, re.IGNORECASE)
PARAM_PATTERN = re.compile(
    r'(S\d{2})\s*(?:parameter)?\s*'
    r'(is\s+below|is\s+above|is\s+less\s+than|is\s+greater\s+than|is|below|above|less\s+than|greater\s+than|<=|>=|=|<|>)\s*'
    r'([-+]?\d+\.?\d*)\s*dB((?:(?!\s+and\s+S\d{2})[^,;])*)',
    re.IGNORECASE,
)
FOR_FREQUENCIES_PATTERN = re.compile(
    r'for\s+frequencies?\s*(<=|>=|=|<|>|below|above|less\s+than|greater\s+than)\s*'
    + FREQ_PATTERN,
    re.IGNORECASE,
)
AT_PATTERN = re.compile(r'at\s*' + FREQ_PATTERN, re.IGNORECASE)
BARE_FREQ_PATTERN = re.compile(FREQ_PATTERN, re.IGNORECASE)
LEGACY_S11_PATTERN = re.compile(
    r'S11\s*(?:below|above|less than|greater than|[<>=]+)\s*-?\d+\.?\d*\s*dB',
    re.IGNORECASE,
)
LEGACY_OPERATOR_PATTERN = re.compile(r'below|above|less\s+than|greater\s+than', re.IGNORECASE)


def extract_design_type(user_request):
    """Return design type from prompt text: antenna=1, coupler=2, filter=3, unknown=0."""
    request_lower = user_request.lower()
    if "antenna" in request_lower:
        return 1
    if "coupler" in request_lower:
        return 2
    if "filter" in request_lower:
        return 3
    return 0


def extract_frequency_hz(user_request, default_hz=2.4e9):
    """Extract first frequency from prompt and convert it to Hz."""
    normalized = _normalize_text(user_request)
    match = FIRST_FREQ_PATTERN.search(normalized)
    if not match:
        return float(default_hz)
    value = float(match.group(1))
    unit = match.group(2).lower()
    return value * {"ghz": 1e9, "mhz": 1e6, "khz": 1e3, "hz": 1.0}[unit]


def extract_second_frequency_hz(user_request, default_hz=None):
    """Extract second frequency from prompt and convert it to Hz if present."""
    normalized = _normalize_text(user_request)
    matches = list(FIRST_FREQ_PATTERN.finditer(normalized))
    if len(matches) < 2:
        return default_hz
    second = matches[1]
    value = float(second.group(1))
    unit = second.group(2).lower()
    return value * {"ghz": 1e9, "mhz": 1e6, "khz": 1e3, "hz": 1.0}[unit]


def extract_requirements(user_request):
    """Extract S-parameter requirements and frequency conditions from user request."""
    user_request = _normalize_text(user_request)
    requirement_clauses = []

    for match in PARAM_PATTERN.finditer(user_request):
        parameter = match.group(1).upper()
        operator = _normalize_operator(match.group(2))
        value_db = match.group(3)
        clause_tail = match.group(4)
        freq_suffix = _extract_frequency_suffix(clause_tail)
        requirement_clauses.append(f"{parameter} {operator} {value_db} dB{freq_suffix}")

    if requirement_clauses:
        return f"Requirements: {'; '.join(requirement_clauses)}"

    freq_match = FIRST_FREQ_PATTERN.search(user_request)
    frequency = freq_match.group(0) if freq_match else "target frequency"

    s11_match = LEGACY_S11_PATTERN.search(user_request)
    if s11_match:
        s11_req = LEGACY_OPERATOR_PATTERN.sub(
            lambda m: _normalize_operator(m.group(0)), s11_match.group(0)
        )
    else:
        s11_req = "S11 requirement"

    return f"Requirement: {s11_req} at {frequency}"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _normalize_text(text):
    for old, new in TEXT_REPLACEMENTS:
        text = text.replace(old, new)
    return text


def _normalize_operator(op):
    op_clean = re.sub(r'\s+', ' ', op.strip().lower())
    return OPERATOR_MAP.get(op_clean, op_clean)


def _extract_frequency_suffix(clause_tail):
    for_match = FOR_FREQUENCIES_PATTERN.search(clause_tail)
    if for_match:
        op = _normalize_operator(for_match.group(1))
        return f" for frequencies {op} {for_match.group(2)} {for_match.group(3)}"
    at_match = AT_PATTERN.search(clause_tail)
    if at_match:
        return f" at {at_match.group(1)} {at_match.group(2)}"
    bare_match = BARE_FREQ_PATTERN.search(clause_tail)
    if bare_match:
        return f" at {bare_match.group(1)} {bare_match.group(2)}"
    return ""


# ---------------------------------------------------------------------------
# Feedback utilities
# ---------------------------------------------------------------------------

def generate_and_queue_feedback(iteration, num_iterations, requirements, main_py_result, messages):
    """
    Generate feedback from simulation results and append it as the next user message.

    Args:
        iteration (int): Current iteration index (0-based).
        num_iterations (int): Total number of iterations.
        requirements (str): Extracted requirements string from the user's request.
        main_py_result (dict): Result dict from ADSInterface.run_ads with 'summary' key.
        messages (list): Current message history to append feedback to.

    Returns:
        dict: {
            'messages': updated message list,
            'feedback_text': str | None,
            'is_valid': bool,
            'should_print_final': bool,
        }
    """
    if iteration >= num_iterations - 1:
        return {
            "messages": messages,
            "feedback_text": None,
            "is_valid": False,
            "should_print_final": True,
        }

    feedback_text = (
        f"{requirements}. "
        f"Simulation results: {main_py_result['summary']}"
        " Please fix errors and modify the design to better meet the requirements. Generate an updated netlist."
    )

    feedback_dir = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "LLM_Feedback"
    )
    os.makedirs(feedback_dir, exist_ok=True)
    feedback_path = os.path.join(feedback_dir, f"feedback_{iteration + 1}.txt")

    with open(feedback_path, "w", encoding="utf-8") as f:
        f.write(feedback_text)

    with open(feedback_path, "r", encoding="utf-8") as f:
        feedback_from_file = f.read()

    messages.append({"role": "user", "content": feedback_from_file})
    is_valid = _validate_feedback_in_context(messages, feedback_from_file)

    if not is_valid and messages and messages[-1]["role"] == "user":
        messages[-1]["content"] += f"\n\n{feedback_from_file}"
        is_valid = _validate_feedback_in_context(messages, feedback_from_file)

    return {
        "messages": messages,
        "feedback_text": feedback_from_file,
        "is_valid": is_valid,
        "should_print_final": False,
    }


def _validate_feedback_in_context(messages, feedback_text):
    """Return True if feedback_text appears in the last user message."""
    if messages and messages[-1]["role"] == "user":
        return feedback_text in messages[-1]["content"]
    return False
