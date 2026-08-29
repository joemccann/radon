"""R-334 / R-352 / R-353 / REL-119: the equity-chain retry retries only what
it was written for, and a Flex policy block is not a transient outage.

R-334: `_connect_with_retry` caught bare `Exception`, so it retried EVERY
failure class identically to the transient handshake timeout it exists for —
including a Gateway awaiting 2FA and the auto-allocator's client-id
exhaustion. `ib_option_chain.py` is not in `_NON_IDEMPOTENT_IB_SCRIPTS`, so
the server's `auth_state != 'authenticated'` refusal never applies to it.
Against a 2FA-pending Gateway the script burned 3 handshakes plus 2s of sleep
instead of failing in ~4s, and each attempt re-entered `_connect_auto_allocate`,
which walks all 30 ids in SUBPROCESS_ID_RANGE on an in-use error — up to 90
serialised handshakes on the same queue `ib_place_order.py` must win.

R-352: the retry budget does not fit inside the caller's 45s cap, so the
`except Exception` that prints the JSON error envelope never runs and the
operator gets a bare subprocess-timeout string.

R-353: `assert_sendrequest_permitted` and the embargo check it invokes sat
INSIDE a `try` whose `except Exception` rewrote them into a generic `IBError`,
so a deliberate policy block and a real 1025 lockout both presented as a
transient Flex outage and every performance reconstruction silently ran on
stale cache.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import ib_option_chain as chain  # noqa: E402
from clients.ib_client import IBConnectionError  # noqa: E402


class _Client:
    def __init__(self, exc):
        self.exc = exc
        self.attempts = 0

    def connect(self, **_kwargs):
        self.attempts += 1
        raise self.exc


class TestConnectRetryClassifies:
    def test_an_auth_pending_gateway_fails_on_the_first_attempt(self, monkeypatch):
        monkeypatch.setattr(chain.time, "sleep", lambda _s: None)
        client = _Client(IBConnectionError("Gateway is logged in but awaiting 2FA"))
        with pytest.raises(IBConnectionError):
            chain._connect_with_retry(client, port=4002, client_id="auto")
        assert client.attempts == 1, (
            "a Gateway awaiting 2FA will not become available inside the retry "
            f"budget; {client.attempts} handshakes each re-enter the allocator "
            "and can walk all 30 subprocess ids"
        )

    def test_client_id_exhaustion_fails_on_the_first_attempt(self, monkeypatch):
        monkeypatch.setattr(chain.time, "sleep", lambda _s: None)
        client = _Client(
            IBConnectionError(
                "Failed to connect to IB on 127.0.0.1:4002: all client IDs 100-129 in use"
            )
        )
        with pytest.raises(IBConnectionError):
            chain._connect_with_retry(client, port=4002, client_id="auto")
        assert client.attempts == 1, (
            "retrying id exhaustion walks the whole SUBPROCESS_ID_RANGE again "
            "on the queue ib_place_order.py must win to transmit an order"
        )

    def test_a_handshake_timeout_still_retries_to_the_cap(self, monkeypatch):
        monkeypatch.setattr(chain.time, "sleep", lambda _s: None)
        client = _Client(TimeoutError("connect timed out"))
        with pytest.raises(TimeoutError):
            chain._connect_with_retry(client, port=4002, client_id="auto")
        assert client.attempts == chain.CONNECT_ATTEMPTS

    def test_a_successful_connect_does_not_retry(self, monkeypatch):
        calls = []

        class _Ok:
            def connect(self, **kwargs):
                calls.append(kwargs)

        chain._connect_with_retry(_Ok(), port=4002, client_id="auto")
        assert len(calls) == 1


class TestConnectBudgetFitsTheCallerCap:
    """R-352: the error envelope has to be able to render."""

    def test_worst_case_connect_plus_two_requests_fits_under_the_cap(self, monkeypatch):
        """Measured off the real schedule, not off the arithmetic that defines it.

        The old form recomputed `CONNECT_TIMEOUT_S`'s own definition, so it
        reduced to `CONNECT_BUDGET_S <= CONNECT_BUDGET_S` and could not fail
        for any attempts/backoff pair.
        """
        from scripts.api.server import _EQUITY_OPTIONS_CHAIN_TIMEOUT_S as CAP
        from utils.ib_preflight import IB_REQUEST_TIMEOUT_S

        slept: list[float] = []
        monkeypatch.setattr(chain.time, "sleep", slept.append)

        class _Recording:
            def __init__(self):
                self.timeouts = []

            def connect(self, **kwargs):
                self.timeouts.append(kwargs.get("timeout"))
                raise TimeoutError("connect timed out")

        client = _Recording()
        with pytest.raises(TimeoutError):
            chain._connect_with_retry(client, port=4002, client_id="auto")

        assert client.timeouts, "connect() was never given a timeout"
        assert all(
            t is not None and t > 0 for t in client.timeouts
        ), f"an unusable handshake window: {client.timeouts}"

        worst_connect = sum(client.timeouts) + sum(slept)
        total = worst_connect + 2 * IB_REQUEST_TIMEOUT_S + chain._ENVELOPE_MARGIN_S
        assert total <= CAP, (
            f"worst case is {total}s against a {CAP}s subprocess cap, so the "
            "except-clause that prints the JSON error envelope never runs and "
            "the operator gets a bare subprocess-timeout string"
        )

    def test_the_module_cap_matches_the_endpoint_that_enforces_it(self):
        """`_CALLER_CAP_S` is a hand-copy of the server constant; pin the pair.

        The old assertion here was `CONNECT_BUDGET_S < 45.0` under the name
        "derived from the cap not hardcoded" — a property the source does not
        have, checked against a fourth copy of the same literal.
        """
        from scripts.api.server import _EQUITY_OPTIONS_CHAIN_TIMEOUT_S as CAP

        assert chain._CALLER_CAP_S == CAP, (
            f"ib_option_chain sizes its retry budget against {chain._CALLER_CAP_S}s "
            f"but /options/chain kills the subprocess at {CAP}s"
        )
        assert chain.CONNECT_ATTEMPTS >= 2, "the transient-timeout retry must survive"


class TestFlexPolicyBlockIsNotATransientOutage:
    def test_flex_send_disabled_propagates_as_itself(self, monkeypatch):
        from clients.ib_client import IBClient
        from utils.flex_send import FlexSendDisabled

        client = IBClient.__new__(IBClient)
        import logging

        client.logger = logging.getLogger("test")
        with pytest.raises(FlexSendDisabled):
            client.run_flex_query(query_id=1234, token="t")

    def test_a_token_lockout_propagates_as_itself(self, monkeypatch):
        from clients.ib_client import IBClient
        from utils import flex_embargo, flex_send
        import logging

        def _blocked():
            raise flex_embargo.FlexTokenLocked("1025 embargo live")

        monkeypatch.setattr(flex_embargo, "raise_if_blocked", _blocked)
        monkeypatch.setattr(flex_send, "assert_sendrequest_permitted",
                            lambda **_k: _blocked())

        client = IBClient.__new__(IBClient)
        client.logger = logging.getLogger("test")
        with pytest.raises(flex_embargo.FlexTokenLocked):
            client.run_flex_query(query_id=1234, token="t")

    def test_a_real_flex_transport_failure_is_still_an_iberror(self, monkeypatch):
        from clients.ib_client import IBClient, IBError
        from utils import flex_send
        import logging

        monkeypatch.setattr(flex_send, "assert_sendrequest_permitted", lambda **_k: None)
        monkeypatch.setattr(
            "ib_insync.FlexReport",
            lambda **_k: (_ for _ in ()).throw(RuntimeError("flex 503")),
            raising=False,
        )
        client = IBClient.__new__(IBClient)
        client.logger = logging.getLogger("test")
        with pytest.raises(IBError, match="Flex query 1234 failed"):
            client.run_flex_query(query_id=1234, token="t")


class TestPortfolioPerformanceNamesThePolicyState:
    """R-353: the degrade warning must not call a permanent state transient."""

    def test_a_policy_block_is_not_reported_as_unavailable(self):
        import portfolio_performance as pp
        from utils.flex_send import FlexSendDisabled

        reason = pp._flex_unavailability_reason(FlexSendDisabled("file-ingest only"))
        assert "DISABLED by policy" in reason
        assert "will not clear on its own" in reason

    def test_a_token_lockout_is_not_reported_as_unavailable(self):
        import portfolio_performance as pp
        from utils.flex_embargo import FlexTokenLocked

        reason = pp._flex_unavailability_reason(FlexTokenLocked("1025"))
        assert "LOCKED OUT" in reason
        assert "1025 token embargo" in reason

    def test_a_real_outage_keeps_the_original_wording(self):
        import portfolio_performance as pp

        assert pp._flex_unavailability_reason(RuntimeError("connection reset")) == (
            "Live IB Flex Query unavailable"
        )
