"""UW channel semantics and crash-safe materialization; no provider calls."""
from __future__ import annotations

import json
import queue
import threading
from pathlib import Path

import pytest

from uw_stream.config import Config, validate_channels
from uw_stream.collector import BoundedFrames, OwnerLock, decode_frame, run
from uw_stream.model import Materializer
from uw_stream.store import Store, read_snapshot

NOW = 1_792_584_000.0


def ready(*channels):
    model = Materializer(tuple(channels))
    model.connect(NOW)
    for channel in channels:
        model.apply(channel, {"response": {}, "status": "ok"}, NOW, 1)
    return model


@pytest.mark.parametrize("channel", ["option_trades", "option_trades:SPY", "price", "quotes", "gex", "periscope:AAPL", "net_flow:SPY/../../secret", "gex:SPY?token=bad"])
def test_channel_manifest_rejects_firehoses_and_untrusted_subjects(channel):
    with pytest.raises(ValueError):
        validate_channels([channel])


def test_manifest_scopes_and_deduplicates_research_channels():
    assert validate_channels(["flow-alerts", "gex:SPY", "gex:SPY", "sec:13f_filings", "futures_blocks:SR3:GN"]) == ("flow-alerts", "gex:SPY", "sec:13f_filings", "futures_blocks:SR3:GN")


def test_all_join_acks_are_required_and_denial_is_not_live():
    model = Materializer(("market_tide", "interval_flow"))
    model.connect(NOW)
    model.apply("market_tide", {"response": {}, "status": "ok"}, NOW, 1)
    assert model.snapshot(NOW)["status"] == "connecting"
    model.apply("interval_flow", {"error": "not entitled"}, NOW, 2)
    state = model.snapshot(NOW)
    assert state["status"] == "degraded"
    assert state["channels"]["interval_flow"]["status"] == "unavailable"


def test_ack_timeout_does_not_become_healthy():
    model = Materializer(("market_tide",))
    model.connect(NOW)
    assert model.snapshot(NOW + 31)["channels"]["market_tide"]["status"] == "unavailable"
    assert model.snapshot(NOW + 31)["status"] == "degraded"


def test_unacknowledged_data_is_not_materialized():
    model = Materializer(("gex:SPY",))
    assert not model.apply("gex:SPY", {"ticker": "SPY", "gamma": 10}, NOW, 1)
    assert model.state["latest"] == {}


def test_greek_flow_identical_partials_add_by_ticker_minute_not_hash():
    model = ready("greek_flow:SPY")
    partial = {"ticker": "SPY", "timestamp": "2026-10-21T14:00:00Z", "total_delta_flow": "-2.25", "dir_vega_flow": "10.5", "transactions": 2, "volume": 3}
    model.apply("greek_flow:SPY", partial, NOW, 2)
    model.apply("greek_flow:SPY", partial, NOW + 1, 3)
    value = next(iter(model.state["latest"]["greek_flow:SPY"].values()))
    assert value["payload"]["total_delta_flow"] == "-4.50"
    assert value["payload"]["dir_vega_flow"] == "21.0"
    assert value["payload"]["transactions"] == 4
    model.gap(NOW + 2, "test-drop")
    model.apply("greek_flow:SPY", partial, NOW + 3, 4)
    assert next(iter(model.state["latest"]["greek_flow:SPY"].values()))["complete"] is False


def test_repeated_interval_snapshot_replaces_bucket_and_ignores_older():
    model = ready("interval_flow")
    for stamp, volume in [("2026-10-21T14:03:00Z", 20), ("2026-10-21T14:04:00Z", 30), ("2026-10-21T14:02:00Z", 5)]:
        model.apply("interval_flow", {"ticker": "SPY", "start_time": "2026-10-21T14:00:00Z", "tape_time": stamp, "call_vol": volume}, NOW, volume)
    latest = model.state["latest"]["interval_flow"]
    assert len(latest) == 1
    assert next(iter(latest.values()))["payload"]["call_vol"] == 30


def test_strike_expiry_and_iv_horizons_remain_distinct():
    model = ready("gex_strike_expiry:SPY", "interpolated_iv:SPY", "iv_term_structure:SPY")
    for n in [1, 2]:
        model.apply("gex_strike_expiry:SPY", {"ticker": "SPY", "strike": str(500+n), "expiry": "2026-10-23", "call_gamma_oi": "12"}, NOW, n)
        model.apply("interpolated_iv:SPY", {"ticker": "SPY", "days": n, "volatility": .2}, NOW, n)
        model.apply("iv_term_structure:SPY", {"ticker": "SPY", "expiry": "2026-10-2"+str(n), "volatility": .3}, NOW, n)
    assert all(len(values) == 2 for values in model.state["latest"].values())


def test_periscope_publishes_only_complete_snapshot_with_separate_units():
    model = ready("periscope:SPX")
    row = {"strike": "6000", "expiry": "2026-10-23", "gamma": 2, "charm": -1, "vanna": 3}
    head = {"ticker": "SPX", "timestamp": 1234567890, "total_rows": 2, "rows": [row], "has_more": True}
    model.apply("periscope:SPX", head, NOW, 2)
    assert model.state["latest"]["periscope:SPX"] == {}
    model.apply("periscope:SPX", {**head, "rows": [{**row, "strike": "6001"}], "has_more": False}, NOW, 3)
    assert len(model.state["latest"]["periscope:SPX"]["SPX"]["payload"]["rows"]) == 2
    assert model.state["channels"]["periscope:SPX"]["units"] == "provider-greek-units"
    assert model.state["latest"]["periscope:SPX"]["SPX"]["complete"] is True


def test_incomplete_periscope_rollover_retains_previous_without_false_completeness():
    model = ready("periscope:SPX")
    row = {"strike": "6000", "expiry": "2026-10-23", "gamma": 2}
    frame = {"ticker": "SPX", "timestamp": 1, "total_rows": 2, "rows": [row], "has_more": True}
    model.apply("periscope:SPX", frame, NOW, 2)
    model.apply("periscope:SPX", {**frame, "timestamp": 2}, NOW, 3)
    assert model.state["connection"]["gaps"] == 1
    assert model.state["latest"]["periscope:SPX"] == {}


def test_futures_lifecycle_uses_exchange_session_trade_id_and_no_price_conversion():
    model = ready("futures_blocks:ESZ6")
    trade = {"sym": "ESZ6", "exchange": "XCME", "trade_date": "2026-10-21", "trade_id": 7, "reported_at": 1_792_584_000_000, "price": "6612.25", "action": "new"}
    model.apply("futures_blocks:ESZ6", trade, NOW, 2)
    model.apply("futures_blocks:ESZ6", {**trade, "action": "update", "reported_at": trade["reported_at"] + 1000, "price": "6613.00"}, NOW, 3)
    model.apply("futures_blocks:ESZ6", trade, NOW, 4)
    latest = model.state["latest"]["futures_blocks:ESZ6"]["XCME:2026-10-21:7"]
    assert latest["payload"]["price"] == "6613.00"
    model.apply("futures_blocks:ESZ6", {**trade, "action": "delete", "reported_at": trade["reported_at"] + 2000}, NOW, 5)
    assert model.state["latest"]["futures_blocks:ESZ6"]["XCME:2026-10-21:7"]["deleted"]


@pytest.mark.parametrize("frame", ['{}', '["market_tide", []]', '["market_tide", {"x":NaN}]', '["market_tide",{}]extra'])
def test_envelope_validation_isolated(frame):
    with pytest.raises((ValueError, TypeError)):
        decode_frame(frame, 1024)


def test_queue_has_a_byte_bound_independent_of_frame_count():
    frames = BoundedFrames(10, max_bytes=30)
    frames.put_nowait(("news", {"v": "a"*20}, NOW))
    with pytest.raises(queue.Full):
        frames.put_nowait(("news", {"v": "b"}, NOW))
    frames.get(timeout=.01)
    assert frames.bytes == 0


def config(tmp_path, **kwargs):
    return Config(directory=tmp_path / "uw", channels=("flow-alerts", "greek_flow:SPY"), min_free_bytes=0, **kwargs)


def test_missing_reader_never_creates_files(tmp_path):
    path = tmp_path / "absent" / "snapshot.json"
    assert read_snapshot(path)["status"] == "missing"
    assert not path.parent.exists()


def test_store_persists_idempotent_events_but_retains_identical_greek_partials(tmp_path):
    store = Store(config(tmp_path))
    frames = [("__control__", {"event": "connect"}, NOW)]
    frames += [(c, {"response": {}, "status": "ok"}, NOW) for c in store.config.channels]
    event = {"id": "x", "ticker": "SPY", "premium": "100"}
    partial = {"ticker": "SPY", "timestamp": "2026-10-21T14:00:00Z", "total_delta_flow": "2"}
    frames += [("flow-alerts", event, NOW), ("flow-alerts", event, NOW), ("greek_flow:SPY", partial, NOW), ("greek_flow:SPY", partial, NOW)]
    store.flush(frames, NOW)
    snapshot = read_snapshot(store.snapshot_path, NOW)
    assert snapshot["status"] == "live"
    assert len(snapshot["latest"]["flow-alerts"]) == 1
    assert next(iter(snapshot["latest"]["greek_flow:SPY"].values()))["payload"]["total_delta_flow"] == "4"
    assert store.db.execute("SELECT count(*) FROM events WHERE channel='greek_flow:SPY'").fetchone()[0] == 3  # ACK + 2 real partials
    assert read_snapshot(store.snapshot_path, NOW + 16)["status"] == "stale"
    store.close()


def test_failed_transaction_never_double_adds_partial_on_retry(tmp_path, monkeypatch):
    store = Store(config(tmp_path))
    store.model = ready(*store.config.channels)
    partial = {"ticker": "SPY", "timestamp": "2026-10-21T14:00:00Z", "total_delta_flow": "2"}
    original = Materializer.apply
    def fail_after_add(self, *args):
        original(self, *args)
        raise RuntimeError("disk rejected commit")
    monkeypatch.setattr(Materializer, "apply", fail_after_add)
    with pytest.raises(RuntimeError):
        store.flush([("greek_flow:SPY", partial, NOW)], NOW)
    assert store.db.execute("SELECT count(*) FROM events").fetchone()[0] == 0
    monkeypatch.setattr(Materializer, "apply", original)
    store.flush([("greek_flow:SPY", partial, NOW)], NOW)
    assert next(iter(store.model.state["latest"]["greek_flow:SPY"].values()))["payload"]["total_delta_flow"] == "2"
    store.close()


def test_restart_retains_state_and_marks_unknown_upstream_gap(tmp_path):
    cfg = config(tmp_path)
    store = Store(cfg)
    store.model = ready(*cfg.channels)
    store.flush([("flow-alerts", {"id": "persisted", "ticker": "SPY"}, NOW)], NOW)
    store.close()
    restarted = Store(cfg)
    assert "persisted" in restarted.model.state["latest"]["flow-alerts"]
    assert restarted.model.state["connection"]["gaps"] >= 1
    restarted.close()


def test_retention_bounds_raw_journal(tmp_path):
    store = Store(config(tmp_path, max_raw_rows=2))
    store.model = ready(*store.config.channels)
    frames = [("flow-alerts", {"id": str(n), "ticker": "SPY"}, NOW) for n in range(5)]
    store.flush(frames, NOW)
    assert store.db.execute("SELECT count(*) FROM events").fetchone()[0] == 2
    store.close()


def test_owner_lock_prevents_second_local_collector(tmp_path):
    with OwnerLock(tmp_path):
        with pytest.raises(RuntimeError):
            with OwnerLock(tmp_path):
                pass


def test_disabled_daemon_never_opens_transport(tmp_path):
    stop = threading.Event()
    stop.set()
    def forbidden(*args, **kwargs):
        raise AssertionError("disabled collector opened provider socket")
    run(config(tmp_path), stop=stop, connect=forbidden, mirror=lambda _: None)
    assert read_snapshot(tmp_path / "uw" / "snapshot.json")["status"] == "disabled"


def test_reconnect_after_historical_gap_returns_to_live(tmp_path):
    """Cumulative gaps persist across restarts; they must not pin status degraded.

    Production 2026-10-31: connection.gaps=106 after restarts, every channel
    acknowledged on a fresh connection, yet status stayed degraded forever.
    """
    cfg = config(tmp_path)
    store = Store(cfg)
    store.model = ready(*cfg.channels)
    store.flush([("flow-alerts", {"id": "persisted", "ticker": "SPY"}, NOW)], NOW)
    store.close()
    restarted = Store(cfg)
    assert restarted.model.state["connection"]["gaps"] >= 1
    later = NOW + 10_000_000  # the restart gap is stamped with wall time; connect after it
    frames = [("__control__", {"event": "connect"}, later)]
    frames += [(c, {"response": {}, "status": "ok"}, later) for c in cfg.channels]
    restarted.flush(frames, later)
    assert restarted.model.state["status"] == "live"
    assert restarted.model.state["connection"]["gaps"] >= 1
    restarted.close()


def test_gap_during_current_connection_keeps_degraded_after_acks():
    model = Materializer(("market_tide", "interval_flow"))
    model.connect(NOW)
    model.apply("market_tide", {"response": {}, "status": "ok"}, NOW + 1, 1)
    model.gap(NOW + 2, "input-queue-overflow")
    model.apply("interval_flow", {"response": {}, "status": "ok"}, NOW + 3, 2)
    assert model.state["status"] == "degraded"


def test_compact_mirror_uses_socket_bounded_turso_writes(monkeypatch):
    """Sync libsql holds the GIL with no client timeout. A hung Turso call
    froze the whole collector on 2026-10-31 09:05Z: no flush, no mirror, the
    UW socket left in CLOSE-WAIT, the unit still active."""
    import db.writer as writer
    from uw_stream.collector import mirror_compact
    calls = []
    monkeypatch.setattr(writer, "upsert_scan_snapshot", lambda *a, **kw: calls.append(kw))
    monkeypatch.setattr(writer, "record_service_health", lambda *a, **kw: None)
    mirror_compact({"status": "live", "updated_at": "2026-10-31T09:05:51Z"})
    assert calls == [{"bounded": True}]
