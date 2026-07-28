#!/bin/bash
#
# MenthorQ CTA refresh wrapper for launchd
#
# Runs at the scheduled post-close windows, with `RunAtLoad` handling
# reboot/login/wake catch-up. The target date is always the latest closed US
# trading session, so if the machine slept through the scheduled run the next
# load-triggered execution backfills the missing session automatically.
#

cd "$(dirname "$0")/.."

_load_env() {
    local f="$1"
    [ -f "$f" ] || return
    local line key value first last
    while IFS= read -r line || [ -n "$line" ]; do
        line="${line#"${line%%[![:space:]]*}"}"
        line="${line%"${line##*[![:space:]]}"}"
        [ -n "$line" ] || continue
        case "$line" in
            \#*) continue ;;
            export\ *) line="${line#export }" ;;
        esac
        [[ "$line" == *=* ]] || continue
        key="${line%%=*}"
        value="${line#*=}"
        key="${key%"${key##*[![:space:]]}"}"
        value="${value#"${value%%[![:space:]]*}"}"
        value="${value%"${value##*[![:space:]]}"}"
        [ -n "$key" ] || continue
        if [ "${#value}" -ge 2 ]; then
            first="${value:0:1}"
            last="${value: -1}"
            if { [ "$first" = "'" ] && [ "$last" = "'" ]; } || { [ "$first" = '"' ] && [ "$last" = '"' ]; }; then
                value="${value:1:${#value}-2}"
            fi
        fi
        export "$key=$value"
    done < "$f"
}

_load_env "web/.env"
_load_env ".env"

mkdir -p data/menthorq_cache logs

. scripts/lib/python_bin.sh

PYTHON_BIN=$(radon_resolve_python playwright)
if [ -z "$PYTHON_BIN" ]; then
    echo "$(date): No Python interpreter with Playwright available for CTA sync"
    exit 1
fi

SOURCE="${RADON_CTA_SYNC_SOURCE:-${CTA_SYNC_TRIGGER:-launchd}}"

echo "$(date): Running hardened CTA sync runtime (source=$SOURCE)..."
"$PYTHON_BIN" scripts/cta_sync_service.py --source "$SOURCE" "$@"
EXIT_CODE=$?
if [ "$EXIT_CODE" -eq 0 ]; then
    echo "$(date): CTA sync runtime complete (OK)"
else
    echo "$(date): CTA sync runtime failed (exit $EXIT_CODE)"
fi
exit "$EXIT_CODE"
