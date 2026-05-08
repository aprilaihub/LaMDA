# Makefile for LaMDA-FPGA Custom Evaluation Pipeline

# Import variables from root Makefile
ROOT_DIR := $(abspath ../..)
include $(ROOT_DIR)/Makefile

CUSTOM_EVAL_ROOT ?= $(ROOT_DIR)/Evaluation/Custom

OUTPUTS_DIR ?= $(CUSTOM_EVAL_ROOT)/Outputs

# Default model to use
MODEL ?= gpt-4o

.PHONY: run_custom clean_outputs organize_outputs help

# Run test with configurable parameters
# Usage: make run_custom MODEL=gpt-4o MAX_TOKENS=4000 TEMPERATURE=0.7
MAX_TOKENS ?= 3000
TEMPERATURE ?= 1.0
TOP_P ?= 1.0
FPGA_PART ?= xc7z020clg400-1

run_custom:
	@if [ -z "$(MODEL)" ]; then \
		echo "❌ Error: MODEL parameter is required. Usage: make MODEL=gpt-4o run_custom"; \
		exit 1; \
	fi
	@echo "============================================================"
	@echo "Running LaMDA-FPGA Testing Pipeline"
	@echo "Model: $(MODEL)"
	@echo "Max Tokens: $(MAX_TOKENS)"
	@echo "Temperature: $(TEMPERATURE)"
	@echo "Top-p: $(TOP_P)"
	@echo "FPGA Part: $(FPGA_PART)"
	@echo "============================================================"
	@( \
		cd $(ROOT_DIR) && $(PYTHON) -B Evaluation/Custom/Run.py \
			--model "$(MODEL)" \
			--max_tokens $(MAX_TOKENS) \
			--temperature $(TEMPERATURE) \
			--top_p $(TOP_P) \
			--fpga_part "$(FPGA_PART)" \
			--verbose \
	)
	@echo "✅ Test pipeline completed!"

# Clean all outputs
clean_outputs:
	@echo "Cleaning test outputs..."
	@rm -rf $(OUTPUTS_DIR)
	@echo "✅ Test outputs cleaned!"

# Organize outputs into design-specific subfolder
# Usage: make organize_outputs
organize_outputs:
	@echo "============================================================"
	@echo "Organizing test outputs into design-specific folder"
	@echo "============================================================"
	@if [ ! -d "$(OUTPUTS_DIR)/Design_Files" ]; then \
		echo "❌ Error: Design_Files directory not found. Run the test first."; \
		exit 1; \
	fi
	@DESIGN_FILE=$$(ls $(OUTPUTS_DIR)/Design_Files/*.v 2>/dev/null | grep -v "_tb.v" | head -n 1); \
	if [ -z "$$DESIGN_FILE" ]; then \
		echo "❌ Error: No design .v file found in Design_Files."; \
		exit 1; \
	fi; \
	DESIGN_NAME=$$(basename $$DESIGN_FILE .v); \
	DESIGN_DIR="$(OUTPUTS_DIR)/$$DESIGN_NAME"; \
	echo "Design name: $$DESIGN_NAME"; \
	echo "Creating directory: $$DESIGN_DIR"; \
	mkdir -p "$$DESIGN_DIR"; \
	if [ -d "$(OUTPUTS_DIR)/Design_Files" ]; then \
		echo "Moving Design_Files..."; \
		mv "$(OUTPUTS_DIR)/Design_Files" "$$DESIGN_DIR/"; \
	fi; \
	if [ -d "$(OUTPUTS_DIR)/vivado_logs" ]; then \
		echo "Moving vivado_logs..."; \
		mv "$(OUTPUTS_DIR)/vivado_logs" "$$DESIGN_DIR/"; \
	fi; \
	if [ -f "$(OUTPUTS_DIR)/parsed_reports.json" ]; then \
		echo "Moving parsed_reports.json..."; \
		mv "$(OUTPUTS_DIR)/parsed_reports.json" "$$DESIGN_DIR/"; \
	fi; \
	if [ -f "$(OUTPUTS_DIR)/recommendations.txt" ]; then \
		echo "Moving recommendations.txt..."; \
		mv "$(OUTPUTS_DIR)/recommendations.txt" "$$DESIGN_DIR/"; \
	fi; \
	if [ -f "$(CUSTOM_EVAL_ROOT)/example_prompt.txt" ]; then \
		echo "Copying example_prompt.txt..."; \
		cp "$(CUSTOM_EVAL_ROOT)/example_prompt.txt" "$$DESIGN_DIR/"; \
	fi; \
	echo "✅ Outputs organized in: $$DESIGN_DIR"

# Display help
help:
	@echo "============================================================"
	@echo "LaMDA-FPGA Custom Evaluation Makefile"
	@echo "============================================================"
	@echo ""
	@echo "Available targets:"
	@echo "  run_custom        - Run the custom pipeline with configurable parameters"
	@echo "                      Usage: make MODEL=gpt-4o MAX_TOKENS=4000 TEMPERATURE=0.7 run_custom"
	@echo ""
	@echo "  organize_outputs  - Move outputs into a design-specific subfolder"
	@echo "                      Usage: make organize_outputs"
	@echo ""
	@echo "  clean_outputs     - Remove all generated outputs"
	@echo ""
	@echo "  help              - Display this help message"
	@echo ""
	@echo "Parameters:"
	@echo "  MODEL        - LLM model to use (required, e.g., gpt-4o, gemini-2.0-flash-exp)"
	@echo "  MAX_TOKENS   - Maximum tokens for generation (default: 3000)"
	@echo "  TEMPERATURE  - Temperature for sampling (default: 1.0)"
	@echo "  TOP_P        - Top-p for sampling (default: 1.0)"
	@echo "  FPGA_PART    - FPGA part number (default: xc7z020clg400-1)"
	@echo ""
	@echo "Examples:"
	@echo "  make MODEL=gpt-4o run_custom"
	@echo "  make MODEL=gemini-2.0-flash-exp run_custom MAX_TOKENS=4000"
	@echo "  make organize_outputs"
	@echo "============================================================"
