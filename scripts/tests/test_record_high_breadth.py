"""Source-coherent SPX breadth: unit, gap, provenance, persistence contracts.

Executed only on GitHub runners. Fixtures are synthetic except the explicit
September 18 reference values documented in the sanitized source receipt.
"""
from __future__ import annotations

import asyncio
import copy
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock

import pytest

import record_high_breadth_scan as scan

NOW = datetime(2026, 9, 18, 23, tzinfo=timezone.utc)


def timestamp(day, hour=13):
    return int(datetime.fromisoformat(day).replace(hour=hour, minute=30, tzinfo=timezone.utc).timestamp())


def sources(days=("2026-09-17", "2026-09-18")):
    values = {"spx_close": 7650.5, "above_200_pct": 49.5, "new_highs": 5, "new_lows": 29}
    return {field: {"symbol": symbol, "interval": "1D", "history_complete": False,
                    "fetched_at": NOW.isoformat(),
                    "bars": [{"timestamp": timestamp(day), "close": values[field]} for day in days]}
            for field, symbol in scan.SYMBOLS.items()}


def test_reference_math_provenance_and_incomplete_history():
    payload = scan.build_output(sources(), now=NOW)
    assert payload["current"]["net_high_low_per_500"] == -4.8
    assert payload["current"]["above_200_pct"] == 49.5
    assert payload["data_date"] == "2026-09-18"
    assert not payload["degraded"]
    source = payload["source"]
    assert source["parity"]["status"] == "mismatch"
    assert source["parity"]["reference"]["above_200_pct"] == 52
    assert source["parity"]["tradingview"]["above_200_pct"] == 49.5
    assert source["denominator"] == 500
    assert source["denominator_verified"] is False
    assert source["history_complete"] is False
    assert source["source_finality"] == "unverified"
    assert payload["current"]["source_timestamps"]["new_highs"] == timestamp("2026-09-18")


def test_parity_recomputed_after_reference_revision():
    data = sources()
    data["above_200_pct"]["bars"][-1]["close"] = 52
    payload = scan.build_output(data, now=NOW)
    assert payload["source"]["parity"]["tradingview"]["above_200_pct"] == 52
    assert payload["source"]["parity"]["status"] == "mismatch"


def test_reference_outside_window_is_unavailable():
    payload = scan.build_output(sources(("2026-09-17",)), now=NOW)
    assert payload["source"]["parity"]["status"] == "unavailable"
    assert payload["source"]["parity"]["tradingview"] is None


def test_gap_is_null_not_zero_or_forward_filled_and_marks_latest_lagging():
    data = sources()
    data["new_lows"]["bars"].pop()
    payload = scan.build_output(data, now=NOW)
    assert payload["current"]["date"] == "2026-09-17"
    assert payload["series"][-1]["new_lows"] is None
    assert payload["series"][-1]["net_high_low_per_500"] is None
    assert payload["degraded"] and payload["source"]["lagging"]
    assert payload["source"]["missing_sessions"] == [{"date": "2026-09-18", "fields": ["new_lows"]}]


def test_zero_counts_are_valid_observations():
    data = sources()
    for field in ("new_highs", "new_lows"):
        data[field]["bars"][-1]["close"] = 0
    assert scan.build_output(data, now=NOW)["current"]["net_high_low_per_500"] == 0


@pytest.mark.parametrize("field,value", [("new_highs", -1), ("new_lows", 1.5),
    ("new_highs", True), ("above_200_pct", 100.1), ("above_200_pct", -0.1),
    ("spx_close", float("nan")), ("spx_close", float("inf"))])
def test_invalid_close_units_quarantined(field, value):
    data = sources()
    data[field]["bars"][-1]["close"] = value
    payload = scan.build_output(data, now=NOW)
    assert payload["series"][-1][field] is None
    assert payload["degraded"]
    assert payload["source"]["invalid_observations"][-1]["field"] == field


@pytest.mark.parametrize("mutation", ["symbol", "interval", "empty", "duplicate", "future"])
def test_bad_source_identity_or_sessions_refused(mutation):
    data = sources()
    if mutation == "symbol":
        data["new_highs"]["symbol"] = "INDEX:MAHN"
    elif mutation == "interval":
        data["new_highs"]["interval"] = "1W"
    elif mutation == "empty":
        data["new_highs"]["bars"] = []
    elif mutation == "duplicate":
        data["new_highs"]["bars"].append(copy.deepcopy(data["new_highs"]["bars"][-1]))
    else:
        data["new_highs"]["bars"][-1]["timestamp"] = timestamp("2026-09-21")
    with pytest.raises(ValueError):
        scan.build_output(data, now=NOW)


def test_cutoff_excludes_current_before_18_et():
    now = datetime(2026, 9, 18, 21, 59, tzinfo=timezone.utc)
    payload = scan.build_output(sources(), now=now)
    assert payload["data_date"] == "2026-09-17"
    assert not payload["degraded"]
    assert payload["source"]["expected_session_date"] == "2026-09-17"


def test_weekend_reuses_last_completed_session():
    now = datetime(2026, 9, 19, 23, tzinfo=timezone.utc)
    assert not scan.build_output(sources(), now=now)["degraded"]


def test_unjoined_histories_are_missing():
    data = sources()
    data["new_highs"]["bars"] = [{"timestamp": timestamp("2026-09-16"), "close": 2}]
    payload = scan.build_output(data, now=NOW)
    assert payload["missing"] and payload["current"] is None
    assert payload["data_date"] is None


def test_naive_collection_time_refused():
    with pytest.raises(ValueError):
        scan.build_output(sources(), now=NOW.replace(tzinfo=None))


def test_collect_exact_bounded_symbols_with_injected_client():
    class Client:
        def __init__(self):
            self.calls = []
        async def get_ohlcv(self, symbol, *, interval, count):
            self.calls.append((symbol, interval, count))
            return next(data for data in sources().values() if data["symbol"] == symbol)
    client = Client()
    payload = asyncio.run(scan.collect(client, now=NOW))
    assert len(payload["series"]) == 2
    assert client.calls == [(symbol, "1D", 5000) for symbol in scan.SYMBOLS.values()]


def test_auth_failure_propagates_without_fabrication():
    class Client:
        async def get_ohlcv(self, *args, **kwargs):
            raise RuntimeError("TV_NOT_CONFIGURED")
    with pytest.raises(RuntimeError, match="TV_NOT_CONFIGURED"):
        asyncio.run(scan.collect(Client(), now=NOW))


def test_persistence_only_complete_rows_and_atomic_snapshot(monkeypatch, tmp_path):
    payload = scan.build_output(sources(), now=NOW)
    payload["series"][0]["new_lows"] = None
    mock = Mock()
    monkeypatch.setattr(scan, "writer", mock)
    monkeypatch.setattr(scan, "CACHE_PATH", tmp_path / "breadth.json")
    scan.persist_result(payload)
    assert mock.upsert_record_high_breadth_rows.call_args.args[0] == [payload["series"][1]]
    mock.upsert_scan_snapshot.assert_called_once_with(scan.SERVICE, payload["scan_time"], payload)
    saved = json.loads((tmp_path / "breadth.json").read_text())
    checksum = saved.pop("_checksum")
    from utils.atomic_io import _compute_checksum
    assert checksum == _compute_checksum(payload)
    assert saved == payload


def test_no_join_retains_prior_snapshot(monkeypatch):
    mock = Mock()
    monkeypatch.setattr(scan, "writer", mock)
    with pytest.raises(RuntimeError, match="no aligned"):
        scan.persist_result({"missing": True})
    assert not mock.mock_calls


def test_writer_idempotent_revisions_and_bounded_chunks(monkeypatch):
    from db import writer
    db = sqlite3.connect(":memory:")
    db.execute("CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT)")
    migration = Path(__file__).parents[1] / "db/migrations/0094_record_high_breadth.sql"
    db.executescript(migration.read_text())
    monkeypatch.setattr(writer, "get_db", lambda: db)
    monkeypatch.setattr(writer, "_PRICE_HISTORY_INSERT_CHUNK_ROWS", 1)
    rows = scan.build_output(sources(), now=NOW)["series"]
    writer.upsert_record_high_breadth_rows(rows, recorded_at=NOW.isoformat())
    rows[0]["new_highs"] = 10
    writer.upsert_record_high_breadth_rows(rows, recorded_at=NOW.isoformat())
    assert db.execute("SELECT count(*) FROM record_high_breadth_history").fetchone()[0] == 2
    assert db.execute("SELECT new_highs FROM record_high_breadth_history ORDER BY date LIMIT 1").fetchone()[0] == 10
    assert json.loads(db.execute("SELECT source_timestamps FROM record_high_breadth_history LIMIT 1").fetchone()[0])["new_highs"] == timestamp("2026-09-17")


def test_daily_unit_and_manifest_registration():
    root = Path(__file__).parents[2]
    service = (root / "cloud/services/radon-record-high-breadth.service").read_text()
    timer = (root / "cloud/services/radon-record-high-breadth.timer").read_text()
    assert "TimeoutStartSec=240" in service
    assert scan.COLLECTION_TIMEOUT < 240
    assert "OnCalendar=*-*-* 18:15:00 America/New_York" in timer
    for path in ("cloud/config/auto-sync-units.txt", "cloud/config/installed-units.sha256", "cloud/scripts/setup-vps.sh"):
        text = (root / path).read_text()
        assert "radon-record-high-breadth.service" in text
        assert "radon-record-high-breadth.timer" in text


def test_run_heartbeats_unchanged_and_errors_on_stale_snapshot(monkeypatch):
    from contextlib import contextmanager
    payload = scan.build_output(sources(), now=NOW)
    cycles = []
    @contextmanager
    def cycle(service, *, market_hours_class):
        assert service == scan.SERVICE and market_hours_class == "daily"
        handle = Mock(finished_at=None)
        cycles.append(handle)
        yield handle
    async def collect(client):
        return payload
    persist = Mock()
    monkeypatch.setattr(scan, "service_cycle", cycle)
    monkeypatch.setattr(scan, "collect", collect)
    monkeypatch.setattr(scan, "persist_result", persist)
    assert scan.run(client=object()) == payload
    assert scan.run(client=object()) == payload
    assert persist.call_count == 2
    assert all(c.finished_at == NOW.isoformat() for c in cycles)
    payload["degraded"] = True
    with pytest.raises(RuntimeError, match="latest expected"):
        scan.run(client=object())
    assert persist.call_count == 3


def test_cli_json_and_failure_stderr(monkeypatch, capsys):
    payload = scan.build_output(sources(), now=NOW)
    monkeypatch.setattr(scan, "run", lambda: payload)
    assert scan.main(["--json"]) == 0
    assert json.loads(capsys.readouterr().out)["data_date"] == "2026-09-18"
    def failure():
        raise RuntimeError("TV_NOT_CONFIGURED")
    monkeypatch.setattr(scan, "run", failure)
    assert scan.main([]) == 1
    captured = capsys.readouterr()
    assert captured.out == "" and "TV_NOT_CONFIGURED" in captured.err


@pytest.mark.parametrize("field", ["new_highs", "new_lows"])
def test_fractional_historical_count_is_gap_never_rounded_to_zero(field):
    data = sources()
    data[field]["bars"][0]["close"] = 0.01
    payload = scan.build_output(data, now=NOW)
    assert payload["current"]["date"] == "2026-09-18"
    assert not payload["degraded"]
    assert payload["series"][0][field] is None
    assert payload["series"][0]["net_high_low_per_500"] is None
    assert payload["series"][0]["source_timestamps"][field] == timestamp("2026-09-17")
    assert payload["source"]["invalid_observations"][0]["raw_value"] == 0.01
    assert payload["source"]["coverage"][field]["accepted_count"] == 1


def test_fractional_latest_count_lags_instead_of_zero_breadth():
    data = sources()
    data["new_highs"]["bars"][-1]["close"] = 0.01
    payload = scan.build_output(data, now=NOW)
    assert payload["degraded"] and payload["source"]["lagging"]
    assert payload["current"]["date"] == "2026-09-17"
    assert payload["series"][-1]["new_highs"] is None
    assert payload["series"][-1]["net_high_low_per_500"] is None
