#!/usr/bin/env python3
"""Execute a command with a dotenv file parsed literally.

Shell-sourcing production credentials expands dollar-shaped substrings. This
helper keeps values off the command line and disables python-dotenv interpolation
before replacing itself with the requested process.
"""

from __future__ import annotations

import os
import sys

from dotenv import dotenv_values


def main() -> int:
    if len(sys.argv) < 4 or sys.argv[2] != "--":
        print("usage: run_with_env.py ENV_FILE -- COMMAND [ARG ...]", file=sys.stderr)
        return 2

    values = dotenv_values(sys.argv[1], interpolate=False)
    env = os.environ.copy()
    for key, value in values.items():
        if value is not None:
            env[key] = value

    command = sys.argv[3:]
    os.execvpe(command[0], command, env)
    return 127  # pragma: no cover - os.execvpe replaces this process


if __name__ == "__main__":
    raise SystemExit(main())
