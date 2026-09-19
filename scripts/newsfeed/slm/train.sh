#!/usr/bin/env bash
# Default trainer: LLaMA-Factory QLoRA (train only). Optional: SLM_TRAINER=mlx.
# Refuses prepaid keys and non-turso.posts sources (HR-5, HR-8).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
TRAINER="${SLM_TRAINER:-llamafactory}"
DATA_DIR="${SLM_DATA_DIR:-$ROOT/data/slm/tagger/v1}"
MANIFEST="$DATA_DIR/manifest.json"
LF_CONFIG="${1:-$ROOT/scripts/newsfeed/slm/configs/llamafactory-qwen25-1p5b-qlora-v1.yaml}"
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

if [ "$TRAINER" = "mlx" ]; then
  exec python3.13 -m mlx_lm lora --config "$MLX_CONFIG"
fi

if [ "$TRAINER" != "llamafactory" ]; then
  echo "unknown SLM_TRAINER=$TRAINER (use llamafactory or mlx)" >&2
  exit 2
fi

cp "$DATASET_INFO_SRC" "$DATA_DIR/dataset_info.json"

if command -v llamafactory-cli >/dev/null 2>&1; then
  exec llamafactory-cli train "$LF_CONFIG" dataset_dir="$DATA_DIR"
fi
if python3.13 -c "import llamafactory" >/dev/null 2>&1; then
  exec python3.13 -m llamafactory.cli train "$LF_CONFIG" dataset_dir="$DATA_DIR"
fi
echo "LLaMA-Factory is not installed. On the Mini: pip install 'llamafactory[torch,metrics]'. Or SLM_TRAINER=mlx." >&2
exit 2
