#!/usr/bin/env python3
"""Build the Grok page responder's stripped EnvironmentFile.

Called by ``setup-grok-page-responder.sh`` step [2/5]:

    grok_responder_env.py PROD_ENV EXISTING_ENV DEST

Secrets come only from PROD_ENV (the production env): an old value in
EXISTING_ENV is never carried forward, so a rotated or revoked token cannot
survive a rerun. Operator kill switches and knobs are the reverse: they live
only in EXISTING_ENV (the operator sets them by hand) and every one listed in
``OPERATOR_FLAGS`` is preserved. Before this, each rerun dropped them and the
responder silently reported ``"skipped": "disabled"`` because
``GROK_PAGE_RESPONDER`` is off when unset (REL-030).

Output is deterministic, so a rerun with unchanged inputs is byte-identical.
Values are never printed.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REQUIRED = ("TURSO_DB_URL", "TURSO_AUTH_TOKEN", "PUSHOVER_USER", "PUSHOVER_TOKEN")
OPTIONAL = ("GH_TOKEN",)
# GROK_PAGE_* knobs grok_page_responder.py reads that only the operator sets.
# GROK_PAGE_NO_DOTENV / GROK_PAGE_SYNC_REMOTE are managed below, not preserved.
OPERATOR_FLAGS = (
    "GROK_PAGE_RESPONDER",
    "GROK_PAGE_AUTOSHIP",
    "GROK_PAGE_AUTOPUSH",
    "GROK_PAGE_MAX_ACTIONS_PER_DAY",
)
MANAGED = (
    ("GROK_PAGE_NO_DOTENV", "1"),
    ("GROK_PAGE_SYNC_REMOTE", "1"),
    ("GROK_BIN", "/home/radon/.local/bin/grok"),
)
# Flags are booleans or small integers; anything else is not preserved.
_FLAG_VALUE = re.compile(r"^[A-Za-z0-9_.-]{0,32}$")


def parse_env(text: str) -> dict[str, str]:
    """KEY=VALUE lines; the last assignment wins, as in systemd."""
    values: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value
    return values


def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


def build(prod_text: str, existing_text: str | None) -> str:
    prod = parse_env(prod_text)
    missing = [key for key in REQUIRED if not prod.get(key)]
    if missing:
        raise SystemExit("missing in production env: " + ", ".join(missing))
    lines = [f"{key}={prod[key]}" for key in REQUIRED]
    lines += [f"{key}={prod[key]}" for key in OPTIONAL if prod.get(key)]
    existing = parse_env(existing_text or "")
    for key in OPERATOR_FLAGS:
        if key not in existing:
            continue
        value = _unquote(existing[key])
        if not _FLAG_VALUE.match(value):
            print(f"warn: dropping malformed {key} from the existing env", file=sys.stderr)
            continue
        lines.append(f"{key}={value}")
    lines += [f"{key}={value}" for key, value in MANAGED]
    return "\n".join(lines) + "\n"


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print("usage: grok_responder_env.py PROD_ENV EXISTING_ENV DEST", file=sys.stderr)
        return 2
    prod, existing, dest = (Path(arg) for arg in argv)
    existing_text = existing.read_text() if existing.is_file() else None
    Path(dest).write_text(build(prod.read_text(), existing_text))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
