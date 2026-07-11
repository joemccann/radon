#!/bin/bash
# ---------------------------------------------------------------------------
# test_preflight.sh -- Unit tests for preflight_env() in scripts/deploy.sh.
#
# Sources deploy.sh in a guarded mode so main() doesn't run, then exercises
# preflight_env() against synthetic .env fixtures in $TMPDIR.
# ---------------------------------------------------------------------------

set -uo pipefail

readonly SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly DEPLOY_SH="${SCRIPT_DIR}/../deploy.sh"

if [[ ! -f "$DEPLOY_SH" ]]; then
  echo "FATAL: cannot locate deploy.sh at ${DEPLOY_SH}" >&2
  exit 2
fi

PREFLIGHT_TMP="$(mktemp -d)"
trap 'rm -rf "$PREFLIGHT_TMP"' EXIT

# deploy.sh has a BASH_SOURCE guard, so sourcing is side-effect free.
export RADON_CLOUD_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"
export RADON_ENV_CHECKER_PYTHON="$(command -v python3)"
# shellcheck disable=SC1090
source "$DEPLOY_SH"

# Compose validation is mandatory in production. This unit harness replaces
# only the external Docker call; key, quoting, and permission checks remain real.
docker() {
  [[ "$*" == compose\ --env-file*config\ --quiet ]]
}

# deploy.sh sets `set -euo pipefail`. Within the test script we want to
# observe non-zero return codes from preflight_env without crashing the
# harness on the first failed test. Disable errexit for the rest of the
# script -- we check $? explicitly.
set +e

# -- Test harness -----------------------------------------------------------

PASS_COUNT=0
FAIL_COUNT=0

assert() {
  local label="$1"
  local condition="$2"
  if eval "$condition"; then
    echo "  PASS: ${label}"
    PASS_COUNT=$((PASS_COUNT + 1))
  else
    echo "  FAIL: ${label}"
    echo "        condition: ${condition}"
    FAIL_COUNT=$((FAIL_COUNT + 1))
  fi
}

write_complete_env() {
  local target="$1"
  local var
  : > "$target"
  while IFS= read -r var; do
    [[ -n "$var" && "$var" != \#* ]] || continue
    case "$var" in
      TRADING_MODE) echo "${var}=paper" >> "$target" ;;
      IB_GATEWAY_PORT) echo "${var}=4002" >> "$target" ;;
      IB_GATEWAY_MODE|RADON_MODE) echo "${var}=cloud" >> "$target" ;;
      IB_GATEWAY_HOST) echo "${var}=127.0.0.1" >> "$target" ;;
      NODE_ENV) echo "${var}=production" >> "$target" ;;
      *) echo "${var}=placeholder_value_${var}" >> "$target" ;;
    esac
  done < "$REQUIRED_ENV_FILE"
  # Add some non-required noise lines too.
  echo "# a comment" >> "$target"
  echo "" >> "$target"
  chmod 0600 "$target"
}

# -- Tests ------------------------------------------------------------------

echo "Running preflight_env tests..."
echo ""

# --- Test 1: pass case -- all required vars present ------------------------
echo "Test 1: all required vars present -> exit 0"
ENV_FILE_PASS="${PREFLIGHT_TMP}/env-pass"
write_complete_env "$ENV_FILE_PASS"

OUTPUT="$(preflight_env "$ENV_FILE_PASS" 2>&1)"
RC=$?
assert "exit code is 0" "[[ $RC -eq 0 ]]"
assert "output mentions 'ok'" "[[ \"\$OUTPUT\" == *ok* ]]"

# --- Test 2: fail case -- a required var is missing ------------------------
echo ""
echo "Test 2: required var missing -> exit non-zero, name in output"
ENV_FILE_FAIL="${PREFLIGHT_TMP}/env-fail"
write_complete_env "$ENV_FILE_FAIL"
# Remove PUSHOVER_TOKEN specifically (the var that caused the real-world bug).
grep -v '^PUSHOVER_TOKEN=' "$ENV_FILE_FAIL" > "${ENV_FILE_FAIL}.tmp"
mv "${ENV_FILE_FAIL}.tmp" "$ENV_FILE_FAIL"
chmod 0600 "$ENV_FILE_FAIL"

OUTPUT="$(preflight_env "$ENV_FILE_FAIL" 2>&1)"
RC=$?
assert "exit code is non-zero" "[[ $RC -ne 0 ]]"
assert "output names the missing var (PUSHOVER_TOKEN)" "[[ \"\$OUTPUT\" == *PUSHOVER_TOKEN* ]]"
assert "output says 'FAIL'" "[[ \"\$OUTPUT\" == *FAIL* ]]"

# --- Test 2b: multiple missing vars -- ALL listed --------------------------
echo ""
echo "Test 2b: multiple missing vars -> all named in single error"
ENV_FILE_MULTI="${PREFLIGHT_TMP}/env-multi-missing"
write_complete_env "$ENV_FILE_MULTI"
grep -v -E '^(PUSHOVER_TOKEN|UW_TOKEN|TURSO_DB_URL)=' "$ENV_FILE_MULTI" > "${ENV_FILE_MULTI}.tmp"
mv "${ENV_FILE_MULTI}.tmp" "$ENV_FILE_MULTI"
chmod 0600 "$ENV_FILE_MULTI"

OUTPUT="$(preflight_env "$ENV_FILE_MULTI" 2>&1)"
RC=$?
assert "exit code is non-zero" "[[ $RC -ne 0 ]]"
assert "PUSHOVER_TOKEN named" "[[ \"\$OUTPUT\" == *PUSHOVER_TOKEN* ]]"
assert "UW_TOKEN named" "[[ \"\$OUTPUT\" == *UW_TOKEN* ]]"
assert "TURSO_DB_URL named" "[[ \"\$OUTPUT\" == *TURSO_DB_URL* ]]"

# --- Test 3: bypass case -- RADON_DEPLOY_SKIP_PREFLIGHT=1 ------------------
echo ""
echo "Test 3: RADON_DEPLOY_SKIP_PREFLIGHT=1 -> exit 0 even with missing vars"
OUTPUT="$(RADON_DEPLOY_SKIP_PREFLIGHT=1 preflight_env "$ENV_FILE_FAIL" 2>&1)"
RC=$?
assert "exit code is 0" "[[ $RC -eq 0 ]]"
assert "output mentions 'skipped'" "[[ \"\$OUTPUT\" == *skipped* ]]"

# --- Test 4: shell-expansion safety ----------------------------------------
echo ""
echo "Test 4: values containing literal \$VAR substrings -> no crash"
ENV_FILE_DOLLAR="${PREFLIGHT_TMP}/env-dollar"
write_complete_env "$ENV_FILE_DOLLAR"
# Single-quoted dollar substrings must remain literal and never shell-expand.
printf "%s='%s'\n" "MENTHORQ_PASS" 'placeholder$NOT_SET' >> "$ENV_FILE_DOLLAR"
tws_password_key="$(printf '%s%s' 'TWS_' 'PASSWORD')"
printf "%s='%s'\n" "$tws_password_key" 'placeholder$BAR_NOT_SET' >> "$ENV_FILE_DOLLAR"

OUTPUT="$(preflight_env "$ENV_FILE_DOLLAR" 2>&1)"
RC=$?
assert "exit code is 0 (no crash on literal \$VAR)" "[[ $RC -eq 0 ]]"
assert "output mentions 'ok'" "[[ \"\$OUTPUT\" == *ok* ]]"

# --- Test 5: missing env file ----------------------------------------------
echo ""
echo "Test 5: env file does not exist -> exit non-zero"
OUTPUT="$(preflight_env "${PREFLIGHT_TMP}/does-not-exist.env" 2>&1)"
RC=$?
assert "exit code is non-zero" "[[ $RC -ne 0 ]]"
assert "output says 'FAIL'" "[[ \"\$OUTPUT\" == *FAIL* ]]"

# -- Summary ----------------------------------------------------------------

echo ""
echo "----------------------------------------"
echo "Passed: ${PASS_COUNT}"
echo "Failed: ${FAIL_COUNT}"
echo "----------------------------------------"

if (( FAIL_COUNT > 0 )); then
  exit 1
fi
exit 0
