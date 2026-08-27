"""ib_option_chain.py must ride out a transient IB handshake timeout.

Production 2026-08-27 14:22:10: `/options/expirations?symbol=NOW` returned 504
because `ib_option_chain.py` connected exactly once and the Gateway handshake
timed out while several scheduled scans were connecting on the same socket.
Two minutes later the identical request served in 1.1s. The sibling
`ib_chain.py` already retries this class of failure; the equity chain did not.

These tests inject a fake IBClient so no live Gateway is needed.
"""

import json

import pytest

from scripts import ib_option_chain


class _FakeExpiryChain:
    def __init__(self):
        self.tradingClass = "NOW"
        self.expirations = ["20260828", "20260904"]
        self.exchange = "SMART"
        self.strikes = [100.0, 110.0]
        self.multiplier = "100"


class _FakeIB:
    RequestTimeout = 0

    def qualifyContracts(self, contract):
        contract.conId = 1234
        return [contract]

    def reqSecDefOptParams(self, *_args):
        return [_FakeExpiryChain()]


class _FakeClient:
    """IBClient stand-in whose first N connects raise the production error."""

    def __init__(self, fail_connects=0):
        self._fail_connects = fail_connects
        self.connect_calls = 0
        self.connect_timeouts = []
        self._ib = _FakeIB()
        self.disconnected = False

    def connect(self, **kwargs):
        self.connect_calls += 1
        self.connect_timeouts.append(kwargs.get("timeout"))
        if self.connect_calls <= self._fail_connects:
            raise ConnectionError("API connection failed: TimeoutError()")

    def disconnect(self):
        self.disconnected = True


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr(ib_option_chain.time, "sleep", lambda _s: None)


def _run(monkeypatch, capsys, client, argv=("--symbol", "NOW")):
    monkeypatch.setattr(ib_option_chain, "IBClient", lambda *a, **k: client)
    monkeypatch.setattr("sys.argv", ["ib_option_chain.py", *argv])
    ib_option_chain.main()
    return json.loads(capsys.readouterr().out.strip())


def test_transient_handshake_timeout_is_retried(monkeypatch, capsys):
    client = _FakeClient(fail_connects=2)
    out = _run(monkeypatch, capsys, client)
    assert "error" not in out, out
    assert out["expirations"] == ["20260828", "20260904"]
    assert client.connect_calls == 3
    assert client.disconnected is True


def test_connect_retries_are_bounded(monkeypatch, capsys):
    client = _FakeClient(fail_connects=99)
    monkeypatch.setattr(ib_option_chain, "IBClient", lambda *a, **k: client)
    monkeypatch.setattr("sys.argv", ["ib_option_chain.py", "--symbol", "NOW"])
    with pytest.raises(SystemExit) as exc:
        ib_option_chain.main()
    assert exc.value.code == 1
    assert client.connect_calls == ib_option_chain.CONNECT_ATTEMPTS
    out = json.loads(capsys.readouterr().out.strip())
    assert "timeout" in out["error"].lower()


def test_connect_budget_fits_the_endpoint_timeout():
    """Worst-case connect must leave room for qualify + reqSecDefOptParams."""
    from scripts.utils.ib_preflight import IB_REQUEST_TIMEOUT_S

    attempts = ib_option_chain.CONNECT_ATTEMPTS
    worst_connect_s = (
        attempts * ib_option_chain.CONNECT_TIMEOUT_S
        + (attempts - 1) * ib_option_chain.CONNECT_BACKOFF_S
    )
    # scripts/api/server.py:_EQUITY_OPTIONS_CHAIN_TIMEOUT_S
    assert worst_connect_s + 2 * IB_REQUEST_TIMEOUT_S <= 45.0
