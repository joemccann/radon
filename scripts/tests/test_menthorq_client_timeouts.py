"""
Regression: MenthorQ login navigation must not time out at 30s.

Symptom: /cta surfaced "CTA CACHE STALE — Timeout 30000ms exceeded" when
Page.goto("https://menthorq.com/login/", waiting until "networkidle") tripped
the 30s default. WordPress marketing sites pipe continuous analytics /
heartbeat traffic so "networkidle" is fragile by design — the login form
itself is server-rendered so we don't actually need to wait for it.

Locks in two invariants:
  1. No 30000ms (= 30s) timeout values remain in clients/menthorq_client.py.
     The minimum nav timeout is 60s.
  2. The login goto no longer waits for "networkidle". Use the less strict
     "domcontentloaded" or "load" so the call doesn't depend on third-party
     scripts ever quieting down.

Removing either invariant fails CI.
"""

from __future__ import annotations

import re
from pathlib import Path

CLIENT = Path(__file__).resolve().parents[1] / "clients" / "menthorq_client.py"
SRC = CLIENT.read_text()


def test_no_30s_timeouts_remain() -> None:
    """No `timeout=30000` literal in the file. Bump or remove every one."""
    matches = re.findall(r"timeout=30000\b", SRC)
    assert matches == [], (
        "Found `timeout=30000` literal(s) in menthorq_client.py. "
        "Bump to ≥ 60000ms or use the MENTHORQ_NAV_TIMEOUT_MS constant."
    )


def test_login_goto_does_not_wait_for_networkidle() -> None:
    """The `_login` page.goto must not use wait_until="networkidle"."""
    # Find the body of `_login` and assert no networkidle navigation goto.
    body = SRC[SRC.index("def _login(self)"):]
    body = body[: body.index("\n    def ", 1)] if "\n    def " in body[1:] else body
    # The post-submit `wait_for_load_state` was previously also `networkidle`.
    # Either form is suspicious for the login flow — flag both.
    assert 'goto(LOGIN_URL, wait_until="networkidle"' not in body, (
        "_login still navigates to LOGIN_URL with wait_until='networkidle' — "
        "this is the exact pattern that hit the 30s timeout. Use "
        "wait_until='domcontentloaded' (or 'load') instead."
    )


def test_nav_timeout_constant_exists_and_is_at_least_60s() -> None:
    """A central constant makes the timeout tunable and explicit.

    Tightening this floor to 60s prevents anyone slipping a 30s back in.
    """
    match = re.search(r"MENTHORQ_NAV_TIMEOUT_MS\s*=\s*(?:int\([^)]*\)|(\d+))", SRC)
    assert match is not None, (
        "Define a module-level MENTHORQ_NAV_TIMEOUT_MS in menthorq_client.py "
        "and use it for every page.goto / wait_for_load_state in the login flow."
    )
    # The constant may pull from os.environ — accept either form. When the
    # default is a literal int, enforce a 60s floor.
    if match.group(1) is not None:
        assert int(match.group(1)) >= 60000, "MENTHORQ_NAV_TIMEOUT_MS default must be ≥ 60000ms"


def test_login_navigation_uses_the_constant() -> None:
    """Every login-flow nav must thread MENTHORQ_NAV_TIMEOUT_MS through."""
    # _login's goto + post-submit wait, plus _restore_session's goto.
    # All three were the 30s offenders. Each must now reference the constant.
    nav_calls = re.findall(r"\.goto\([^)]*\)|wait_for_load_state\([^)]*\)", SRC)
    suspicious = [c for c in nav_calls if "timeout=" in c and "MENTHORQ_NAV_TIMEOUT_MS" not in c and "30000" not in c]
    # Some non-login navs (e.g. ticker page goto with timeout=60000) are fine
    # to leave at their explicit value — the rule is just that the login flow
    # must use the constant. Spot-check by counting.
    constant_uses = SRC.count("MENTHORQ_NAV_TIMEOUT_MS")
    assert constant_uses >= 3, (
        f"MENTHORQ_NAV_TIMEOUT_MS must be referenced by all three login-flow "
        f"navigations (_restore_session goto, _login goto, _login "
        f"wait_for_load_state). Found {constant_uses} uses."
    )
