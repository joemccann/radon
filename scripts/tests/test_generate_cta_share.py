"""Freshness gating for the CTA share payload (generate_cta_share.py).

Bug 2026-06-12: POST /cta/share re-rendered all four cards fresh at 17:15 ET
but from data/menthorq_cache/cta_2026-06-11.json — the glob-latest disk file —
while the CTA page itself read the fresher Turso menthorq_cta row. The share
generator must read the same source as the page (DB-first, disk fallback) and
must flag the payload stale when even the freshest data is behind the latest
closed trading day.
"""

from __future__ import annotations

import json
import sys
import types
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

import generate_cta_share as gcs

ET = ZoneInfo("America/New_York")
FRIDAY_AFTER_CLOSE = datetime(2026, 6, 12, 17, 15, tzinfo=ET)


def make_payload(date_str: str, *, empty_tables: bool = False) -> dict:
    def row(name: str) -> dict:
        return {
            "underlying": name,
            "position_today": -1.5,
            "position_1m_ago": 1.2,
            "percentile_3m": 1,
            "percentile_1y": 1,
            "z_score_3m": -2.4,
        }

    main = [] if empty_tables else [
        row("E-Mini S&P 500"),
        row("CME Nasdaq 100"),
        row("2-Year T-Note"),
        row("10-Year T-Note"),
    ]
    return {
        "date": date_str,
        "fetched_at": f"{date_str}T21:30:00Z",
        "tables": {"main": main, "index": [], "commodity": [], "currency": []},
    }


def write_disk_cache(cache_dir, date_str: str) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    (cache_dir / f"cta_{date_str}.json").write_text(json.dumps(make_payload(date_str)))


class _FakeCursor:
    """Mirrors the real libsql_experimental cursor: fetchall(), no .rows
    attribute (libsql-experimental 0.0.55 — verified on the production VPS)."""

    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


class _FakeDb:
    def __init__(self, rows):
        self._rows = rows

    def execute(self, _sql, _params=()):
        return _FakeCursor(self._rows)


@pytest.fixture
def stub_db(monkeypatch):
    """Install a fake db.client whose latest menthorq_cta rows are `payloads`."""

    def _install(payloads=None, *, raise_error=False):
        client_mod = types.ModuleType("db.client")
        if raise_error:
            def get_db():
                raise RuntimeError("turso unreachable")
        else:
            rows = [(json.dumps(p),) for p in (payloads or [])]

            def get_db():
                return _FakeDb(rows)

        client_mod.get_db = get_db
        parent = types.ModuleType("db")
        parent.client = client_mod
        monkeypatch.setitem(sys.modules, "db", parent)
        monkeypatch.setitem(sys.modules, "db.client", client_mod)

    return _install


@pytest.fixture
def cache_dir(tmp_path, monkeypatch):
    cache = tmp_path / "menthorq_cache"
    monkeypatch.setattr(gcs, "CACHE_DIR", cache)
    return cache


# ── load_cta: same source as the CTA page ────────────────────────────────────

class TestLoadCtaSource:
    def test_prefers_fresher_db_row_over_stale_disk_cache(self, cache_dir, stub_db):
        """THE bug: disk has only yesterday, Turso has today — share served yesterday."""
        write_disk_cache(cache_dir, "2026-06-11")
        stub_db([make_payload("2026-06-12")])

        data = gcs.load_cta()

        assert data["date"] == "2026-06-12"

    def test_falls_back_to_disk_when_db_unavailable(self, cache_dir, stub_db):
        write_disk_cache(cache_dir, "2026-06-11")
        stub_db(raise_error=True)

        data = gcs.load_cta()

        assert data["date"] == "2026-06-11"

    def test_ignores_structurally_empty_db_payload(self, cache_dir, stub_db):
        """An empty-tables row must never displace a valid disk payload."""
        write_disk_cache(cache_dir, "2026-06-11")
        stub_db([make_payload("2026-06-12", empty_tables=True)])

        data = gcs.load_cta()

        assert data["date"] == "2026-06-11"

    def test_uses_disk_when_disk_is_newer_than_db(self, cache_dir, stub_db):
        write_disk_cache(cache_dir, "2026-06-12")
        stub_db([make_payload("2026-06-11")])

        data = gcs.load_cta()

        assert data["date"] == "2026-06-12"

    def test_explicit_target_date_still_reads_disk(self, cache_dir, stub_db):
        write_disk_cache(cache_dir, "2026-06-10")
        stub_db([make_payload("2026-06-12")])

        data = gcs.load_cta("2026-06-10")

        assert data["date"] == "2026-06-10"

    def test_raises_when_no_source_has_data(self, cache_dir, stub_db):
        stub_db([])
        with pytest.raises(FileNotFoundError):
            gcs.load_cta()


# ── assess_freshness: gate against the latest closed trading day ─────────────

class TestAssessFreshness:
    def test_flags_yesterday_dated_payload_after_close(self):
        freshness = gcs.assess_freshness("2026-06-11", now=FRIDAY_AFTER_CLOSE)

        assert freshness["stale"] is True
        assert freshness["data_date"] == "2026-06-11"
        assert freshness["expected_date"] == "2026-06-12"

    def test_current_payload_is_not_stale(self):
        freshness = gcs.assess_freshness("2026-06-12", now=FRIDAY_AFTER_CLOSE)

        assert freshness["stale"] is False
        assert freshness["expected_date"] == "2026-06-12"

    def test_before_close_yesterday_is_the_expected_date(self):
        midday = datetime(2026, 6, 12, 11, 0, tzinfo=ET)
        freshness = gcs.assess_freshness("2026-06-11", now=midday)

        assert freshness["stale"] is False
        assert freshness["expected_date"] == "2026-06-11"


# ── main(): the share result must carry the freshness verdict ────────────────

class TestShareResultFreshness:
    @pytest.fixture
    def render_stubs(self, tmp_path, monkeypatch):
        reports = tmp_path / "reports"
        reports.mkdir()
        monkeypatch.setattr(gcs, "REPORTS_DIR", reports)

        def fake_screenshot(_html_path, png_path, selector=".card"):
            with open(png_path, "wb") as fh:
                fh.write(b"png")
            return True

        monkeypatch.setattr(gcs, "screenshot_card", fake_screenshot)
        monkeypatch.setattr(
            sys, "argv", ["generate_cta_share.py", "--json", "--no-open"]
        )
        return reports

    def test_result_flags_stale_payload(self, cache_dir, stub_db, render_stubs):
        write_disk_cache(cache_dir, "2026-06-11")
        stub_db([])

        result = gcs.main()

        assert result["stale"] is True
        assert result["data_date"] == "2026-06-11"
        assert result["expected_date"] > "2026-06-11"

    def test_stale_preview_carries_visible_warning(self, cache_dir, stub_db, render_stubs):
        write_disk_cache(cache_dir, "2026-06-11")
        stub_db([])

        result = gcs.main()

        preview = open(result["preview_path"]).read()
        assert "STALE" in preview
