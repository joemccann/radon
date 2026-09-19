#!/usr/bin/env bash
# mlx-lm QLoRA on the Mini. Refuses prepaid keys and non-turso.posts sources (HR-5, HR-8).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
CONFIG="${1:-$ROOT/scripts/newsfeed/slm/configs/qwen25-1p5b-qlora-v1.yaml}"
DATA_DIR="${SLM_DATA_DIR:-$ROOT/data/slm/tagger/v1}"
MANIFEST="$DATA_DIR/manifest.json"

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
  echo "missing $MANIFEST; run extract_cli.py on a credentialed host" >&2
  exit 2
fi

python3.13 - "$MANIFEST" <<'PY'
import json, sys
manifest = json.loads(open(sys.argv[1], encoding="utf-8").read())
if manifest.get("sources") != ["turso.posts"]:
    sys.stderr.write(f"train.sh refuses sources={manifest.get('sources')!r}; expected [\"turso.posts\"]\n")
    sys.exit(2)
PY

exec python3.13 -m mlx_lm lora --config "$CONFIG"
