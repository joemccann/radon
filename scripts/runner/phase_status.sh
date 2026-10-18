# Machine-readable nightly phase status. Sourced by run_loop.sh and the
# security post-run hook. The agent never writes this. A killed phase still
# posts because the caller runs it from finish_phase and from the SIGTERM trap.
#
# Public comment, and nothing else on the security issues:
#   <!-- radon-phase loop=<loop> phase=<phase> exit=<n> audited-through=<sha> ts=<utc> -->
#   radon-phase loop=<loop> phase=<phase> exit=<n> audited-through=<sha> ts=<utc>
#   audited-through: <sha>
# The sha line is only for phase=audit with exit 0.
# Codes: 0 OK, 1 FAILED, 2 REFUSED, 70 GROUND TRUTH FAILED, 75 INCOMPLETE, 124 TIMEOUT.
# A signalled phase (143) posts 1.

phase_status_exit() {
  local status="${1:-}" rc="${PHASE_RC:-0}"
  if [[ -n "${PHASE_KILLED:-}" || "$rc" == 143 ]]; then printf '1'; return 0; fi
  if [[ -n "${PHASE_REFUSED:-}" || "$rc" == 2 ]]; then printf '2'; return 0; fi
  if [[ "$rc" == 124 ]]; then printf '124'; return 0; fi
  if [[ "$rc" == 70 ]]; then printf '70'; return 0; fi
  if [[ "${PHASE_HOOK_REQUIRED:-}" == 1 && -z "$status" && -z "${PHASE_REFUSED:-}" ]]; then
    printf '1'
    return 0
  fi
  case "$status" in
    INCOMPLETE*|TRUNCATED*) printf '75' ;;
    TIMEOUT*) printf '124' ;;
    REFUSED*) printf '2' ;;
    *GROUND\ TRUTH*) printf '70' ;;
    FAILED*|KILLED*|CRASHED*) printf '1' ;;
    OK*) printf '0' ;;
    *)
      if [[ "$rc" == 0 ]]; then printf '0'
      elif [[ "$rc" == 75 ]]; then printf '75'
      else printf '1'
      fi
      ;;
  esac
}

# phase_status_body LOOP PHASE STATUS [TIMESTAMP]
# Prints the public comment. Returns 1 when loop or phase is not a token.
phase_status_body() {
  local loop phase status ts exit_code sha fields
  loop="$(printf '%s' "${1:-}" | tr -cd 'a-z0-9-')"
  phase="$(printf '%s' "${2:-}" | tr -cd 'a-z0-9-')"
  status="${3:-}"
  ts="${4:-}"
  [[ "$loop" =~ ^[a-z0-9][a-z0-9-]*$ && "$phase" =~ ^[a-z0-9][a-z0-9-]*$ ]] || return 1
  if [[ -z "$ts" ]]; then ts="$(date -u +%Y-%m-%dT%H:%M:%SZ)"; fi
  [[ "$ts" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$ ]] || return 1
  exit_code="$(phase_status_exit "$status")"
  sha=""
  if [[ "$exit_code" == 0 && "$phase" == audit ]]; then
    case "${PHASE_AUDITED_SHA:-}" in
      [0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]*)
        sha="$(printf '%s' "$PHASE_AUDITED_SHA" | tr -cd '0-9a-f')"
        [[ "$sha" =~ ^[0-9a-f]{7,40}$ ]] || sha=""
        ;;
    esac
  fi
  fields="loop=${loop} phase=${phase} exit=${exit_code}"
  if [[ -n "$sha" ]]; then fields="${fields} audited-through=${sha}"; fi
  fields="${fields} ts=${ts}"
  printf '<!-- radon-phase %s -->\nradon-phase %s\n' "$fields" "$fields"
  if [[ -n "$sha" ]]; then printf 'audited-through: %s\n' "$sha"; fi
}
