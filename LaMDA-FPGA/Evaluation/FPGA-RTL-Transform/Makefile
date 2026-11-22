# Makefile for FPGA-RTL-Transform: LLM-driven RTL transformation for Xilinx 7-Series (LUT / FF / DSP / BRAM / Fmax).
#
# Run `make help` for the targets, every variable with its default and valid values, and a typical workflow.
# The README of this folder describes the flow, the dataset and the output layout.

# Folder of this Makefile, so that `make -C <dir>` and `make -f <path>` work from anywhere.
MAKEFILE_DIR := $(dir $(abspath $(lastword $(MAKEFILE_LIST))))

# Import PYTHON, the .env loading and `check_env` from the LaMDA-FPGA Makefile.
ROOT_DIR := $(abspath $(MAKEFILE_DIR)/../..)
include $(ROOT_DIR)/Makefile

# `make` with no target shows the help (the root Makefile would otherwise make `check_env` the default).
.DEFAULT_GOAL := help

TRANSFORM_ROOT ?= $(ROOT_DIR)/Evaluation/FPGA-RTL-Transform
OUTPUTS_DIR ?= $(TRANSFORM_ROOT)/Outputs

# ---------------------------------------------------------------------------------------------------------------
# Variables (override on the command line: make VAR=value target)
# ---------------------------------------------------------------------------------------------------------------

# LLM (LLM_PROVIDER has no default: set it on the command line or in .env)
MODEL ?= gpt-4o
MAX_TOKENS ?= 12000
TEMPERATURE ?= 1.0
TOP_P ?= 1.0

# Experiment switches (see `make help`)
KNOWLEDGE ?= primers
FEEDBACK_DETAIL ?= reports
KNOWLEDGE_CHOICES := none primers primers+patterns
FEEDBACK_CHOICES := none metrics reports reports+logs reports+logs+edits
STAGE_CHOICES := synth impl

# Flow
FPGA_PART ?= xc7a100tcsg324-1
STOP_STAGE ?= impl
MAX_ATTEMPTS ?= 6
STOP_ON_SUCCESS ?= false
# Aggregated results, one entry per Run.py invocation (relative to this folder)
RESULTS_FILE ?= Outputs/fabric_opt_results.json

# Designs of Dataset/fabric_opt_problems.json
DESIGN_ID ?= 1
DESIGN_IDS ?= 1 2 3

# Feedback-level sweep
SWEEP_LEVELS ?= $(FEEDBACK_CHOICES)
SWEEP_REPEATS ?= 1

# Optional pass-through arguments (empty by default)
EXTRA_ARGS ?=
ABLATION_ARGS ?=
RUN_STATS_ARGS ?=
ATTRIBUTE_ARGS ?=
CHECK_ARGS ?=

.PHONY: help run_rtl_transform run_rtl_transform_all run_feedback_sweep measure_baselines \
        ablation_report run_stats attribute build_dataset apply_measurements check_designs \
        test organize_outputs clean_outputs \
        _require_provider _check_switches _check_design_id

# ---------------------------------------------------------------------------------------------------------------
# Input validation (fail early, before any Vivado or LLM time is spent)
# ---------------------------------------------------------------------------------------------------------------

# $(call check_choice,<variable name>,<value>,<allowed values>)
define check_choice
@case " $(3) " in *" $(2) "*) ;; *) echo "Error: $(1)=$(2) is not valid. Allowed: $(3)"; exit 1;; esac
endef

# $(call check_ids,<variable name>,<value>): every word must be a positive integer
define check_ids
@for id in $(2); do case "$$id" in ''|*[!0-9]*) echo "Error: $(1) must be positive integers (see make help), got '$(2)'"; exit 1;; esac; done
endef

_require_provider:
	@if [ -z "$(LLM_PROVIDER)" ]; then \
		echo "Error: LLM_PROVIDER is required (openai, gemini, anthropic, openrouter or elm)."; \
		echo "Example: make LLM_PROVIDER=openai MODEL=gpt-4o DESIGN_ID=1 run_rtl_transform"; \
		echo "Run 'make check_env' to see which API keys are set."; \
		exit 1; \
	fi

_check_switches:
	$(call check_choice,KNOWLEDGE,$(KNOWLEDGE),$(KNOWLEDGE_CHOICES))
	$(call check_choice,FEEDBACK_DETAIL,$(FEEDBACK_DETAIL),$(FEEDBACK_CHOICES))
	$(call check_choice,STOP_STAGE,$(STOP_STAGE),$(STAGE_CHOICES))
	$(call check_choice,STOP_ON_SUCCESS,$(STOP_ON_SUCCESS),true false)

_check_design_id:
	$(call check_ids,DESIGN_ID,$(DESIGN_ID))

# ---------------------------------------------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------------------------------------------

# Transform one design: the LLM receives the reference RTL and its measured baseline, and iterates on the Vivado
# feedback; every attempt is compared with the baseline.
# Usage: make LLM_PROVIDER=openai MODEL=gpt-4o DESIGN_ID=1 [KNOWLEDGE=none] [FEEDBACK_DETAIL=metrics] run_rtl_transform
run_rtl_transform: _require_provider _check_switches _check_design_id
	@echo "============================================================"
	@echo "FPGA-RTL-Transform"
	@echo "Provider: $(LLM_PROVIDER)  Model: $(MODEL)  Design ID: $(DESIGN_ID)"
	@echo "Knowledge: $(KNOWLEDGE)  Feedback detail: $(FEEDBACK_DETAIL)"
	@echo "Max attempts: $(MAX_ATTEMPTS)  Stop on success: $(STOP_ON_SUCCESS)  Stop stage: $(STOP_STAGE)  Part: $(FPGA_PART)"
	@echo "============================================================"
	@( \
		cd $(ROOT_DIR) && $(PYTHON) -B Evaluation/FPGA-RTL-Transform/Run.py \
			--design_id $(DESIGN_ID) \
			--model "$(MODEL)" \
			--max_tokens $(MAX_TOKENS) \
			--temperature $(TEMPERATURE) \
			--top_p $(TOP_P) \
			--fpga_part "$(FPGA_PART)" \
			--knowledge "$(KNOWLEDGE)" \
			--feedback_detail "$(FEEDBACK_DETAIL)" \
			--max_attempts $(MAX_ATTEMPTS) \
			$(if $(filter true,$(STOP_ON_SUCCESS)),--stop_on_success) \
			--stop_stage "$(STOP_STAGE)" \
			--output_json "$(RESULTS_FILE)" \
			$(EXTRA_ARGS) \
			--verbose \
	)

# Transform every design in DESIGN_IDS. Sequential on purpose (one Vivado job at a time).
# Usage: make LLM_PROVIDER=openai MODEL=gpt-4o [DESIGN_IDS="1 2 3 4 5 6 7"] run_rtl_transform_all
run_rtl_transform_all: _require_provider _check_switches
	$(call check_ids,DESIGN_IDS,$(DESIGN_IDS))
	@for design_id in $(DESIGN_IDS); do \
		echo ""; \
		echo "Transform design ID $$design_id using $(MODEL)"; \
		$(MAKE) --no-print-directory DESIGN_ID=$$design_id run_rtl_transform || exit 1; \
	done

# Feedback-level ablation: every design in DESIGN_IDS at every level in SWEEP_LEVELS, SWEEP_REPEATS times, with the
# same KNOWLEDGE, STOP_STAGE and attempt budget. The levels are cumulative, so two neighbouring levels differ by
# exactly one kind of evidence. Sequential on purpose (one Vivado job at a time).
# Usage: make LLM_PROVIDER=openai MODEL=gpt-4o [SWEEP_LEVELS="metrics reports"] [SWEEP_REPEATS=3] run_feedback_sweep
run_feedback_sweep: _require_provider _check_switches
	$(call check_ids,SWEEP_REPEATS,$(SWEEP_REPEATS))
	@for level in $(SWEEP_LEVELS); do \
		case " $(FEEDBACK_CHOICES) " in *" $$level "*) ;; *) echo "Error: SWEEP_LEVELS entry '$$level' is not valid. Allowed: $(FEEDBACK_CHOICES)"; exit 1;; esac; \
	done
	@for repeat in $$(seq 1 $(SWEEP_REPEATS)); do \
		for level in $(SWEEP_LEVELS); do \
			echo ""; \
			echo "Sweep: repeat $$repeat, feedback level $$level"; \
			$(MAKE) --no-print-directory FEEDBACK_DETAIL=$$level run_rtl_transform_all || exit 1; \
		done; \
	done

# Vivado baseline of the reference RTL of every design in DESIGN_IDS (no LLM call, no API key needed).
# Usage: make measure_baselines [DESIGN_IDS="1 2"] [STOP_STAGE=synth]
measure_baselines:
	$(call check_choice,STOP_STAGE,$(STOP_STAGE),$(STAGE_CHOICES))
	$(call check_ids,DESIGN_IDS,$(DESIGN_IDS))
	@for design_id in $(DESIGN_IDS); do \
		echo "Baseline of design ID $$design_id"; \
		( cd $(ROOT_DIR) && $(PYTHON) -B Evaluation/FPGA-RTL-Transform/Run.py --design_id $$design_id --baseline_only \
			--fpga_part "$(FPGA_PART)" --stop_stage "$(STOP_STAGE)" --output_json "$(RESULTS_FILE)" --verbose ) || exit 1; \
	done

# ---------------------------------------------------------------------------------------------------------------
# Analyse (finished runs)
# ---------------------------------------------------------------------------------------------------------------

# Compare the configurations (stage, KNOWLEDGE, FEEDBACK_DETAIL) over the finished runs in Outputs/ (no Vivado).
# Usage: make ablation_report [ABLATION_ARGS="--model gpt-4o --markdown Outputs/ablation.md"]
ablation_report:
	@cd $(TRANSFORM_ROOT) && $(PYTHON) Analysis/AblationReport.py $(ABLATION_ARGS)

# Time, tokens, attempts, quality and transformations of the latest run of each design, written to
# Outputs/run_statistics.md (no Vivado, no LLM). RUN_STATS_ARGS are passed to Analysis/RunStats.py.
# Usage: make run_stats [RUN_STATS_ARGS="--model gpt-4o --price_in 3 --price_out 15"]
run_stats:
	@cd $(TRANSFORM_ROOT) && $(PYTHON) Analysis/RunStats.py $(RUN_STATS_ARGS)

# Per-edit attribution of one attempt of a finished run: reference + each group of edits, attempt - each group
# (2 Vivado flows per group). REPORT is the fabric_report.json of the run (path relative to this folder).
# Usage: make REPORT=Outputs/.../run_<id>/fabric_report.json [ATTEMPT=3] [ATTRIBUTE_ARGS="--group hunk --plan_only"] attribute
attribute:
	@if [ -z "$(REPORT)" ]; then \
		echo "Error: REPORT is required. Usage: make REPORT=Outputs/.../run_<id>/fabric_report.json attribute"; \
		exit 1; \
	fi
	@cd $(TRANSFORM_ROOT) && $(PYTHON) -B Analysis/Attribute.py --report "$(REPORT)" $(if $(ATTEMPT),--attempt $(ATTEMPT)) $(ATTRIBUTE_ARGS)

# ---------------------------------------------------------------------------------------------------------------
# Dataset
# ---------------------------------------------------------------------------------------------------------------

# Rebuild Dataset/fabric_opt_problems.json from Dataset/src/ (no Vivado needed).
build_dataset:
	@cd $(TRANSFORM_ROOT) && $(PYTHON) Dataset/build_dataset.py

# Fold the latest measure_baselines results into Dataset/src/*/measured.json, then rebuild the dataset.
apply_measurements:
	@cd $(TRANSFORM_ROOT) && $(PYTHON) Dataset/apply_measurements.py --table && $(PYTHON) Dataset/build_dataset.py

# Simulate every reference RTL against its testbench with xsim and check the latency contract (needs Vivado).
# Usage: make check_designs [CHECK_ARGS="1 2"]
check_designs:
	@cd $(TRANSFORM_ROOT) && $(PYTHON) Dataset/check_designs.py $(CHECK_ARGS)

# ---------------------------------------------------------------------------------------------------------------
# Housekeeping
# ---------------------------------------------------------------------------------------------------------------

# Offline unit tests (no Vivado, no LLM). The Tests/ folder is not part of the public repository.
test:
	@if [ ! -d "$(TRANSFORM_ROOT)/Tests" ]; then \
		echo "No Tests/ folder here (it is kept out of the public repository). Nothing to run."; \
	else \
		cd $(TRANSFORM_ROOT) && $(PYTHON) -m unittest discover -s Tests; \
	fi

# Move the most recent run of one KNOWLEDGE / FEEDBACK_DETAIL combination into a flat Runs/ folder.
# Every invocation of Run.py already gets its own directory
#   Outputs/<knowledge>/<feedback_detail>/design_<id>_<module>/run_<timestamp>_<pid>/
# so nothing is overwritten; this target only moves a finished run out of Outputs/.
# Usage: make KNOWLEDGE=primers FEEDBACK_DETAIL=reports organize_outputs
organize_outputs: _check_switches
	@SEARCH_DIR="$(OUTPUTS_DIR)/$(KNOWLEDGE)/$(FEEDBACK_DETAIL)"; \
	if [ ! -d "$$SEARCH_DIR" ]; then \
		echo "Error: no outputs in $$SEARCH_DIR. Run the pipeline first."; \
		exit 1; \
	fi; \
	LATEST_REPORT=$$(find "$$SEARCH_DIR" -name "fabric_report.json" -printf "%T@ %p\n" 2>/dev/null | sort -n | tail -1 | cut -d' ' -f2-); \
	if [ -z "$$LATEST_REPORT" ]; then \
		echo "Error: no fabric_report.json under $$SEARCH_DIR."; \
		exit 1; \
	fi; \
	RUN_DIR=$$(dirname "$$LATEST_REPORT"); \
	DESIGN_NAME=$$($(PYTHON) -c "import json,sys; print(json.load(open(sys.argv[1]))['module'])" "$$LATEST_REPORT"); \
	DEST_DIR="$(TRANSFORM_ROOT)/Runs/$${DESIGN_NAME}_$(KNOWLEDGE)_$(FEEDBACK_DETAIL)_$$(date +%Y%m%d_%H%M%S)"; \
	echo "Source run directory: $$RUN_DIR"; \
	mkdir -p "$$DEST_DIR"; \
	mv "$$RUN_DIR"/* "$$DEST_DIR/"; \
	rmdir "$$RUN_DIR" 2>/dev/null || true; \
	echo "Outputs organized in: $$DEST_DIR"

# Remove all generated outputs (Outputs/, including the aggregated results file).
clean_outputs:
	@echo "Cleaning FPGA-RTL-Transform outputs..."
	@rm -rf $(OUTPUTS_DIR)
	@echo "Outputs cleaned."

# ---------------------------------------------------------------------------------------------------------------
# Help (plain text; the current values of the variables are filled in)
# ---------------------------------------------------------------------------------------------------------------

define HELP_TEXT
FPGA-RTL-Transform: an LLM rewrites Verilog to cut LUT/FF/DSP/BRAM or raise Fmax at the same latency;
Vivado (xsim, synthesis, implementation) checks every attempt.

USAGE   make [VARIABLE=value ...] <target>

TYPICAL WORKFLOW
  1. make check_env                     API keys, LLM_PROVIDER and VIVADO_PATH set? (from the LaMDA-FPGA Makefile)
  2. make measure_baselines             measure the reference RTL once (Vivado, no LLM); then: make apply_measurements
  3. make LLM_PROVIDER=openai MODEL=gpt-4o DESIGN_ID=1 run_rtl_transform
  4. make ablation_report               compare configurations over the finished runs

TARGETS: RUN
  run_rtl_transform       Transform one design (DESIGN_ID). Needs LLM_PROVIDER and Vivado.
                            make LLM_PROVIDER=openai MODEL=gpt-4o DESIGN_ID=1 run_rtl_transform
  run_rtl_transform_all   Transform every design in DESIGN_IDS, one after the other.
                            make LLM_PROVIDER=openai MODEL=gpt-4o DESIGN_IDS="1 2 3 4 5 6 7" run_rtl_transform_all
  run_feedback_sweep      Every design in DESIGN_IDS at every level in SWEEP_LEVELS, SWEEP_REPEATS times.
                            make LLM_PROVIDER=openai MODEL=gpt-4o SWEEP_LEVELS="metrics reports" SWEEP_REPEATS=3 run_feedback_sweep
  measure_baselines       Vivado baseline of the reference RTL of every design in DESIGN_IDS. No LLM call.
                            make DESIGN_IDS="1 2" STOP_STAGE=synth measure_baselines

TARGETS: ANALYSE (finished runs)
  ablation_report         Table of the finished runs per (stage, KNOWLEDGE, FEEDBACK_DETAIL). No Vivado.
                            make ablation_report ABLATION_ARGS="--model gpt-4o --markdown Outputs/ablation.md"
  run_stats               Time, tokens, attempts, quality and transformations of the latest run of each design,
                          written to Outputs/run_statistics.md. No Vivado, no LLM.
                            make run_stats RUN_STATS_ARGS="--price_in 3 --price_out 15"
  attribute               Which edit of an attempt caused which metric change. 2 Vivado flows per group of edits.
                            make REPORT=Outputs/.../run_<id>/fabric_report.json ATTEMPT=3 attribute

TARGETS: DATASET
  build_dataset           Rebuild Dataset/fabric_opt_problems.json from Dataset/src/. No Vivado.
  apply_measurements      Fold the measured baselines into Dataset/src/*/measured.json, then rebuild.
  check_designs           xsim check of every reference RTL against its testbench and mutants. Needs Vivado.
                            make check_designs CHECK_ARGS="1 2"

TARGETS: HOUSEKEEPING
  test                    Offline unit tests (no Vivado, no LLM), when a Tests/ folder is present.
  organize_outputs        Move the latest run of KNOWLEDGE / FEEDBACK_DETAIL from Outputs/ to Runs/.
  clean_outputs           Delete Outputs/ (all runs and the aggregated results file).
  help                    This text.

VARIABLES (current value in brackets; the valid values follow)
  LLM_PROVIDER      [$(LLM_PROVIDER)]  openai | gemini | anthropic | openrouter | elm. Required for run_*; set it here or in .env.
  MODEL             [$(MODEL)]  model name understood by the provider.
  MAX_TOKENS        [$(MAX_TOKENS)]  token limit of one LLM answer (all modules plus reasoning).
  TEMPERATURE       [$(TEMPERATURE)]  sampling temperature.
  TOP_P             [$(TOP_P)]  nucleus sampling.
  DESIGN_ID         [$(DESIGN_ID)]  design of run_rtl_transform:
                      1 sa_os_4x4 (output-stationary array)    5 conv3x3 (3x3 convolution, line buffer)
                      2 sa_ws_4x4 (weight-stationary array)    6 sat_dot32 (saturating dot product)
                      3 sa_is_4x4 (input-stationary array)     7 sa_os_8x8 (8x8 output-stationary array)
                      4 fir16 (16-tap FIR filter)
  DESIGN_IDS        [$(DESIGN_IDS)]  space-separated design numbers (quote the list) for the *_all, sweep and baseline targets.
  KNOWLEDGE         [$(KNOWLEDGE)]  what the LLM is told about the FPGA fabric:
                      none              nothing beyond what it already knows
                      primers           + resource primers (LUT, DSP48E1, block RAM)
                      primers+patterns  + generic RTL transformation patterns and a guide to reading the Vivado results
  FEEDBACK_DETAIL   [$(FEEDBACK_DETAIL)]  evidence after each attempt; every level adds to the previous one:
                      none | metrics | reports | reports+logs | reports+logs+edits
                      (verdict only | + LUT/FF/DSP/BRAM/WNS/Fmax | + report digest | + tool log digest | + record of earlier edits)
  STOP_STAGE        [$(STOP_STAGE)]  synth (synthesis only, faster) | impl (adds place and route; Fmax needs it).
  MAX_ATTEMPTS      [$(MAX_ATTEMPTS)]  LLM attempts per run (the baseline run is not counted).
  STOP_ON_SUCCESS   [$(STOP_ON_SUCCESS)]  true | false. true stops at the first correct attempt that beats the baseline in one metric.
  FPGA_PART         [$(FPGA_PART)]  Xilinx 7-Series part.
  RESULTS_FILE      [$(RESULTS_FILE)]  aggregated results (one entry per run), relative to this folder.
  SWEEP_LEVELS      [$(SWEEP_LEVELS)]  levels compared by run_feedback_sweep.
  SWEEP_REPEATS     [$(SWEEP_REPEATS)]  repetitions of each level.
  EXTRA_ARGS        extra arguments appended to Run.py by run_rtl_transform, e.g. EXTRA_ARGS="--dataset_file my_problems.json".
  REPORT, ATTEMPT   attribute: fabric_report.json of the run, and the attempt to analyse (default: the best attempt).
  ATTRIBUTE_ARGS    extra arguments of Analysis/Attribute.py, e.g. "--group hunk --plan_only" (see its --help).
  ABLATION_ARGS     extra arguments of Analysis/AblationReport.py.
  RUN_STATS_ARGS    extra arguments of Analysis/RunStats.py: --model, --markdown FILE, --json FILE, --price_in / --price_out (USD per million tokens).
  CHECK_ARGS        design numbers for check_designs; empty checks all.
  PYTHON            [$(PYTHON)]  Python interpreter.

ENVIRONMENT (not Makefile variables; put them in the environment or a .env file)
  VIVADO_PATH       Vivado 2024.1 installation, used by every Vivado step.
  <PROVIDER>_API_KEY  OPENAI_API_KEY, GEMINI_API_KEY, ANTHROPIC_API_KEY, OPENROUTER_API_KEY or ELM_API_KEY.

OUTPUT   Outputs/<KNOWLEDGE>/<FEEDBACK_DETAIL>/design_<id>_<module>/run_<timestamp>_<pid>/  (see README.md)
endef
export HELP_TEXT

help:
	@echo "$$HELP_TEXT"
