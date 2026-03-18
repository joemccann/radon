#!/usr/bin/env bash
# Pre-commit type check hook for Claude Code
# Intercepts git commit commands and runs tsc + Python syntax checks
# on staged files before allowing the commit through.

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"

# Read tool input from stdin
INPUT=$(cat)

# Only trigger on Bash tool
TOOL_NAME=$(echo "$INPUT" | jq -r '.tool_name // empty')
if [[ "$TOOL_NAME" != "Bash" ]]; then
  exit 0
fi

# Only trigger on git commit commands
COMMAND=$(echo "$INPUT" | jq -r '.tool_input.command // empty')
if ! echo "$COMMAND" | grep -qE '^\s*git commit'; then
  exit 0
fi

ERRORS=""

# Check for staged TypeScript/TSX changes
if git -C "$PROJECT_ROOT" diff --cached --name-only 2>/dev/null | grep -qE '\.(ts|tsx)$'; then
  TSC_OUTPUT=$(cd "$PROJECT_ROOT/web" && npx tsc --noEmit 2>&1 | head -30) || true
  TSC_EXIT=${PIPESTATUS[0]:-$?}
  if [[ $TSC_EXIT -ne 0 && -n "$TSC_OUTPUT" ]]; then
    ERRORS="TypeScript errors found — fix before committing:\n$TSC_OUTPUT"
  fi
fi

# Check for staged Python changes — syntax validation only (fast)
if git -C "$PROJECT_ROOT" diff --cached --name-only 2>/dev/null | grep -qE '\.py$'; then
  PY_ERRORS=""
  while IFS= read -r pyfile; do
    full_path="$PROJECT_ROOT/$pyfile"
    if [[ -f "$full_path" ]]; then
      RESULT=$(python3 -c "
import ast, sys
try:
    ast.parse(open('$full_path').read())
except SyntaxError as e:
    print(f'$pyfile:{e.lineno}: {e.msg}')
    sys.exit(1)
" 2>&1) || true
      if [[ -n "$RESULT" ]]; then
        PY_ERRORS="${PY_ERRORS}${RESULT}\n"
      fi
    fi
  done < <(git -C "$PROJECT_ROOT" diff --cached --name-only 2>/dev/null | grep -E '\.py$')

  if [[ -n "$PY_ERRORS" ]]; then
    if [[ -n "$ERRORS" ]]; then
      ERRORS="$ERRORS\n\n"
    fi
    ERRORS="${ERRORS}Python syntax errors — fix before committing:\n$PY_ERRORS"
  fi
fi

# Block commit if errors found
if [[ -n "$ERRORS" ]]; then
  ESCAPED=$(echo -e "$ERRORS" | jq -Rs .)
  echo "{\"decision\": \"block\", \"reason\": $ESCAPED}"
  exit 0
fi

exit 0
