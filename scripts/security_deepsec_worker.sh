#!/usr/bin/env bash
# DeepSec sibling worker — NOT a second security nightly.
# launchd job com.radon.security-deepsec runs `run`. It owns its lock,
# wall-clock cap, and sanitized dead-man. It writes verified exports into
# the shared private scratch; security nightly harvests them into the
# one remediate/deliver loop. Never remediate. Never deliver. Never merge.
set -Eeuo pipefail

MODE="${1:?usage: security_deepsec_worker.sh run|status|harvest}"
[[ "$MODE" == "run" || "$MODE" == "status" || "$MODE" == "harvest" ]] || {
  echo "unknown mode: $MODE" >&2; exit 2;
}

REPO="${RADON_WEEKEND_DEEPSEC_REPO:-${RADON_WEEKEND_REPO:-$HOME/radon-weekend/radon-security-deepsec}}"
WEEKEND_ROOT="${RADON_WEEKEND_ROOT:-$(dirname "$REPO")}"
SCRATCH="${RADON_WEEKEND_SECURITY_SCRATCH:-$WEEKEND_ROOT/.security-nightly-scratch}"
LOCK="$WEEKEND_ROOT/.security-deepsec.lock"
LOG_DIR="$REPO/logs/security-deepsec"
CAP_SECS="${RADON_WEEKEND_DEEPSEC_CAP_SECS:-28800}"
NET_TIMEOUT_SECS="${RADON_WEEKEND_NET_TIMEOUT_SECS:-120}"
TIMEOUT_BIN="$(command -v timeout || true)"
GH_BIN="$(command -v gh || true)"
DEADMAN_LABEL="security-deepsec"
DEADMAN_TITLE="Nightly DeepSec worker"
DEADMAN_CREATE_BODY="Rolling dead-man for the DeepSec sibling worker. Sanitized status only. Never a route, file, attack, secret, or account. A missing comment means the worker did not fire."
STAMP="$(date +%Y%m%dT%H%M%S)"
PHASE="deepsec"

mkdir -p "$SCRATCH" "$LOG_DIR"
chmod 700 "$SCRATCH" "$LOG_DIR" 2>/dev/null || true
RUN_LOG="$LOG_DIR/deepsec-$STAMP.log"
: > "$RUN_LOG"
chmod 600 "$RUN_LOG" 2>/dev/null || true

acquire_lock() {
  local dir="$1" held
  if ! mkdir "$dir" 2>/dev/null; then
    held="$(cat "$dir/pid" 2>/dev/null || true)"
    if [[ -z "$held" ]]; then
      echo "DeepSec lock held (pid not yet published): $dir" >&2
      return 1
    fi
    if kill -0 "$held" 2>/dev/null; then
      echo "DeepSec lock held by pid $held ($dir)" >&2
      return 1
    fi
    echo "[deepsec] reclaiming stale lock (pid $held)" >&2
    rm -rf -- "$dir"
    mkdir "$dir" 2>/dev/null || { echo "cannot take DeepSec lock $dir" >&2; return 1; }
  fi
  printf '%s\n' "$$" > "$dir/pid.tmp" && mv -f "$dir/pid.tmp" "$dir/pid"
  return 0
}

release_lock() {
  local dir="${1:-}" held
  [[ -n "$dir" && -d "$dir" ]] || return 0
  held="$(cat "$dir/pid" 2>/dev/null || true)"
  [[ "$held" == "$$" ]] || return 0
  rm -rf -- "$dir"
}

_sanitize() {
  local text="$1"
  [[ -n "$text" ]] || { printf '%s' "$text"; return 0; }
  text="$(printf '%s' "$text" | /usr/bin/sed -E \
    -e 's,https?://[^[:space:]]+,[REDACTED],g' \
    -e 's,(^|[^[:alnum:].])/(api|admin)/[A-Za-z0-9._/-]+,\1[REDACTED],g' \
    -e 's,[A-Za-z0-9./_-]+\.(py|ts|tsx|js|mjs|cjs|sh|go|rb|java|json|yml|yaml|toml|md):[0-9]+,[REDACTED],g' \
    -e 's,[Bb]earer [^[:space:]]+,Bearer [REDACTED],g' \
    -e 's#(^|[^[:alnum:]_])(sk-(ant-)?[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{22,}|xox[abpors]-[A-Za-z0-9-]{10,}|AKIA[0-9A-Z]{16}|eyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,})#\1[REDACTED]#g' \
    -e 's,[A-Za-z0-9_]*(TOKEN|SECRET|PASSWORD|PASSWD|PASS|AUTH|CREDENTIAL|API_KEY|APIKEY|_KEY)[A-Za-z0-9_]*[[:space:]]*[=:][[:space:]]*[^[:space:]]+,[REDACTED],g' \
    || true)"
  printf '%s' "$text"
}

report() {
  local status="$1" detail="$2"
  local body issue
  status="$(_sanitize "$status")"
  detail="$(_sanitize "$detail")"
  body="$(printf '**%s** %s **%s**' "$PHASE" "$STAMP" "$status")"
  [[ -n "$detail" ]] && body="${body}"$'\n'"$detail"
  [[ -n "$GH_BIN" ]] || return 0
  if [[ -n "${TIMEOUT_BIN:-}" ]]; then
    issue="$("$TIMEOUT_BIN" "$NET_TIMEOUT_SECS" "$GH_BIN" issue list --label "$DEADMAN_LABEL" --state open \
      --json number -q '.[0].number' 2>/dev/null || true)"
  else
    issue="$("$GH_BIN" issue list --label "$DEADMAN_LABEL" --state open \
      --json number -q '.[0].number' 2>/dev/null || true)"
  fi
  if [[ -z "$issue" ]]; then
    "$GH_BIN" issue create --title "$DEADMAN_TITLE" --label "$DEADMAN_LABEL" \
      --body "$DEADMAN_CREATE_BODY" >/dev/null 2>&1 || true
    issue="$("$GH_BIN" issue list --label "$DEADMAN_LABEL" --state open \
      --json number -q '.[0].number' 2>/dev/null || true)"
  fi
  [[ -n "$issue" ]] || return 0
  "$GH_BIN" issue comment "$issue" --body "$body" >/dev/null 2>&1 || true
}

_python() {
  # Prefer origin/main's copy so an agent-writable clone cannot redirect this.
  if [[ -n "${TIMEOUT_BIN:-}" ]] && git -C "$REPO" show origin/main:scripts/security_deepsec.py >/dev/null 2>&1; then
    git -C "$REPO" show origin/main:scripts/security_deepsec.py \
      | "$TIMEOUT_BIN" 30 /usr/bin/python3 -I - "$@"
    return
  fi
  /usr/bin/python3 -I - "$@" <<'PY' || true
import sys
sys.stderr.write("security_deepsec.py unavailable\n")
sys.exit(2)
PY
}

write_status() {
  local status="$1"
  mkdir -p "$SCRATCH"
  umask 077
  /usr/bin/python3 -I - "$SCRATCH" "$status" "$$" "${HEAD_SHA:-}" <<'PY'
import json, os, sys, time
from pathlib import Path
scratch, status, pid, head = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
payload = {
    "status": status,
    "pid": int(pid) if pid.isdigit() else pid,
    "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
}
if head:
    payload["head_sha"] = head
path = Path(scratch) / "deepsec-status.json"
tmp = path.with_name(path.name + ".tmp")
tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
os.chmod(tmp, 0o600)
os.replace(tmp, path)
PY
}

if [[ "$MODE" == "status" ]]; then
  _python status --scratch "$SCRATCH" || echo "DEEPSEC failed"
  exit 0
fi

if [[ "$MODE" == "harvest" ]]; then
  _python harvest --scratch "$SCRATCH" || true
  exit 0
fi

# run
if [[ ! -f "$REPO/.radon-weekend-runner" || ! -f "$REPO/.radon-security-deepsec-runner" ]]; then
  echo "REFUSING: DeepSec worktree needs .radon-weekend-runner and .radon-security-deepsec-runner" >&2
  exit 2
fi

if ! acquire_lock "$LOCK"; then
  report "still running" "another DeepSec worker holds the lock"
  echo "[deepsec] already running" | tee -a "$RUN_LOG"
  exit 0
fi
trap 'release_lock "$LOCK"' EXIT

HEAD_SHA="$(git -C "$REPO" rev-parse HEAD 2>/dev/null || true)"
write_status "running"
report "still running" "DeepSec sibling started"

DEEPSEC_BIN="$REPO/.deepsec/node_modules/.bin/deepsec"
if [[ ! -x "$DEEPSEC_BIN" ]]; then
  write_status "failed"
  report "failed" "OPERATOR_REQUIRED: DeepSec workspace is not bootstrapped"
  echo "[deepsec] OPERATOR_REQUIRED: no deepsec binary" | tee -a "$RUN_LOG"
  exit 0
fi

RUN_ID="deepsec-$STAMP"
RUN_DIR="$SCRATCH/$RUN_ID"
mkdir -p "$RUN_DIR"
chmod 700 "$RUN_DIR"
LAST_AUDITED="$(/usr/bin/python3 -I - "$SCRATCH" <<'PY' || true
import json, sys
from pathlib import Path
p = Path(sys.argv[1]) / "last-audited.json"
if not p.is_file():
    sys.exit(0)
try:
    data = json.loads(p.read_text())
except Exception:
    sys.exit(0)
engines = data.get("engines") if isinstance(data, dict) else {}
sha = ""
if isinstance(engines, dict):
    sha = engines.get("deepsec") or ""
sha = sha or (data.get("deepsec") if isinstance(data, dict) else "")
if sha:
    print(sha)
PY
)"

echo "[deepsec] start cap=${CAP_SECS}s head=${HEAD_SHA:-unknown}" | tee -a "$RUN_LOG"

set +e
if [[ -n "${TIMEOUT_BIN:-}" ]]; then
  "$TIMEOUT_BIN" -k 15 "$CAP_SECS" "$DEEPSEC_BIN" --version \
    >"$RUN_DIR/deepsec-version.log" 2>&1
else
  "$DEEPSEC_BIN" --version >"$RUN_DIR/deepsec-version.log" 2>&1
fi
# The process/revalidate/export steps stay in the private run dir. A missing
# binary already exited above; a missing range is a no-op export-ready night.
RANGE=""
if [[ -n "$LAST_AUDITED" && -n "$HEAD_SHA" && "$LAST_AUDITED" != "$HEAD_SHA" ]]; then
  RANGE="$LAST_AUDITED..$HEAD_SHA"
fi
if [[ -n "$RANGE" ]]; then
  if [[ -n "${TIMEOUT_BIN:-}" ]]; then
    "$TIMEOUT_BIN" -k 15 "$CAP_SECS" "$DEEPSEC_BIN" process --project-id radon \
      --diff "$RANGE" --concurrency 2 \
      --comment-out "$RUN_DIR/deepsec-findings.md" \
      >"$RUN_DIR/deepsec-process.log" 2>&1
    PROC_RC=$?
  else
    "$DEEPSEC_BIN" process --project-id radon --diff "$RANGE" --concurrency 2 \
      --comment-out "$RUN_DIR/deepsec-findings.md" \
      >"$RUN_DIR/deepsec-process.log" 2>&1
    PROC_RC=$?
  fi
else
  PROC_RC=0
fi
case "$PROC_RC" in
  0|1)
    "$DEEPSEC_BIN" revalidate --project-id radon --min-severity MEDIUM --concurrency 2 \
      >"$RUN_DIR/deepsec-revalidate.log" 2>&1 || true
    "$DEEPSEC_BIN" export --project-id radon --format json \
      --out "$RUN_DIR/deepsec-verified-findings.json" \
      >"$RUN_DIR/deepsec-export.log" 2>&1 || true
    ;;
  124)
    write_status "running"
    report "still running" "DeepSec hit its own cap and will resume"
    echo "[deepsec] cap hit; resume next fire" | tee -a "$RUN_LOG"
    exit 0
    ;;
  *)
    write_status "failed"
    report "failed" "DeepSec process ended without an export"
    echo "[deepsec] failed rc=$PROC_RC" | tee -a "$RUN_LOG"
    exit 1
    ;;
esac
set -e

if [[ ! -f "$RUN_DIR/deepsec-verified-findings.json" ]]; then
  printf '%s\n' '{"findings":[]}' >"$RUN_DIR/deepsec-verified-findings.json"
fi

/usr/bin/python3 -I - "$SCRATCH" "$RUN_ID" "$RUN_DIR/deepsec-verified-findings.json" "$HEAD_SHA" <<'PY'
import json, os, sys, time
from pathlib import Path
scratch, run_id, export, head = sys.argv[1:5]
payload = {
    "status": "export_ready",
    "run_id": run_id,
    "export_path": export,
    "head_sha": head,
    "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
}
path = Path(scratch) / "deepsec-status.json"
tmp = path.with_name(path.name + ".tmp")
tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
os.chmod(tmp, 0o600)
os.replace(tmp, path)
PY

report "export ready" "verified export is ready for harvest"
echo "[deepsec] export ready run_id=$RUN_ID" | tee -a "$RUN_LOG"
_python harvest --scratch "$SCRATCH" || true
exit 0
