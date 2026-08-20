# Makefile for ResBench Evaluation

# Import variables from root Makefile
ROOT_DIR := $(abspath ../..)
include $(ROOT_DIR)/Makefile

# ResBench configuration
RESBENCH_ROOT ?= $(ROOT_DIR)/Evaluation/ResBench
ANALYSIS_DIR ?= $(RESBENCH_ROOT)/Analysis

# Experiment parameters
ID_START ?= 1
ID_END ?= 56
ITER ?= 1
MODEL ?= gpt-4o
DATASET_VARIANT ?= constrained
PREPROCESS_SEED ?=

# CSV processing parameters
JSON_FILE ?= exp_results.json
INPUT_CSV ?= full_results.csv
OUTPUT_CSV ?= summary_results.csv

.PHONY: preprocess_dataset preprocess_dataset_noconstraints run_llm_eda_resbench clean_experiments_log json_to_csv compress_csv generate_csv reorganize_results help

# Preprocess the ResBench dataset with custom constraints
# Usage: make preprocess_dataset
preprocess_dataset:
	@echo "============================================================"
	@echo "Preprocessing ResBench Dataset"
	@echo "============================================================"
	@( \
		$(PYTHON) -B $(RESBENCH_ROOT)/Dataset/Preprocessed/ResBenchPreprocessing.py \
			--delay_min_ns 1.0 \
			--delay_max_ns 10.0 \
			--freq_min_mhz 100.0 \
			--freq_max_mhz 1000.0 \
			$(if $(PREPROCESS_SEED),--seed $(PREPROCESS_SEED),) \
			--include_constraints_in_problem \
			--output_json_name problems_preprocessed.json \
	)
	@echo "✅ Dataset preprocessing completed!"

# Preprocess the ResBench dataset without embedding constraints in the Problem text
# Usage: make preprocess_dataset_noconstraints
preprocess_dataset_noconstraints:
	@echo "============================================================"
	@echo "Preprocessing ResBench Dataset (No Constraints in Problem)"
	@echo "============================================================"
	@( \
		$(PYTHON) -B $(RESBENCH_ROOT)/Dataset/Preprocessed/ResBenchPreprocessing.py \
			--delay_min_ns 1.0 \
			--delay_max_ns 10.0 \
			--freq_min_mhz 100.0 \
			--freq_max_mhz 1000.0 \
			$(if $(PREPROCESS_SEED),--seed $(PREPROCESS_SEED),) \
			--exclude_constraints_in_problem \
			--exclude_timing_constraints \
			--output_json_name problems_preprocessed_noconstraints.json \
	)
	@echo "✅ No-constraints dataset preprocessing completed!"

# Run LLM-EDA pipeline on ResBench dataset
# Usage: make MODEL=gpt-4o run_llm_eda_resbench
# Usage: make MODEL=gemini-2.0-flash-exp ID_START=1 ID_END=10 ITER=5 run_llm_eda_resbench
run_llm_eda_resbench:
	@if [ -z "$(MODEL)" ]; then \
		echo "❌ Error: MODEL parameter is required. Usage: make MODEL=gpt-4o run_llm_eda_resbench"; \
		exit 1; \
	fi
	@echo "============================================================"
	@echo "Running ResBench Evaluation Pipeline"
	@echo "Model: $(MODEL)"
	@echo "Design IDs: $(ID_START) to $(ID_END)"
	@echo "Iterations per design: $(ITER)"
	@echo "Dataset Variant: $(DATASET_VARIANT)"
	@echo "============================================================"
	@( \
		timestamp=$$(date +"%Y%m%d_%H%M%S"); \
		for exp_id in $(shell seq $(ID_START) $(ID_END)); do \
			for iter in $(shell seq 1 $(ITER)); do \
				echo ""; \
				echo "▶ Iteration $$iter/$(ITER) for Design ID $$exp_id using $(MODEL)"; \
				cd $(ROOT_DIR) && $(PYTHON) -B Evaluation/ResBench/Run.py \
					--design_id $$exp_id \
					--model "$(MODEL)" \
					--max_tokens 3000 \
					--temperature 1.5 \
					--top_p 0.75 \
					--fpga_part "xc7z020clg400-1" \
					--dataset_variant "$(DATASET_VARIANT)" \
					--output_json "exp_$(MODEL)_$(DATASET_VARIANT)_$${timestamp}.json" \
					--verbose; \
				rm -rf $(ROOT_DIR)/Evaluation/ResBench/Outputs/; \
			done \
		done \
	)
	@echo "✅ ResBench evaluation pipeline completed!"

# Convert JSON experiment results to full CSV
# Usage: make json_to_csv JSON_FILE=exp_results.json
json_to_csv:
	@if [ ! -f "$(ANALYSIS_DIR)/$(JSON_FILE)" ]; then \
		echo "❌ Error: JSON file $(ANALYSIS_DIR)/$(JSON_FILE) not found"; \
		exit 1; \
	fi
	@echo "============================================================"
	@echo "Converting JSON to CSV"
	@echo "Input: $(JSON_FILE)"
	@echo "Output: $(INPUT_CSV)"
	@echo "============================================================"
	@$(PYTHON) -c "from Analysis.ResBenchAnalysis import json_to_csv; \
		json_to_csv('$(ANALYSIS_DIR)/$(JSON_FILE)', '$(ANALYSIS_DIR)/$(INPUT_CSV)')"
	@echo "✅ CSV file generated at $(ANALYSIS_DIR)/$(INPUT_CSV)"

# Compress full CSV to summary CSV
# Usage: make compress_csv INPUT_CSV=full_results.csv OUTPUT_CSV=summary_results.csv
compress_csv:
	@if [ ! -f "$(ANALYSIS_DIR)/$(INPUT_CSV)" ]; then \
		echo "❌ Error: CSV file $(ANALYSIS_DIR)/$(INPUT_CSV) not found"; \
		exit 1; \
	fi
	@echo "============================================================"
	@echo "Compressing CSV"
	@echo "Input: $(INPUT_CSV)"
	@echo "Output: $(OUTPUT_CSV)"
	@echo "============================================================"
	@$(PYTHON) -c "from Analysis.ResBenchAnalysis import compress_csv; \
		compress_csv('$(ANALYSIS_DIR)/$(INPUT_CSV)', '$(ANALYSIS_DIR)/$(OUTPUT_CSV)')"
	@echo "✅ Compressed CSV file generated at $(ANALYSIS_DIR)/$(OUTPUT_CSV)"

# Generate both full and summary CSV from JSON (runs both steps)
# Usage: make generate_csv JSON_FILE=exp_results.json
generate_csv: json_to_csv
	@$(MAKE) compress_csv INPUT_CSV=$(INPUT_CSV) OUTPUT_CSV=$(OUTPUT_CSV)
	@echo "============================================================"
	@echo "✅ CSV generation complete!"
	@echo "   Full results: $(ANALYSIS_DIR)/$(INPUT_CSV)"
	@echo "   Summary: $(ANALYSIS_DIR)/$(OUTPUT_CSV)"
	@echo "============================================================"

# Reorganize experiment results into proper directory structure
# Usage: make reorganize_results JSON_FILE=exp_gpt-4o_20260113_113910.json
reorganize_results:
	@if [ -z "$(JSON_FILE)" ]; then \
		echo "❌ Error: JSON_FILE parameter is required."; \
		echo "Usage: make JSON_FILE=exp_gpt-4o_20260113_113910.json reorganize_results"; \
		exit 1; \
	fi
	@echo "============================================================"
	@echo "Reorganizing Experiment Results"
	@echo "JSON File: $(JSON_FILE)"
	@echo "============================================================"
	@$(PYTHON) -c "from Analysis.ResBenchAnalysis import PostProcessor; \
		PostProcessor.reorganize_experiment_results('$(ANALYSIS_DIR)', '$(JSON_FILE)', '$(INPUT_CSV)', '$(OUTPUT_CSV)')"
	@echo "✅ Reorganization complete!"

# Clean experiment logs
# Usage: make clean_experiments_log
clean_experiments_log:
	@echo "Cleaning experiment logs..."
	@rm -f $(RESBENCH_ROOT)/runs/exp*.json
	@echo "✅ Experiment logs cleaned!"

# Display help
help:
	@echo "============================================================"
	@echo "ResBench Evaluation Makefile"
	@echo "============================================================"
	@echo ""
	@echo "Available targets:"
	@echo "  preprocess_dataset     - Preprocess the ResBench dataset"
	@echo "                           Usage: make preprocess_dataset"
	@echo ""
	@echo "  preprocess_dataset_noconstraints - Preprocess dataset without constraints in Problem text"
	@echo "                           Usage: make preprocess_dataset_noconstraints"
	@echo ""
	@echo "  run_llm_eda_resbench   - Run LLM-EDA pipeline on ResBench"
	@echo "                           Usage: make MODEL=gpt-4o run_llm_eda_resbench"
	@echo ""
	@echo "  json_to_csv            - Convert JSON results to full CSV"
	@echo "                           Usage: make JSON_FILE=exp_results.json json_to_csv"
	@echo ""
	@echo "  compress_csv           - Compress full CSV to summary CSV"
	@echo "                           Usage: make compress_csv INPUT_CSV=full.csv OUTPUT_CSV=summary.csv"
	@echo ""
	@echo "  generate_csv           - Generate both full and summary CSV from JSON"
	@echo "                           Usage: make JSON_FILE=exp_results.json generate_csv"
	@echo ""
	@echo "  reorganize_results     - Reorganize experiment results"
	@echo "                           Usage: make JSON_FILE=exp_gpt-4o_20260318.json reorganize_results"
	@echo ""
	@echo "  clean_experiments_log  - Clean experiment log files"
	@echo "                           Usage: make clean_experiments_log"
	@echo ""
	@echo "  help                   - Display this help message"
	@echo ""
	@echo "Parameters:"
	@echo "  MODEL        - LLM model to use (required for run_llm_eda_resbench)"
	@echo "  ID_START     - Starting design ID (default: 1)"
	@echo "  ID_END       - Ending design ID (default: 56)"
	@echo "  ITER         - Number of iterations per design (default: 1)"
	@echo "  DATASET_VARIANT - constrained|noconstraints (default: constrained)"
	@echo "  PREPROCESS_SEED - Integer seed for deterministic preprocessing (optional)"
	@echo "  JSON_FILE    - Input JSON file name (default: exp_results.json)"
	@echo "  INPUT_CSV    - Input CSV file name (default: full_results.csv)"
	@echo "  OUTPUT_CSV   - Output CSV file name (default: summary_results.csv)"
	@echo ""
	@echo "Examples:"
	@echo "  make preprocess_dataset"
	@echo "  make PREPROCESS_SEED=123 preprocess_dataset"
	@echo "  make PREPROCESS_SEED=123 preprocess_dataset_noconstraints"
	@echo "  make preprocess_dataset_noconstraints"
	@echo "  make MODEL=gpt-4o run_llm_eda_resbench"
	@echo "  make MODEL=gpt-4o DATASET_VARIANT=noconstraints run_llm_eda_resbench"
	@echo "  make MODEL=gemini-2.0-flash-exp ID_START=1 ID_END=10 ITER=5 run_llm_eda_resbench"
	@echo "  make JSON_FILE=exp_gpt-4o_20260318.json generate_csv"
	@echo "  make JSON_FILE=exp_gpt-4o_20260318.json reorganize_results"
	@echo "============================================================"
