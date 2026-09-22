#!/usr/bin/env python3
"""Page when the installed Claude Code CLI references an env name nobody reviewed.

Claude Code is not version-pinned (operator decision 2026-09-19), so a new
release can add an env var that reroutes model billing off the claude.ai
subscription. This diffs every ANTHROPIC_* / CLAUDE_CODE_* / AWS_BEARER_* name
in the installed binary against scripts/claude_cli_env_reviewed.txt.

Exit 0: nothing new. Exit 1: unreviewed names (printed). With --notify, one
Pushover per CLI version. Review each name in context (see
docs/security-approved-tools.md), add reroutes to BILLING_REROUTE_KEYS /
BILLING_REROUTE_FLAGS in every loop wrapper, then append all of them to the
reviewed list. Stdlib only.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import urllib.parse
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
NAME = re.compile(rb"(?:ANTHROPIC_|CLAUDE_CODE_|AWS_BEARER_)[A-Z0-9_]+")
WEEKEND_ROOT = Path.home() / "radon-weekend"


def env_names(binary: Path) -> set[str]:
    return {m.decode() for m in NAME.findall(binary.read_bytes())}


def reviewed_names(path: Path) -> set[str]:
    lines = path.read_text().splitlines()
    return {s for s in (line.strip() for line in lines) if s and not s.startswith("#")}


def installed_binary() -> Path:
    claude = shutil.which("claude")
    if not claude:
        raise SystemExit("claude CLI not on PATH")
    version = subprocess.run([claude, "--version"], capture_output=True, text=True, check=True).stdout.split()[0]
    candidate = Path.home() / ".local" / "share" / "claude" / "versions" / version
    return candidate if candidate.is_file() else Path(os.path.realpath(claude))


def _cred(key: str) -> str:
    if os.environ.get(key):
        return os.environ[key]
    env = WEEKEND_ROOT / ".env"
    if env.is_file():
        for line in env.read_text().splitlines():
            if line.startswith(f"{key}="):
                return line.split("=", 1)[1].strip().strip("'\"")
    return ""


def pushover(title: str, message: str) -> bool:
    user, token = _cred("PUSHOVER_USER"), _cred("PUSHOVER_TOKEN")
    if not (user and token):
        print("PUSHOVER_USER / PUSHOVER_TOKEN missing; not paged", file=sys.stderr)
        return False
    data = urllib.parse.urlencode({"token": token, "user": user, "title": title, "message": message}).encode()
    try:
        with urllib.request.urlopen("https://api.pushover.net/1/messages.json", data, timeout=10) as resp:
            return resp.status == 200
    except OSError as exc:
        print(f"pushover failed: {exc}", file=sys.stderr)
        return False


def main(argv: list[str] | None = None, send=pushover) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--binary", type=Path)
    ap.add_argument("--reviewed", type=Path, default=REPO / "scripts" / "claude_cli_env_reviewed.txt")
    ap.add_argument("--state-dir", type=Path, default=WEEKEND_ROOT / "logs")
    ap.add_argument("--notify", action="store_true")
    args = ap.parse_args(argv)

    binary = args.binary or installed_binary()
    new = sorted(env_names(binary) - reviewed_names(args.reviewed))
    if not new:
        print(f"claude CLI {binary.name}: no unreviewed env names")
        return 0
    print(f"claude CLI {binary.name}: {len(new)} unreviewed env name(s)")
    print("\n".join(new))
    if args.notify:
        stamp = args.state_dir / f"claude-cli-env-drift.{binary.name}.paged"
        if not stamp.exists():
            message = (f"Claude Code {binary.name} references {len(new)} unreviewed env name(s): "
                       f"{', '.join(new[:12])}{' …' if len(new) > 12 else ''}. "
                       "Review for billing reroutes per docs/security-approved-tools.md.")
            if send("radon CLI env drift", message):
                args.state_dir.mkdir(parents=True, exist_ok=True)
                stamp.write_text("\n".join(new) + "\n")
    return 1


if __name__ == "__main__":
    sys.exit(main())
