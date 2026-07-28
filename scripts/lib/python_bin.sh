# shellcheck shell=bash
#
# Shared interpreter resolution for the scripts/run_*.sh scheduler wrappers.
#
# Each wrapper used to carry its own copy of resolve_python(). The copies had
# drifted into three behaviours — a real dependency probe, a stdlib-only
# `import sys` probe that every interpreter on earth passes, and no probe at
# all — but they shared one defect: none of them listed the project
# virtualenv. With RADON_PYTHON_BIN unset (its state in every deployed .env)
# the first match on the VPS was /usr/bin/python3.13, which carries none of
# the project dependencies. So the "FastAPI unreachable, invoke the scanner
# directly" fallback died on ModuleNotFoundError at precisely the moment it
# existed to help. See scripts/tests/test_wrapper_python_resolution.py.
#
# Usage:
#   . "$(dirname "$0")/lib/python_bin.sh"
#   PYTHON_BIN=$(radon_resolve_python ib_insync libsql_experimental) || exit 1
#
# Modules are optional; with none given the probe only checks that the
# interpreter runs. Set RADON_PYTHON_MIN_VERSION (e.g. "3.13") to add a
# version floor.

# Repo root derived from this file's own location so resolution does not
# depend on the caller's cwd.
radon_repo_root() {
    local lib_dir
    lib_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    dirname "$(dirname "$lib_dir")"
}

radon_python_candidates() {
    local root
    root="$(radon_repo_root)"
    # Order matters: an operator override wins, then the project virtualenv,
    # then progressively less specific interpreters on PATH.
    printf '%s\n' \
        "${RADON_PYTHON_BIN:-}" \
        "$root/.venv/bin/python3.13" \
        "$root/.venv/bin/python" \
        /opt/homebrew/bin/python3.13 \
        /usr/local/bin/python3.13 \
        /usr/bin/python3.13 \
        python3.13 \
        python3.11 \
        python3.9 \
        /usr/bin/python3 \
        python3
}

radon_python_satisfies() {
    local interpreter="$1"
    shift
    RADON_PYTHON_MIN_VERSION="${RADON_PYTHON_MIN_VERSION:-}" \
        "$interpreter" - "$@" <<'PY' >/dev/null 2>&1
import importlib.util
import os
import sys

floor = os.environ.get("RADON_PYTHON_MIN_VERSION", "").strip()
if floor:
    required = tuple(int(part) for part in floor.split("."))
    if sys.version_info[: len(required)] < required:
        raise SystemExit(1)

missing = [name for name in sys.argv[1:] if importlib.util.find_spec(name) is None]
raise SystemExit(1 if missing else 0)
PY
}

# Echo the first interpreter satisfying every requested module; return 1 when
# none qualifies so callers can fail loudly instead of limping into an
# ImportError further downstream.
radon_resolve_python() {
    local candidate resolved
    while IFS= read -r candidate; do
        [ -n "$candidate" ] || continue
        if [ "${candidate#*/}" != "$candidate" ]; then
            [ -x "$candidate" ] || continue
            resolved="$candidate"
        else
            resolved=$(command -v "$candidate" 2>/dev/null) || continue
        fi
        radon_python_satisfies "$resolved" "$@" || continue
        printf '%s\n' "$resolved"
        return 0
    done < <(radon_python_candidates)
    return 1
}
