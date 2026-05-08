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
# Usage: make run_test
# Usage: make MODEL=gemini-2.5-pro run_test
run_test:
	@echo "============================================================"
	@echo "Running test pipeline"
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
	@echo "                     Usage: make MODEL=gpt-4o run_test"
	@echo ""
	@echo "  help             - Display this help message"
	@echo ""
	@echo "Parameters:"
	@echo "  MODEL        - LLM model (default: gpt-4o)"
	@echo "  MAX_TOKENS   - Maximum tokens (default: 3000)"
	@echo "  TEMPERATURE  - Sampling temperature (default: 1.0)"
	@echo "  TOP_P        - Top-p value (default: 1.0)"
	@echo "  FPGA_PART    - Target FPGA part (default: xc7z020clg400-1)"
	@echo "  VERBOSE      - Verbose mode for runner (0/1, default: 0)"
	@echo ""
	@echo "Examples:"
	@echo "  make run_test"
	@echo "  make MODEL=gemini-2.5-pro run_test"
	@echo "  make VERBOSE=1 run_test"
	@echo "  make MODEL=gpt-4o MAX_TOKENS=4000 TEMPERATURE=0.7 TOP_P=0.95 run_test"
	@echo "============================================================"