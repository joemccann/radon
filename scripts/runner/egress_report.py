#!/usr/bin/env python3
"""Summarize log-only egress would-block decisions by day."""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

DEFAULT_LOG = Path("/Library/Radon/egress/log/decisions.log")


def _printable(text: str) -> str:
    """Escape anything a client could put in a CONNECT host before it reaches a terminal."""
    return text.encode("unicode_escape").decode("ascii")


def summarize(text: str) -> dict[str, dict[str, int]]:
    counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for raw in text.splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            row = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if not isinstance(row, dict) or row.get("decision") != "would_block":
            continue
        day = row.get("day")
        host = row.get("host")
        port = row.get("port")
        if not isinstance(day, str) or not isinstance(host, str) or not host:
            continue
        if any(ch.isspace() for ch in host) or any(ch.isspace() for ch in day):
            continue
        host, day = _printable(host), _printable(day)
        label = f"{host}:{port}" if isinstance(port, int) else host
        counts[day][label] += 1
    return {day: dict(hosts) for day, hosts in counts.items()}


def render(counts: dict[str, dict[str, int]]) -> str:
    if not counts:
        return "no would-block hits\n"
    lines = []
    for day in sorted(counts):
        lines.append(day)
        for label, count in sorted(counts[day].items()):
            lines.append(f"{label} {count}")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Summarize egress would-block hits per day.")
    parser.add_argument("--log", type=Path, default=DEFAULT_LOG)
    args = parser.parse_args(argv)
    if not args.log.is_file():
        print(f"no decision log at {args.log}", file=sys.stderr)
        return 1
    print(render(summarize(args.log.read_text(encoding="utf-8"))), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
