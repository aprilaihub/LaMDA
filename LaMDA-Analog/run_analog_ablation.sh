#!/usr/bin/env bash
set -u -o pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

MODEL_LIST="gpt-4o-mini,gpt-4o,o1,codestral-2508,deepseek-chat-v3.1"
REPEATS=5
CASE_SET="full8"
TAG="ota_ablation"
TECH_CFG="config/tech_config.yml"
MAX_TOKENS="3000"
TEMPERATURE="1.0"
TOP_P="1.0"
FAIL_FAST=0
VENV_ACT="${VENV_ACT:-venv/bin/activate}"

usage() {
  cat <<'EOF'
Usage: ./run_analog_ablation.sh [options]

Options:
  --tag <label>                 Study label prefix (default: ota_ablation)
  --repeats <n>                 Repeats per model/case (default: 5)
  --models <csv>                Comma-separated model list
  --case_set <minimum4|full8>   Ablation case set (default: full8)
  --tech_cfg <path>             Tech config path (default: config/tech_config.yml)
  --max_tokens <n>              Max tokens per run (default: 3000)
  --temperature <x>             Temperature (default: 1.0)
  --top_p <x>                   Top-p (default: 1.0)
  --fail_fast                   Stop at first failed run
  -h, --help                    Show this help
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --tag)
      TAG="$2"
      shift 2
      ;;
    --repeats)
      REPEATS="$2"
      shift 2
      ;;
    --models)
      MODEL_LIST="$2"
      shift 2
      ;;
    --case_set)
      CASE_SET="$2"
      shift 2
      ;;
    --tech_cfg)
      TECH_CFG="$2"
      shift 2
      ;;
    --max_tokens)
      MAX_TOKENS="$2"
      shift 2
      ;;
    --temperature)
      TEMPERATURE="$2"
      shift 2
      ;;
    --top_p)
      TOP_P="$2"
      shift 2
      ;;
    --fail_fast)
      FAIL_FAST=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1"
      usage
      exit 1
      ;;
  esac
done

if [[ -f "$VENV_ACT" ]]; then
  # shellcheck disable=SC1090
  source "$VENV_ACT"
fi

python3 - <<'PY'
import sys
try:
    import numpy, pandas, matplotlib, yaml
    print("Deps OK")
except Exception as e:
    print("Missing deps:", e)
    sys.exit(1)
PY

case "$CASE_SET" in
  minimum4|full8)
    ;;
  *)
    echo "Invalid --case_set: $CASE_SET (expected minimum4 or full8)"
    exit 2
    ;;
esac

DATE_TAG="$(date +%Y%m%d)"
CAMPAIGN_ROOT="Evaluation/OTA/ota_results_${DATE_TAG}"
GEN_RUNS_ROOT="${CAMPAIGN_ROOT}/gen_runs"
MANIFEST="${CAMPAIGN_ROOT}/ablation_manifest.csv"
EMPTY_SYSTEM_PROMPT="Evaluation/OTA/system_prompt_empty.md"
USER_PROMPT_NORMAL="Evaluation/OTA/user_prompt.txt"
USER_PROMPT_VAGUE="Evaluation/OTA/user_prompt_vague.txt"

mkdir -p "$GEN_RUNS_ROOT"

echo "timestamp,model,case_id,repeat,exp_label,system_prompt_on,constraints_on,binder_on,user_prompt_variant,run_dir,status" > "$MANIFEST"

sanitize_tag() {
  local in="$1"
  local out
  out="$(echo "$in" | tr '/: ' '___' | tr -cd '[:alnum:]_.-')"
  if [[ -z "$out" ]]; then
    out="model"
  fi
  echo "$out"
}

build_cases() {
  if [[ "$CASE_SET" == "minimum4" ]]; then
    cat <<'EOF'
C0_Pplus_Tplus_Unorm|1|1|normal
C1_Pminus_Tplus_Unorm|0|1|normal
C2_Pplus_Tminus_Unorm|1|0|normal
C3_Pminus_Tminus_Unorm|0|0|normal
EOF
  else
    cat <<'EOF'
C0_Pplus_Tplus_Unorm|1|1|normal
C1_Pminus_Tplus_Unorm|0|1|normal
C2_Pplus_Tminus_Unorm|1|0|normal
C3_Pminus_Tminus_Unorm|0|0|normal
C4_Pplus_Tplus_Uvague|1|1|vague
C5_Pminus_Tplus_Uvague|0|1|vague
C6_Pplus_Tminus_Uvague|1|0|vague
C7_Pminus_Tminus_Uvague|0|0|vague
EOF
  fi
}

echo "Campaign root: $CAMPAIGN_ROOT"
echo "Models: $MODEL_LIST"
echo "Case set: $CASE_SET"
echo "Repeats: $REPEATS"

IFS=',' read -r -a MODELS <<< "$MODEL_LIST"

for model in "${MODELS[@]}"; do
  model_trimmed="$(echo "$model" | xargs)"
  model_tag="$(sanitize_tag "$model_trimmed")"

  while IFS='|' read -r case_id prompt_on binder_on user_variant; do
    [[ -z "$case_id" ]] && continue

    for ((rep=1; rep<=REPEATS; rep++)); do
      rep_tag="R$(printf '%02d' "$rep")"
      ts="$(date +%Y%m%d_%H%M%S)"
      exp_label="${TAG}_${case_id}_${rep_tag}"
      run_dir="${GEN_RUNS_ROOT}/${ts}_ota_${model_tag}_${case_id}_${rep_tag}"

      user_prompt="$USER_PROMPT_NORMAL"
      if [[ "$user_variant" == "vague" ]]; then
        user_prompt="$USER_PROMPT_VAGUE"
      fi

      declare -a cmd
      cmd=(
        python3 Evaluation/OTA/Run.py
        --model "$model_trimmed"
        --max_tokens "$MAX_TOKENS"
        --temperature "$TEMPERATURE"
        --top_p "$TOP_P"
        --run_root "$CAMPAIGN_ROOT"
        --run_dir "$run_dir"
        --tech_cfg "$TECH_CFG"
        --user_prompt "$user_prompt"
        --exp_label "$exp_label"
      )

      if [[ "$prompt_on" == "0" ]]; then
        cmd+=(--system_prompt "$EMPTY_SYSTEM_PROMPT" --ablate_constraints)
      fi
      if [[ "$binder_on" == "0" ]]; then
        cmd+=(--ablate_binder)
      fi

      echo "[RUN] model=$model_trimmed case=$case_id rep=$rep_tag"
      "${cmd[@]}"
      rc=$?

      status="ok"
      if [[ $rc -ne 0 ]]; then
        status="failed_${rc}"
      fi

      constraints_on="1"
      if [[ "$prompt_on" == "0" ]]; then
        constraints_on="0"
      fi

      echo "${ts},${model_trimmed},${case_id},${rep_tag},${exp_label},${prompt_on},${constraints_on},${binder_on},${user_variant},${run_dir},${status}" >> "$MANIFEST"

      if [[ $rc -ne 0 && $FAIL_FAST -eq 1 ]]; then
        echo "Fail-fast: stopping on first failure."
        exit "$rc"
      fi
    done
  done < <(build_cases)
done

echo "Done. Manifest: $MANIFEST"