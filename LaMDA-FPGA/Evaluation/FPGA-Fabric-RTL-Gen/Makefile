# Makefile for LaMDA-FPGA Fabric-Aware RTL Generation Evaluation

# Import variables from root Makefile
ROOT_DIR := $(abspath ../..)
include $(ROOT_DIR)/Makefile

FABRIC_EVAL_ROOT ?= $(ROOT_DIR)/Evaluation/FPGA-Fabric-RTL-Gen
ANALYSIS_DIR ?= $(FABRIC_EVAL_ROOT)/Analysis

OUTPUTS_DIR ?= $(FABRIC_EVAL_ROOT)/Outputs

# Default model / generation parameters
MODEL ?= gpt-4o
MAX_TOKENS ?= 3000
TEMPERATURE ?= 1.0
TOP_P ?= 1.0
FPGA_PART ?= xc7a100tcsg324-1
MAX_ATTEMPTS ?= 2
STOP_STAGE ?= impl
STYLE ?= fabric_aware
STYLES ?= baseline fabric_aware explicit_primitive
FEEDBACK_MODE ?= compressed

# Design selection (dataset has IDs 1-5)
DESIGN_ID ?= 1
ID_START ?= 1
ID_END ?= 5

# CSV / results processing parameters
JSON_FILE ?= fabric_exp_results.json
INPUT_CSV ?= fabric_full_results.csv
OUTPUT_CSV ?= fabric_summary_results.csv
COMPARISON_CSV ?= fabric_style_comparison.csv

.PHONY: run_fabric_rtl_gen run_fabric_rtl_gen_all run_fabric_rtl_gen_all_styles \
        organize_outputs clean_outputs \
        fabric_json_to_csv fabric_compress_csv fabric_generate_csv fabric_style_comparison help

# Run the pipeline for a single design
# Usage: make MODEL=gpt-4o DESIGN_ID=1 run_fabric_rtl_gen
run_fabric_rtl_gen:
	@if [ -z "$(MODEL)" ]; then \
		echo "❌ Error: MODEL parameter is required. Usage: make MODEL=gpt-4o DESIGN_ID=1 run_fabric_rtl_gen"; \
		exit 1; \
	fi
	@echo "============================================================"
	@echo "Running FPGA-Fabric-RTL-Gen Pipeline"
	@echo "Model: $(MODEL)"
	@echo "Design ID: $(DESIGN_ID)"
	@echo "Style: $(STYLE)"
	@echo "FPGA Part: $(FPGA_PART)"
	@echo "Max Attempts: $(MAX_ATTEMPTS)"
	@echo "Stop Stage: $(STOP_STAGE)"
	@echo "Feedback Mode: $(FEEDBACK_MODE)"
	@echo "============================================================"
	@( \
		cd $(ROOT_DIR) && $(PYTHON) -B Evaluation/FPGA-Fabric-RTL-Gen/Run.py \
			--design_id $(DESIGN_ID) \
			--model "$(MODEL)" \
			--max_tokens $(MAX_TOKENS) \
			--temperature $(TEMPERATURE) \
			--top_p $(TOP_P) \
			--fpga_part "$(FPGA_PART)" \
			--style "$(STYLE)" \
			--max_attempts $(MAX_ATTEMPTS) \
			--stop_stage "$(STOP_STAGE)" \
			--feedback_mode "$(FEEDBACK_MODE)" \
			--output_json "$(JSON_FILE)" \
			--verbose \
	)
	@echo "✅ FPGA-Fabric-RTL-Gen pipeline completed!"

# Run the pipeline across all 5 mini-benchmark designs
# Usage: make MODEL=gpt-4o run_fabric_rtl_gen_all
run_fabric_rtl_gen_all:
	@if [ -z "$(MODEL)" ]; then \
		echo "❌ Error: MODEL parameter is required. Usage: make MODEL=gpt-4o run_fabric_rtl_gen_all"; \
		exit 1; \
	fi
	@echo "============================================================"
	@echo "Running FPGA-Fabric-RTL-Gen Pipeline on all designs ($(ID_START)-$(ID_END))"
	@echo "Model: $(MODEL)"
	@echo "Style: $(STYLE)"
	@echo "============================================================"
	@( \
		for design_id in $(shell seq $(ID_START) $(ID_END)); do \
			echo ""; \
			echo "▶ Design ID $$design_id using $(MODEL) (style: $(STYLE))"; \
			cd $(ROOT_DIR) && $(PYTHON) -B Evaluation/FPGA-Fabric-RTL-Gen/Run.py \
				--design_id $$design_id \
				--model "$(MODEL)" \
				--max_tokens $(MAX_TOKENS) \
				--temperature $(TEMPERATURE) \
				--top_p $(TOP_P) \
				--fpga_part "$(FPGA_PART)" \
				--style "$(STYLE)" \
				--max_attempts $(MAX_ATTEMPTS) \
				--stop_stage "$(STOP_STAGE)" \
				--feedback_mode "$(FEEDBACK_MODE)" \
				--output_json "$(JSON_FILE)" \
				--verbose; \
		done \
	)
	@echo "✅ FPGA-Fabric-RTL-Gen pipeline completed for all designs!"

# Run the pipeline across all designs AND all 3 generation styles
# (baseline, fabric_aware, explicit_primitive), appending every run into
# the same aggregated JSON_FILE so they can be compared afterwards with
# fabric_style_comparison.
# Usage: make MODEL=gpt-4o run_fabric_rtl_gen_all_styles
run_fabric_rtl_gen_all_styles:
	@if [ -z "$(MODEL)" ]; then \
		echo "❌ Error: MODEL parameter is required. Usage: make MODEL=gpt-4o run_fabric_rtl_gen_all_styles"; \
		exit 1; \
	fi
	@echo "============================================================"
	@echo "Running FPGA-Fabric-RTL-Gen Pipeline on all designs ($(ID_START)-$(ID_END)) x all styles ($(STYLES))"
	@echo "Model: $(MODEL)"
	@echo "============================================================"
	@( \
		for style in $(STYLES); do \
			for design_id in $(shell seq $(ID_START) $(ID_END)); do \
				echo ""; \
				echo "▶ Design ID $$design_id using $(MODEL) (style: $$style)"; \
				cd $(ROOT_DIR) && $(PYTHON) -B Evaluation/FPGA-Fabric-RTL-Gen/Run.py \
					--design_id $$design_id \
					--model "$(MODEL)" \
					--max_tokens $(MAX_TOKENS) \
					--temperature $(TEMPERATURE) \
					--top_p $(TOP_P) \
					--fpga_part "$(FPGA_PART)" \
					--style "$$style" \
					--max_attempts $(MAX_ATTEMPTS) \
					--stop_stage "$(STOP_STAGE)" \
					--feedback_mode "$(FEEDBACK_MODE)" \
					--output_json "$(JSON_FILE)" \
					--verbose; \
			done; \
		done \
	)
	@echo "✅ FPGA-Fabric-RTL-Gen pipeline completed for all designs x all styles!"

# Archive the most recent run (for a given STYLE) into a flat Runs/ folder.
# Since every invocation of Run.py already gets its own unique directory
# (Outputs/<STYLE>/design_<id>_<module>/run_<timestamp>_<pid>/, so nothing is
# ever overwritten across runs), this target is only needed if you want to
# move a completed run's artifacts out of Outputs/ into a separate archive.
# Usage: make STYLE=fabric_aware organize_outputs
organize_outputs:
	@echo "============================================================"
	@echo "Organizing FPGA-Fabric-RTL-Gen outputs (style: $(STYLE))"
	@echo "============================================================"
	@if [ ! -d "$(OUTPUTS_DIR)/$(STYLE)" ]; then \
		echo "❌ Error: Outputs directory not found for style $(STYLE). Run the pipeline first."; \
		exit 1; \
	fi
	@LATEST_REPORT=$$(find "$(OUTPUTS_DIR)/$(STYLE)" -name "fabric_report.json" -printf "%T@ %p\n" 2>/dev/null | sort -n | tail -1 | cut -d' ' -f2-); \
	if [ -z "$$LATEST_REPORT" ]; then \
		echo "❌ Error: No fabric_report.json found under $(OUTPUTS_DIR)/$(STYLE). Run the pipeline first."; \
		exit 1; \
	fi; \
	RUN_DIR=$$(dirname "$$LATEST_REPORT"); \
	DESIGN_NAME=$$($(PYTHON) -c "import json; print(json.load(open('$$LATEST_REPORT'))['module'])"); \
	timestamp=$$(date +"%Y%m%d_%H%M%S"); \
	DEST_DIR="$(OUTPUTS_DIR)/../Runs/$${DESIGN_NAME}_$(STYLE)_$${timestamp}"; \
	echo "Design name: $$DESIGN_NAME"; \
	echo "Source run directory: $$RUN_DIR"; \
	echo "Creating directory: $$DEST_DIR"; \
	mkdir -p "$$DEST_DIR"; \
	mv "$$RUN_DIR"/* "$$DEST_DIR/"; \
	rmdir "$$RUN_DIR" 2>/dev/null || true; \
	echo "✅ Outputs organized in: $$DEST_DIR"

# Clean all outputs
clean_outputs:
	@echo "Cleaning FPGA-Fabric-RTL-Gen outputs..."
	@rm -rf $(OUTPUTS_DIR)
	@echo "✅ Outputs cleaned!"

# Convert JSON experiment results to full CSV
# Usage: make fabric_json_to_csv JSON_FILE=fabric_exp_results.json
fabric_json_to_csv:
	@if [ ! -f "$(FABRIC_EVAL_ROOT)/$(JSON_FILE)" ]; then \
		echo "❌ Error: JSON file $(FABRIC_EVAL_ROOT)/$(JSON_FILE) not found"; \
		exit 1; \
	fi
	@echo "============================================================"
	@echo "Converting JSON to CSV"
	@echo "Input: $(JSON_FILE)"
	@echo "Output: $(INPUT_CSV)"
	@echo "============================================================"
	@( \
		cd $(FABRIC_EVAL_ROOT) && $(PYTHON) -c "from Analysis.FabricAnalysis import fabric_json_to_csv; \
			fabric_json_to_csv('$(JSON_FILE)', '$(INPUT_CSV)')" \
	)
	@echo "✅ CSV file generated at $(FABRIC_EVAL_ROOT)/$(INPUT_CSV)"

# Compress full CSV to summary CSV
# Usage: make fabric_compress_csv INPUT_CSV=fabric_full_results.csv OUTPUT_CSV=fabric_summary_results.csv
fabric_compress_csv:
	@if [ ! -f "$(FABRIC_EVAL_ROOT)/$(INPUT_CSV)" ]; then \
		echo "❌ Error: CSV file $(FABRIC_EVAL_ROOT)/$(INPUT_CSV) not found"; \
		exit 1; \
	fi
	@echo "============================================================"
	@echo "Compressing CSV"
	@echo "Input: $(INPUT_CSV)"
	@echo "Output: $(OUTPUT_CSV)"
	@echo "============================================================"
	@( \
		cd $(FABRIC_EVAL_ROOT) && $(PYTHON) -c "from Analysis.FabricAnalysis import fabric_compress_csv; \
			fabric_compress_csv('$(INPUT_CSV)', '$(OUTPUT_CSV)')" \
	)
	@echo "✅ Compressed CSV file generated at $(FABRIC_EVAL_ROOT)/$(OUTPUT_CSV)"

# Generate both full and summary CSV from JSON (runs both steps)
# Usage: make fabric_generate_csv JSON_FILE=fabric_exp_results.json
fabric_generate_csv: fabric_json_to_csv
	@$(MAKE) fabric_compress_csv INPUT_CSV=$(INPUT_CSV) OUTPUT_CSV=$(OUTPUT_CSV)
	@echo "============================================================"
	@echo "✅ CSV generation complete!"
	@echo "   Full results: $(FABRIC_EVAL_ROOT)/$(INPUT_CSV)"
	@echo "   Summary: $(FABRIC_EVAL_ROOT)/$(OUTPUT_CSV)"
	@echo "============================================================"

# Build a per-module, per-style comparison CSV from an aggregated results
# JSON containing runs from multiple --style values (e.g. produced by
# run_fabric_rtl_gen_all_styles). One row per (module, style) pair.
# Usage: make JSON_FILE=fabric_exp_results.json fabric_style_comparison
fabric_style_comparison:
	@if [ ! -f "$(FABRIC_EVAL_ROOT)/$(JSON_FILE)" ]; then \
		echo "❌ Error: JSON file $(FABRIC_EVAL_ROOT)/$(JSON_FILE) not found"; \
		exit 1; \
	fi
	@echo "============================================================"
	@echo "Building style comparison CSV"
	@echo "Input: $(JSON_FILE)"
	@echo "Output: $(COMPARISON_CSV)"
	@echo "============================================================"
	@( \
		cd $(FABRIC_EVAL_ROOT) && $(PYTHON) -c "from Analysis.FabricAnalysis import fabric_style_comparison; \
			fabric_style_comparison('$(JSON_FILE)', '$(COMPARISON_CSV)')" \
	)
	@echo "✅ Style comparison CSV generated at $(FABRIC_EVAL_ROOT)/$(COMPARISON_CSV)"

# Display help
help:
	@echo "============================================================"
	@echo "FPGA-Fabric-RTL-Gen Evaluation Makefile"
	@echo "============================================================"
	@echo ""
	@echo "Available targets:"
	@echo "  run_fabric_rtl_gen      - Run the pipeline for a single design"
	@echo "                            Usage: make MODEL=gpt-4o DESIGN_ID=1 run_fabric_rtl_gen"
	@echo ""
	@echo "  run_fabric_rtl_gen_all  - Run the pipeline across all mini-benchmark designs (single style)"
	@echo "                            Usage: make MODEL=gpt-4o run_fabric_rtl_gen_all"
	@echo ""
	@echo "  run_fabric_rtl_gen_all_styles - Run the pipeline across all designs x all 3 styles"
	@echo "                            (baseline, fabric_aware, explicit_primitive), appending"
	@echo "                            every run to JSON_FILE for later comparison"
	@echo "                            Usage: make MODEL=gpt-4o run_fabric_rtl_gen_all_styles"
	@echo ""
	@echo "  organize_outputs        - Move outputs into a design/timestamp-specific subfolder"
	@echo "                            Usage: make STYLE=fabric_aware organize_outputs"
	@echo ""
	@echo "  clean_outputs           - Remove all generated outputs"
	@echo ""
	@echo "  fabric_json_to_csv      - Convert JSON results to full CSV"
	@echo "                            Usage: make JSON_FILE=fabric_exp_results.json fabric_json_to_csv"
	@echo ""
	@echo "  fabric_compress_csv     - Compress full CSV to summary CSV"
	@echo "                            Usage: make fabric_compress_csv INPUT_CSV=full.csv OUTPUT_CSV=summary.csv"
	@echo ""
	@echo "  fabric_generate_csv     - Generate both full and summary CSV from JSON"
	@echo "                            Usage: make JSON_FILE=fabric_exp_results.json fabric_generate_csv"
	@echo ""
	@echo "  fabric_style_comparison - Build a per-module, per-style comparison CSV"
	@echo "                            (requires JSON_FILE with entries from multiple --style runs)"
	@echo "                            Usage: make JSON_FILE=fabric_exp_results.json fabric_style_comparison"
	@echo ""
	@echo "  help                    - Display this help message"
	@echo ""
	@echo "Parameters:"
	@echo "  MODEL           - LLM model to use (required, e.g., gpt-4o, gemini-2.0-flash-exp)"
	@echo "  DESIGN_ID       - Dataset design ID for a single run (default: 1, range: 1-5)"
	@echo "  ID_START/ID_END - Design ID range for run_fabric_rtl_gen_all(_styles) (default: 1-5)"
	@echo "  MAX_TOKENS      - Maximum tokens for generation (default: 3000)"
	@echo "  TEMPERATURE     - Temperature for sampling (default: 1.0)"
	@echo "  TOP_P           - Top-p for sampling (default: 1.0)"
	@echo "  FPGA_PART       - FPGA part number (default: xc7a100tcsg324-1)"
	@echo "  STYLE           - RTL generation style for single-style targets: baseline,"
	@echo "                    fabric_aware, or explicit_primitive (default: fabric_aware)"
	@echo "  STYLES          - Space-separated styles used by run_fabric_rtl_gen_all_styles"
	@echo "                    (default: baseline fabric_aware explicit_primitive)"
	@echo "  MAX_ATTEMPTS    - Max total generation attempts, including the first (default: 2)"
	@echo "  STOP_STAGE      - Final Vivado stage: synth (skip impl) or impl (default: impl)"
	@echo "  FEEDBACK_MODE   - Self-correction log feedback: compressed (capped extracted"
	@echo "                    errors/failing vectors) or full (complete raw log verbatim)"
	@echo "                    (default: compressed)"
	@echo "  JSON_FILE       - Aggregated results JSON file name (default: fabric_exp_results.json)"
	@echo "  INPUT_CSV       - Input CSV file name (default: fabric_full_results.csv)"
	@echo "  OUTPUT_CSV      - Output CSV file name (default: fabric_summary_results.csv)"
	@echo "  COMPARISON_CSV  - Style comparison CSV file name (default: fabric_style_comparison.csv)"
	@echo ""
	@echo "Examples:"
	@echo "  make MODEL=gpt-4o DESIGN_ID=3 STYLE=explicit_primitive run_fabric_rtl_gen"
	@echo "  make MODEL=gpt-4o run_fabric_rtl_gen_all"
	@echo "  make MODEL=gpt-4o run_fabric_rtl_gen_all_styles"
	@echo "  make JSON_FILE=fabric_exp_results.json fabric_generate_csv"
	@echo "  make JSON_FILE=fabric_exp_results.json fabric_style_comparison"
	@echo "============================================================"
