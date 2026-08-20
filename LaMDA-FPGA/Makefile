# Makefile for LaMDA-FPGA

# Python configuration
PYTHON ?= python
VENV_DIR ?= venv
REQ_FILE ?= requirements.txt

# Load environment variables from .env file if it exists
-include .env
export

.PHONY: freeze_deps clean_env check_env help_root

# Check if required environment variables are set
# Usage: make check_env
check_env:
	@echo "============================================================"
	@echo "Checking environment variables"
	@echo "============================================================"
	@echo "VIVADO_PATH: $${VIVADO_PATH:-❌ Not set}"
	@echo "OPENAI_API_KEY: $${OPENAI_API_KEY:+✅ Set}$${OPENAI_API_KEY:-❌ Not set}"
	@echo "GEMINI_API_KEY: $${GEMINI_API_KEY:+✅ Set}$${GEMINI_API_KEY:-❌ Not set}"
	@echo "OPENROUTER_API_KEY: $${OPENROUTER_API_KEY:+✅ Set}$${OPENROUTER_API_KEY:-❌ Not set}"
	@echo ""
	@if [ -z "$${VIVADO_PATH}" ]; then \
		echo "⚠️  VIVADO_PATH is not set"; \
	fi
	@if [ -z "$${OPENAI_API_KEY}" ] && [ -z "$${GEMINI_API_KEY}" ] && [ -z "$${OPENROUTER_API_KEY}" ]; then \
		echo "⚠️  No API keys set. Set at least OPENAI_API_KEY, GEMINI_API_KEY, or OPENROUTER_API_KEY"; \
	fi
	@echo "============================================================"

# Freeze current dependencies to requirements.txt
# Usage: make freeze_deps
freeze_deps:
	@echo "============================================================"
	@echo "Freezing dependencies to $(REQ_FILE)"
	@echo "============================================================"
	@. $(VENV_DIR)/bin/activate && pip freeze > $(REQ_FILE)
	@echo "✅ Dependencies frozen to $(REQ_FILE)"

# Clean Python virtual environment
# Usage: make clean_env
clean_env:
	@echo "Cleaning virtual environment..."
	@rm -rf $(VENV_DIR)
	@echo "✅ Virtual environment removed!"

# Display help
help_root:
	@echo "============================================================"
	@echo "LaMDA-FPGA Root Makefile"
	@echo "============================================================"
	@echo ""
	@echo "Available targets:"
	@echo "  check_env     - Check if required environment variables are set"
	@echo "                  Usage: make check_env"
	@echo ""
	@echo "  freeze_deps   - Freeze current dependencies to requirements.txt"
	@echo "                  Usage: make freeze_deps"
	@echo ""
	@echo "  clean_env     - Remove Python virtual environment"
	@echo "                  Usage: make clean_env"
	@echo ""
	@echo "  help_root     - Display this help message"
	@echo ""
	@echo "Parameters:"
	@echo "  VENV_DIR      - Virtual environment directory (default: venv)"
	@echo "  REQ_FILE      - Requirements file (default: requirements.txt)"
	@echo ""
	@echo "Setup (handled by activate.sh, not make):"
	@echo "  source activate.sh      # First-time setup + activate venv + load .env"
	@echo ""
	@echo "Environment Setup:"
	@echo "  1. Create .env file:     cp .env.example .env"
	@echo "  2. Edit with your keys:  set OPENAI_API_KEY, GEMINI_API_KEY, or OPENROUTER_API_KEY"
	@echo "  3. Check variables:      make check_env"
	@echo ""
	@echo "Examples:"
	@echo "  make check_env"
	@echo "  make freeze_deps"
	@echo "  make clean_env"
	@echo "============================================================"