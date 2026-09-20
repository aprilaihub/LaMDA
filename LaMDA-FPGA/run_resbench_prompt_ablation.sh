#!/usr/bin/env bash
set -u

# Residual prompt ablation runner for ResBench ID 9.
# Runs only missing conditions by default:
# - no_system: constrained + --no_system_prompt
# - no_design: constrained + --no_design_prompt
#
# Optional include mode adds baseline conditions:
#   INCLUDE_BASELINE=1 ./run_resbench_prompt_ablation.sh

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
RESBENCH_DIR="$ROOT_DIR/Evaluation/ResBench"
ANALYSIS_DIR="$RESBENCH_DIR/Analysis"
ABLATION_DIR="$ANALYSIS_DIR/ablation"

PYTHON_BIN="${PYTHON:-python}"
DESIGN_ID="${DESIGN_ID:-9}"
REPEATS="${REPEATS:-5}"
MAX_TOKENS="${MAX_TOKENS:-3000}"
TEMPERATURE="${TEMPERATURE:-1.5}"
TOP_P="${TOP_P:-0.75}"
FPGA_PART="${FPGA_PART:-xc7z020clg400-1}"
INCLUDE_BASELINE="${INCLUDE_BASELINE:-0}"
CONDITION="${CONDITION:-}"
MODEL_FILTER="${MODEL_FILTER:-}"
SKIP_EXISTING="${SKIP_EXISTING:-1}"
RUN_SCOPE="${RUN_SCOPE:-residual}"
EXP_LABEL="${EXP_LABEL:-}"
TIMESTAMP="$(date +"%Y%m%d_%H%M%S")"

build_case_label() {
  local condition_name="$1"
  case "$condition_name" in
    full)
      echo "systemON_designON_resbenchON"
      ;;
    no_resbench)
      echo "systemON_designON_resbenchOFF"
      ;;
    no_system)
      echo "systemOFF_designON_resbenchON"
      ;;
    no_design)
      echo "systemON_designOFF_resbenchON"
      ;;
    *)
      echo "$condition_name"
      ;;
  esac
}

normalize_condition_alias() {
  local c="$1"
  case "$c" in
    full) echo "systemON_designON_resbenchON" ;;
    no_resbench) echo "systemON_designON_resbenchOFF" ;;
    no_system) echo "systemOFF_designON_resbenchON" ;;
    no_design) echo "systemON_designOFF_resbenchON" ;;
    *) echo "$c" ;;
  esac
}

MODELS=(
  "gpt-4o-mini"
  "gpt-4o"
  "o1"
  "codestral-2508"
  "deepseek-chat-v3.1"
)

mkdir -p "$ABLATION_DIR"
SUMMARY_FILE="$ABLATION_DIR/ablation_runs_${TIMESTAMP}.tsv"
echo -e "model\tcondition\trepeat\tdesign_id\toutput_json\tstatus\texit_code" > "$SUMMARY_FILE"

# condition format: case_label|dataset_variant|extra_flags
BASELINE_CONDITIONS=(
  "systemON_designON_resbenchON|constrained|"
  "systemON_designON_resbenchOFF|noconstraints|"
)

NON_BASELINE_CONDITIONS=(
  "systemOFF_designON_resbenchON|constrained|--no_system_prompt"
  "systemON_designOFF_resbenchON|constrained|--no_design_prompt"
  "systemON_designOFF_resbenchOFF|noconstraints|--no_design_prompt"
  "systemOFF_designON_resbenchOFF|noconstraints|--no_system_prompt"
  "systemOFF_designOFF_resbenchON|constrained|--no_system_prompt --no_design_prompt"
  "systemOFF_designOFF_resbenchOFF|noconstraints|--no_system_prompt --no_design_prompt"
)

REQUESTED_MISSING_CONDITIONS=(
  "systemON_designOFF_resbenchON|constrained|--no_design_prompt"
  "systemON_designOFF_resbenchOFF|noconstraints|--no_design_prompt"
  "systemOFF_designON_resbenchOFF|noconstraints|--no_system_prompt"
  "systemOFF_designOFF_resbenchON|constrained|--no_system_prompt --no_design_prompt"
  "systemOFF_designOFF_resbenchOFF|noconstraints|--no_system_prompt --no_design_prompt"
)

CONDITIONS=("${NON_BASELINE_CONDITIONS[@]}")

if [[ "$RUN_SCOPE" == "all" ]] || [[ "$INCLUDE_BASELINE" == "1" ]]; then
  CONDITIONS+=("${BASELINE_CONDITIONS[@]}")
fi

if [[ "$RUN_SCOPE" != "residual" && "$RUN_SCOPE" != "all" && "$RUN_SCOPE" != "all_except_baseline" && "$RUN_SCOPE" != "requested_missing" ]]; then
  echo "Invalid RUN_SCOPE='$RUN_SCOPE'. Valid values: residual, all, all_except_baseline, requested_missing"
  exit 1
fi

# residual keeps legacy two-condition behavior.
if [[ "$RUN_SCOPE" == "residual" ]]; then
  CONDITIONS=(
    "systemOFF_designON_resbenchON|constrained|--no_system_prompt"
    "systemON_designOFF_resbenchON|constrained|--no_design_prompt"
  )
fi

# all_except_baseline explicitly means run every non-baseline ablation condition.
if [[ "$RUN_SCOPE" == "all_except_baseline" ]]; then
  CONDITIONS=("${NON_BASELINE_CONDITIONS[@]}")
fi

# requested_missing runs the 5 combinations requested by user.
if [[ "$RUN_SCOPE" == "requested_missing" ]]; then
  CONDITIONS=("${REQUESTED_MISSING_CONDITIONS[@]}")
fi

if [[ -n "$CONDITION" ]]; then
  CONDITION="$(normalize_condition_alias "$CONDITION")"
  FILTERED_CONDITIONS=()
  for cond in "${CONDITIONS[@]}"; do
    IFS='|' read -r cond_name _ _ <<< "$cond"
    if [[ "$cond_name" == "$CONDITION" ]]; then
      FILTERED_CONDITIONS+=("$cond")
    fi
  done
  if [[ ${#FILTERED_CONDITIONS[@]} -eq 0 ]]; then
    echo "Invalid CONDITION='$CONDITION'."
    exit 1
  fi
  CONDITIONS=("${FILTERED_CONDITIONS[@]}")
fi

if [[ -n "$MODEL_FILTER" ]]; then
  FILTERED_MODELS=()
  for model in "${MODELS[@]}"; do
    if [[ "$model" == "$MODEL_FILTER" ]]; then
      FILTERED_MODELS+=("$model")
    fi
  done
  if [[ ${#FILTERED_MODELS[@]} -eq 0 ]]; then
    echo "Invalid MODEL_FILTER='$MODEL_FILTER'."
    exit 1
  fi
  MODELS=("${FILTERED_MODELS[@]}")
fi

expected_runs=$(( ${#MODELS[@]} * ${#CONDITIONS[@]} * REPEATS ))
run_count=0
fail_count=0
skip_count=0

echo "============================================================"
echo "ResBench Prompt Ablation Runner"
echo "Root: $ROOT_DIR"
echo "Output folder: $ABLATION_DIR"
echo "Design ID: $DESIGN_ID"
echo "Repeats: $REPEATS"
echo "Include baseline: $INCLUDE_BASELINE"
echo "Run scope: $RUN_SCOPE"
if [[ -n "$EXP_LABEL" ]]; then
  echo "Experiment label prefix: $EXP_LABEL"
fi
if [[ -n "$CONDITION" ]]; then
  echo "Condition filter: $CONDITION"
fi
if [[ -n "$MODEL_FILTER" ]]; then
  echo "Model filter: $MODEL_FILTER"
fi
echo "Skip existing: $SKIP_EXISTING"
echo "Expected runs: $expected_runs"
echo "============================================================"

cd "$ROOT_DIR" || exit 1

for model in "${MODELS[@]}"; do
  for cond in "${CONDITIONS[@]}"; do
    IFS='|' read -r cond_name dataset_variant cond_flags <<< "$cond"
    case_label="$(build_case_label "$cond_name")"

    for rep in $(seq 1 "$REPEATS"); do
      run_count=$((run_count + 1))
      if [[ -n "$EXP_LABEL" ]]; then
        file_stem="exp_${EXP_LABEL}_${model}_${case_label}_r${rep}"
      else
        file_stem="exp_${model}_${case_label}_r${rep}"
      fi
      existing_pattern="$ABLATION_DIR/${file_stem}_*.json"
      legacy_pattern="$ABLATION_DIR/exp_*_${model}_${cond_name}_r${rep}_*.json"

      if [[ "$SKIP_EXISTING" == "1" ]] && { compgen -G "$existing_pattern" > /dev/null || compgen -G "$legacy_pattern" > /dev/null; }; then
        skip_count=$((skip_count + 1))
        echo "[$run_count/$expected_runs] model=$model condition=$cond_name case=$case_label repeat=$rep -> SKIP (existing result found)"
        echo -e "${model}\t${cond_name}\t${rep}\t${DESIGN_ID}\t\tSKIPPED\t0" >> "$SUMMARY_FILE"
        continue
      fi

      json_name="ablation/${file_stem}_${TIMESTAMP}.json"

      echo "[$run_count/$expected_runs] model=$model condition=$cond_name case=$case_label repeat=$rep"

      cmd=(
        "$PYTHON_BIN" -B "Evaluation/ResBench/Run.py"
        --design_id "$DESIGN_ID"
        --model "$model"
        --max_tokens "$MAX_TOKENS"
        --temperature "$TEMPERATURE"
        --top_p "$TOP_P"
        --fpga_part "$FPGA_PART"
        --dataset_variant "$dataset_variant"
        --output_json "$json_name"
        --verbose
      )

      if [[ -n "$cond_flags" ]]; then
        # shellcheck disable=SC2206
        flags_array=($cond_flags)
        cmd+=("${flags_array[@]}")
      fi

      "${cmd[@]}"
      exit_code=$?

      echo -e "${model}\t${cond_name}\t${rep}\t${DESIGN_ID}\t${json_name}\tRUN\t${exit_code}" >> "$SUMMARY_FILE"

      if [[ $exit_code -ne 0 ]]; then
        fail_count=$((fail_count + 1))
      fi

      rm -rf "$RESBENCH_DIR/Outputs/"
    done
  done
done

echo "============================================================"
echo "Completed runs: $run_count / $expected_runs"
echo "Skipped existing: $skip_count"
echo "Failures: $fail_count"
echo "Run manifest: $SUMMARY_FILE"
echo "Results folder: $ABLATION_DIR"
echo "============================================================"
