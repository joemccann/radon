#!/usr/bin/env python3
"""CI isolation guard for a demo.radon.run deploy environment.

Run in CI against the demo deploy's env to STRUCTURALLY reject a misconfigured
demo stack before it ships. Three independent assertions (mirrors the plan's
"three independent guarantees"):

  1. Separate Turso  — TURSO_DEMO_DB_URL is set, is NOT the prod DB, and the
     prod TURSO_DB_URL (if present) is not the same database.
  2. No IB           — RADON_API_TEST_MODE=1 AND no reachable/real IB host
     (IB_GATEWAY_HOST unset or loopback).
  3. Test-mode on    — RADON_API_TEST_MODE=1 so every IB path is stubbed.

Pure + testable: ``check_demo_isolation(env: Mapping) -> list[str]`` returns
the list of violation messages (empty == clean). ``main`` reads os.environ,
prints them, and exits non-zero on any violation.

Usage (CI):
    python3 scripts/ci/check_demo_isolation.py        # reads os.environ
"""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping

# A demo DB URL containing this substring IS the production database.
PROD_DB_MARKER = "radon-joemccann"

# Hosts that mean "no real IB reachable". An empty/unset host or one of these
# loopback forms is acceptable on the demo VM (there is no IB container/route).
_LOOPBACK_HOSTS = {"", "127.0.0.1", "localhost", "::1", "[::1]", "0.0.0.0"}


def _is_loopback_or_unset(host: str | None) -> bool:
    if host is None:
        return True
    return host.strip().lower() in _LOOPBACK_HOSTS


def check_demo_isolation(env: Mapping[str, str]) -> list[str]:
    """Return a list of isolation violations for the demo env (empty == OK)."""
    violations: list[str] = []

    demo_url = env.get("TURSO_DEMO_DB_URL", "")
    if not demo_url:
        violations.append("TURSO_DEMO_DB_URL is not set (demo needs its own DB).")
    elif PROD_DB_MARKER in demo_url:
        violations.append(
            f"TURSO_DEMO_DB_URL contains the prod marker {PROD_DB_MARKER!r} "
            f"({demo_url!r}) — demo must use a SEPARATE Turso database."
        )

    prod_url = env.get("TURSO_DB_URL", "")
    if prod_url and demo_url and prod_url == demo_url:
        violations.append(
            "TURSO_DB_URL == TURSO_DEMO_DB_URL — demo and prod point at the "
            "same database."
        )

    if env.get("RADON_API_TEST_MODE") != "1":
        violations.append(
            "RADON_API_TEST_MODE must be '1' on the demo VM so every IB path "
            "is stubbed at source."
        )

    ib_host = env.get("IB_GATEWAY_HOST")
    if not _is_loopback_or_unset(ib_host):
        violations.append(
            f"IB_GATEWAY_HOST is set to a real host ({ib_host!r}) — the demo "
            "VM must have NO route to an IB Gateway."
        )

    return violations


def main(env: Mapping[str, str] | None = None) -> int:
    env = os.environ if env is None else env
    violations = check_demo_isolation(env)
    if violations:
        sys.stderr.write("DEMO ISOLATION GUARD FAILED:\n")
        for v in violations:
            sys.stderr.write(f"  ✗ {v}\n")
        return 1
    print("DEMO ISOLATION GUARD PASSED: separate DB, TEST_MODE on, no real IB.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
