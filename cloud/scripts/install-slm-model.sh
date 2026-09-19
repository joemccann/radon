#!/usr/bin/env bash
# Install a pinned GGUF from B2 (or a local file) and point current.gguf at it.
# Usage: cloud/scripts/install-slm-model.sh <version>
# Verifies sha256 against models/slm-tagger/<version>/manifest.json.
set -euo pipefail

VERSION="${1:-v1}"
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
MANIFEST="$ROOT/models/slm-tagger/$VERSION/manifest.json"
DEST_DIR="${RADON_SLM_MODEL_DIR:-/var/lib/radon/models}"
QUANT="${RADON_SLM_QUANT:-Q4_K_M}"

if [ ! -f "$MANIFEST" ]; then
  echo "missing $MANIFEST" >&2
  exit 2
fi

python3.13 - "$MANIFEST" "$DEST_DIR" "$VERSION" "$QUANT" <<'PY'
import hashlib, json, os, sys, urllib.request
from pathlib import Path

manifest_path, dest_dir, version, quant = sys.argv[1:5]
manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
info = (manifest.get("gguf") or {}).get(quant)
if not info or not info.get("sha256"):
    sys.stderr.write(f"manifest has no gguf.{quant}.sha256; export on the Mini first\n")
    sys.exit(2)
expected = info["sha256"]
name = info.get("name") or f"radon-slm-tagger-{version}.{quant}.gguf"
dest = Path(dest_dir)
dest.mkdir(parents=True, exist_ok=True)
target = dest / f"radon-slm-tagger-{version}.gguf"
src = os.environ.get("RADON_SLM_GGUF_PATH")
if src:
    data = Path(src).read_bytes()
else:
    endpoint = (os.environ.get("RADON_ARCHIVE_S3_ENDPOINT") or "").rstrip("/")
    bucket = os.environ.get("RADON_ARCHIVE_S3_BUCKET") or "radon-archive"
    key = info.get("b2_key") or f"radon-models/slm-tagger/{version}/{name}"
    if not endpoint:
        sys.stderr.write("set RADON_SLM_GGUF_PATH or RADON_ARCHIVE_S3_ENDPOINT\n")
        sys.exit(2)
    url = f"{endpoint}/{bucket}/{key}"
    sys.stderr.write(f"download {url}\n")
    with urllib.request.urlopen(url, timeout=120) as resp:
        data = resp.read()
digest = hashlib.sha256(data).hexdigest()
if digest != expected:
    sys.stderr.write(f"sha256 mismatch: got {digest} expected {expected}\n")
    sys.exit(3)
target.write_bytes(data)
current = dest / "current.gguf"
if current.is_symlink() or current.exists():
    current.unlink()
current.symlink_to(target.name)
print(f"installed {target} sha256={digest}")
PY
