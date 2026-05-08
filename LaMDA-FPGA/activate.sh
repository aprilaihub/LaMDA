#!/usr/bin/env bash
# activate.sh — First-time setup, load .env variables, and activate the venv.
# Must be SOURCED to take effect in your current shell:
#   source activate.sh   or   . activate.sh

# First-time setup: create venv and install dependencies if not already done
if [ ! -d "venv" ]; then
    echo "No virtual environment found. Running first-time setup..."
    python -m venv venv
    source venv/bin/activate
    pip install --upgrade pip setuptools wheel
    pip install -r requirements.txt
    echo "Setup complete."
else
    source venv/bin/activate
fi

# Load environment variables from .env
set -a
source .env
set +a

echo "Tip: run 'make check_env' to verify required variables, or 'make freeze_deps' to snapshot current dependencies."
