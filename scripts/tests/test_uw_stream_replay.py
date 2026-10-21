"""Offline replay is bounded, isolated from production state and honest about gaps."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import sqlite3
import sys

import pytest

from uw_stream.collector import OwnerLock
from uw_stream.reconcile import FLOW_FIELDS
from uw_stream.replay import replay_retained

NOW = 1_792_584_000.0


def journal(tmp_path, records):
    directory = tmp_path / "journal"
    directory.mkdir()
    (directory / "collector.lock").touch()
    (directory / "snapshot.json").write_text('{"production":"unchanged"}')
    with sqlite3.connect(directory / "events.sqlite3") as conn:
        conn.execute("CREATE TABLE events(ingest_id INTEGER PRIMARY KEY,received_at REAL,channel TEXT,payload TEXT)")
        conn.executemany("INSERT INTO events VALUES(?,?,?,?)", [(identity, stamp, channel, json.dumps(payload)) for identity, stamp, channel, payload in records])
    return directory


def partial(amount="2"):
    return {"ticker": "SPY", "timestamp": datetime.fromtimestamp(NOW - 300, timezone.utc).isoformat().replace("+00:00", "Z"), "total_delta_flow": amount}


def value(snapshot):
    return next(iter(snapshot["latest"]["greek_flow:SPY"].values()))


def test_local_ingest_order_retains_identical_partials_and_never_publishes(tmp_path):
    directory = journal(tmp_path, [(7, NOW, "greek_flow:SPY", partial()), (8, NOW + 1, "greek_flow:SPY", partial())])
    before = {path.name: path.read_bytes() for path in directory.iterdir()}
    result = replay_retained(directory, from_ingest_id=7, to_ingest_id=8)
    assert value(result)["payload"]["total_delta_flow"] == "4"
    assert value(result)["complete"] is False
    assert result["status"] == "offline-replay"
    assert result["connection"]["connected"] is False
    assert result["channels"]["greek_flow:SPY"]["acknowledged"] is False
    assert result["replay"]["retained_range_complete"] is True
    assert result["replay"]["prior_baseline_available"] is False
    assert result["replay"]["gap_history_available"] is False
    assert result["upstream_replay_available"] is False
    assert {path.name: path.read_bytes() for path in directory.iterdir()} == before


def test_range_cap_and_pruned_start_are_explicit(tmp_path):
    directory = journal(tmp_path, [(n, NOW, "greek_flow:SPY", partial()) for n in range(5, 9)])
    capped = replay_retained(directory, max_events=2)
    assert capped["replay"]["truncated"] is True
    assert capped["replay"]["last_replayed_ingest_id"] == 6
    assert capped["replay"]["pruned_before_requested_start"] is True
    assert capped["replay"]["retained_range_complete"] is False
    later = replay_retained(directory, from_ingest_id=7, to_ingest_id=10)
    assert value(later)["payload"]["total_delta_flow"] == "4"
    assert later["replay"]["requested_end_not_retained"] is True
    empty = replay_retained(directory, from_ingest_id=100)
    assert empty["replay"]["events_selected"] == 0
    assert empty["replay"]["retained_range_complete"] is False


def test_retained_rest_confirmation_replaces_total_and_late_partial_preserves_baseline(tmp_path):
    confirmed = {**partial("9"), **{field: "9" for field in FLOW_FIELDS}, "volume": 3, "transactions": 2}
    records = [(1, NOW - 300, "greek_flow:SPY", partial()),
               (2, NOW, "__reconcile__", {"channel": "greek_flow:SPY", "rows": [confirmed]}),
               (3, NOW + 1, "greek_flow:SPY", partial("100"))]
    directory = journal(tmp_path, records)
    closed = replay_retained(directory, to_ingest_id=2)
    assert value(closed)["payload"]["total_delta_flow"] == "9"
    assert value(closed)["complete"] is True
    assert closed["connection"]["gaps"] >= 1
    late = replay_retained(directory)
    assert value(late)["payload"]["total_delta_flow"] == "9"
    assert value(late)["complete"] is False
    assert value(late)["requires_reconciliation"] is True


def test_running_collector_excludes_offline_replay(tmp_path):
    directory = journal(tmp_path, [])
    with OwnerLock(directory):
        with pytest.raises(ValueError, match="Stop the research collector"):
            replay_retained(directory)


def test_missing_journal_never_creates_files(tmp_path):
    directory = tmp_path / "missing"
    with pytest.raises(ValueError):
        replay_retained(directory)
    assert not directory.exists()


@pytest.mark.parametrize("kwargs", [{"max_events": 0}, {"max_events": 100001}, {"from_ingest_id": True}, {"from_ingest_id": 3, "to_ingest_id": 2}])
def test_replay_rejects_unbounded_or_invalid_ranges(tmp_path, kwargs):
    with pytest.raises(ValueError):
        replay_retained(tmp_path, **kwargs)


def test_payload_memory_bound_is_reported(tmp_path, monkeypatch):
    import uw_stream.replay as module
    directory = journal(tmp_path, [(1, NOW, "greek_flow:SPY", partial()), (2, NOW, "greek_flow:SPY", partial())])
    monkeypatch.setattr(module, "MAX_REPLAY_BYTES", len(json.dumps(partial()).encode()))
    result = replay_retained(directory)
    assert result["replay"]["payload_byte_limited"] is True
    assert result["replay"]["events_selected"] == 1
    assert result["replay"]["retained_range_complete"] is False


def test_corrupt_record_isolated_and_range_not_certified(tmp_path):
    directory = journal(tmp_path, [(1, NOW, "risk_reversal_skew", {"ticker": "SPY", "delta": []}), (2, NOW, "greek_flow:SPY", partial())])
    result = replay_retained(directory)
    assert result["replay"]["invalid_records"] == 1
    assert result["replay"]["retained_range_complete"] is False
    assert value(result)["payload"]["total_delta_flow"] == "2"


def test_replay_cli_bypasses_credentials_and_transport(tmp_path, monkeypatch, capsys):
    import uw_stream_collect as cli
    directory = journal(tmp_path, [(1, NOW, "greek_flow:SPY", partial())])
    def forbidden(*args, **kwargs):
        raise AssertionError("offline replay touched runtime credentials or transport")
    monkeypatch.setattr(cli, "hydrate_settings", forbidden)
    monkeypatch.setattr(cli, "run", forbidden)
    monkeypatch.setattr(sys, "argv", ["uw_stream_collect.py", "--replay", "--replay-directory", str(directory), "--from-ingest-id", "1", "--max-events", "10"])
    assert cli.main() == 0
    result = json.loads(capsys.readouterr().out)
    assert result["replay"]["network_used"] is False
    assert result["replay"]["published"] is False


def test_uncheckpointed_wal_is_read_from_copy_without_source_changes(tmp_path):
    directory = journal(tmp_path, [])
    connection = sqlite3.connect(directory / "events.sqlite3")
    try:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA wal_autocheckpoint=0")
        connection.execute("INSERT INTO events VALUES(?,?,?,?)", (1, NOW, "greek_flow:SPY", json.dumps(partial())))
        connection.commit()
        assert (directory / "events.sqlite3-wal").exists()
        before = {path.name: path.read_bytes() for path in directory.iterdir()}
        result = replay_retained(directory)
        assert value(result)["payload"]["total_delta_flow"] == "2"
        assert {path.name: path.read_bytes() for path in directory.iterdir()} == before
    finally:
        connection.close()


def test_oversized_payload_never_enters_python_and_later_records_recover(tmp_path, monkeypatch):
    import uw_stream.replay as module
    directory = journal(tmp_path, [(1, NOW, "news", {"text": "x" * (module.MAX_PAYLOAD_BYTES + 1)}), (2, NOW, "greek_flow:SPY", partial())])
    before = {path.name: path.read_bytes() for path in directory.iterdir()}
    connect = module.sqlite3.connect
    observed = []
    class Connection:
        def __init__(self, *args, **kwargs):
            self.connection = connect(*args, **kwargs)
        def execute(self, sql, *args):
            cursor = self.connection.execute(sql, *args)
            if sql.startswith("SELECT ingest_id,"):
                def bounded_rows():
                    for record in cursor:
                        observed.append(record[3])
                        assert record[3] is None or len(record[3]) <= module.MAX_PAYLOAD_BYTES
                        yield record
                return bounded_rows()
            return cursor
        def close(self):
            self.connection.close()
    monkeypatch.setattr(module.sqlite3, "connect", Connection)
    result = replay_retained(directory)
    assert observed[0] is None
    assert result["replay"]["invalid_records"] == 1
    assert result["replay"]["retained_range_complete"] is False
    assert value(result)["payload"]["total_delta_flow"] == "2"
    assert {path.name: path.read_bytes() for path in directory.iterdir()} == before
