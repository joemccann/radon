#!/usr/bin/env bash
# mlx_lm.fuse -> convert_hf_to_gguf.py -> llama-quantize. Writes models/slm-tagger/v1/manifest.json.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
VERSION="${1:-v1}"
OUT="$ROOT/models/slm-tagger/$VERSION"
FUSED="$OUT/fused"
ADAPTER="$OUT/adapter"
BASE_BF16="${SLM_BASE_BF16:-$OUT/base-bf16}"
LLAMA_CPP="${LLAMA_CPP_DIR:-$HOME/llama.cpp}"
PIN_FILE="$ROOT/cloud/config/llama-cpp.pin"

mkdir -p "$OUT"

python3.13 -m mlx_lm fuse \
  --model "$BASE_BF16" \
  --adapter-path "$ADAPTER" \
  --save-path "$FUSED"

CONVERT="$LLAMA_CPP/convert_hf_to_gguf.py"
QUANTIZE="$LLAMA_CPP/llama-quantize"
if [ ! -f "$CONVERT" ]; then
  echo "missing $CONVERT; set LLAMA_CPP_DIR to a llama.cpp checkout at the pin in $PIN_FILE" >&2
  exit 2
fi

python3.13 "$CONVERT" "$FUSED" --outtype f16 --outfile "$OUT/radon-slm-tagger-$VERSION.f16.gguf"
"$QUANTIZE" "$OUT/radon-slm-tagger-$VERSION.f16.gguf" "$OUT/radon-slm-tagger-$VERSION.Q4_K_M.gguf" Q4_K_M
"$QUANTIZE" "$OUT/radon-slm-tagger-$VERSION.f16.gguf" "$OUT/radon-slm-tagger-$VERSION.Q8_0.gguf" Q8_0

python3.13 - "$OUT" "$VERSION" "$ROOT" <<'PY'
import hashlib, json, sys
from datetime import datetime, timezone
from pathlib import Path
out = Path(sys.argv[1])
version = sys.argv[2]
root = Path(sys.argv[3])
sys.path.insert(0, str(root / "scripts"))
from newsfeed.slm.contract import HONESTY_LABEL, SLM_BASE_ID, prompt_contract_sha256

def sha256(path: Path) -> dict:
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return {"sha256": digest, "bytes": path.stat().st_size, "name": path.name}

gguf = {}
for quant in ("Q4_K_M", "Q8_0"):
    path = out / f"radon-slm-tagger-{version}.{quant}.gguf"
    if path.exists():
        info = sha256(path)
        info["b2_key"] = f"radon-models/slm-tagger/{version}/{path.name}"
        gguf[quant] = info
adapter = out / "adapter" / "adapters.safetensors"
dataset = root / "data/slm/tagger/v1/manifest.json"
payload = {
    "version": "1.0.0",
    "honesty": HONESTY_LABEL,
    "base": SLM_BASE_ID,
    "adapter_sha256": sha256(adapter)["sha256"] if adapter.exists() else None,
    "gguf": gguf,
    "dataset_manifest_sha256": hashlib.sha256(dataset.read_bytes()).hexdigest() if dataset.exists() else None,
    "prompt_contract_sha256": prompt_contract_sha256(),
    "trained_at": datetime.now(timezone.utc).isoformat(),
    "eval": {},
}
(out / "manifest.json").write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
print(json.dumps(payload, indent=2, sort_keys=True))
PY
