#!/usr/bin/env python3
"""Resolve the security / DeepSec Claude model ladder from the live CLI catalog.

Policy (Joe 2026-09-21): list the Mini Claude Code catalog, skip the newest
generation, run the prior / second-newest first, then the rest. Anthropic
keeps shipping top-tier models; a static opus pin goes stale.

Catalog source is the same subscription CLI the wrappers already launch
(`claude models`). This module never calls the Anthropic HTTP API and unsets
every billing-reroute key before spawning Claude Code.

Order: newest-first as printed by `claude models` (Mini 2026-09-21 listed
Fable, then Opus, then Sonnet, then Haiku). Aliases of one generation
(`claude-opus-5[1m]` / `claude-opus-5`, `claude-fable-5-1` / `claude-fable-5[1m]`)
collapse so "second-newest" is a real generation.

Stdout is the space-separated ladder (no newest). Empty stdout + exit 1
means the caller must use SAFETY_LADDER and log the failure. Tests fixture
the catalog via --from-text or RADON_WEEKEND_CLAUDE_CATALOG; they must not
hit the network.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
from typing import Iterable

# Today's known-good prior + fallback when discovery fails. Excludes Fable /
# the elite newest family. Update only when the safety pair itself is retired.
SAFETY_LADDER = ("claude-opus-5", "claude-sonnet-5")

CLAUDE_ID_RE = re.compile(r"claude-[a-z0-9]+(?:[-.][a-z0-9\[\]]+)+", re.I)
DATE_SUFFIX_RE = re.compile(r"-20\d{6}$")
BRACKET_RE = re.compile(r"\[.*?\]")

# Same names the security wrappers unset. Catalog discovery must not start
# billing a prepaid Anthropic path.
BILLING_REROUTE_KEYS = (
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_BASE_URL",
    "CLAUDE_CODE_API_KEY",
    "CLAUDE_CODE_API_BASE_URL",
    "CLAUDE_CODE_HFI_BEARER_TOKEN",
    "CLAUDE_API_KEY",
    "CLAUDE_CODE_API_KEY_FILE_DESCRIPTOR",
    "CLAUDE_CODE_GATEWAY_TOKEN",
    "CLAUDE_CODE_GATEWAY_TOKEN_FILE_DESCRIPTOR",
    "CLAUDE_CODE_HOST_AUTH_ENV_VAR",
    "CLAUDE_CODE_HOST_CREDS_FILE",
    "ANTHROPIC_UNIX_SOCKET",
    "ANTHROPIC_PROFILE",
    "ANTHROPIC_FEDERATION_RULE_ID",
    "ANTHROPIC_ORGANIZATION_ID",
    "AWS_BEARER_TOKEN_BEDROCK",
    "ANTHROPIC_AWS_API_KEY",
    "ANTHROPIC_AWS_BASE_URL",
    "ANTHROPIC_BEDROCK_BASE_URL",
    "ANTHROPIC_BEDROCK_MANTLE_BASE_URL",
    "ANTHROPIC_VERTEX_BASE_URL",
    "ANTHROPIC_GOOGLE_CLOUD_BASE_URL",
    "ANTHROPIC_FOUNDRY_API_KEY",
    "ANTHROPIC_FOUNDRY_AUTH_TOKEN",
    "ANTHROPIC_FOUNDRY_BASE_URL",
    "ANTHROPIC_FOUNDRY_RESOURCE",
    "ANTHROPIC_IDENTITY_TOKEN",
    "ANTHROPIC_IDENTITY_TOKEN_FILE",
    "CLAUDE_CODE_USE_BEDROCK",
    "CLAUDE_CODE_USE_VERTEX",
    "CLAUDE_CODE_USE_FOUNDRY",
    "CLAUDE_CODE_USE_GATEWAY",
)

DEFAULT_MODELS_CMD = "claude models"


def family_key(model_id: str) -> str:
    """Strip context-window brackets and dated snapshots."""
    s = model_id.strip().lower()
    s = BRACKET_RE.sub("", s)
    s = DATE_SUFFIX_RE.sub("", s)
    return s


def same_family(left: str, right: str) -> bool:
    """True when one id is a hyphen-token prefix of the other.

    `claude-fable-5` and `claude-fable-5-1` are one generation.
    `claude-opus-5` and `claude-opus-4` are not.
    """
    a = family_key(left).split("-")
    b = family_key(right).split("-")
    n = min(len(a), len(b))
    return n > 0 and a[:n] == b[:n]


def extract_ids(text: str) -> list[str]:
    """Newest-first ids from `claude models` text or a models JSON body."""
    stripped = text.strip()
    if stripped.startswith("{") or stripped.startswith("["):
        try:
            payload = json.loads(stripped)
        except json.JSONDecodeError:
            payload = None
        if isinstance(payload, dict):
            rows = payload.get("data") or payload.get("models") or []
            ids = [
                row["id"]
                for row in rows
                if isinstance(row, dict) and isinstance(row.get("id"), str)
            ]
            if ids:
                return ids
        elif isinstance(payload, list):
            ids = []
            for row in payload:
                if isinstance(row, str):
                    ids.append(row)
                elif isinstance(row, dict) and isinstance(row.get("id"), str):
                    ids.append(row["id"])
            if ids:
                return ids
    ids: list[str] = []
    seen: set[str] = set()
    for match in CLAUDE_ID_RE.finditer(text):
        mid = match.group(0)
        if mid not in seen:
            seen.add(mid)
            ids.append(mid)
    return ids


def dedupe_generations(ids: Iterable[str]) -> list[str]:
    """Keep the first (newest) spelling of each generation."""
    unique: list[str] = []
    for mid in ids:
        if any(same_family(mid, kept) for kept in unique):
            continue
        unique.append(mid)
    return unique


def skip_newest(ids: Iterable[str]) -> list[str]:
    """Drop generation 0; return the prior model then deeper fallbacks."""
    unique = dedupe_generations(ids)
    if len(unique) < 2:
        return []
    return unique[1:]


def ladder_from_text(text: str) -> list[str]:
    return skip_newest(extract_ids(text))


def _scrubbed_env() -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k not in BILLING_REROUTE_KEYS}
    return env


def read_live_catalog(cmd: str | None = None, timeout: float = 20.0) -> str:
    """Run the subscription `claude models` command. No API key path."""
    raw = cmd or os.environ.get("RADON_WEEKEND_CLAUDE_MODELS_CMD") or DEFAULT_MODELS_CMD
    argv = shlex.split(raw)
    if not argv:
        raise RuntimeError("empty claude models command")
    try:
        proc = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=_scrubbed_env(),
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"claude models failed: {exc}") from exc
    text = (proc.stdout or "") + "\n" + (proc.stderr or "")
    if proc.returncode != 0 and not extract_ids(text):
        raise RuntimeError(
            f"claude models exited {proc.returncode}: {(proc.stderr or proc.stdout or '').strip()[:200]}"
        )
    return text


def resolve_ladder(text: str | None = None) -> list[str]:
    catalog = text
    if catalog is None:
        path = os.environ.get("RADON_WEEKEND_CLAUDE_CATALOG")
        if path:
            catalog = open(path, encoding="utf-8").read()
        else:
            catalog = read_live_catalog()
    return ladder_from_text(catalog)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--from-text",
        metavar="PATH",
        help="Fixture catalog (use - for stdin). Tests must pass this; no network.",
    )
    args = parser.parse_args(argv)
    try:
        if args.from_text:
            src = sys.stdin.read() if args.from_text == "-" else open(args.from_text, encoding="utf-8").read()
            ladder = ladder_from_text(src)
            skipped = dedupe_generations(extract_ids(src))
        else:
            if os.environ.get("RADON_WEEKEND_CLAUDE_CATALOG"):
                src = open(os.environ["RADON_WEEKEND_CLAUDE_CATALOG"], encoding="utf-8").read()
            else:
                src = read_live_catalog()
            ladder = ladder_from_text(src)
            skipped = dedupe_generations(extract_ids(src))
    except (OSError, RuntimeError) as exc:
        print(f"security_claude_ladder: {exc}", file=sys.stderr)
        return 1
    if not ladder:
        print(
            "security_claude_ladder: catalog empty or only one generation; no skip-newest ladder",
            file=sys.stderr,
        )
        return 1
    newest = skipped[0] if skipped else "?"
    print(f"security_claude_ladder: skip-newest {newest}; ladder {' '.join(ladder)}", file=sys.stderr)
    print(" ".join(ladder))
    return 0


if __name__ == "__main__":
    sys.exit(main())
