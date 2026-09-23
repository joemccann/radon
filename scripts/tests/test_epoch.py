"""Epoch AI public CSVs: exact series, fail-closed parse, attribution, two-URL collect."""

import hashlib
from pathlib import Path

import pytest

from scripts.ai_cycle.collect import SOURCE_HISTORY_STARTS, source_windows
from scripts.ai_cycle.collectors import SourceError, Transport
from scripts.ai_cycle.epoch import (
    ATTRIBUTION,
    CENTERS_URL,
    LICENSE,
    MODELS_URL,
    SOURCE_ID,
    parse_centers,
    parse_models,
)
from scripts.ai_cycle.registry import INDICATORS, SOURCES
from scripts.ai_cycle.snapshot import build_snapshot
from scripts.ai_cycle.store import ObservationStore

HASH = "c" * 64
CENTERS_HASH = "d" * 64
FETCHED = "2026-09-22T12:00:00+00:00"
FIXTURE_DIR = Path(__file__).resolve().parents[1] / "ai_cycle" / "fixtures"
MODELS_FIXTURE = FIXTURE_DIR / "epoch_notable_ai_models.csv"
CENTERS_FIXTURE = FIXTURE_DIR / "epoch_data_centers.csv"


def _models(text=None, **kwargs):
    return parse_models(text or MODELS_FIXTURE.read_text(), HASH, FETCHED, **kwargs)


def _centers(text=None, **kwargs):
    return parse_centers(text or CENTERS_FIXTURE.read_text(), CENTERS_HASH, FETCHED, **kwargs)


def test_registry_is_public_snapshot_not_aa():
    assert SOURCE_ID == "epoch"
    assert SOURCES[SOURCE_ID]["name"] == "Epoch AI"
    assert SOURCES[SOURCE_ID]["cadence"] == "snapshot"
    assert SOURCES[SOURCE_ID]["lineage_group"] == SOURCE_ID
    assert "CC BY 4.0" in SOURCES[SOURCE_ID]["license"]
    assert "Source: Epoch AI" in SOURCES[SOURCE_ID]["license"]
    assert SOURCE_ID not in INDICATORS["C3"]["source_ids"]
    assert SOURCE_ID not in INDICATORS["C4"]["source_ids"]
    assert INDICATORS["S1"]["source_ids"] == [SOURCE_ID]
    assert INDICATORS["S1"]["priority"] == "P2"
    assert INDICATORS["S1"]["pane"] == "delivery"
    assert "not a trade signal" in INDICATORS["S1"]["methodology"].lower()
    assert "Source: Epoch AI" in INDICATORS["S1"]["methodology"]
    assert SOURCE_ID not in SOURCE_HISTORY_STARTS
    assert source_windows(SOURCE_ID, "2009-01-01", "2026-01-01", backfill=True) == []


def test_fixtures_stay_small_and_include_ignored_rows():
    models = MODELS_FIXTURE.read_text().splitlines()
    centers = CENTERS_FIXTURE.read_text().splitlines()
    assert 2 <= len(models) <= 31
    assert 2 <= len(centers) <= 11
    assert "NonFrontierBigger" in MODELS_FIXTURE.read_text()
    assert "9.0e+25" in MODELS_FIXTURE.read_text()
    assert "BlankFlop" in MODELS_FIXTURE.read_text()
    assert "Blank Power" in CENTERS_FIXTURE.read_text()


def test_parse_emits_exact_three_estimated_series_with_attribution():
    models = _models(etag='"models-etag"', published="2026-09-21T00:00:00+00:00")
    centers = _centers(etag='"centers-etag"', published="2026-09-21T00:00:00+00:00")
    rows = models + centers
    assert [row["series_id"] for row in rows] == [
        "frontier.max_training_flop",
        "data_center.max_current_h100e",
        "data_center.max_current_h100e.power_mw",
    ]
    assert [row["unit"] for row in rows] == ["FLOP", "H100e", "MW"]
    assert [row["value"] for row in rows] == [2.5e25, 500.5, 42.0]
    assert all(row["measurement"] == "estimated" for row in rows)
    assert all(row["source_id"] == SOURCE_ID for row in rows)
    assert all(row["indicator_id"] == "S1" for row in rows)
    assert all(row["methodology_version"] == "epoch-csv-v1" for row in rows)
    assert all(row["cohort_version"] == "epoch-public-csv-v1" for row in rows)
    assert all(row["metadata"]["attribution"] == ATTRIBUTION for row in rows)
    assert all(row["metadata"]["license"] == LICENSE for row in rows)
    assert all(row["metadata"]["attribution"] == "Source: Epoch AI" for row in rows)
    flop = rows[0]
    assert flop["raw_hash"] == HASH
    assert flop["source_url"] == MODELS_URL
    assert flop["metadata"]["entity"] == "FrontierMax"
    assert flop["metadata"]["org"] == "Lab D"
    assert flop["metadata"]["publication_date"] == "2026-04-01"
    assert flop["metadata"]["confidence"] == "Confident"
    assert flop["metadata"]["source_file"] == "notable_ai_models.csv"
    assert flop["metadata"]["csv_etag"] == '"models-etag"'
    assert flop["metadata"]["estimation_method"]
    h100e, power = rows[1], rows[2]
    assert h100e["raw_hash"] == CENTERS_HASH == power["raw_hash"]
    assert h100e["source_url"] == CENTERS_URL == power["source_url"]
    assert h100e["metadata"]["entity"] == "Max Cluster"
    assert h100e["metadata"]["owner"] == "SpaceXAI"
    assert h100e["metadata"]["confidence"] == "confident"
    assert power["metadata"]["entity"] == "Max Cluster"
    assert power["value"] == 42.0
    assert power["unit"] == "MW"


def test_non_frontier_larger_flop_is_ignored():
    flop = _models()[0]
    assert flop["value"] == 2.5e25
    assert flop["metadata"]["entity"] == "FrontierMax"
    assert flop["value"] != 9.0e25


def test_header_drift_is_fail_closed():
    drifted = MODELS_FIXTURE.read_text().replace("Training compute (FLOP)", "Training FLOPs")
    with pytest.raises(SourceError, match="header"):
        parse_models(drifted, HASH, FETCHED)
    drifted_dc = CENTERS_FIXTURE.read_text().replace("Current H100 equivalents", "H100e")
    with pytest.raises(SourceError, match="header"):
        parse_centers(drifted_dc, CENTERS_HASH, FETCHED)


def test_non_numeric_frontier_flop_is_fail_closed():
    text = MODELS_FIXTURE.read_text().replace("2.5e+25", "unknown")
    with pytest.raises(SourceError, match="numeric"):
        parse_models(text, HASH, FETCHED)


def test_zero_frontier_rows_is_fail_closed():
    text = MODELS_FIXTURE.read_text().replace(",True,", ",,")
    with pytest.raises(SourceError, match="frontier"):
        parse_models(text, HASH, FETCHED)


def test_blank_power_on_max_h100e_row_is_fail_closed():
    text = CENTERS_FIXTURE.read_text().replace("500.5,42.0", "500.5,")
    with pytest.raises(SourceError, match="power"):
        parse_centers(text, CENTERS_HASH, FETCHED)


def test_collect_source_fetches_exactly_two_csv_urls(tmp_path):
    from scripts.ai_cycle.collectors import collect_source

    calls = []

    class Response:
        def __init__(self, raw, etag):
            self.status_code = 200
            self.headers = {"ETag": etag, "Last-Modified": "Mon, 21 Sep 2026 00:00:00 GMT"}
            self._raw = raw

        def iter_content(self, _size):
            yield self._raw

        def close(self):
            pass

    class Session:
        def request(self, method, url, **kwargs):
            assert method == "GET"
            assert "Mozilla" not in (kwargs.get("headers") or {}).get("User-Agent", "")
            assert "epoch" in (kwargs.get("headers") or {}).get("User-Agent", "")
            calls.append(url)
            if url == MODELS_URL:
                return Response(MODELS_FIXTURE.read_bytes(), '"models-etag"')
            if url == CENTERS_URL:
                return Response(CENTERS_FIXTURE.read_bytes(), '"centers-etag"')
            raise AssertionError(f"unexpected URL {url}")

    rows = collect_source(SOURCE_ID, Transport(tmp_path, session=Session()), "2026-09-01", "2026-09-21", env={})
    assert calls == [MODELS_URL, CENTERS_URL]
    assert [row["series_id"] for row in rows] == [
        "frontier.max_training_flop",
        "data_center.max_current_h100e",
        "data_center.max_current_h100e.power_mw",
    ]
    assert all(row["metadata"]["attribution"] == "Source: Epoch AI" for row in rows)
    assert {row["raw_hash"] for row in rows} == {
        hashlib.sha256(MODELS_FIXTURE.read_bytes()).hexdigest(),
        hashlib.sha256(CENTERS_FIXTURE.read_bytes()).hexdigest(),
    }


def test_snapshot_surfaces_s1_as_experimental_research(tmp_path):
    store = ObservationStore(":memory:")
    store.append_observations(_models() + _centers())
    snapshot = build_snapshot(store, "2026-09-22T16:00:00Z")
    assert len(snapshot["indicators"]) == 19
    panel = next(item for item in snapshot["indicators"] if item["id"] == "S1")
    assert panel["status"] == "experimental"
    assert "not a trade signal" in panel["reason"].lower()
    assert panel["source_ids"] == [SOURCE_ID]
    assert {metric["id"] for metric in panel["metrics"]} == {
        "frontier.max_training_flop",
        "data_center.max_current_h100e",
        "data_center.max_current_h100e.power_mw",
    }
    source = next(item for item in snapshot["sources"] if item["id"] == SOURCE_ID)
    assert source["name"] == "Epoch AI"
    assert source["cadence"] == "snapshot"
    assert "CC BY 4.0" in source["license"]
