#!/usr/bin/env python3
"""Root-installed entrypoint for the Mac-local model gateway.

install.sh copies GATEWAY_FILES into ./src beside this file and launchd runs
it as the dedicated gateway account with `python -I`, so only that root-owned
copy is importable: never the bot's clone, a caller's cwd or PYTHONPATH.
"""
from __future__ import annotations

import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent / "src"


def main(argv: list[str]) -> int:
    sys.path[:0] = [str(SRC)]
    if argv[:1] == ["serve"]:
        from model_gateway.server import main as serve
        return serve(argv[1:]) or 0
    print("usage: gateway_launch.py serve [--socket PATH --control-socket PATH]", file=sys.stderr)
    return 64


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
