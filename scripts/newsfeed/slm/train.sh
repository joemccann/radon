#!/usr/bin/env bash
# Default trainer: LLaMA-Factory QLoRA (train only). Optional: SLM_TRAINER=mlx.
# Refuses prepaid keys and non-turso.posts sources (HR-5, HR-8).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
TRAINER="${SLM_TRAINER:-llamafactory}"
DATA_DIR="${SLM_DATA_DIR:-$ROOT/data/slm/tagger/v1}"
MANIFEST="$DATA_DIR/manifest.json"
# Default stays the Qwen2.5-1.5B baseline. SLM_LF_CONFIG selects another YAML
# (the Qwen3.5-2B evaluation candidate). A positional argument wins over both.
LF_CONFIG="${1:-${SLM_LF_CONFIG:-$ROOT/scripts/newsfeed/slm/configs/llamafactory-qwen25-1p5b-qlora-v1.yaml}}"
MLX_CONFIG="${SLM_MLX_CONFIG:-$ROOT/scripts/newsfeed/slm/configs/qwen25-1p5b-qlora-v1.yaml}"
DATASET_INFO_SRC="$ROOT/scripts/newsfeed/slm/configs/dataset_info.json"

for var in $(compgen -e || true); do
  case "$var" in
    *_API_KEY)
      if [ -n "${!var:-}" ]; then
        echo "train.sh refuses $var in the environment (HR-5). Unset prepaid keys." >&2
        exit 2
      fi
      ;;
  esac
done

if [ ! -f "$MANIFEST" ]; then
  echo "missing $MANIFEST; run scripts/slm/export_dataset.py on a credentialed host" >&2
  exit 2
fi

python3.13 - "$MANIFEST" <<'PY'
import json, sys
manifest = json.loads(open(sys.argv[1], encoding="utf-8").read())
if manifest.get("sources") != ["turso.posts"]:
    sys.stderr.write(f"train.sh refuses sources={manifest.get('sources')!r}; expected [\"turso.posts\"]\n")
    sys.exit(2)
PY

# The trainer loads third-party model and dataset code; it gets an allowlisted
# environment (toolchain, locale, HF cache and GPU knobs), never operator tokens.
# REL-319 / R-731: namespaces also contain credential variables; admit exact
# cache/offline/device options rather than every variable sharing a prefix.
TRAIN_ENV=()
for name in $(compgen -e || true); do
  case "$name" in
    PATH|HOME|USER|LOGNAME|LANG|LC_*|TMPDIR|TERM|VIRTUAL_ENV|CONDA_PREFIX|PYTHONPATH|LD_LIBRARY_PATH|DYLD_LIBRARY_PATH \
      |HF_HOME|HF_HUB_CACHE|HF_HUB_OFFLINE|HF_HUB_DISABLE_TELEMETRY|HF_HUB_DISABLE_PROGRESS_BARS \
      |HF_HUB_DOWNLOAD_TIMEOUT|HF_HUB_ETAG_TIMEOUT|HF_DATASETS_CACHE|HF_DATASETS_OFFLINE \
      |TRANSFORMERS_CACHE|TRANSFORMERS_OFFLINE|TRANSFORMERS_VERBOSITY \
      |CUDA_HOME|CUDA_PATH|CUDA_VISIBLE_DEVICES|CUDA_DEVICE_ORDER|CUDA_LAUNCH_BLOCKING \
      |NVIDIA_VISIBLE_DEVICES|NVIDIA_DRIVER_CAPABILITIES|PYTORCH_CUDA_ALLOC_CONF \
      |PYTORCH_ALLOC_CONF|PYTORCH_ENABLE_MPS_FALLBACK|TORCH_HOME|TORCH_LOGS|OMP_NUM_THREADS)
      TRAIN_ENV+=("$name=${!name}") ;;
  esac
done

if [ "$TRAINER" = "mlx" ]; then
  exec env -i "${TRAIN_ENV[@]}" python3.13 -m mlx_lm lora --config "$MLX_CONFIG"
fi

if [ "$TRAINER" != "llamafactory" ]; then
  echo "unknown SLM_TRAINER=$TRAINER (use llamafactory or mlx)" >&2
  exit 2
fi

cp "$DATASET_INFO_SRC" "$DATA_DIR/dataset_info.json"

if command -v llamafactory-cli >/dev/null 2>&1; then
  exec env -i "${TRAIN_ENV[@]}" llamafactory-cli train "$LF_CONFIG" dataset_dir="$DATA_DIR"
fi
if python3.13 -c "import llamafactory" >/dev/null 2>&1; then
  exec env -i "${TRAIN_ENV[@]}" python3.13 -m llamafactory.cli train "$LF_CONFIG" dataset_dir="$DATA_DIR"
fi
echo "LLaMA-Factory is not installed. pip install -r scripts/newsfeed/slm/requirements-slm.txt in the train venv only. Or SLM_TRAINER=mlx." >&2
exit 2
