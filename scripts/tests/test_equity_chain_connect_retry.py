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

    def test_worst_case_connect_plus_two_requests_fits_under_the_cap(self):
        from utils.ib_preflight import IB_REQUEST_TIMEOUT_S

        cap = 45.0
        worst_connect = (
            chain.CONNECT_ATTEMPTS * chain.CONNECT_TIMEOUT_S
            + (chain.CONNECT_ATTEMPTS - 1) * chain.CONNECT_BACKOFF_S
        )
        total = worst_connect + 2 * IB_REQUEST_TIMEOUT_S
        assert total < cap, (
            f"worst case is {total}s against a {cap}s subprocess cap, so the "
            "except-clause that prints the JSON error envelope never runs and "
            "the operator gets a bare subprocess-timeout string"
        )

    def test_the_budget_is_derived_from_the_cap_not_hardcoded(self):
        assert chain.CONNECT_BUDGET_S < 45.0
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
