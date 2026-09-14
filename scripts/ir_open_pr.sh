#!/usr/bin/env bash
# Post-push helper: ensure an open PR exists for a fix/** IR branch.
# Never merges. Fail closed if gh is missing or the PAT lacks pull_requests:write.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
if command -v python3.13 >/dev/null 2>&1; then
  PY=python3.13
else
  PY=python3
fi
exec "$PY" "$ROOT/scripts/ir_ensure_pr.py" "$@"
