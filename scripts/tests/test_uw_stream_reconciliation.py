"""Closed-minute REST confirmation never changes stream partial semantics."""
from __future__ import annotations
from datetime import datetime, timezone
import pytest
from uw_stream.model import Materializer
from uw_stream.reconcile import FLOW_FIELDS, completed_row, pending_requests, reconcile_cycle
from uw_stream.config import Config
from uw_stream.store import Store

NOW = 1_792_584_000.0


def row(ticker="SPY", stamp=None, amount="3"):
    return {"ticker": ticker, "timestamp": datetime.fromtimestamp(NOW - 300 if stamp is None else stamp, timezone.utc).isoformat().replace("+00:00", "Z"),
            **{field: amount for field in FLOW_FIELDS}, "transactions": 2, "volume": 3}


def model():
    result = Materializer(("greek_flow:SPY",))
    result.connect(NOW)
    result.apply("greek_flow:SPY", {"response": {}, "status": "ok"}, NOW, 1)
    return result


@pytest.mark.parametrize("timestamp", [None, "not-time", "2026-10-21T14:00:00", "2026-10-21T14:00:00+02:00", "2026-10-21T14:00:01Z"])
def test_recovery_refuses_unverified_minute_timestamps(timestamp):
    assert completed_row("greek_flow:SPY", {**row(), "timestamp": timestamp}, NOW) is None


def test_current_and_unsettled_minute_never_confirmed():
    assert completed_row("greek_flow:SPY", row(stamp=NOW), NOW) is None
    assert completed_row("greek_flow:SPY", row(stamp=NOW - 60), NOW) is None
    assert completed_row("greek_flow:SPY", row(stamp=NOW - 120), NOW) is not None


@pytest.mark.parametrize("change", [{"ticker": "QQQ"}, {"expiry": "2026-10-23"}, {"total_delta_flow": "NaN"}, {"dir_delta_flow": None}, {"volume": -1}, {"transactions": True}])
def test_recovery_rejects_mismatched_or_nonfinite_total(change):
    assert completed_row("greek_flow:SPY", {**row(), **change}, NOW) is None


def test_confirmed_total_replaces_partial_preserving_gap_and_late_baseline():
    state = model()
    partial = row(amount="1")
    state.apply("greek_flow:SPY", partial, NOW - 300, 2)
    state.gap(NOW - 100, "dropped")
    assert state.reconcile_greek("greek_flow:SPY", [row(amount="3")], NOW, 3) == 1
    key = next(iter(state.state["latest"]["greek_flow:SPY"]))
    record = state.state["latest"]["greek_flow:SPY"][key]
    assert record["payload"]["total_delta_flow"] == "3"
    assert record["complete"] is True
    assert record["basis"] == "REST-confirmed-completed-minute"
    assert state.state["connection"]["gaps"] == 1
    state.apply("greek_flow:SPY", row(amount="10"), NOW + 1, 4)
    record = state.state["latest"]["greek_flow:SPY"][key]
    assert record["payload"]["total_delta_flow"] == "3"  # never 3 + 10
    assert record["complete"] is False
    assert record["requires_reconciliation"] is True
    state.reconcile_greek("greek_flow:SPY", [row(amount="14")], NOW + 2, 5)
    assert state.state["latest"]["greek_flow:SPY"][key]["payload"]["total_delta_flow"] == "14"
    assert state.state["connection"]["gaps"] == 1


def test_completed_rest_control_is_transactional_and_restarts_with_basis(tmp_path):
    config = Config(directory=tmp_path, channels=("greek_flow:SPY",), min_free_bytes=0)
    store = Store(config)
    store.model = model()
    store.flush([("__reconcile__", {"channel": "greek_flow:SPY", "rows": [row()]}, NOW)], NOW)
    store.close()
    reopened = Store(config)
    record = next(iter(reopened.model.state["latest"]["greek_flow:SPY"].values()))
    assert record["basis"] == "REST-confirmed-completed-minute"
    assert record["payload"]["total_delta_flow"] == "3"
    assert reopened.db.execute("SELECT count(*) FROM events WHERE channel='__reconcile__'").fetchone()[0] == 1
    reopened.close()


class Client:
    def __init__(self, result):
        self.result = result
        self.calls = []
    def get(self, path, params, **kwargs):
        self.calls.append((path, params, kwargs))
        return self.result


def test_recovery_uses_governed_uncached_background_get_and_closed_rows_only():
    state = model()
    state.apply("greek_flow:SPY", row(amount="1"), NOW - 300, 2)
    emitted = []
    client = Client({"data": [row(), row(stamp=NOW), {**row(), "ticker": "QQQ"}]})
    assert reconcile_cycle(("greek_flow:SPY",), state.snapshot(NOW), NOW, client, lambda *args: emitted.append(args)) == 1
    path, params, options = client.calls[0]
    assert path == "stock/SPY/greek-flow"
    assert options == {"priority": "background", "wait_timeout": 0, "use_cache": False}
    assert emitted[0][0] == "__reconcile__"
    assert len(emitted[0][1]["rows"]) == 1
    state.reconcile_greek("greek_flow:SPY", emitted[0][1]["rows"], NOW, 3)
    assert pending_requests(("greek_flow:SPY",), state.snapshot(NOW), NOW) == []


def test_recovery_is_bounded_and_rotates_large_manifests():
    channels = tuple("greek_flow:S" + str(n) for n in range(12))
    snapshot = {"latest": {channel: {"minute": {"payload": row(ticker=channel.split(":")[1]), "complete": False}} for channel in channels}}
    first, second = Client({"data": []}), Client({"data": []})
    assert reconcile_cycle(channels, snapshot, NOW, first, lambda *args: None) == 10
    assert reconcile_cycle(channels, snapshot, NOW, second, lambda *args: None, offset=10) == 10
    assert len(set(call[0] for call in first.calls + second.calls)) == 12


def test_failed_recovery_is_safe_and_retains_unconfirmed_values():
    state = model()
    state.apply("greek_flow:SPY", row(amount="1"), NOW - 300, 2)
    class Broken(Client):
        def get(self, *args, **kwargs):
            raise RuntimeError("provider URL contains secret token")
    emitted = []
    reconcile_cycle(("greek_flow:SPY",), state.snapshot(NOW), NOW, Broken(None), lambda *args: emitted.append(args))
    assert emitted[0][0] == "__control__"
    assert emitted[0][1] == {"event": "reconciliation_unavailable", "channel": "greek_flow:SPY"}
    assert next(iter(state.state["latest"]["greek_flow:SPY"].values()))["payload"]["total_delta_flow"] == "1"


def test_risk_reversal_preserves_10_and_25_delta_buckets():
    state = Materializer(("risk_reversal_skew",))
    state.connect(NOW)
    state.apply("risk_reversal_skew", {"response": {}, "status": "ok"}, NOW, 1)
    for delta, skew in [(10, .025), (25, .0142)]:
        state.apply("risk_reversal_skew", {"ticker": "AAPL", "date": "2026-10-21", "expiry": "2026-11-20", "delta": delta, "risk_reversal": skew}, NOW, delta)
    values = state.state["latest"]["risk_reversal_skew"]
    assert len(values) == 2
    assert values["AAPL:2026-11-20:10"]["payload"]["risk_reversal"] == .025
    assert values["AAPL:2026-11-20:25"]["payload"]["risk_reversal"] == .0142
    assert state.state["channels"]["risk_reversal_skew"]["units"] == "IV-fraction-difference; delta-in-whole-percent"


def test_interpolated_iv_preserves_official_days_horizons():
    state = Materializer(("interpolated_iv:AAPL",))
    state.connect(NOW)
    state.apply("interpolated_iv:AAPL", {"response": {}, "status": "ok"}, NOW, 1)
    for days, iv in [(30, .299), (90, .321)]:
        state.apply("interpolated_iv:AAPL", {"ticker": "AAPL", "date": "2026-10-21", "days": days, "volatility": iv, "implied_move_perc": .058, "is_exact": False}, NOW, days)
    values = state.state["latest"]["interpolated_iv:AAPL"]
    assert len(values) == 2
    assert values["AAPL:30"]["payload"]["volatility"] == .299
    assert values["AAPL:90"]["payload"]["volatility"] == .321
