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
    """R-352 shrank the attempt count, so derive it rather than hardcode 3.

    The original intent — a transient handshake timeout does NOT fail the
    request — is unchanged: the last attempt still succeeds. Only the literal
    `3` moved, because the retry budget is now derived from the caller's 45s
    cap instead of asserted to fit inside it.
    """
    client = _FakeClient(fail_connects=ib_option_chain.CONNECT_ATTEMPTS - 1)
    out = _run(monkeypatch, capsys, client)
    assert "error" not in out, out
    assert out["expirations"] == ["20260828", "20260904"]
    assert client.connect_calls == ib_option_chain.CONNECT_ATTEMPTS
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


def test_the_worst_case_connect_schedule_fits_the_callers_subprocess_cap(
    monkeypatch, capsys
):
    """R-352: the JSON error envelope must still render inside the caller's cap.

    MEASURED, not derived. The old form recomputed `CONNECT_TIMEOUT_S`'s own
    definition and compared it to a third hand-copy of the cap, so it reduced
    to `CONNECT_BUDGET_S <= CONNECT_BUDGET_S` for any attempts/backoff and
    could not fail. Here the schedule is whatever `main()` actually spends: the
    timeouts it hands `client.connect()` plus the backoffs it actually sleeps,
    against the cap imported from the endpoint that enforces it.
    """
    from scripts.api.server import _EQUITY_OPTIONS_CHAIN_TIMEOUT_S as CAP
    from scripts.utils.ib_preflight import IB_REQUEST_TIMEOUT_S

    assert ib_option_chain._CALLER_CAP_S == CAP, (
        f"ib_option_chain sizes its retry budget against {ib_option_chain._CALLER_CAP_S}s "
        f"but /options/chain kills the subprocess at {CAP}s"
    )

    slept: list[float] = []
    monkeypatch.setattr(ib_option_chain.time, "sleep", slept.append)
    client = _FakeClient(fail_connects=99)
    monkeypatch.setattr(ib_option_chain, "IBClient", lambda *a, **k: client)
    monkeypatch.setattr("sys.argv", ["ib_option_chain.py", "--symbol", "NOW"])

    with pytest.raises(SystemExit):
        ib_option_chain.main()
    assert "error" in json.loads(capsys.readouterr().out.strip())

    assert client.connect_timeouts, "connect() was never given a timeout"
    assert all(
        t is not None and t > 0 for t in client.connect_timeouts
    ), f"an unusable handshake window: {client.connect_timeouts}"

    worst_connect_s = sum(client.connect_timeouts) + sum(slept)
    # _qualify_underlying + _request_option_chains each bounded at
    # IB_REQUEST_TIMEOUT_S, then the except-clause has to print the envelope.
    total_s = worst_connect_s + 2 * IB_REQUEST_TIMEOUT_S + ib_option_chain._ENVELOPE_MARGIN_S
    assert total_s <= CAP, (
        f"worst case is {total_s}s against a {CAP}s subprocess cap, so the "
        "except-clause that prints the JSON error envelope never runs and the "
        "operator gets a bare subprocess-timeout string (R-352)"
    )
