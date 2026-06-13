"""Structural tests for scripts/monitor_daemon/handlers/preset_rebalance.py.

These tests exercise:
  1. Handler identity via the BaseHandler contract.
  2. Constituent-diff logic with NO network calls.
  3. Preset-file rewrite logic with tmp directories.
  4. Fetcher HTML/CSV parsing with mock urllib responses.
  5. Changelog append logic.
  6. Registration in monitor_daemon/run.py:create_daemon.

No HTTP is made to Wikipedia, iShares, or any live endpoint.
No production files are written (tmp_path is used for all file I/O).
"""
from __future__ import annotations

import csv
import io
import json
import sys
import textwrap
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import MagicMock, patch

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_SCRIPTS_DIR = _PROJECT_ROOT / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

# Bring in the module under test.
import monitor_daemon.handlers.preset_rebalance as pr


# ── Helper: minimal fake SP500 HTML ──────────────────────────────────

def _make_sp500_html(rows: List[Dict[str, str]]) -> str:
    """Build the minimal Wikipedia SP500 table HTML that fetch_sp500 parses."""
    inner_rows = ""
    for row in rows:
        ticker = row.get("ticker", "")
        name = row.get("name", "")
        sector = row.get("sector", "")
        sub = row.get("sub_industry", "")
        inner_rows += (
            f"<tr>"
            f"<td>{ticker}</td>"
            f"<td>{name}</td>"
            f"<td>{sector}</td>"
            f"<td>{sub}</td>"
            f"</tr>\n"
        )
    # Header row that fetch_sp500 skips (rows[1:]).
    header_row = "<tr><th>Symbol</th><th>Security</th><th>GICS Sector</th><th>Sub-industry</th></tr>\n"
    return f"<table>{header_row}{inner_rows}</table>"


# ── Helper: minimal fake NDX100 HTML ─────────────────────────────────

def _make_ndx100_html(rows: List[Dict[str, str]]) -> str:
    """Build fake Wikipedia NDX100 page HTML.

    fetch_ndx100 uses tables[4], so we prepend 4 empty dummy tables.
    """
    dummy_tables = "<table></table>" * 4
    inner_rows = ""
    for row in rows:
        ticker = row.get("ticker", "")
        name = row.get("name", "")
        sector = row.get("sector", "")
        sub = row.get("sub_industry", "")
        inner_rows += (
            f"<tr>"
            f"<td>{ticker}</td>"
            f"<td>{name}</td>"
            f"<td>{sector}</td>"
            f"<td>{sub}</td>"
            f"</tr>\n"
        )
    header_row = "<tr><th>Ticker</th><th>Company</th><th>ICB Industry</th><th>ICB Subsector</th></tr>\n"
    target_table = f"<table>{header_row}{inner_rows}</table>"
    return dummy_tables + target_table


# ── Helper: minimal fake IWM CSV content ─────────────────────────────

def _make_iwm_csv(rows: List[Dict[str, Any]]) -> str:
    """Build fake iShares IWM CSV content that fetch_r2k parses.

    fetch_r2k skips the first 10 lines then parses CSV starting line 11
    (0-indexed line 10). We add 9 header lines then a column-header line.
    """
    header_lines = "\n".join(f"IWM line {i}" for i in range(9))
    col_header = "Ticker,Name,Sector,Asset Class,Weight (%)"
    data_lines = []
    for row in rows:
        ticker = row.get("ticker", "TICK")
        name = row.get("name", "Company Name")
        sector = row.get("sector", "Technology")
        asset_class = row.get("asset_class", "Equity")
        weight = row.get("weight", "0.05")
        data_lines.append(f"{ticker},{name},{sector},{asset_class},{weight}")
    return "\n".join([header_lines, col_header] + data_lines)


# ── 1. Handler identity ───────────────────────────────────────────────

class TestPresetRebalanceHandlerIdentity:
    def test_handler_name(self):
        from monitor_daemon.handlers.preset_rebalance_handler import PresetRebalanceHandler
        h = PresetRebalanceHandler()
        assert h.name == "preset_rebalance"

    def test_interval_is_weekly(self):
        from monitor_daemon.handlers.preset_rebalance_handler import PresetRebalanceHandler, WEEKLY
        h = PresetRebalanceHandler()
        assert h.interval_seconds == WEEKLY
        assert WEEKLY == 7 * 24 * 60 * 60

    def test_does_not_require_market_hours(self):
        from monitor_daemon.handlers.preset_rebalance_handler import PresetRebalanceHandler
        h = PresetRebalanceHandler()
        assert h.requires_market_hours is False

    def test_service_name(self):
        from monitor_daemon.handlers.preset_rebalance_handler import PresetRebalanceHandler
        h = PresetRebalanceHandler()
        assert h.service_name == "preset-rebalance"

    def test_execute_delegates_to_module(self):
        from monitor_daemon.handlers.preset_rebalance_handler import PresetRebalanceHandler
        h = PresetRebalanceHandler()
        sentinel = {"status": "ok", "total_changes": 0}
        with patch(
            "monitor_daemon.handlers.preset_rebalance.execute",
            return_value=sentinel,
        ) as mock_exec:
            result = h.execute()
        mock_exec.assert_called_once()
        assert result is sentinel


# ── 2. Constituent-diff logic ────────────────────────────────────────

class TestDiffTickers:
    def test_no_changes(self):
        added, removed = pr.diff_tickers(["AAPL", "MSFT"], ["AAPL", "MSFT"])
        assert added == set()
        assert removed == set()

    def test_addition(self):
        added, removed = pr.diff_tickers(["AAPL"], ["AAPL", "NVDA"])
        assert added == {"NVDA"}
        assert removed == set()

    def test_removal(self):
        added, removed = pr.diff_tickers(["AAPL", "AMZN"], ["AAPL"])
        assert added == set()
        assert removed == {"AMZN"}

    def test_simultaneous_add_and_remove(self):
        added, removed = pr.diff_tickers(
            ["AAPL", "AMZN", "MSFT"],
            ["AAPL", "NVDA", "MSFT"],
        )
        assert added == {"NVDA"}
        assert removed == {"AMZN"}

    def test_completely_different_lists(self):
        added, removed = pr.diff_tickers(["AAPL", "MSFT"], ["NVDA", "TSLA"])
        assert added == {"NVDA", "TSLA"}
        assert removed == {"AAPL", "MSFT"}

    def test_empty_current_all_added(self):
        added, removed = pr.diff_tickers([], ["AAPL", "MSFT"])
        assert added == {"AAPL", "MSFT"}
        assert removed == set()

    def test_empty_fresh_all_removed(self):
        added, removed = pr.diff_tickers(["AAPL", "MSFT"], [])
        assert added == set()
        assert removed == {"AAPL", "MSFT"}


# ── 3. Fetcher parsing — SP500 ───────────────────────────────────────

class TestFetchSp500Parsing:
    def _mock_response(self, html: str):
        mock = MagicMock()
        mock.read.return_value = html.encode("utf-8")
        mock.__enter__ = lambda s: s
        mock.__exit__ = MagicMock(return_value=False)
        return mock

    def test_parses_basic_ticker_list(self):
        companies = [
            {"ticker": "AAPL", "name": "Apple Inc.", "sector": "IT", "sub_industry": "Hardware"},
            {"ticker": "MSFT", "name": "Microsoft", "sector": "IT", "sub_industry": "Software"},
        ]
        html = _make_sp500_html(companies)
        with patch("monitor_daemon.handlers.preset_rebalance.urlopen",
                   return_value=self._mock_response(html)):
            result = pr.fetch_sp500()
        tickers = [c["ticker"] for c in result]
        assert "AAPL" in tickers
        assert "MSFT" in tickers

    def test_deduplicates_tickers(self):
        companies = [
            {"ticker": "AAPL", "name": "Apple Inc.", "sector": "IT", "sub_industry": "HW"},
            {"ticker": "AAPL", "name": "Apple Inc. dup", "sector": "IT", "sub_industry": "HW"},
        ]
        html = _make_sp500_html(companies)
        with patch("monitor_daemon.handlers.preset_rebalance.urlopen",
                   return_value=self._mock_response(html)):
            result = pr.fetch_sp500()
        tickers = [c["ticker"] for c in result]
        assert tickers.count("AAPL") == 1

    def test_skips_rows_with_no_ticker(self):
        html = (
            "<table>"
            "<tr><th>header</th></tr>"
            "<tr><td></td><td>No ticker</td><td>IT</td><td>SW</td></tr>"
            "<tr><td>MSFT</td><td>Microsoft</td><td>IT</td><td>SW</td></tr>"
            "</table>"
        )
        with patch("monitor_daemon.handlers.preset_rebalance.urlopen",
                   return_value=self._mock_response(html)):
            result = pr.fetch_sp500()
        assert len(result) == 1
        assert result[0]["ticker"] == "MSFT"

    def test_strips_html_tags_from_ticker(self):
        html = (
            "<table>"
            "<tr><th>header</th></tr>"
            "<tr><td><a href='/wiki/AAPL'>AAPL</a></td><td>Apple</td><td>IT</td><td>HW</td></tr>"
            "</table>"
        )
        with patch("monitor_daemon.handlers.preset_rebalance.urlopen",
                   return_value=self._mock_response(html)):
            result = pr.fetch_sp500()
        assert result[0]["ticker"] == "AAPL"

    def test_returns_sector_and_sub_industry(self):
        companies = [
            {"ticker": "XOM", "name": "Exxon", "sector": "Energy", "sub_industry": "Oil & Gas"},
        ]
        html = _make_sp500_html(companies)
        with patch("monitor_daemon.handlers.preset_rebalance.urlopen",
                   return_value=self._mock_response(html)):
            result = pr.fetch_sp500()
        assert result[0]["sector"] == "Energy"
        assert result[0]["sub_industry"] == "Oil & Gas"


# ── 4. Fetcher parsing — NDX100 ──────────────────────────────────────

class TestFetchNdx100Parsing:
    def _mock_response(self, html: str):
        mock = MagicMock()
        mock.read.return_value = html.encode("utf-8")
        mock.__enter__ = lambda s: s
        mock.__exit__ = MagicMock(return_value=False)
        return mock

    def test_parses_basic_ticker_list(self):
        companies = [
            {"ticker": "AAPL", "name": "Apple", "sector": "Technology", "sub_industry": "HW"},
            {"ticker": "AMZN", "name": "Amazon", "sector": "Consumer", "sub_industry": "Retail"},
        ]
        html = _make_ndx100_html(companies)
        with patch("monitor_daemon.handlers.preset_rebalance.urlopen",
                   return_value=self._mock_response(html)):
            result = pr.fetch_ndx100()
        tickers = [c["ticker"] for c in result]
        assert "AAPL" in tickers
        assert "AMZN" in tickers

    def test_deduplicates_tickers(self):
        companies = [
            {"ticker": "AAPL", "name": "Apple", "sector": "Technology", "sub_industry": "HW"},
            {"ticker": "AAPL", "name": "Apple dup", "sector": "Technology", "sub_industry": "HW"},
        ]
        html = _make_ndx100_html(companies)
        with patch("monitor_daemon.handlers.preset_rebalance.urlopen",
                   return_value=self._mock_response(html)):
            result = pr.fetch_ndx100()
        assert len([c for c in result if c["ticker"] == "AAPL"]) == 1


# ── 5. Fetcher parsing — R2K CSV ─────────────────────────────────────

class TestFetchR2kParsing:
    def _mock_response(self, content: str):
        mock = MagicMock()
        mock.read.return_value = content.encode("utf-8")
        mock.__enter__ = lambda s: s
        mock.__exit__ = MagicMock(return_value=False)
        return mock

    def test_parses_equity_rows(self):
        csv_content = _make_iwm_csv([
            {"ticker": "TICK1", "name": "Company One", "sector": "Technology",
             "asset_class": "Equity", "weight": "0.10"},
            {"ticker": "TICK2", "name": "Company Two", "sector": "Healthcare",
             "asset_class": "Equity", "weight": "0.05"},
        ])
        with patch("monitor_daemon.handlers.preset_rebalance.urlopen",
                   return_value=self._mock_response(csv_content)):
            result = pr.fetch_r2k()
        tickers = [c["ticker"] for c in result]
        assert "TICK1" in tickers
        assert "TICK2" in tickers

    def test_skips_non_equity_rows(self):
        csv_content = _make_iwm_csv([
            {"ticker": "TICK1", "name": "Company One", "sector": "Technology",
             "asset_class": "Equity", "weight": "0.10"},
            {"ticker": "CASH1", "name": "Cash Collateral", "sector": "",
             "asset_class": "Cash", "weight": "0.01"},
        ])
        with patch("monitor_daemon.handlers.preset_rebalance.urlopen",
                   return_value=self._mock_response(csv_content)):
            result = pr.fetch_r2k()
        tickers = [c["ticker"] for c in result]
        assert "TICK1" in tickers
        assert "CASH1" not in tickers

    def test_skips_dash_tickers(self):
        csv_content = _make_iwm_csv([
            {"ticker": "-", "name": "Placeholder", "sector": "Technology",
             "asset_class": "Equity", "weight": "0.00"},
            {"ticker": "REAL", "name": "Real Co", "sector": "Healthcare",
             "asset_class": "Equity", "weight": "0.10"},
        ])
        with patch("monitor_daemon.handlers.preset_rebalance.urlopen",
                   return_value=self._mock_response(csv_content)):
            result = pr.fetch_r2k()
        tickers = [c["ticker"] for c in result]
        assert "REAL" in tickers
        assert "-" not in tickers

    def test_deduplicates_tickers(self):
        csv_content = _make_iwm_csv([
            {"ticker": "TICK1", "name": "Company One A", "sector": "Technology",
             "asset_class": "Equity", "weight": "0.10"},
            {"ticker": "TICK1", "name": "Company One B", "sector": "Technology",
             "asset_class": "Equity", "weight": "0.05"},
        ])
        with patch("monitor_daemon.handlers.preset_rebalance.urlopen",
                   return_value=self._mock_response(csv_content)):
            result = pr.fetch_r2k()
        assert len([c for c in result if c["ticker"] == "TICK1"]) == 1

    def test_filters_known_non_equity_names(self):
        csv_content = _make_iwm_csv([
            {"ticker": "FCASH", "name": "FUTURES Overlay", "sector": "",
             "asset_class": "Equity", "weight": "0.00"},
            {"ticker": "REAL", "name": "Real Company", "sector": "Energy",
             "asset_class": "Equity", "weight": "0.08"},
        ])
        with patch("monitor_daemon.handlers.preset_rebalance.urlopen",
                   return_value=self._mock_response(csv_content)):
            result = pr.fetch_r2k()
        tickers = [c["ticker"] for c in result]
        assert "REAL" in tickers
        assert "FCASH" not in tickers


# ── 6. Preset-file rewrite — SP500 ───────────────────────────────────

class TestUpdateSp500Presets:
    """Tests the update_sp500_presets function with a tmp presets dir."""

    @pytest.fixture
    def sp500_preset_dir(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
        """Create a minimal sp500.json in a tmp dir; point PRESETS_DIR there."""
        preset_dir = tmp_path / "presets"
        preset_dir.mkdir()
        master = {
            "name": "sp500",
            "description": "S&P 500",
            "tickers": ["AAPL", "MSFT"],
            "pairs": [["AAPL", "MSFT"]],
            "groups": {
                "technology": {
                    "name": "Technology",
                    "sector": "Information Technology",
                    "tickers": ["AAPL", "MSFT"],
                    "pairs": [["AAPL", "MSFT"]],
                    "vol_driver": "tech-vol",
                }
            },
        }
        (preset_dir / "sp500.json").write_text(json.dumps(master))
        monkeypatch.setattr(pr, "PRESETS_DIR", preset_dir)
        monkeypatch.setattr(pr, "CHANGELOG_PATH", preset_dir / "changelog.json")
        return preset_dir

    def test_write_returns_positive_file_count(self, sp500_preset_dir):
        fresh = [
            {"ticker": "AAPL", "name": "Apple", "sector": "Information Technology",
             "sub_industry": "Technology"},
            {"ticker": "NVDA", "name": "NVIDIA", "sector": "Information Technology",
             "sub_industry": "Technology"},
        ]
        files_written = pr.update_sp500_presets(fresh, {"NVDA"}, {"MSFT"})
        assert files_written > 0

    def test_ticker_added_to_master_tickers(self, sp500_preset_dir):
        fresh = [
            {"ticker": "AAPL", "name": "Apple", "sector": "IT", "sub_industry": "Technology"},
            {"ticker": "NVDA", "name": "NVIDIA", "sector": "IT", "sub_industry": "Technology"},
        ]
        pr.update_sp500_presets(fresh, {"NVDA"}, set())
        master = json.loads((sp500_preset_dir / "sp500.json").read_text())
        assert "NVDA" in master["tickers"]

    def test_ticker_removed_from_master_tickers(self, sp500_preset_dir):
        fresh = [
            {"ticker": "AAPL", "name": "Apple", "sector": "IT", "sub_industry": "Technology"},
        ]
        pr.update_sp500_presets(fresh, set(), {"MSFT"})
        master = json.loads((sp500_preset_dir / "sp500.json").read_text())
        assert "MSFT" not in master["tickers"]

    def test_sub_preset_json_files_written(self, sp500_preset_dir):
        fresh = [
            {"ticker": "AAPL", "name": "Apple", "sector": "Information Technology",
             "sub_industry": "Technology"},
        ]
        pr.update_sp500_presets(fresh, set(), set())
        sub_files = list(sp500_preset_dir.glob("sp500-*.json"))
        assert len(sub_files) > 0


# ── 7. Preset-file rewrite — NDX100 ─────────────────────────────────

class TestUpdateNdx100Presets:
    @pytest.fixture
    def ndx100_preset_dir(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
        preset_dir = tmp_path / "presets"
        preset_dir.mkdir()
        master = {
            "name": "ndx100",
            "description": "NASDAQ 100",
            "tickers": ["AAPL", "AMZN"],
            "pairs": [["AAPL", "AMZN"]],
            "groups": {
                "misc-singles": {
                    "name": "Miscellaneous",
                    "tickers": ["AAPL", "AMZN"],
                    "pairs": [["AAPL", "AMZN"]],
                    "sector": "Multi",
                    "vol_driver": "misc",
                }
            },
        }
        (preset_dir / "ndx100.json").write_text(json.dumps(master))
        monkeypatch.setattr(pr, "PRESETS_DIR", preset_dir)
        monkeypatch.setattr(pr, "CHANGELOG_PATH", preset_dir / "changelog.json")
        return preset_dir

    def test_master_tickers_replaced_with_fresh(self, ndx100_preset_dir):
        fresh = [
            {"ticker": "AAPL", "name": "Apple", "sector": "IT", "sub_industry": "HW"},
            {"ticker": "NVDA", "name": "NVIDIA", "sector": "IT", "sub_industry": "GPU"},
        ]
        pr.update_ndx100_presets(fresh, {"NVDA"}, {"AMZN"})
        master = json.loads((ndx100_preset_dir / "ndx100.json").read_text())
        assert "NVDA" in master["tickers"]
        # Old ticker replaced by the full fresh list
        assert "AMZN" not in master["tickers"]

    def test_returns_positive_file_count(self, ndx100_preset_dir):
        fresh = [
            {"ticker": "AAPL", "name": "Apple", "sector": "IT", "sub_industry": "HW"},
        ]
        files_written = pr.update_ndx100_presets(fresh, set(), set())
        assert files_written > 0


# ── 8. Changelog append ──────────────────────────────────────────────

class TestLogChanges:
    @pytest.fixture
    def changelog_dir(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
        monkeypatch.setattr(pr, "CHANGELOG_PATH", tmp_path / "changelog.json")
        return tmp_path

    def test_creates_changelog_when_missing(self, changelog_dir):
        pr.log_changes("sp500", {"NVDA"}, {"AMZN"})
        entries = json.loads((changelog_dir / "changelog.json").read_text())
        assert len(entries) == 1
        assert entries[0]["index"] == "sp500"
        assert entries[0]["added"] == ["NVDA"]
        assert entries[0]["removed"] == ["AMZN"]

    def test_appends_to_existing_changelog(self, changelog_dir):
        pr.log_changes("sp500", {"NVDA"}, set())
        pr.log_changes("ndx100", {"TSLA"}, set())
        entries = json.loads((changelog_dir / "changelog.json").read_text())
        assert len(entries) == 2
        assert entries[0]["index"] == "sp500"
        assert entries[1]["index"] == "ndx100"

    def test_keeps_only_last_100_entries(self, changelog_dir):
        for i in range(105):
            pr.log_changes("sp500", {f"TICK{i}"}, set())
        entries = json.loads((changelog_dir / "changelog.json").read_text())
        assert len(entries) == 100

    def test_changelog_entry_has_counts(self, changelog_dir):
        pr.log_changes("r2k", {"A", "B"}, {"C"})
        entries = json.loads((changelog_dir / "changelog.json").read_text())
        entry = entries[0]
        assert entry["added_count"] == 2
        assert entry["removed_count"] == 1

    def test_invalid_existing_changelog_is_reset(self, changelog_dir):
        (changelog_dir / "changelog.json").write_text("NOT JSON {{{")
        pr.log_changes("sp500", {"NVDA"}, set())
        entries = json.loads((changelog_dir / "changelog.json").read_text())
        assert len(entries) == 1


# ── 9. create_daemon handler registration ────────────────────────────

class TestCreateDaemonHandlerRegistration:
    """Verify that create_daemon() registers PresetRebalanceHandler."""

    def test_preset_rebalance_handler_registered(self, tmp_path: Path):
        # Stub out state_file so load_state doesn't look for prod files.
        with patch(
            "monitor_daemon.run.STATE_FILE",
            tmp_path / "daemon_state.json",
        ):
            from monitor_daemon.run import create_daemon
            daemon = create_daemon()

        handler_names = [h.name for h in daemon.handlers]
        assert "preset_rebalance" in handler_names

    def test_expected_handlers_registered(self, tmp_path: Path):
        with patch(
            "monitor_daemon.run.STATE_FILE",
            tmp_path / "daemon_state.json",
        ):
            from monitor_daemon.run import create_daemon
            daemon = create_daemon()

        handler_names = set(h.name for h in daemon.handlers)
        expected = {
            "preset_rebalance",
            "flex_token_check",
            "replica-watchdog",
        }
        assert expected.issubset(handler_names)
