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
    """Build current-style Wikipedia markup with attributes and nested links."""
    inner_rows = ""
    for row in rows:
        ticker = row.get("ticker", "")
        name = row.get("name", "")
        sector = row.get("sector", "")
        sub = row.get("sub_industry", "")
        inner_rows += (
            f'<tr id="constituent-{ticker}" class="wikitable-row">'
            f"<td><a href='/wiki/{ticker}'>{ticker}</a></td>"
            f"<td>{name}</td>"
            f"<td>{sector}</td>"
            f"<td>{sub}</td>"
            f"</tr>\n"
        )
    header_row = (
        '<tr class="header"><th>Symbol</th><th>Security</th>'
        "<th>GICS Sector</th><th>GICS Sub-Industry</th></tr>\n"
    )
    unrelated = "<table><tr><th>Effective Date</th><th>Change</th></tr></table>"
    return f'{unrelated}<table id="constituents" class="wikitable">{header_row}{inner_rows}</table>'


# ── Helper: minimal fake Nasdaq API payload ──────────────────────────

def _make_ndx100_payload(rows: List[Dict[str, str]]) -> str:
    api_rows = [
        {"symbol": row.get("ticker", ""), "companyName": row.get("name", "")}
        for row in rows
    ]
    return json.dumps(
        {
            "data": {
                "totalrecords": len(api_rows),
                "date": "Jul 9, 2026",
                "data": {"rows": api_rows},
            },
            "status": {"rCode": 200},
        }
    )


# ── Helper: minimal fake BlackRock holdings payload ──────────────────

def _make_iwm_payload(rows: List[Dict[str, Any]]) -> str:
    columns = {
        "ticker": [row.get("ticker", "TICK") for row in rows],
        "issueName": [row.get("name", "Company Name") for row in rows],
        "sectorName": [row.get("sector", "Technology") for row in rows],
        "assetClass": [row.get("asset_class", "Equity") for row in rows],
        "holdingPercent": [str(row.get("weight", "0.05")) for row in rows],
    }
    points = {
        name: {"name": name, "value": values, "formattedValue": values}
        for name, values in columns.items()
    }
    points["asOfDate"] = {
        "name": "asOfDate",
        "value": 20260709,
        "formattedValue": "Jul 09, 2026",
    }
    return json.dumps(
        {
            "componentsByNameMap": {
                "holdings": {
                    "containersByNameMap": {
                        "all": {"dataPointsByNameMap": points}
                    }
                }
            }
        }
    )


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


class TestConstituentValidation:
    @staticmethod
    def _companies(prefix: str, count: int) -> List[Dict[str, Any]]:
        return [
            {
                "ticker": f"{prefix}{index:04d}",
                "name": f"Company {index}",
                "sector": "Technology",
                "sub_industry": "Software",
                "weight": 0.1,
            }
            for index in range(count)
        ]

    def test_empty_parse_is_rejected(self):
        with pytest.raises(pr.InvalidConstituentData, match="expected 450..550"):
            pr.validate_constituents("sp500", [], ["AAPL"] * 500)

    def test_years_from_wrong_wikipedia_table_are_rejected(self):
        rows = self._companies("N", 100)
        for offset, row in enumerate(rows):
            row["ticker"] = str(1926 + offset)

        with pytest.raises(pr.InvalidConstituentData, match="invalid tickers"):
            pr.validate_constituents("ndx100", rows, ["AAPL"] * 100)

    def test_valid_count_with_implausible_overlap_is_rejected(self):
        rows = self._companies("NEW", 500)
        current = [f"OLD{index:04d}" for index in range(500)]

        with pytest.raises(pr.InvalidConstituentData, match="overlap"):
            pr.validate_constituents("sp500", rows, current)

    def test_current_ndx_count_and_churn_are_accepted(self):
        current = [f"N{index:04d}" for index in range(101)]
        rows = self._companies("N", 94) + self._companies("NEW", 9)

        pr.validate_constituents("ndx100", rows, current)


class TestFailClosedExecution:
    @staticmethod
    def _companies(prefix: str, count: int) -> List[Dict[str, Any]]:
        return [
            {
                "ticker": f"{prefix}{index:04d}",
                "name": f"Company {index}",
                "sector": "Technology",
                "sub_industry": "Software",
                "weight": 0.1,
            }
            for index in range(count)
        ]

    @staticmethod
    def _master(name: str, tickers: List[str]) -> dict:
        if name == "r2k":
            return {"name": name, "tickers": tickers, "pairs": [], "groups": {}}
        return {
            "name": name,
            "description": name,
            "tickers": tickers,
            "pairs": [],
            "groups": {
                "software": {
                    "name": "Software",
                    "sector": "Technology",
                    "tickers": list(tickers),
                    "pairs": [],
                    "vol_driver": "software",
                }
            },
        }

    def test_handler_validates_all_sources_before_any_preset_write(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from monitor_daemon.handlers.preset_rebalance_handler import (
            PresetRebalanceHandler,
        )

        preset_dir = tmp_path / "presets"
        preset_dir.mkdir()
        sp500 = self._companies("S", 500)
        ndx100 = self._companies("N", 100)
        r2k = self._companies("R", 1900)
        for name, rows in (("sp500", sp500), ("ndx100", ndx100), ("r2k", r2k)):
            (preset_dir / f"{name}.json").write_text(
                json.dumps(self._master(name, [row["ticker"] for row in rows]))
            )

        sp500_fresh = list(sp500[:-1]) + [
            {
                "ticker": "SNEW",
                "name": "New Company",
                "sector": "Technology",
                "sub_industry": "Software",
                "weight": 0.1,
            }
        ]
        before = {path.name: path.read_bytes() for path in preset_dir.iterdir()}

        monkeypatch.setattr(pr, "PRESETS_DIR", preset_dir)
        monkeypatch.setattr(pr, "CHANGELOG_PATH", preset_dir / "changelog.json")
        monkeypatch.setattr(pr, "fetch_sp500", lambda: sp500_fresh)
        monkeypatch.setattr(pr, "fetch_ndx100", lambda: [])
        monkeypatch.setattr(pr, "fetch_r2k", lambda: r2k)

        handler = PresetRebalanceHandler()
        with patch.object(handler, "record_cycle_health"):
            result = handler.run()

        assert result["status"] == "error"
        assert "ndx100" in result["error"]
        assert handler.last_run is None
        after = {path.name: path.read_bytes() for path in preset_dir.iterdir()}
        assert after == before


def test_atomic_json_write_preserves_target_on_serialization_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(pr, "PRESETS_DIR", tmp_path)
    target = tmp_path / "preset.json"
    target.write_text('{"source":"canonical"}\n')

    with pytest.raises(TypeError):
        pr._write_json_atomic(target, {"bad": object()})

    assert target.read_text() == '{"source":"canonical"}\n'
    assert list(tmp_path.glob(".preset.json.*.tmp")) == []


def test_atomic_json_write_preserves_target_permissions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(pr, "PRESETS_DIR", tmp_path)
    target = tmp_path / "preset.json"
    target.write_text('{"source":"canonical"}\n')
    target.chmod(0o640)

    pr._write_json_atomic(target, {"source": "updated"})

    assert target.stat().st_mode & 0o777 == 0o640


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
        with patch(
            "monitor_daemon.handlers.preset_rebalance.urlopen",
            return_value=self._mock_response(html),
        ) as mocked_open:
            result = pr.fetch_sp500()
        tickers = [c["ticker"] for c in result]
        assert "AAPL" in tickers
        assert "MSFT" in tickers
        assert mocked_open.call_args.kwargs["timeout"] == 30

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
        html = _make_sp500_html(
            [
                {"ticker": "", "name": "No ticker", "sector": "IT", "sub_industry": "SW"},
                {"ticker": "MSFT", "name": "Microsoft", "sector": "IT", "sub_industry": "SW"},
            ]
        )
        with patch("monitor_daemon.handlers.preset_rebalance.urlopen",
                   return_value=self._mock_response(html)):
            result = pr.fetch_sp500()
        assert len(result) == 1
        assert result[0]["ticker"] == "MSFT"

    def test_strips_html_tags_from_ticker(self):
        html = _make_sp500_html(
            [{"ticker": "AAPL", "name": "Apple", "sector": "IT", "sub_industry": "HW"}]
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

    def test_rejects_html_without_semantic_constituent_headers(self):
        html = "<table><tr><th>Year</th><th>Change</th></tr><tr><td>2025</td></tr></table>"
        with patch(
            "monitor_daemon.handlers.preset_rebalance.urlopen",
            return_value=self._mock_response(html),
        ):
            with pytest.raises(pr.InvalidConstituentData, match="constituent table"):
                pr.fetch_sp500()


# ── 4. Fetcher parsing — NDX100 ──────────────────────────────────────

class TestFetchNdx100Parsing:
    def _mock_response(self, payload: str):
        mock = MagicMock()
        mock.read.return_value = payload.encode("utf-8")
        mock.__enter__ = lambda s: s
        mock.__exit__ = MagicMock(return_value=False)
        return mock

    def test_parses_basic_ticker_list(self):
        companies = [
            {"ticker": "AAPL", "name": "Apple", "sector": "Technology", "sub_industry": "HW"},
            {"ticker": "AMZN", "name": "Amazon", "sector": "Consumer", "sub_industry": "Retail"},
        ]
        payload = _make_ndx100_payload(companies)
        with patch("monitor_daemon.handlers.preset_rebalance.urlopen",
                   return_value=self._mock_response(payload)):
            result = pr.fetch_ndx100()
        tickers = [c["ticker"] for c in result]
        assert "AAPL" in tickers
        assert "AMZN" in tickers
        assert result[0]["name"] == "Apple"

    def test_deduplicates_tickers(self):
        companies = [
            {"ticker": "AAPL", "name": "Apple", "sector": "Technology", "sub_industry": "HW"},
            {"ticker": "AAPL", "name": "Apple dup", "sector": "Technology", "sub_industry": "HW"},
        ]
        payload = _make_ndx100_payload(companies)
        with patch("monitor_daemon.handlers.preset_rebalance.urlopen",
                   return_value=self._mock_response(payload)):
            result = pr.fetch_ndx100()
        assert len([c for c in result if c["ticker"] == "AAPL"]) == 1

    def test_rejects_html_instead_of_nasdaq_json(self):
        with patch(
            "monitor_daemon.handlers.preset_rebalance.urlopen",
            return_value=self._mock_response("<html>not json</html>"),
        ):
            with pytest.raises(pr.InvalidConstituentData, match="valid JSON"):
                pr.fetch_ndx100()

    def test_rejects_json_without_constituent_rows(self):
        with patch(
            "monitor_daemon.handlers.preset_rebalance.urlopen",
            return_value=self._mock_response('{"data": {}}'),
        ):
            with pytest.raises(pr.InvalidConstituentData, match="rows are missing"):
                pr.fetch_ndx100()


# ── 5. Fetcher parsing — BlackRock R2K JSON ──────────────────────────

class TestFetchR2kParsing:
    def _mock_response(self, content: str):
        mock = MagicMock()
        mock.read.return_value = content.encode("utf-8")
        mock.__enter__ = lambda s: s
        mock.__exit__ = MagicMock(return_value=False)
        return mock

    def test_parses_equity_rows(self):
        payload = _make_iwm_payload([
            {"ticker": "TICK1", "name": "Company One", "sector": "Technology",
             "asset_class": "Equity", "weight": "0.10"},
            {"ticker": "TICK2", "name": "Company Two", "sector": "Healthcare",
             "asset_class": "Equity", "weight": "0.05"},
        ])
        with patch(
            "monitor_daemon.handlers.preset_rebalance.urlopen",
            return_value=self._mock_response(payload),
        ) as mocked_open:
            result = pr.fetch_r2k()
        tickers = [c["ticker"] for c in result]
        assert "TICK1" in tickers
        assert "TICK2" in tickers
        assert mocked_open.call_args.kwargs["timeout"] == 30

    def test_skips_non_equity_rows(self):
        payload = _make_iwm_payload([
            {"ticker": "TICK1", "name": "Company One", "sector": "Technology",
             "asset_class": "Equity", "weight": "0.10"},
            {"ticker": "CASH1", "name": "Cash Collateral", "sector": "",
             "asset_class": "Cash", "weight": "0.01"},
        ])
        with patch("monitor_daemon.handlers.preset_rebalance.urlopen",
                   return_value=self._mock_response(payload)):
            result = pr.fetch_r2k()
        tickers = [c["ticker"] for c in result]
        assert "TICK1" in tickers
        assert "CASH1" not in tickers

    def test_skips_dash_tickers(self):
        payload = _make_iwm_payload([
            {"ticker": "-", "name": "Placeholder", "sector": "Technology",
             "asset_class": "Equity", "weight": "0.00"},
            {"ticker": "REAL", "name": "Real Co", "sector": "Healthcare",
             "asset_class": "Equity", "weight": "0.10"},
        ])
        with patch("monitor_daemon.handlers.preset_rebalance.urlopen",
                   return_value=self._mock_response(payload)):
            result = pr.fetch_r2k()
        tickers = [c["ticker"] for c in result]
        assert "REAL" in tickers
        assert "-" not in tickers

    def test_deduplicates_tickers(self):
        payload = _make_iwm_payload([
            {"ticker": "TICK1", "name": "Company One A", "sector": "Technology",
             "asset_class": "Equity", "weight": "0.10"},
            {"ticker": "TICK1", "name": "Company One B", "sector": "Technology",
             "asset_class": "Equity", "weight": "0.05"},
        ])
        with patch("monitor_daemon.handlers.preset_rebalance.urlopen",
                   return_value=self._mock_response(payload)):
            result = pr.fetch_r2k()
        assert len([c for c in result if c["ticker"] == "TICK1"]) == 1

    def test_filters_known_non_equity_names(self):
        payload = _make_iwm_payload([
            {"ticker": "FCASH", "name": "FUTURES Overlay", "sector": "",
             "asset_class": "Equity", "weight": "0.00"},
            {"ticker": "REAL", "name": "Real Company", "sector": "Energy",
             "asset_class": "Equity", "weight": "0.08"},
        ])
        with patch("monitor_daemon.handlers.preset_rebalance.urlopen",
                   return_value=self._mock_response(payload)):
            result = pr.fetch_r2k()
        tickers = [c["ticker"] for c in result]
        assert "REAL" in tickers
        assert "FCASH" not in tickers

    def test_rejects_html_instead_of_blackrock_json(self):
        with patch(
            "monitor_daemon.handlers.preset_rebalance.urlopen",
            return_value=self._mock_response("<html>not json</html>"),
        ):
            with pytest.raises(pr.InvalidConstituentData, match="valid JSON"):
                pr.fetch_r2k()

    def test_rejects_misaligned_holdings_columns(self):
        payload = json.loads(
            _make_iwm_payload(
                [
                    {
                        "ticker": "REAL",
                        "name": "Real Company",
                        "sector": "Energy",
                        "asset_class": "Equity",
                        "weight": "0.08",
                    }
                ]
            )
        )
        points = payload["componentsByNameMap"]["holdings"]["containersByNameMap"][
            "all"
        ]["dataPointsByNameMap"]
        points["issueName"]["formattedValue"] = []
        with patch(
            "monitor_daemon.handlers.preset_rebalance.urlopen",
            return_value=self._mock_response(json.dumps(payload)),
        ):
            with pytest.raises(pr.InvalidConstituentData, match="column lengths"):
                pr.fetch_r2k()


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
        assert master["source"].startswith("Nasdaq official Nasdaq-100 API")

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
        monkeypatch.setattr(pr, "PRESETS_DIR", tmp_path)
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
