#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
llm_generate_scs.py

Generates a generic, PDK-agnostic Spectre netlist using a Large Language Model.

ROBUST CLI:
  Accepts BOTH orders (auto-detects):
    1) python3 llm_generate_scs.py RUN_DIR SYSTEM_PROMPT USER_PROMPT
    2) python3 llm_generate_scs.py SYSTEM_PROMPT RUN_DIR USER_PROMPT

Model:
  Default: gpt-4o
  Override: export OPENAI_MODEL=o1 (or other supported model)

Notes:
  - o1-family models do NOT accept 'temperature' => we omit it for o1*
  - Writes:
      RUN_DIR/llm_raw_<run_id>.scs
      RUN_DIR/llm_stats.json
"""

import os
import sys
import re
import json
import time
import datetime
import requests
from pathlib import Path

MODEL = os.getenv("OPENAI_MODEL", "gpt-4o")


# ============================================================
# ARG ORDER AUTO-DETECT
# ============================================================

def _looks_like_prompt_file(p: Path) -> bool:
    # Common: .md, .txt, .yaml/.yml; and exists as a file
    if p.suffix.lower() in (".md", ".txt", ".yaml", ".yml"):
        return True
    return p.exists() and p.is_file()


def _looks_like_run_dir(p: Path) -> bool:
    # Run dir is usually a directory path, often under gen_runs/*
    # But might not exist yet; treat "has no suffix" as likely dir.
    if p.suffix:  # has an extension => likely a file
        return False
    return True


def resolve_args(argv):
    """
    Returns: (run_dir: Path, system_path: Path, user_path: Path)
    Accepts both:
      A) RUN_DIR SYSTEM USER
      B) SYSTEM RUN_DIR USER
    """
    if len(argv) != 4:
        print("Usage: python3 llm_generate_scs.py RUN_DIR SYSTEM_PROMPT USER_PROMPT", file=sys.stderr)
        print("   or: python3 llm_generate_scs.py SYSTEM_PROMPT RUN_DIR USER_PROMPT", file=sys.stderr)
        sys.exit(1)

    a1 = Path(argv[1])
    a2 = Path(argv[2])
    a3 = Path(argv[3])

    # Case B: argv[1] is a file-like prompt, argv[2] is dir-like
    if _looks_like_prompt_file(a1) and _looks_like_run_dir(a2):
        system_path = a1
        run_dir = a2
        user_path = a3
        return run_dir, system_path, user_path

    # Case A: argv[2] is a file-like prompt, argv[1] is dir-like
    if _looks_like_run_dir(a1) and _looks_like_prompt_file(a2):
        run_dir = a1
        system_path = a2
        user_path = a3
        return run_dir, system_path, user_path

    # Fallback heuristic: if a1 exists and is file => treat as system prompt
    if a1.exists() and a1.is_file():
        system_path = a1
        run_dir = a2
        user_path = a3
        return run_dir, system_path, user_path

    # Otherwise assume RUN_DIR first
    run_dir = a1
    system_path = a2
    user_path = a3
    return run_dir, system_path, user_path


# ============================================================
# MODEL PARAMS
# ============================================================

def _model_supports_temperature(model: str) -> bool:
    m = (model or "").strip().lower()
    return not m.startswith("o1")


# ============================================================
# LLM CALL
# ============================================================

def call_llm(system_prompt: str, user_prompt: str, run_dir: Path) -> str:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        print("ERROR: OPENAI_API_KEY not set.", file=sys.stderr)
        sys.exit(1)

    url = "https://api.openai.com/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    }

    # o1 rejects temperature; keep deterministic for others
    if _model_supports_temperature(MODEL):
        payload["temperature"] = 0

    start = time.time()
    r = requests.post(url, headers=headers, json=payload, timeout=300)
    end = time.time()

    try:
        r.raise_for_status()
    except requests.HTTPError:
        print("OpenAI API Error:")
        print(r.text)
        sys.exit(2)

    data = r.json()
    content = data["choices"][0]["message"]["content"]
    usage = data.get("usage", {})
    exec_time = end - start

    print("\n=== LLM Usage Stats ===")
    print(f"Model: {MODEL}")
    print(f"Prompt tokens: {usage.get('prompt_tokens', '?')}")
    print(f"Completion tokens: {usage.get('completion_tokens', '?')}")
    print(f"Total tokens: {usage.get('total_tokens', '?')}")
    print(f"Generation time: {exec_time:.2f} s")
    print("========================\n")

    stats = {
        "model": MODEL,
        "usage": usage,
        "generation_time_s": exec_time,
        "timestamp": datetime.datetime.now().isoformat(),
        "endpoint": "chat.completions",
        "temperature_sent": ("temperature" in payload),
    }
    (run_dir / "llm_stats.json").write_text(json.dumps(stats, indent=2))

    return content


# ============================================================
# NETLIST EXTRACTION
# ============================================================

def extract_spectre_block(text: str) -> str:
    m = re.search(r"```(?:spectre)?\s*(.*?)```", text, flags=re.S | re.I)
    return m.group(1).strip() if m else text.strip()


def ensure_header(deck: str) -> str:
    lower = deck.lower()
    if "simulator lang=spectre" not in lower:
        deck = "simulator lang=spectre\nglobal 0\n\n" + deck
    elif "global 0" not in lower:
        deck = deck.replace("simulator lang=spectre",
                            "simulator lang=spectre\nglobal 0", 1)
    return deck


# ============================================================
# MAIN
# ============================================================

def main():
    run_dir, system_path, user_path = resolve_args(sys.argv)

    # Guard: if run_dir points to an existing file, that is wrong
    if run_dir.exists() and run_dir.is_file():
        print(f"ERROR: RUN_DIR resolves to a file: {run_dir}", file=sys.stderr)
        print("Check your argument order (this script supports both orders).", file=sys.stderr)
        sys.exit(1)

    if not system_path.exists():
        print(f"System prompt not found: {system_path}", file=sys.stderr)
        sys.exit(1)
    if not user_path.exists():
        print(f"User prompt not found: {user_path}", file=sys.stderr)
        sys.exit(1)

    run_dir.mkdir(parents=True, exist_ok=True)

    system_prompt = system_path.read_text()
    user_prompt = user_path.read_text()

    raw_response = call_llm(system_prompt, user_prompt, run_dir)
    netlist_body = extract_spectre_block(raw_response)
    final_deck = ensure_header(netlist_body)

    run_id = run_dir.name
    out_file = run_dir / f"llm_raw_{run_id}.scs"
    out_file.write_text(final_deck)

    print(f"RUN_DIR = {run_dir}")
    print(f"SYSTEM  = {system_path}")
    print(f"USER    = {user_path}")
    print(f"WROTE   = {out_file}")


if __name__ == "__main__":
    main()
