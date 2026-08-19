#!/usr/bin/env bash
set -euo pipefail

MODEL="${MODEL:-gpt-4o-mini}"
VENV_ACT="${VENV_ACT:-venv/bin/activate}"
TECH_CFG="${TECH_CFG:-config/tech_config.yml}"
MAX_TOKENS="${MAX_TOKENS:-3000}"
TEMPERATURE="${TEMPERATURE:-1.0}"
TOP_P="${TOP_P:-1.0}"
RUN_SWEEP="${RUN_SWEEP:-0}"
DESIGN="${DESIGN:-inverter}"
SYSTEM_PROMPT="${SYSTEM_PROMPT:-}"
USER_PROMPT="${USER_PROMPT:-}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --design)
      DESIGN="$2"
      shift 2
      ;;
    --model)
      MODEL="$2"
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
    --run-sweep)
      RUN_SWEEP=1
      shift
      ;;
    --system_prompt)
      SYSTEM_PROMPT="$2"
      shift 2
      ;;
    --user_prompt)
      USER_PROMPT="$2"
      shift 2
      ;;
    *)
      echo "Unknown argument: $1"
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

RUN_ID="$(date +%Y%m%d_%H%M%S)"
MODEL_TAG="$(echo "$MODEL" | tr '/: ' '___' | tr -cd '[:alnum:]_.-')"
MODEL_TAG="${MODEL_TAG:-model}"
PROMPT_LABEL=""
if [[ -n "$SYSTEM_PROMPT" || -n "$USER_PROMPT" ]]; then
  PROMPT_LABEL="_customprompt"
fi

# Normalize design selector and map to canonical folder names.
DESIGN="$(echo "$DESIGN" | tr '[:upper:]' '[:lower:]')"
if [[ "$DESIGN" == "inverter" ]]; then
  DESIGN_DIR="Inverter"
elif [[ "$DESIGN" == "ota" ]]; then
  DESIGN_DIR="OTA"
else
  echo "Unsupported design: $DESIGN (expected inverter or ota)"
  exit 2
fi

RUN_DIR="Evaluation/${DESIGN_DIR}/output/gen_runs/${RUN_ID}_${DESIGN}_${MODEL_TAG}${PROMPT_LABEL}"
mkdir -p "$RUN_DIR"

echo "DESIGN=$DESIGN"
echo "MODEL=$MODEL"
echo "RUN_DIR=$RUN_DIR"

declare -a EXTRA=()
if [[ "$RUN_SWEEP" == "1" ]]; then
  EXTRA+=("--run_sweep")
fi
if [[ -n "$SYSTEM_PROMPT" ]]; then
  EXTRA+=("--system_prompt" "$SYSTEM_PROMPT")
fi
if [[ -n "$USER_PROMPT" ]]; then
  EXTRA+=("--user_prompt" "$USER_PROMPT")
fi

if [[ "$DESIGN" == "inverter" ]]; then
  python3 Evaluation/Inverter/Run.py \
    --model "$MODEL" \
    --max_tokens "$MAX_TOKENS" \
    --temperature "$TEMPERATURE" \
    --top_p "$TOP_P" \
    --run_dir "$RUN_DIR" \
    --tech_cfg "$TECH_CFG" \
    "${EXTRA[@]+${EXTRA[@]}}"
elif [[ "$DESIGN" == "ota" ]]; then
  python3 Evaluation/OTA/Run.py \
    --model "$MODEL" \
    --max_tokens "$MAX_TOKENS" \
    --temperature "$TEMPERATURE" \
    --top_p "$TOP_P" \
    --run_dir "$RUN_DIR" \
    --tech_cfg "$TECH_CFG" \
    "${EXTRA[@]+${EXTRA[@]}}"
else
  echo "Unsupported design: $DESIGN (expected inverter or ota)"
  exit 2
fi

echo "Done: $RUN_DIR"
