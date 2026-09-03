"""REL-195 (R-555, R-556, R-557): ma-ratio heartbeats every exit path, the
SPX overlay is IB-first, and finished_at is stamped at write time."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import ma_ratio_scan  # noqa: E402


@pytest.fixture()
def health_rows(monkeypatch):
    from db import writer

    rows: list[dict] = []

    def _record(service, state, **kwargs):
        rows.append({"service": service, "state": state, **kwargs})
        return True

    monkeypatch.setattr(writer, "record_service_health", _record)
    return rows


class TestGatedRunWritesAHeartbeat:
    def test_insufficient_coverage_lands_an_error_row(self, monkeypatch, health_rows):
        """R-555: the gated path logged and returned with NO row — a dead
        Yahoo sweep was invisible until the 26h window aged out."""
        monkeypatch.setattr(
            ma_ratio_scan, "resolve_spx_constituents",
            lambda: (["AAA", "BBB"], "test-seed"),
        )
        monkeypatch.setattr(
            ma_ratio_scan, "ensure_member_history",
            lambda members, **kw: ({}, {"spark": 0}),
        )
        monkeypatch.setattr(ma_ratio_scan, "install_sigterm_unwind", lambda: None)
        monkeypatch.setattr(
            ma_ratio_scan, "fetch_spx_overlay_closes",
            lambda fallback, **kw: (dict(fallback), "yahoo"),
            raising=False,
        )
        payload = ma_ratio_scan.run()
        assert payload.get("missing") is True
        assert health_rows, "the gated run wrote no service_health row"
        row = health_rows[-1]
        assert row["service"] == ma_ratio_scan.SERVICE
        assert row["state"] == "error"
        assert "coverage" in str(row.get("error", "")).lower() or payload.get(
            "reason", ""
        ) in str(row.get("error", ""))


class TestCrashWritesAHeartbeat:
    def test_an_exception_in_run_lands_an_error_row(self, monkeypatch, health_rows):
        monkeypatch.setattr(
            ma_ratio_scan, "run",
            lambda **kw: (_ for _ in ()).throw(RuntimeError("turso died")),
        )
        code = ma_ratio_scan.main([])
        assert code != 0
        assert health_rows and health_rows[-1]["state"] == "error"


class TestFinishedAtIsWriteTime:
    def test_heartbeat_finished_at_is_not_run_start(self, monkeypatch, health_rows):
        """R-557: finished_at was scan_time (run START), overstating age by
        up to the 1500s sweep budget."""
        from db import writer

        monkeypatch.setattr(writer, "ensure_no_replica_for_writers", lambda: None)
        monkeypatch.setattr(writer, "upsert_ma_ratio_rows", lambda *a, **k: None)
        monkeypatch.setattr(writer, "upsert_scan_snapshot", lambda *a, **k: None)
        monkeypatch.setattr(ma_ratio_scan, "_write_json_cache", lambda payload: None)

        start = "2026-09-03T00:00:00Z"
        ma_ratio_scan.persist_result({"scan_time": start}, [])
        assert health_rows
        finished = health_rows[-1].get("finished_at")
        assert finished and finished != start, (
            "persist_result stamped finished_at with the run-start scan_time"
        )


class TestOverlayIsIbFirst:
    def test_ib_series_wins_and_yahoo_is_fallback_only(self):
        """R-556 / rule 7: the single-symbol SPX overlay must try IB before
        riding the Yahoo constituent sweep."""
        calls = []

        def fake_ib(tickers):
            calls.append(tuple(tickers))
            return {"SPX": {"2026-09-02": 6500.0}}

        merged, source = ma_ratio_scan.fetch_spx_overlay_closes(
            {"2026-09-01": 6400.0}, fetch_ib=fake_ib
        )
        assert calls == [("SPX",)]
        assert merged["2026-09-02"] == 6500.0
        assert merged["2026-09-01"] == 6400.0
        assert "ib" in source

    def test_ib_failure_falls_back_to_the_swept_series(self):
        def broken_ib(tickers):
            raise RuntimeError("gateway down")

        merged, source = ma_ratio_scan.fetch_spx_overlay_closes(
            {"2026-09-01": 6400.0}, fetch_ib=broken_ib
        )
        assert merged == {"2026-09-01": 6400.0}
        assert source == "yahoo"

    def test_run_routes_the_overlay_through_the_ladder(self):
        src = "\n".join(
            line for line in (SCRIPTS / "ma_ratio_scan.py").read_text().splitlines()
            if not line.lstrip().startswith("#")
        )
        body = src[src.index("def run("):]
        assert "fetch_spx_overlay_closes(" in body
