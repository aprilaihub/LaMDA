#!/usr/bin/env bash
set -euo pipefail

PYTHON_BIN="${PYTHON_BIN:-python3.12}"
VENV_DIR="${VENV_DIR:-venv312}"
REQ_FILE="${REQ_FILE:-requirements.txt}"

if [[ ! -d "$VENV_DIR" ]]; then
  "$PYTHON_BIN" -m venv "$VENV_DIR"
   source "$VENV_DIR/bin/activate"
   pip install --upgrade pip setuptools wheel
   pip install -r "$REQ_FILE"
else
   source "$VENV_DIR/bin/activate"
fi

if [[ -f .env ]]; then
  set -a
  source .env
  set +a
fi

echo "Environment ready: $(python --version)"
