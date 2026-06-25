"""Tests for scanner.py refactor — DB-sourced inputs and direct imports."""
from pathlib import Path
from unittest.mock import patch

import scanner
from scanner import fetch_flow_data


class TestScannerSourceInputs:
    """Scanner should not rely on flat JSON source files."""

    def test_no_legacy_source_file_constants(self):
        assert not hasattr(scanner, "WATCHLIST")
        assert not hasattr(scanner, "PORTFOLIO")

    def test_source_mentions_no_legacy_flat_files(self):
        source = (Path(__file__).resolve().parent.parent / "scanner.py").read_text()
        assert "watchlist.json" not in source
        assert "portfolio.json" not in source


class TestFetchFlowDataDirect:
    """Verify scanner uses direct function import instead of subprocess."""

    @patch("scanner.fetch_flow_module")
    def test_fetch_flow_data_calls_module_directly(self, mock_module):
        """fetch_flow_data should call fetch_flow.fetch_flow directly, not subprocess."""
        mock_module.return_value = {"ticker": "AAPL", "dark_pool": {}}
        result = fetch_flow_data("AAPL", days=5)
        mock_module.assert_called_once_with(
            "AAPL",
            lookback_days=5,
            skip_options_flow=True,
            fetch_missing_history=False,
            retry_transient=False,
        )
        assert result == {"ticker": "AAPL", "dark_pool": {}}

    @patch("scanner.fetch_flow_module")
    def test_fetch_flow_data_handles_exception(self, mock_module):
        """If the imported function raises, return error dict."""
        mock_module.side_effect = Exception("API timeout")
        result = fetch_flow_data("AAPL", days=5)
        assert "error" in result

    @patch("scanner.fetch_flow_module")
    def test_fetch_flow_data_returns_data(self, mock_module):
        """On success, return the flow data dict."""
        expected = {
            "ticker": "GOOG",
            "dark_pool": {"aggregate": {"flow_direction": "ACCUMULATION"}},
        }
        mock_module.return_value = expected
        result = fetch_flow_data("GOOG", days=3)
        assert result == expected
