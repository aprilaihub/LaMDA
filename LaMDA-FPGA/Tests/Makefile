# Makefile for LaMDA-FPGA Tests

# Import variables from root Makefile
ROOT_DIR := $(abspath ..)
include $(ROOT_DIR)/Makefile

TESTS_ROOT ?= $(ROOT_DIR)/Tests

# Default parameters
MODEL ?= gpt-4o
MAX_TOKENS ?= 3000
TEMPERATURE ?= 1.0
TOP_P ?= 1.0
FPGA_PART ?= xc7z020clg400-1
VERBOSE ?= 0

.PHONY: run_test help

# Run test pipeline with default parameters
# Usage: make LLM_PROVIDER=openai run_test
# Usage: make LLM_PROVIDER=gemini MODEL=gemini-2.5-pro run_test
run_test:
	@if [ -z "$(LLM_PROVIDER)" ]; then \
		echo "Error: LLM_PROVIDER is required (openai, gemini, anthropic, openrouter, elm). Usage: make LLM_PROVIDER=openai MODEL=gpt-4o run_test"; \
		exit 1; \
	fi
	@echo "============================================================"
	@echo "Running test pipeline"
	@echo "Provider: $(LLM_PROVIDER)"
	@echo "Model: $(MODEL)"
	@echo "============================================================"
	@( \
		cd $(ROOT_DIR) && $(PYTHON) -B Tests/Run_Test.py \
			--model "$(MODEL)" \
			--max_tokens $(MAX_TOKENS) \
			--temperature $(TEMPERATURE) \
			--top_p $(TOP_P) \
			--fpga_part "$(FPGA_PART)" \
			$(if $(filter 1 true TRUE yes YES,$(VERBOSE)),--verbose,) \
	)
	@echo "✅ Test pipeline completed!"

# Display help
help:
	@echo "============================================================"
	@echo "LaMDA-FPGA Tests Makefile"
	@echo "============================================================"
	@echo ""
	@echo "Available targets:"
	@echo "  run_test         - Run the test pipeline"
	@echo "                     Usage: make LLM_PROVIDER=openai MODEL=gpt-4o run_test"
	@echo ""
	@echo "  help             - Display this help message"
	@echo ""
	@echo "Parameters:"
	@echo "  LLM_PROVIDER - openai|gemini|anthropic|openrouter|elm (required)"
	@echo "  MODEL        - LLM model (default: gpt-4o)"
	@echo "  MAX_TOKENS   - Maximum tokens (default: 3000)"
	@echo "  TEMPERATURE  - Sampling temperature (default: 1.0)"
	@echo "  TOP_P        - Top-p value (default: 1.0)"
	@echo "  FPGA_PART    - Target FPGA part (default: xc7z020clg400-1)"
	@echo "  VERBOSE      - Verbose mode for runner (0/1, default: 0)"
	@echo ""
	@echo "Examples:"
	@echo "  make LLM_PROVIDER=openai run_test"
	@echo "  make LLM_PROVIDER=gemini MODEL=gemini-2.5-pro run_test"
	@echo "  make LLM_PROVIDER=openai VERBOSE=1 run_test"
	@echo "  make LLM_PROVIDER=openai MODEL=gpt-4o MAX_TOKENS=4000 TEMPERATURE=0.7 TOP_P=0.95 run_test"
	@echo "============================================================"