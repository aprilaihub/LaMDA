# Makefile for LaMDA-Analog

PYTHON ?= python3
VENV_DIR ?= venv
REQ_FILE ?= requirements.txt

-include .env
export

.PHONY: check_env freeze_deps clean_env help_root

check_env:
	@echo "============================================================"
	@echo "Checking environment variables"
	@echo "============================================================"
	@echo "SPECTRE_PATH: $${SPECTRE_PATH:-Not set}"
	@if [ -n "$${OPENAI_API_KEY}" ]; then \
		echo "OPENAI_API_KEY: Set"; \
	else \
		echo "OPENAI_API_KEY: Not set"; \
	fi
	@if [ -n "$${GEMINI_API_KEY}" ]; then \
		echo "GEMINI_API_KEY: Set"; \
	else \
		echo "GEMINI_API_KEY: Not set"; \
	fi
	@echo ""
	@if [ -z "$${SPECTRE_PATH}" ]; then \
		echo "Warning: SPECTRE_PATH is not set"; \
	fi
	@if [ -z "$${OPENAI_API_KEY}" ] && [ -z "$${GEMINI_API_KEY}" ]; then \
		echo "Warning: No API keys set. Set OPENAI_API_KEY or GEMINI_API_KEY"; \
	fi
	@echo "============================================================"

freeze_deps:
	@echo "Freezing dependencies to $(REQ_FILE)"
	@. $(VENV_DIR)/bin/activate && pip freeze > $(REQ_FILE)
	@echo "Dependencies frozen to $(REQ_FILE)"

clean_env:
	@echo "Cleaning virtual environment..."
	@rm -rf $(VENV_DIR)
	@echo "Virtual environment removed"

help_root:
	@echo "============================================================"
	@echo "LaMDA-Analog Root Makefile"
	@echo "============================================================"
	@echo "Available targets:"
	@echo "  check_env     - Check required environment variables"
	@echo "  freeze_deps   - Freeze dependencies to requirements.txt"
	@echo "  clean_env     - Remove Python virtual environment"
	@echo "  help_root     - Display this help message"
	@echo "============================================================"
