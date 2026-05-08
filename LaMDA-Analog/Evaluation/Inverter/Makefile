# Makefile for LaMDA-Analog Inverter Evaluation

ROOT_DIR := $(abspath ../..)
include $(ROOT_DIR)/Makefile

INVERTER_ROOT ?= $(ROOT_DIR)/Evaluation/Inverter
MODEL ?= gpt-4o-mini
MAX_TOKENS ?= 3000
TEMPERATURE ?= 1.0
TOP_P ?= 1.0
TECH_CFG ?= $(ROOT_DIR)/config/tech_config.yml

.PHONY: run clean_outputs help

run:
	@echo "============================================================"
	@echo "Running inverter pipeline"
	@echo "Model: $(MODEL)"
	@echo "============================================================"
	@( \
		cd $(ROOT_DIR) && $(PYTHON) -B Evaluation/Inverter/Run.py \
			--model "$(MODEL)" \
			--max_tokens $(MAX_TOKENS) \
			--temperature $(TEMPERATURE) \
			--top_p $(TOP_P) \
			--tech_cfg "$(TECH_CFG)" \
	)

clean_outputs:
	@echo "Cleaning generated runs..."
	@rm -rf $(INVERTER_ROOT)/output/gen_runs $(INVERTER_ROOT)/output/sweep_results
	@echo "Done"

help:
	@echo "Targets:"
	@echo "  run            - Run inverter pipeline"
	@echo "  clean_outputs  - Remove Inverter output/gen_runs and output/sweep_results"
