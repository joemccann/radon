"""Liquid Compute ticker: parse, host-tagged upsert, backoff. Never the rental book."""

import json
from pathlib import Path

import pytest

from scripts.ai_cycle.collectors import SourceError, Transport
from scripts.ai_cycle.liquidcompute import (
    BACKOFF_SECONDS,
    SOURCE_ID,
    TICKER_URL,
    UNIT,
    USER_AGENT,
    fetch_ticker,
    parse_ticker,
    persist_ticker,
)
from scripts.ai_cycle.registry import INDICATORS, SOURCES
from scripts.ai_cycle.snapshot import build_snapshot
from scripts.ai_cycle.store import ObservationStore

HASH = "b" * 64
FETCHED = "2026-09-15T12:00:00+00:00"
FIXTURE = Path(__file__).resolve().parents[1] / "ai_cycle" / "fixtures" / "liquidcompute_ticker.json"


def _payload():
    return json.loads(FIXTURE.read_text())


def test_fixture_matches_verified_homepage_ticker_shape():
    payload = _payload()
    assert payload["asOf"] == "2026-09-14"
    assert payload["units"] == UNIT
    assert [row["id"] for row in payload["indices"]] == ["a100-us", "h100-us", "h200-us", "b200-us", "b300-us"]
    assert payload["indices"][1]["value"] == pytest.approx(2.6766)


def test_parse_uses_asof_for_date_and_vintage_not_poll_clock():
    store_rows, observations = parse_ticker(_payload(), HASH, FETCHED)
    assert len(store_rows) == 5
    h100 = next(row for row in store_rows if row["series_id"] == "h100-us")
    assert h100["date"] == "2026-09-14"
    assert h100["source"] == SOURCE_ID
    assert h100["series_id"] == "h100-us"
    assert h100["value"] == pytest.approx(2.6766)
    assert h100["unit"] == UNIT
    assert h100["vintage"] == "2026-09-14"
    assert h100["label"] == "H100"
    assert h100["fetched_at"] == FETCHED
    assert h100["raw_hash"] == HASH
    obs = next(row for row in observations if row["series_id"] == "h100-us")
    assert obs["indicator_id"] == "C5"
    assert obs["source_id"] == SOURCE_ID
    assert obs["lineage_group"] == SOURCE_ID
    assert obs["lineage_group"] != "gpu-aggregator"
    assert obs["period_start"].startswith("2026-09-14")
    assert obs["period_end"].startswith("2026-09-14")
    assert obs["published_at"].startswith("2026-09-14")
    assert obs["fetched_at"].startswith("2026-09-15")
    assert obs["unit"] == UNIT
    assert obs["metadata"]["vintage"] == "2026-09-14"
    assert obs["metadata"]["not_rental_book"] is True
    assert obs["metadata"]["not_silicon_data"] is True
    assert "opaque until licensed" in obs["metadata"]["definition"]
    assert "third-venue" in obs["metadata"]["definition"].lower() or obs["metadata"]["lane"] == "gpu-index-third-venue"


def test_c5_is_a_separate_compute_venue_from_rental_book():
    assert SOURCES[SOURCE_ID]["lineage_group"] == SOURCE_ID
    assert SOURCES["gpu-rental"]["lineage_group"] == "gpu-aggregator"
    assert SOURCE_ID not in INDICATORS["C1"]["source_ids"]
    assert INDICATORS["C5"]["source_ids"] == [SOURCE_ID]
    assert INDICATORS["C5"]["pane"] == "compute"
    assert "opaque" in INDICATORS["C5"]["methodology"].lower()
    assert "third venue" in INDICATORS["C5"]["methodology"].lower()


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"asOf": "2026-09-14", "units": "usd_per_hour", "indices": [{"id": "h100-us", "value": 1}]},
        {"asOf": "not-a-date", "units": UNIT, "indices": [{"id": "h100-us", "value": 1}]},
        {"asOf": "2026-09-16", "units": UNIT, "indices": [{"id": "h100-us", "value": 1}]},
        {"asOf": "2026-09-14", "units": UNIT, "indices": []},
        {"asOf": "2026-09-14", "units": UNIT, "indices": [{"id": "h100-us", "value": 1}, {"id": "h100-us", "value": 2}]},
        {"asOf": "2026-09-14", "units": UNIT, "indices": [{"id": "h100-us", "value": -1}]},
    ],
)
def test_parse_rejects_broken_ticker_schema(payload):
    with pytest.raises(SourceError):
        parse_ticker(payload, HASH, FETCHED)


def test_upsert_is_idempotent_on_source_series_asof(tmp_path):
    store = ObservationStore(tmp_path / "lc.sqlite")
    store.initialize()
    first_store, first_obs = parse_ticker(_payload(), HASH, FETCHED)
    persist_ticker(store, first_obs)
    later = "2026-09-15T13:00:00+00:00"
    _second_store, second_obs = parse_ticker(_payload(), "c" * 64, later)
    persist_ticker(store, second_obs)
    tagged = store.read_liquidcompute()
    assert len(tagged) == 5
    assert {(row["source"], row["series_id"], row["date"]) for row in tagged} == {
        (SOURCE_ID, series, "2026-09-14") for series in ("a100-us", "h100-us", "h200-us", "b200-us", "b300-us")
    }
    assert all(row["vintage"] == "2026-09-14" for row in tagged)
    observations = store.read_observations(as_of="2026-09-15T16:00:00Z")
    assert len(observations) == 5
    assert all(row["source_id"] == SOURCE_ID for row in observations)
    assert all(row["fetched_at"].startswith("2026-09-15T13:00:00") for row in observations)


def test_upsert_replaces_revised_value_for_same_asof(tmp_path):
    store = ObservationStore(tmp_path / "lc.sqlite")
    store.initialize()
    payload = _payload()
    store_rows, observations = parse_ticker(payload, HASH, FETCHED)
    persist_ticker(store, observations)
    payload["indices"][1]["value"] = 2.5
    _revised_store, revised_obs = parse_ticker(payload, "d" * 64, "2026-09-15T13:00:00+00:00")
    persist_ticker(store, revised_obs)
    tagged = {row["series_id"]: row for row in store.read_liquidcompute()}
    assert tagged["h100-us"]["value"] == pytest.approx(2.5)
    obs = {row["series_id"]: row for row in store.read_observations(as_of="2026-09-15T16:00:00Z")}
    assert obs["h100-us"]["value"] == pytest.approx(2.5)
    assert len(obs) == 5


def test_non_200_backs_off_then_raises(tmp_path, monkeypatch):
    sleeps = []

    class Response:
        def __init__(self, status):
            self.status_code = status

        def close(self):
            pass

    class Session:
        def __init__(self):
            self.calls = 0

        def request(self, method, url, **kwargs):
            self.calls += 1
            assert method == "GET"
            assert url == TICKER_URL
            assert kwargs["headers"]["User-Agent"] == USER_AGENT
            return Response(503)

    session = Session()
    with pytest.raises(SourceError, match="HTTP 503"):
        fetch_ticker(Transport(tmp_path, session=session), sleep=sleeps.append)
    assert session.calls == len(BACKOFF_SECONDS) + 1
    assert sleeps == list(BACKOFF_SECONDS)
    assert not list(tmp_path.iterdir())


def test_backoff_succeeds_after_transient_non_200(tmp_path):
    raw = FIXTURE.read_bytes()

    class Response:
        def __init__(self, status, body=b""):
            self.status_code = status
            self.headers = {"Content-Type": "application/json"}
            self._body = body

        def iter_content(self, _size):
            yield self._body

        def close(self):
            pass

    class Session:
        def __init__(self):
            self.calls = 0

        def request(self, method, url, **kwargs):
            self.calls += 1
            if self.calls < 3:
                return Response(502)
            return Response(200, raw)

    payload, digest, _fetched = fetch_ticker(Transport(tmp_path, session=Session()), sleep=lambda _delay: None)
    assert payload["asOf"] == "2026-09-14"
    assert digest
    assert list(tmp_path.glob("*.json"))


def test_collect_source_identifies_radon_and_surfaces_c5(tmp_path):
    from scripts.ai_cycle.collectors import collect_source

    raw = FIXTURE.read_bytes()

    class Response:
        status_code = 200
        headers = {"Content-Type": "application/json"}

        def iter_content(self, _size):
            yield raw

        def close(self):
            pass

    class Session:
        def request(self, method, url, **kwargs):
            assert kwargs["headers"]["User-Agent"] == USER_AGENT
            return Response()

    rows = collect_source(SOURCE_ID, Transport(tmp_path, session=Session()), "2026-09-08", "2026-09-14", env={})
    assert {row["series_id"] for row in rows} == {"a100-us", "h100-us", "h200-us", "b200-us", "b300-us"}
    assert {row["indicator_id"] for row in rows} == {"C5"}
    assert all(row["lineage_group"] == SOURCE_ID for row in rows)


def test_dedicated_main_records_host_tagged_rows(tmp_path, monkeypatch):
    from scripts.ai_cycle.liquidcompute import main

    monkeypatch.setattr("scripts.ai_cycle.collectors.now_iso", lambda: "2026-09-15T12:00:00+00:00")

    class Response:
        status_code = 200
        headers = {"Content-Type": "application/json"}

        def iter_content(self, _size):
            yield FIXTURE.read_bytes()

        def close(self):
            pass

    class Session:
        def request(self, *args, **kwargs):
            assert kwargs["headers"]["User-Agent"] == USER_AGENT
            return Response()

    monkeypatch.setattr("scripts.ai_cycle.collectors.requests.Session", lambda: Session())
    db = tmp_path / "lc.sqlite"
    assert (
        main(
            [
                "--record",
                "--database",
                str(db),
                "--archive",
                str(tmp_path / "raw"),
            ]
        )
        == 0
    )
    store = ObservationStore(db)
    tagged = store.read_liquidcompute()
    assert len(tagged) == 5
    assert all(row["source"] == SOURCE_ID for row in tagged)
    assert all(row["vintage"] == "2026-09-14" for row in tagged)
    snapshot = build_snapshot(store, "2026-09-15T16:00:00Z")
    panel = next(item for item in snapshot["indicators"] if item["id"] == "C5")
    assert panel["status"] == "experimental"
    assert {metric["id"] for metric in panel["metrics"]} == {
        "a100-us",
        "h100-us",
        "h200-us",
        "b200-us",
        "b300-us",
    }


def test_recorded_ticker_lands_on_compute_c5_not_c1(tmp_path, monkeypatch):
    from scripts.ai_cycle.collect import main

    monkeypatch.setattr("scripts.ai_cycle.collect.now_iso", lambda: "2026-09-15T12:00:00Z")
    monkeypatch.setattr("scripts.ai_cycle.collectors.now_iso", lambda: "2026-09-15T12:00:00+00:00")

    class Response:
        status_code = 200
        headers = {"Content-Type": "application/json"}

        def iter_content(self, _size):
            yield FIXTURE.read_bytes()

        def close(self):
            pass

    class Session:
        def request(self, *args, **kwargs):
            return Response()

    monkeypatch.setattr("scripts.ai_cycle.collectors.requests.Session", lambda: Session())
    db = tmp_path / "cycle.sqlite"
    assert (
        main(
            [
                "--record",
                "--database",
                str(db),
                "--sources",
                SOURCE_ID,
                "--end",
                "2026-09-14",
                "--archive",
                str(tmp_path / "raw"),
            ]
        )
        == 0
    )
    store = ObservationStore(db)
    snapshot = build_snapshot(store, "2026-09-15T16:00:00Z")
    panel = next(item for item in snapshot["indicators"] if item["id"] == "C5")
    c1 = next(item for item in snapshot["indicators"] if item["id"] == "C1")
    source = next(item for item in snapshot["sources"] if item["id"] == SOURCE_ID)
    assert panel["pane"] == "compute"
    assert panel["status"] == "experimental"
    assert "opaque" in panel["methodology"].lower()
    assert any(metric["id"] == "h100-us" for metric in panel["metrics"])
    assert all(metric["source_id"] == SOURCE_ID for metric in panel["metrics"])
    assert all(metric["lineage_group"] == SOURCE_ID for metric in panel["metrics"])
    assert not any(metric["source_id"] == SOURCE_ID for metric in c1["metrics"])
    assert source["lineage_group"] == SOURCE_ID
    assert store.read_liquidcompute()
    assert all(row["source"] == SOURCE_ID for row in store.read_liquidcompute())
