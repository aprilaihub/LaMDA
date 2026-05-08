# Makefile for LaMDA-Analog OTA Evaluation

ROOT_DIR := $(abspath ../..)
include $(ROOT_DIR)/Makefile

OTA_ROOT ?= $(ROOT_DIR)/Evaluation/OTA
MODEL ?= gpt-4o-mini
MAX_TOKENS ?= 3000
TEMPERATURE ?= 1.0
TOP_P ?= 1.0
TECH_CFG ?= $(ROOT_DIR)/config/tech_config.yml

.PHONY: run clean_outputs help

run:
	@echo "============================================================"
	@echo "Running OTA pipeline"
	@echo "Model: $(MODEL)"
	@echo "============================================================"
	@( \
		cd $(ROOT_DIR) && $(PYTHON) -B Evaluation/OTA/Run.py \
			--model "$(MODEL)" \
			--max_tokens $(MAX_TOKENS) \
			--temperature $(TEMPERATURE) \
			--top_p $(TOP_P) \
			--tech_cfg "$(TECH_CFG)" \
	)

clean_outputs:
	@echo "Cleaning generated runs..."
	@rm -rf $(OTA_ROOT)/output/gen_runs $(OTA_ROOT)/output/sweep_results
	@echo "Done"

help:
	@echo "Targets:"
	@echo "  run            - Run OTA pipeline"
	@echo "  clean_outputs  - Remove OTA output/gen_runs and output/sweep_results"
