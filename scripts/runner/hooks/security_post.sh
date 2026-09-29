#!/bin/bash
# POST_RUN hook for loops/security.env and loops/security-deepsec.env.
# Installed root-owned in /usr/local/radon-runner/hooks; run_loop.sh runs it
# after every phase (and, bounded to 10s, from its TERM trap). It decides the
# phase status from the phase's own log slice, publishes the agent's private
# report to the PRIVATE reports repository, posts the sanitized dead-man
# comment, and prints `status=` / `pr_url=` / `report_url=` for the page.
#
# Ported from scripts/security_nightly.sh: phase_status, phase_marker_in_slice,
# deliver_status, _deliver_urls_verified, deliver_record_fresh_terminal,
# publish_private_report, _redact_secret_classes, resolve_pr_url,
# _sanitize_issue_text, _format_issue_body, report, prune_deadman_comments.
# Never runs agent-writable code: the helpers are the root-owned copies in
# $RUNNER_DIR/lib, run with an isolated interpreter.
set -uo pipefail

# Restrict command lookup to trusted system directories.
# Prevent PATH hijacking from agent-writable locations.
export PATH="/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin:/usr/local/bin"

: "${LOOP:?}" "${PHASE:?}" "${WORK:?}" "${LOOP_STATE:?}" "${RUNNER_DIR:?}"
PHASE_RC="${PHASE_RC:-0}"
PHASE_LOG="${PHASE_LOG:-/dev/null}"
# Never a bot-writable directory: the agent's CLI dirs are not on this PATH.
export PATH="${RADON_RUNNER_PATH:-/opt/homebrew/bin:/usr/bin:/bin}"
PY="${RUNNER_PYTHON:-/opt/homebrew/bin/python3.13}"
GH="${RADON_RUNNER_GH:-/opt/homebrew/bin/gh}"
GH_REPO="${RADON_WEEKEND_GH_REPO:-joemccann/radon}"
TIMEOUT_BIN="$(command -v gtimeout || command -v timeout || true)"
NET_TIMEOUT_SECS="${RADON_WEEKEND_NET_TIMEOUT_SECS:-120}"
SCRATCH="$LOOP_STATE/scratch"
STAMP="$(date +%Y%m%dT%H%M%S)"
REPORTS_REMOTE="${RADON_SECURITY_REPORTS_REMOTE:-git@github.com:joemccann/radon-security-reports.git}"
REPORTS_WEB="${RADON_SECURITY_REPORTS_WEB:-https://github.com/joemccann/radon-security-reports/blob/main}"
REPORTS_KEY="$HOME/.radon-runner-reports-key"
BG_CEILING_MARKER="Background tasks still running after"
DELIVER_READY_MARKER="NIGHTLY DELIVER READY:"
DELIVER_INCOMPLETE_MARKER="NIGHTLY DELIVER INCOMPLETE:"

case "$LOOP" in
  security)
    PHASE_COMPLETE_MARKER="SECURITY-NIGHTLY PHASE COMPLETE:"
    DEADMAN_TITLE="Nightly security runner"
    DEADMAN_LABEL="security-nightly" ;;
  security-deepsec)
    PHASE_COMPLETE_MARKER="SECURITY-DEEPSEC PHASE COMPLETE:"
    DEADMAN_TITLE="Nightly DeepSec runner"
    DEADMAN_LABEL="security-deepsec" ;;
  *) echo "status=FAILED (security_post.sh does not know loop $LOOP)"; exit 0 ;;
esac
PR_BRANCH_PREFIX="$LOOP/"
DEADMAN_CREATE_BODY="Rolling dead-man for the nightly ${LOOP} loop. Sanitized status only. Never a route, file, attack, secret, or account. A missing daily comment means the runner did not fire."

net_bounded() { "$TIMEOUT_BIN" "$NET_TIMEOUT_SECS" "$@"; }
refuse_symlink() { [[ ! -L "$1" ]]; }
protocol_value() {
  local value="${1//$'\r'/}"
  printf '%s' "${value%%$'\n'*}"
}

# --- status ------------------------------------------------------------------

phase_status() {
  if [[ "$PHASE_RC" -eq 124 ]]; then
    printf 'TIMEOUT'
  elif [[ "$PHASE_RC" -ne 0 ]]; then
    printf 'FAILED (exit %s)' "$PHASE_RC"
  elif grep -qF "$BG_CEILING_MARKER" "$PHASE_LOG" 2>/dev/null; then
    printf 'TRUNCATED (background work killed before the phase finished)'
  else
    printf 'OK'
  fi
}

# A deliver stamp without READY/INCOMPLETE is still complete when this phase
# wrote a terminal record (green / incomplete). Fail closed on a missing,
# launched, or stale record.
deliver_record_fresh_terminal() {
  local rec="$LOOP_STATE/.${LOOP}-deliver/record.json"
  [[ -f "$rec" && ! -L "$rec" ]] || return 1
  "$PY" -I -c '
import json, sys
from datetime import datetime, timezone
from pathlib import Path
path, since = Path(sys.argv[1]), int(float(sys.argv[2]))
try:
    rec = json.loads(path.read_text(encoding="utf-8"))
except Exception:
    sys.exit(1)
if rec.get("status") not in ("green", "incomplete"):
    sys.exit(1)
raw = rec.get("updated_at") or ""
if raw.endswith("Z"):
    raw = raw[:-1] + "+00:00"
try:
    ts = datetime.fromisoformat(raw)
except Exception:
    ts = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
if ts.tzinfo is None:
    ts = ts.replace(tzinfo=timezone.utc)
sys.exit(0 if int(ts.timestamp()) >= since else 1)
' "$rec" "$(stat -c %Y "$PHASE_START_MARK" 2>/dev/null || stat -f %m "$PHASE_START_MARK" 2>/dev/null || echo 0)"
}

# REL-188 (R-535): only a dedicated column-0 marker line counts, never a
# recital. Deliver also needs the stamp AFTER a verdict line.
phase_marker_present() {
  local marker="" verdict_seen=0 saw_stamp=0 line
  while IFS= read -r line || [[ -n "$line" ]]; do
    case "$line" in
      "$DELIVER_READY_MARKER"*|"$DELIVER_INCOMPLETE_MARKER"*) verdict_seen=1 ;;
      "$PHASE_COMPLETE_MARKER"*)
        saw_stamp=1
        if [[ "$PHASE" == deliver && "$verdict_seen" -eq 0 ]]; then marker=""; else marker="$line"; fi ;;
    esac
  done < "$PHASE_LOG"
  [[ -n "$marker" ]] && return 0
  [[ "$PHASE" == deliver && "$saw_stamp" -eq 1 ]] && deliver_record_fresh_terminal && return 0
  return 1
}

# An agent-asserted URL is not a merge cue: each must be a same-repo OPEN PR
# on this loop's branch prefix, and there must be one per claimed PR ($2).
deliver_urls_verified() {
  local tok verified count=0
  for tok in $1; do
    case "$tok" in
      http://*|https://*)
        verified="$(net_bounded "$GH" pr view "$tok" -R "$GH_REPO" \
          --json state,headRefName,isCrossRepository \
          -q "select(.state == \"OPEN\" and .isCrossRepository == false and (.headRefName | startswith(\"$PR_BRANCH_PREFIX\"))) | \"ok\"" \
          2>/dev/null || true)"
        [[ "$verified" == "ok" ]] || return 1
        count=$((count + 1)) ;;
    esac
  done
  [[ "$count" -gt 0 && "$count" == "$2" ]]
}

# R-613: the durable record first, then the verdict line of this slice.
deliver_status() {
  local from_record line rest tok n="" urls="" check=""
  from_record="$(RADON_WEEKEND_ROOT="$LOOP_STATE" "$TIMEOUT_BIN" 30 "$PY" -I "$RUNNER_DIR/lib/nightly_deliver.py" \
    deliver-status --loop "$LOOP" 2>/dev/null || true)"
  case "$from_record" in
    ""|*"no deliver record"*) ;;
    *"deliver record has a branch but no PR"*) ;;
    *"ready to merge:"*)
      if deliver_urls_verified "${from_record#*ready to merge:}" "${from_record%% PR(s)*}"; then printf '%s' "$from_record"
      else printf 'INCOMPLETE: unverified-pr-url'; fi
      return 0 ;;
    *) printf '%s' "$from_record"; return 0 ;;
  esac
  line="$(grep -E "^(${DELIVER_READY_MARKER}|${DELIVER_INCOMPLETE_MARKER})" "$PHASE_LOG" 2>/dev/null | tail -n 1 || true)"
  case "$line" in
    "$DELIVER_READY_MARKER"*)
      rest="${line#"$DELIVER_READY_MARKER"}"
      for tok in $rest; do
        case "$tok" in
          prs=*) n="${tok#prs=}" ;;
          http://*|https://*) urls="${urls:+$urls }$tok" ;;
        esac
      done
      if [[ "${n:-0}" == "0" ]]; then printf '0 PR(s), nothing to merge'
      elif deliver_urls_verified "$urls" "$n"; then printf '%s PR(s) green, ready to merge: %s' "$n" "$urls"
      else printf 'INCOMPLETE: unverified-pr-url'; fi ;;
    "$DELIVER_INCOMPLETE_MARKER"*)
      rest="${line#"$DELIVER_INCOMPLETE_MARKER"}"
      for tok in $rest; do case "$tok" in check=*) check="${tok#check=}" ;; esac; done
      printf 'INCOMPLETE: %s' "${check:-unnamed check}" ;;
    *) printf 'INCOMPLETE (exit 0 without the deliver verdict line)' ;;
  esac
}

resume_detail() {
  if [[ "$PHASE" == deliver ]]; then
    printf '%s' "the next fire resumes the same private run, branch and PR"
    return 0
  fi
  case "${1:-}" in
    INCOMPLETE*)
      printf '%s' "the agent exited 0 without declaring the phase complete — this phase is INCOMPLETE; the audited SHA was NOT advanced and the next fire resumes the same private run" ;;
    TRUNCATED*)
      printf '%s' "the harness killed unfinished background work and the agent still exited 0 — this phase is INCOMPLETE; the audited SHA was NOT advanced" ;;
    TIMEOUT*)
      printf '%s' "the phase hit its wall-clock cap; incomplete, the audited SHA was NOT advanced" ;;
    *)
      printf '%s' "the phase ended with a non-zero status; the audited SHA was NOT advanced" ;;
  esac
}

# Sets STATUS (the page and dead-man text; OK* means the phase succeeded) and
# DETAIL (hook-generated, never agent text).
decide() {
  if [[ -n "${PHASE_STATUS:-}" ]]; then
    STATUS="$PHASE_STATUS"
    DETAIL="the runner was signalled before the phase finished — launchd ExitTimeOut, a bootout, an operator kill or a reboot; partial work may exist on the dated branch"
    return 0
  fi
  if [[ -n "${PHASE_REFUSED:-}" ]]; then
    STATUS="REFUSED"
    DETAIL="$PHASE_REFUSED"
    return 0
  fi
  STATUS="$(phase_status)"
  if [[ "$STATUS" == OK ]] && ! phase_marker_present; then
    STATUS="INCOMPLETE (exit 0 without the phase-completion marker)"
  fi
  if [[ "$PHASE" == deliver ]]; then
    if [[ "$STATUS" == OK ]]; then
      STATUS="$(deliver_status)"
    elif [[ "$STATUS" == TIMEOUT* ]]; then
      STATUS="INCOMPLETE: deliver cap hit before CI went green ($STATUS)"
    fi
  fi
  case "$STATUS" in
    *"ready to merge: "*|"0 PR(s), nothing to merge")
      DETAIL="CI is green on every PR this cycle delivered; merging is the operator's call. Verified findings stay private."
      STATUS="OK: $STATUS" ;;
    "INCOMPLETE: "*)
      DETAIL="CI could not be made green inside the deliver cap — this phase is INCOMPLETE; the branch and PR number are in the private run-record and the next fire resumes them" ;;
    "INCOMPLETE (exit 0 without the deliver verdict line)")
      DETAIL="the agent exited 0 without declaring the deliver verdict — this phase is INCOMPLETE; the next fire resumes the same private run, branch and PR" ;;
    OK)
      DETAIL="0 public findings to disclose. The phase completed. Verified findings stay private." ;;
    *)
      DETAIL="$(resume_detail "$STATUS")" ;;
  esac
}

# --- private report ------------------------------------------------------------

# Credential assignments, case-insensitive (BSD sed has no I flag): the key
# may be quoted (JSON) or a hyphenated header name, the value quoted (spaces
# inside) or led by an auth scheme word. POSIX ERE takes the longest alternative.
_SECRET_KEY_ERE='[A-Za-z0-9_-]*([Tt][Oo][Kk][Ee][Nn]|[Ss][Ee][Cc][Rr][Ee][Tt]|[Pp][Aa][Ss][Ss]|[Aa][Uu][Tt][Hh]|[Cc][Rr][Ee][Dd][Ee][Nn][Tt][Ii][Aa][Ll]|[Aa][Pp][Ii][-_]?[Kk][Ee][Yy]|[-_][Kk][Ee][Yy])[A-Za-z0-9_-]*'
_SECRET_SEP_ERE="[\"']?[[:space:]]*[=:][[:space:]]*"
_SECRET_VALUE_ERE="(\"[^\"]*\"|'[^']*'|([Bb]asic|[Bb]earer|[Dd]igest|[Tt]oken)[[:space:]]+[^[:space:]]+|[^[:space:]]+)"

_redact_secret_classes() {
  # Secret literals only. Routes, file:line and findings stay: this text
  # goes to the PRIVATE repository, not the public issue.
  /usr/bin/sed -E \
    -e 's,[Bb]earer [^[:space:]]+,Bearer [REDACTED],g' \
    -e 's#(^|[^[:alnum:]_])(sk-(ant-)?[A-Za-z0-9_-]{20,}|sk_(live|test)_[A-Za-z0-9]{6,}|(xai|nvapi|csk)-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{22,}|xox[abpors]-[A-Za-z0-9-]{10,}|AKIA[0-9A-Z]{16}|eyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,})#\1[REDACTED]#g' \
    -e "s,(${_SECRET_KEY_ERE}${_SECRET_SEP_ERE})${_SECRET_VALUE_ERE},\\1[REDACTED],g"
}

# Best-effort: a failure leaves REPORT_URL empty and the page says so. The
# deploy key lives only in this hook's GIT_SSH_COMMAND; every push is a fresh
# clone with a freshly pinned GitHub host key.
publish_private_report() {
  REPORT_URL=""
  local src="$SCRATCH/latest-report-${PHASE}.md" day rel dir hosts ssh_cmd
  [[ -f "$src" && -f "$REPORTS_KEY" ]] || return 0
  refuse_symlink "$src" || return 0
  refuse_symlink "$SCRATCH" || return 0
  # Only a report written during THIS phase; a stale one would be re-linked.
  [[ -n "${PHASE_START_MARK:-}" && "$src" -nt "$PHASE_START_MARK" ]] || return 0
  day="${STAMP:0:4}-${STAMP:4:2}-${STAMP:6:2}"
  rel="reports/$LOOP/$day/$PHASE.md"
  dir="$(mktemp -d "${TMPDIR:-/tmp}/radon-reports.XXXXXX")" || return 0
  hosts="$dir/known_hosts"
  # GitHub's published ed25519 host key (docs.github.com "GitHub's SSH key fingerprints").
  printf '%s\n' 'github.com ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOMqqnkVzrm0SdG6UOoqKLsabgH5C9okWi0dh2l9GKJl' > "$hosts"
  ssh_cmd="/usr/bin/ssh -i $REPORTS_KEY -o IdentitiesOnly=yes -o UserKnownHostsFile=$hosts -o StrictHostKeyChecking=yes -o ConnectTimeout=20"
  if GIT_SSH_COMMAND="$ssh_cmd" net_bounded git clone --quiet --depth 1 "$REPORTS_REMOTE" "$dir/repo" >/dev/null 2>&1 \
     && mkdir -p "$(dirname "$dir/repo/$rel")" \
     && _redact_secret_classes < "$src" > "$dir/repo/$rel" \
     && git -C "$dir/repo" add -- "$rel" >/dev/null 2>&1 \
     && git -C "$dir/repo" -c user.name="radon-$LOOP" -c user.email="radon-nightly@users.noreply.github.com" \
          commit --quiet -m "$LOOP $PHASE $STAMP" >/dev/null 2>&1 \
     && GIT_SSH_COMMAND="$ssh_cmd" net_bounded git -C "$dir/repo" push --quiet origin HEAD:main >/dev/null 2>&1; then
    REPORT_URL="$REPORTS_WEB/$rel"
    echo "private report published: $rel" >&2
  fi
  rm -rf -- "$dir"
}

# Newest open same-repo PR on this loop's prefix; a fork can carry the name.
resolve_pr_url() {
  local url
  url="$(net_bounded "$GH" pr list -R "$GH_REPO" --state open --limit 20 --json url,headRefName,updatedAt,isCrossRepository \
    -q "[.[] | select(.isCrossRepository == false) | select(.headRefName | startswith(\"$PR_BRANCH_PREFIX\"))] | sort_by(.updatedAt) | reverse | .[0].url" \
    2>/dev/null || true)"
  [[ "$url" == "null" ]] && url=""
  printf '%s' "$url"
}

# --- dead-man -------------------------------------------------------------------

_sanitize_issue_text() {
  local text="$1"
  [[ -n "$text" ]] || { printf '%s' "$text"; return 0; }
  text="${text//https:\/\/claude.ai\/settings\/usage/$'\x01USAGE\x01'}"
  text="${text//http:\/\/claude.ai\/settings\/usage/$'\x01USAGE\x01'}"
  text="${text//claude.ai\/settings\/usage/$'\x01USAGE\x01'}"
  text="$(printf '%s' "$text" | /usr/bin/sed -E \
    -e 's,[A-Za-z][A-Za-z0-9+.-]*://[^[:space:]]+,[REDACTED],g' \
    -e 's,(^|[^[:alnum:].])/(api|admin)/[A-Za-z0-9._/-]+,\1[REDACTED],g' \
    -e 's,[A-Za-z0-9./_-]+\.(py|ts|tsx|js|mjs|cjs|sh|go|rb|java|json|yml|yaml|toml|md):[0-9]+,[REDACTED],g' \
    -e 's,[Bb]earer [^[:space:]]+,Bearer [REDACTED],g' \
    -e 's#(^|[^[:alnum:]_])(sk-(ant-)?[A-Za-z0-9_-]{20,}|sk_(live|test)_[A-Za-z0-9]{6,}|(xai|nvapi|csk)-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{22,}|xox[abpors]-[A-Za-z0-9-]{10,}|AKIA[0-9A-Z]{16}|eyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,})#\1[REDACTED]#g' \
    -e "s,${_SECRET_KEY_ERE}${_SECRET_SEP_ERE}${_SECRET_VALUE_ERE},[REDACTED],g" \
    -e 's,[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z][A-Za-z]+,[REDACTED],g' \
    -e 's,(^|[^A-Za-z0-9])(radon)?(trader|operator)[0-9]+,\1[REDACTED],g' \
    -e 's,[Cc]heck the runner,,g' \
    -e 's,[Oo]n the runner,,g' \
    || true)"
  text="${text//$'\x01USAGE\x01'/claude.ai/settings/usage}"
  printf '%s' "$text"
}

_format_issue_body() {
  local phase="$1" status="$2" detail="$3"
  local clean body
  phase="$(_sanitize_issue_text "$phase")"
  status="$(_sanitize_issue_text "$status")"
  detail="$(_sanitize_issue_text "$detail")"
  clean="${detail%%\`\`\`*}"
  body="$(printf '**%s** %s **%s**' "$phase" "${STAMP:-}" "$status")"
  if [[ -n "$clean" ]]; then
    body="${body}"$'\n'"${clean}"
  fi
  body="$(_sanitize_issue_text "$body")"
  printf '%s\n' "$body"
}

# R-612: post FIRST; prune only once the post is confirmed, keeping it.
report() {
  local body issue posted keep
  body="$(_format_issue_body "$PHASE" "$STATUS" "$DETAIL")"
  issue="$(net_bounded "$GH" issue list -R "$GH_REPO" --label "$DEADMAN_LABEL" --state open \
    --json number -q '.[0].number' 2>/dev/null || true)"
  if [[ -z "$issue" ]]; then
    echo "gh issue call failed or found no dead-man issue (auth expired?)" >&2
    net_bounded "$GH" issue create -R "$GH_REPO" --title "$DEADMAN_TITLE" --label "$DEADMAN_LABEL" \
      --body "$DEADMAN_CREATE_BODY" >/dev/null 2>&1 || true
    issue="$(net_bounded "$GH" issue list -R "$GH_REPO" --label "$DEADMAN_LABEL" --state open \
      --json number -q '.[0].number' 2>/dev/null || true)"
  fi
  [[ -n "$issue" ]] || return 0
  posted="$(net_bounded "$GH" issue comment "$issue" -R "$GH_REPO" --body "$body" 2>/dev/null || true)"
  if [[ -z "$posted" ]]; then
    echo "gh issue call failed: the phase comment did not post" >&2
    return 0
  fi
  # R-657: no parseable comment id, no prune.
  keep="${posted##*issuecomment-}"
  [[ "$keep" =~ ^[0-9]+$ ]] || return 0
  [[ "${RADON_WEEKEND_SKIP_ISSUE_PRUNE:-0}" == 1 ]] && return 0
  "$TIMEOUT_BIN" 30 "$PY" -I "$RUNNER_DIR/lib/nightly_issue_prune.py" --gh-bin "$GH" --issue "$issue" \
    --branch-prefix "$PR_BRANCH_PREFIX" --keep "$keep" >/dev/null 2>&1 || true
}

# Run dirs older than 30 days go (the old weekend_prune.py job).
prune_scratch() {
  [[ -d "$SCRATCH" && ! -L "$SCRATCH" ]] || return 0
  find "$SCRATCH" -mindepth 1 -maxdepth 1 -type d -mtime +30 -exec rm -rf {} + 2>/dev/null || true
}

main() {
  local pr status_line pr_line report_line
  decide
  status_line="$(protocol_value "$STATUS")"
  # Printed first so the runner has a verdict even if a network step below
  # hangs past the hook's cap; the final status line supersedes it.
  echo "status=$status_line"
  if [[ -z "${PHASE_STATUS:-}" ]]; then publish_private_report; else REPORT_URL=""; fi
  pr="$(resolve_pr_url)"
  report
  prune_scratch
  pr_line="$(protocol_value "$pr")"
  report_line="$(protocol_value "$REPORT_URL")"
  if [[ -n "$REPORT_URL" ]]; then
    echo "status=$status_line"
  else
    echo "status=${status_line} (no private report this phase)"
  fi
  echo "pr_url=$pr_line"
  echo "report_url=$report_line"
}

main
exit 0
