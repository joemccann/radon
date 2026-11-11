"""Tests for fetch_menthorq_cta.py — MenthorQ CTA positioning.

All tests are pure computation — no network calls, no browser.
"""
import json
import tempfile
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pytest

import fetch_menthorq_cta as fetch_menthorq_cta_module
from clients.vision_cascade import parse_json_rows
from fetch_menthorq_cta import (
    CACHE_DIR,
    CTA_TABLES,
    EXTRACTION_PROMPT,
    cache_path,
    find_by_underlying,
    load_menthorq_cache,
    read_cache,
    resolve_menthorq_creds,
    resolve_trading_date,
    write_cache,
)


# ══════════════════════════════════════════════════════════════════
# Sample Data
# ══════════════════════════════════════════════════════════════════

SAMPLE_MAIN_TABLE = [
    {
        "underlying": "E-Mini S&P 500 Index",
        "position_today": 0.45,
        "position_yesterday": 0.21,
        "position_1m_ago": 1.06,
        "percentile_1m": 38,
        "percentile_3m": 13,
        "percentile_1y": 38,
        "z_score_3m": -1.56,
    },
    {
        "underlying": "Nasdaq 100 Index",
        "position_today": 0.32,
        "position_yesterday": 0.18,
        "position_1m_ago": 0.95,
        "percentile_1m": 25,
        "percentile_3m": 10,
        "percentile_1y": 30,
        "z_score_3m": -1.80,
    },
    {
        "underlying": "10 Year T-Note",
        "position_today": -0.55,
        "position_yesterday": -0.60,
        "position_1m_ago": -0.30,
        "percentile_1m": 15,
        "percentile_3m": 8,
        "percentile_1y": 20,
        "z_score_3m": -2.10,
    },
    {
        "underlying": "Gold (COMEX)",
        "position_today": 0.85,
        "position_yesterday": 0.80,
        "position_1m_ago": 0.60,
        "percentile_1m": 72,
        "percentile_3m": 65,
        "percentile_1y": 80,
        "z_score_3m": 0.95,
    },
]

SAMPLE_INDEX_TABLE = [
    {
        "underlying": "S&P 500",
        "position_today": 0.45,
        "position_yesterday": 0.21,
        "position_1m_ago": 1.06,
        "percentile_1m": 38,
        "percentile_3m": 13,
        "percentile_1y": 38,
        "z_score_3m": -1.56,
    },
    {
        "underlying": "MSCI World",
        "position_today": 0.55,
        "position_yesterday": 0.50,
        "position_1m_ago": 0.80,
        "percentile_1m": 40,
        "percentile_3m": 30,
        "percentile_1y": 45,
        "z_score_3m": -0.80,
    },
]


# ══════════════════════════════════════════════════════════════════
# 1. Cache Read/Write
# ══════════════════════════════════════════════════════════════════

class TestCacheReadWrite:
    """Tests for cache operations."""

    def test_write_and_read_cache(self, tmp_path, monkeypatch):
        """Write cache, read it back, verify contents."""
        monkeypatch.setattr("fetch_menthorq_cta.CACHE_DIR", tmp_path)

        tables = {"main": SAMPLE_MAIN_TABLE, "index": SAMPLE_INDEX_TABLE}
        write_cache("2026-03-07", tables)

        result = read_cache("2026-03-07")
        assert result is not None
        assert result["date"] == "2026-03-07"
        assert result["source"] == "menthorq_s3_vision"
        assert len(result["tables"]["main"]) == 4
        assert len(result["tables"]["index"]) == 2

    def test_cache_miss_returns_none(self, tmp_path, monkeypatch):
        """Missing file returns None."""
        monkeypatch.setattr("fetch_menthorq_cta.CACHE_DIR", tmp_path)
        result = read_cache("2020-01-01")
        assert result is None

    def test_corrupt_cache_returns_none(self, tmp_path, monkeypatch):
        """Corrupt JSON returns None."""
        monkeypatch.setattr("fetch_menthorq_cta.CACHE_DIR", tmp_path)
        p = tmp_path / "cta_2026-03-07.json"
        p.write_text("not valid json{{{")
        result = read_cache("2026-03-07")
        assert result is None

    def test_cache_path_format(self, tmp_path, monkeypatch):
        """Cache path follows expected naming convention."""
        monkeypatch.setattr("fetch_menthorq_cta.CACHE_DIR", tmp_path)
        p = cache_path("2026-03-07")
        assert p.name == "cta_2026-03-07.json"


# ══════════════════════════════════════════════════════════════════
# 2. find_by_underlying
# ══════════════════════════════════════════════════════════════════

class TestFindByUnderlying:
    """Tests for find_by_underlying() search."""

    def test_find_spx_exact(self):
        """Find S&P 500 in main table."""
        result = find_by_underlying(SAMPLE_MAIN_TABLE, "S&P 500")
        assert result is not None
        assert result["underlying"] == "E-Mini S&P 500 Index"

    def test_find_spx_partial(self):
        """Find by partial match."""
        result = find_by_underlying(SAMPLE_MAIN_TABLE, "E-Mini S&P")
        assert result is not None
        assert "S&P 500" in result["underlying"]

    def test_find_case_insensitive(self):
        """Case-insensitive search."""
        result = find_by_underlying(SAMPLE_MAIN_TABLE, "nasdaq")
        assert result is not None
        assert "Nasdaq" in result["underlying"]

    def test_find_missing_returns_none(self):
        """Non-existent asset returns None."""
        result = find_by_underlying(SAMPLE_MAIN_TABLE, "Bitcoin")
        assert result is None

    def test_find_in_empty_table(self):
        """Empty table returns None."""
        result = find_by_underlying([], "S&P 500")
        assert result is None

    def test_find_gold(self):
        """Find Gold entry."""
        result = find_by_underlying(SAMPLE_MAIN_TABLE, "Gold")
        assert result is not None
        assert result["z_score_3m"] == 0.95


# ══════════════════════════════════════════════════════════════════
# 3. Vision Response Parsing
# ══════════════════════════════════════════════════════════════════

class TestVisionParsing:
    """The vision ladder's row parser accepts the response shapes the CTA prompt yields."""

    def test_clean_json_response(self):
        assert parse_json_rows(json.dumps(SAMPLE_MAIN_TABLE)) == SAMPLE_MAIN_TABLE

    def test_markdown_fenced_response(self):
        raw = "```json\n" + json.dumps(SAMPLE_MAIN_TABLE) + "\n```"
        assert parse_json_rows(raw) == SAMPLE_MAIN_TABLE

    def test_extraction_prompt_format(self):
        """Verify extraction prompt mentions required fields."""
        assert "underlying" in EXTRACTION_PROMPT
        assert "position_today" in EXTRACTION_PROMPT
        assert "z_score_3m" in EXTRACTION_PROMPT
        assert "percentile_3m" in EXTRACTION_PROMPT


# ══════════════════════════════════════════════════════════════════
# 4. Trading Date Resolution
# ══════════════════════════════════════════════════════════════════

class TestResolveTradingDate:
    """Tests for resolve_trading_date()."""

    def test_returns_string_format(self):
        """Always returns YYYY-MM-DD format."""
        date = resolve_trading_date()
        assert len(date) == 10
        assert date[4] == "-"
        assert date[7] == "-"
        # Should parse without error
        datetime.strptime(date, "%Y-%m-%d")

    def test_not_a_weekend(self):
        """Result should never be a Saturday or Sunday."""
        date = resolve_trading_date()
        dt = datetime.strptime(date, "%Y-%m-%d")
        assert dt.weekday() < 5, f"Trading date {date} is a weekend (day {dt.weekday()})"


# ══════════════════════════════════════════════════════════════════
# 5. load_menthorq_cache
# ══════════════════════════════════════════════════════════════════

class TestLoadMenthorqCache:
    """Tests for the load helper used by CRI scanner."""

    def test_load_specific_date(self, tmp_path, monkeypatch):
        """Load by specific date."""
        monkeypatch.setattr("fetch_menthorq_cta.CACHE_DIR", tmp_path)
        write_cache("2026-03-07", {"main": SAMPLE_MAIN_TABLE})
        result = load_menthorq_cache("2026-03-07")
        assert result is not None
        assert result["date"] == "2026-03-07"


class TestFetchMenthorqCtaFailures:
    def test_client_import_failure_returns_none(self, monkeypatch, capsys):
        """Client import failures should report cleanly instead of raising UnboundLocalError."""
        import builtins

        real_import = builtins.__import__

        def guarded_import(name, *args, **kwargs):
            if name == "clients.menthorq_client":
                raise ModuleNotFoundError("playwright")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", guarded_import)

        result = fetch_menthorq_cta_module.fetch_menthorq_cta(
            date_str="2026-03-07",
            force=True,
        )

        captured = capsys.readouterr()
        assert result is None
        assert "Failed to initialize MenthorQ client" in captured.err

    def test_load_missing_date(self, tmp_path, monkeypatch):
        """Missing date returns None."""
        monkeypatch.setattr("fetch_menthorq_cta.CACHE_DIR", tmp_path)
        result = load_menthorq_cache("2020-01-01")
        assert result is None


# ══════════════════════════════════════════════════════════════════
# 6. CRI Integration (MenthorQ data shape)
# ══════════════════════════════════════════════════════════════════

class TestCRIIntegration:
    """Test that MenthorQ data shape is compatible with CRI scanner expectations."""

    def test_spx_entry_has_required_fields(self):
        """SPX entry has all fields CRI scanner needs."""
        spx = find_by_underlying(SAMPLE_MAIN_TABLE, "S&P 500")
        assert spx is not None
        required = ["position_today", "position_yesterday", "percentile_3m", "z_score_3m"]
        for field in required:
            assert field in spx, f"Missing field: {field}"

    def test_cache_structure_for_cri(self, tmp_path, monkeypatch):
        """Cache entry has the shape CRI scanner expects."""
        monkeypatch.setattr("fetch_menthorq_cta.CACHE_DIR", tmp_path)
        write_cache("2026-03-07", {"main": SAMPLE_MAIN_TABLE})
        data = read_cache("2026-03-07")
        assert "tables" in data
        assert "main" in data["tables"]
        assert "date" in data
        assert "source" in data


# ══════════════════════════════════════════════════════════════════
# 7. Credential Resolution
# ══════════════════════════════════════════════════════════════════

class TestCredentialResolution:
    """Tests for resolve_menthorq_creds() — env-only, no hardcoded secrets."""

    def test_creds_from_env(self, monkeypatch):
        """Credentials come from environment variables."""
        monkeypatch.setenv("MENTHORQ_USER", "test@example.com")
        monkeypatch.setenv("MENTHORQ_PASS", "testpass123")
        user, passwd = resolve_menthorq_creds()
        assert user == "test@example.com"
        assert passwd == "testpass123"

    def test_missing_user_returns_none(self, monkeypatch):
        """Missing MENTHORQ_USER returns None."""
        monkeypatch.delenv("MENTHORQ_USER", raising=False)
        monkeypatch.setenv("MENTHORQ_PASS", "testpass123")
        user, passwd = resolve_menthorq_creds()
        assert user is None

    def test_missing_pass_returns_none(self, monkeypatch):
        """Missing MENTHORQ_PASS returns None."""
        monkeypatch.setenv("MENTHORQ_USER", "test@example.com")
        monkeypatch.delenv("MENTHORQ_PASS", raising=False)
        user, passwd = resolve_menthorq_creds()
        assert passwd is None

    def test_empty_string_returns_none(self, monkeypatch):
        """Empty string env vars return None."""
        monkeypatch.setenv("MENTHORQ_USER", "")
        monkeypatch.setenv("MENTHORQ_PASS", "")
        user, passwd = resolve_menthorq_creds()
        assert user is None
        assert passwd is None

    def test_no_hardcoded_defaults(self):
        """Verify no credentials are hardcoded in the source file."""
        import inspect
        source = inspect.getsource(resolve_menthorq_creds)
        assert "joseph" not in source.lower()
        assert "gmail" not in source.lower()
        assert "RX$" not in source
        assert "@" not in source
