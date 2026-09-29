"""REL-257 / R-678 (Liquid Compute): execute actual SQL through fake Hrana."""
import json
import sqlite3
from unittest.mock import Mock

import pytest

from knowledge import http_db
from scripts.ai_cycle import liquidcompute
from scripts.ai_cycle.store import ObservationStore, SCHEMA
from scripts.db.hrana_http import HranaHttpError
from test_knowledge_http import atomic_server  # noqa: F401 - shared SQLite-backed wire fixture
from test_rel257_liquidcompute_atomic import _make_valid_obs, _payload, HASH, FETCHED


@pytest.fixture
def cloud_store(atomic_server, monkeypatch):
    server, _ = atomic_server
    for sql in SCHEMA:
        server.db.execute(sql)
    def execute(sql, args=(), **kwargs):
        try:
            server.db.execute(sql, args)
        except sqlite3.Error as exc:
            raise HranaHttpError(str(exc)) from exc
    monkeypatch.setattr("scripts.db.hrana_http.hrana_execute", execute)
    store = ObservationStore()
    store._initialized = True
    store._query = lambda sql, args=(): server.db.execute(sql, args).fetchall()
    return store, server


def payloads(server):
    return server.db.execute("SELECT payload FROM ai_cycle_observations ORDER BY identity,payload").fetchall()


def test_replacement_and_identical_replay_retain_exactly_one_current_observation(cloud_store):
    store, server = cloud_store
    old = _make_valid_obs(value=2)
    revised = _make_valid_obs(value=3, raw_hash="c" * 64)
    store.append_observations([old])
    for _ in range(2):
        store.upsert_observations_by_identity([revised])
        rows = payloads(server)
        assert len(rows) == 1
        assert json.loads(rows[0][0])["value"] == 3


def test_failure_mid_replacement_preserves_entire_previous_batch(cloud_store):
    store, server = cloud_store
    store.append_observations([_make_valid_obs("h100-us", 1), _make_valid_obs("h200-us", 2)])
    before = payloads(server)
    server.db.execute("""CREATE TRIGGER reject_revision BEFORE INSERT ON ai_cycle_observations
        WHEN json_extract(NEW.payload, '$.value') = 999
        BEGIN SELECT RAISE(ABORT, 'injected replacement failure'); END""")
    with pytest.raises((HranaHttpError, http_db.HranaHttpError), match="injected replacement failure"):
        store.upsert_observations_by_identity([
            _make_valid_obs("h100-us", 3, raw_hash="c" * 64),
            _make_valid_obs("h200-us", 999, raw_hash="d" * 64),
        ])
    assert payloads(server) == before
    assert not server.db.in_transaction


@pytest.mark.parametrize("failed_step", ["upsert_liquidcompute", "upsert_observations_by_identity"])
@pytest.mark.parametrize("isolated", [False, True])
def test_cli_reports_persistence_failure_once_and_never_writes_health_for_isolated_store(
    monkeypatch, failed_step, isolated,
):
    monkeypatch.delenv("RADON_AI_CYCLE_DB_PATH", raising=False)
    monkeypatch.setattr(liquidcompute, "fetch_ticker", lambda _: (_payload(), HASH, FETCHED))
    store = Mock()
    getattr(store, failed_step).side_effect = HranaHttpError("injected persistence failure")
    monkeypatch.setattr("scripts.ai_cycle.store.ObservationStore", lambda *a: store)
    writes = []
    monkeypatch.setattr("scripts.db.hrana_http.write_service_health_http", lambda *a, **kw: writes.append((a, kw)))

    args = ["--record", "--database", ":memory:"] if isolated else ["--record"]
    assert liquidcompute.main(args) == 1
    assert len(writes) == (0 if isolated else 1)
    if writes:
        assert writes[0][0] == ("liquidcompute", "error")
        assert "injected persistence failure" in writes[0][1]["error"]["message"]


def test_lost_commit_receipt_can_replay_the_entire_replacement(cloud_store, monkeypatch):
    store, server = cloud_store
    revised = _make_valid_obs(value=7)
    original_open = server.open
    lost = False
    def lose_once(request, timeout):
        nonlocal lost
        response = original_open(request, timeout)
        if not lost:
            lost = True
            raise TimeoutError("injected lost commit receipt")
        return response
    monkeypatch.setattr(server, "open", lose_once)
    with pytest.raises(http_db.TransportError, match="lost commit receipt"):
        store.upsert_observations_by_identity([revised])
    assert not server.db.in_transaction
    store.upsert_observations_by_identity([revised])
    assert len(payloads(server)) == 1
    assert json.loads(payloads(server)[0][0])["value"] == 7
