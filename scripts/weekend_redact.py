#!/usr/bin/env python3
"""Scrub secrets from a run-log tail before a loop wrapper posts it publicly.

REL-180 (R-505): the nightly wrappers post the agent's last 1500 bytes to a
public dead-man issue. The clones carry credentials (web/.env, .env), so an
agent that echoes an env value would publish it. Stdin -> stdout; every value
read from the clone's env files is scrubbed by exact match (longest first),
then any KEY=value / KEY: value whose key looks secret, then bearer tokens.
Stdlib only: it runs under whatever python3 the launchd PATH finds.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

ENV_FILES = (".env", ".env.ib-mode", "web/.env")
MIN_VALUE_LEN = 8
REDACTED = "[REDACTED]"
SECRET_KEY_RE = re.compile(
    r"\b([A-Za-z0-9_]*(?:TOKEN|SECRET|PASSWORD|PASSWD|PASS|AUTH|CREDENTIAL|API_KEY|APIKEY|_KEY)[A-Za-z0-9_]*)"
    r"(\s*[=:]\s*)(\S+)",
    re.IGNORECASE,
)
BEARER_RE = re.compile(r"(Bearer\s+)\S+", re.IGNORECASE)


def env_values(repo: Path) -> list[str]:
    """Secret-looking values from the clone's env files (and the runner's)."""
    values: set[str] = set()
    candidates = [repo / rel for rel in ENV_FILES]
    home = os.environ.get("HOME")
    if home:
        candidates.append(Path(home) / "radon-weekend" / ".env")
    for path in candidates:
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            _, _, value = line.partition("=")
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
                value = value[1:-1]
            if len(value) >= MIN_VALUE_LEN:
                values.add(value)
    return sorted(values, key=len, reverse=True)


def _fragment_candidates(value: str) -> list[str]:
    """Longest-first prefixes and suffixes of ``value`` down to the floor.

    REL-206 (R-565): a byte-bounded tail cuts a secret mid-value; the exact
    match misses the fragment and no `KEY=` shape remains on a cut line, so
    the key-pattern pass misses it too. Any prefix/suffix at or above the
    floor is scrubbed — the floor keeps ordinary prose intact.
    """
    fragments: list[str] = []
    for length in range(len(value) - 1, MIN_VALUE_LEN - 1, -1):
        fragments.append(value[:length])
        fragments.append(value[-length:])
    return fragments


def redact(text: str, values: list[str]) -> str:
    for value in values:
        text = text.replace(value, REDACTED)
    for value in values:
        for fragment in _fragment_candidates(value):
            if fragment in text:
                text = text.replace(fragment, REDACTED)
    # Bearer first: `Authorization: Bearer x` also matches the key pattern
    # (AUTH), which would otherwise consume the word `Bearer` as the value
    # and leave the token standing.
    text = BEARER_RE.sub(lambda m: f"{m.group(1)}{REDACTED}", text)
    text = SECRET_KEY_RE.sub(lambda m: f"{m.group(1)}{m.group(2)}{REDACTED}", text)
    return text


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="weekend_redact")
    parser.add_argument("--repo", required=True)
    args = parser.parse_args(argv)
    data = sys.stdin.buffer.read().decode("utf-8", errors="replace")
    sys.stdout.write(redact(data, env_values(Path(args.repo))))
    return 0


if __name__ == "__main__":
    sys.exit(main())
