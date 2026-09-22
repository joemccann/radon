#!/usr/bin/env python3
"""Agent PATH guard for nightly PR creation; other gh commands pass through.

The wrapper snapshots this file and nightly_publish.py from origin/main outside
its mutable checkout. This prevents an accidental direct gh command from
bypassing publication policy. It is not a sandbox against a malicious agent.
"""
from __future__ import annotations

import os
from pathlib import Path
import re
import subprocess
import sys


class Refused(ValueError):
    pass


def option(args: list[str], long: str, short: str = "", default: str = "") -> str:
    value = default
    for i, arg in enumerate(args):
        if arg in (long, short):
            if i + 1 == len(args):
                raise Refused(f"missing value for {arg}")
            value = args[i + 1]
        elif arg.startswith(long + "="):
            value = arg.split("=", 1)[1]
        elif short and arg.startswith(short) and arg != short:
            value = arg[len(short):]
    return value


def creation_kind(args: list[str]) -> str:
    """Handle global gh flags as well as the usual subcommand-first form."""
    for i in range(len(args) - 1):
        if args[i:i + 2] == ["pr", "create"]:
            return "pr"
    if "api" in args:
        tail = args[args.index("api") + 1:]
        endpoint = next((a for a in tail if a.rstrip("/").endswith("/graphql") or a == "graphql" or re.search(r"(?:^|/)repos/[^/]+/[^/]+/pulls/?(?:\?.*)?$", a)), "")
        method = option(tail, "--method", "-X").upper()
        body = any(a in ("--input", "--field", "--raw-field", "-f", "-F") or a.startswith(("--input=", "--field=", "--raw-field=", "-f", "-F")) for a in tail)
        if endpoint == "graphql" or endpoint.rstrip("/").endswith("/graphql"):
            # Inline read-only queries are safe. File/stdin payloads and mutations
            # must use the normal gh commands so publication cannot hide in JSON.
            if "mutation" in " ".join(tail) or "createPullRequest" in " ".join(tail) or any(a == "--input" or a.startswith("--input=") or a == "query=@-" or "query=@" in a for a in tail):
                return "api"
        elif endpoint and (method == "POST" or (not method and body)):
            return "api"
    return ""


def guard(args: list[str], *, run=subprocess.run) -> None:
    kind = creation_kind(args)
    if not kind:
        return
    if kind == "api":
        raise Refused("nightly PR API creation is disabled; use scripts/nightly_publish.py publish")
    if any(a in ("--repo", "-R") or a.startswith(("--repo=", "-R")) for a in args):
        raise Refused("nightly PR creation must target the checkout's origin repository")
    base = option(args, "--base", "-B", "main")
    head = option(args, "--head", "-H")
    if base != "main" or not head or ":" in head or head.startswith("-"):
        raise Refused("nightly PR creation requires --base main and an explicit origin --head branch")
    run(["git", "check-ref-format", "refs/heads/" + head], check=True, capture_output=True, timeout=30)
    # GitHub publishes the remote branch, so classify that exact fetched tree,
    # never an unpushed local fix which could conceal an empty remote branch.
    run(["git", "fetch", "--quiet", "origin", "+refs/heads/main:refs/remotes/origin/main", f"+refs/heads/{head}:refs/remotes/origin/{head}"], check=True, capture_output=True, timeout=120)
    result = run([sys.executable, "-I", str(Path(__file__).with_name("nightly_publish.py")), "check", "--base", "origin/main", "--head", "origin/" + head], check=False, capture_output=True, text=True, timeout=120)
    if result.returncode:
        reason = "no substantive changes" if result.returncode == 3 else "publication check failed"
        raise Refused(f"{reason}; no PR submitted. Use the rolling issue for audit-only results.")


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    real_gh = os.environ.get("RADON_NIGHTLY_REAL_GH", "")
    if not real_gh or not Path(real_gh).is_absolute():
        print("nightly PR guard: missing absolute gh executable", file=sys.stderr)
        return 1
    try:
        guard(args)
    except (Refused, OSError, subprocess.SubprocessError) as exc:
        print(f"nightly PR guard: {exc}", file=sys.stderr)
        return 1
    os.execv(real_gh, [real_gh, *args])
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
