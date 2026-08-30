"""Robinhood MCP client — read-only contract, unconfigured skip, parsing.

Robinhood ranks BELOW IB / UW / Cboe and ABOVE Yahoo. These tests pin the
three properties the ladder integration depends on:

  1. Unconfigured (no ROBINHOOD_MCP_TOKEN) is a clean, network-free no-op.
  2. The client is READ-ONLY: any tool outside the documented read
     allowlist — every place_* / cancel_* order-write — is refused
     client-side before any I/O. Execution stays on IB.
  3. The token never leaks into reprs or exception text.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from clients import robinhood_client as rh  # noqa: E402
from clients.robinhood_client import (  # noqa: E402
    DEFAULT_MCP_URL,
    EQUITY_QUOTES_BATCH_MAX,
    READ_ONLY_TOOLS,
    RobinhoodClient,
    RobinhoodClientError,
    RobinhoodNotConfiguredError,
    RobinhoodReadOnlyError,
    fetch_robinhood_closes,
    fetch_robinhood_quote,
    robinhood_configured,
)


@pytest.fixture
def unconfigured(monkeypatch):
    monkeypatch.delenv("ROBINHOOD_MCP_TOKEN", raising=False)
    monkeypatch.delenv("ROBINHOOD_MCP_URL", raising=False)


@pytest.fixture
def no_network(monkeypatch):
    """Any HTTP attempt fails the test — unconfigured must never touch the wire."""
    def _boom(*_a, **_k):
        raise AssertionError("network I/O attempted while Robinhood is unconfigured")

    monkeypatch.setattr("requests.Session.post", _boom)
    monkeypatch.setattr("requests.Session.get", _boom)


class TestUnconfiguredSkip:
    def test_configured_is_false_without_token(self, unconfigured):
        assert robinhood_configured() is False
        assert RobinhoodClient().is_configured() is False

    def test_fetch_closes_returns_empty_with_no_network(self, unconfigured, no_network):
        assert fetch_robinhood_closes(["SPY", "HYG"]) == {}

    def test_fetch_quote_returns_none_with_no_network(self, unconfigured, no_network):
        assert fetch_robinhood_quote("SPY") is None
        assert fetch_robinhood_quote("VIX", index=True) is None

    def test_call_tool_raises_not_configured_before_any_io(self, unconfigured, no_network):
        with pytest.raises(RobinhoodNotConfiguredError):
            RobinhoodClient().call_tool("get_equity_quotes", {"symbols": ["SPY"]})

    def test_default_url_is_the_official_trading_mcp(self, unconfigured):
        assert DEFAULT_MCP_URL == "https://agent.robinhood.com/mcp/trading"
        assert RobinhoodClient()._url == DEFAULT_MCP_URL


class TestReadOnlyContract:
    """Radon never writes through Robinhood. Execution stays on IB."""

    @pytest.mark.parametrize("tool", [
        "place_equity_order",
        "place_option_order",
        "place_crypto_order",
        "cancel_equity_order",
        "cancel_option_order",
        "replace_equity_order",
        "transfer_funds",
    ])
    def test_write_tools_are_refused_before_any_io(self, tool, no_network):
        client = RobinhoodClient(token="tok-secret")
        with pytest.raises(RobinhoodReadOnlyError):
            client.call_tool(tool, {})

    def test_allowlist_contains_no_write_verbs(self):
        for tool in READ_ONLY_TOOLS:
            assert not tool.startswith(("place_", "cancel_", "replace_", "modify_",
                                        "create_", "update_", "delete_", "transfer_",
                                        "submit_", "set_")), tool
            assert tool.startswith(("get_", "run_")), tool

    def test_documented_read_tools_are_allowlisted(self):
        assert {
            "get_equity_quotes", "get_equity_historicals", "get_equity_price_book",
            "get_option_chains", "get_option_instruments", "get_option_quotes",
            "get_option_historicals", "get_index_quotes",
            "get_popular_watchlists", "get_watchlists", "get_watchlist_items",
            "get_scans", "get_scanner_filter_specs", "run_scan",
            "get_earnings_calendar", "get_earnings_results",
        } <= READ_ONLY_TOOLS


class TestSecretHygiene:
    def test_repr_never_contains_the_token(self):
        client = RobinhoodClient(token="tok-super-secret")
        assert "tok-super-secret" not in repr(client)

    def test_http_error_text_is_redacted(self, monkeypatch):
        client = RobinhoodClient(token="tok-super-secret")

        class _Resp:
            status_code = 500
            headers: dict = {}
            content = b"x"
            text = "boom Bearer tok-super-secret boom"

        monkeypatch.setattr(client._session, "post", lambda *a, **k: _Resp())
        with pytest.raises(RobinhoodClientError) as excinfo:
            client._post({"jsonrpc": "2.0", "id": 1, "method": "x"})
        assert "tok-super-secret" not in str(excinfo.value)
        assert "[REDACTED]" in str(excinfo.value)


class TestQuoteBatching:
    def test_equity_quotes_chunk_at_the_documented_20_symbol_max(self, monkeypatch):
        client = RobinhoodClient(token="tok")
        calls: list[list[str]] = []
        monkeypatch.setattr(
            client, "call_tool",
            lambda name, args: calls.append(list(args["symbols"]))
            or [{"symbol": s} for s in args["symbols"]],
        )

        symbols = [f"S{i:02d}" for i in range(45)]
        rows = client.get_equity_quotes(symbols)

        assert [len(c) for c in calls] == [20, 20, 5]
        assert all(len(c) <= EQUITY_QUOTES_BATCH_MAX for c in calls)
        assert len(rows) == 45


class TestPayloadParsing:
    """The option/equity payload schema is unpublished — parse defensively."""

    def test_closes_from_plausible_shapes(self):
        for payload in (
            [{"begins_at": "2026-08-27T00:00:00Z", "close_price": "12.5"}],
            [{"date": "2026-08-27", "close": 12.5}],
            [{"timestamp": "2026-08-27", "last_trade_price": 12.5}],
        ):
            assert rh._closes_from_historicals(payload) == {"2026-08-27": 12.5}

    def test_unreadable_shapes_yield_empty_not_raise(self):
        assert rh._closes_from_historicals([{"foo": 1}, {"close": "n/a", "date": "x"}]) == {}
        assert rh._result_rows({"unexpected": {"nested": True}}) == []
        assert rh._result_rows("not json rows") == []

    def test_result_rows_probe_known_list_keys(self):
        assert rh._result_rows({"results": [{"a": 1}]}) == [{"a": 1}]
        assert rh._result_rows({"data_points": [{"a": 1}]}) == [{"a": 1}]
        assert rh._result_rows([{"a": 1}, "junk"]) == [{"a": 1}]

    def test_sse_body_yields_the_rpc_result_message(self):
        body = (
            "event: message\n"
            'data: {"jsonrpc":"2.0","method":"notifications/progress"}\n'
            "\n"
            'data: {"jsonrpc":"2.0","id":1,"result":{"ok":true}}\n'
        )
        assert rh._parse_sse_response(body) == {
            "jsonrpc": "2.0", "id": 1, "result": {"ok": True},
        }


class TestLadderHelpers:
    def test_fetch_closes_uses_the_client_when_configured(self, monkeypatch):
        monkeypatch.setenv("ROBINHOOD_MCP_TOKEN", "tok")
        monkeypatch.setattr(
            rh.RobinhoodClient, "fetch_daily_closes",
            lambda self, symbol: {"2026-08-27": 100.0} if symbol == "SPY" else {},
        )
        assert fetch_robinhood_closes(["SPY", "EMPTY"]) == {
            "SPY": {"2026-08-27": 100.0}
        }

    def test_fetch_closes_swallows_client_errors(self, monkeypatch):
        monkeypatch.setenv("ROBINHOOD_MCP_TOKEN", "tok")

        def _raise(self, symbol):
            raise RobinhoodClientError("upstream 502")

        monkeypatch.setattr(rh.RobinhoodClient, "fetch_daily_closes", _raise)
        assert fetch_robinhood_closes(["SPY"]) == {}

    def test_fetch_quote_reads_first_plausible_price_field(self, monkeypatch):
        monkeypatch.setenv("ROBINHOOD_MCP_TOKEN", "tok")
        monkeypatch.setattr(
            rh.RobinhoodClient, "get_equity_quotes",
            lambda self, symbols: [{"symbol": symbols[0], "last_trade_price": "641.10"}],
        )
        assert fetch_robinhood_quote("SPY") == pytest.approx(641.10)

    def test_fetch_index_quote_uses_the_index_tool(self, monkeypatch):
        monkeypatch.setenv("ROBINHOOD_MCP_TOKEN", "tok")
        monkeypatch.setattr(
            rh.RobinhoodClient, "get_index_quotes",
            lambda self, symbols: [{"symbol": symbols[0], "last": 15.2}],
        )
        monkeypatch.setattr(
            rh.RobinhoodClient, "get_equity_quotes",
            lambda self, symbols: (_ for _ in ()).throw(AssertionError("equity tool used")),
        )
        assert fetch_robinhood_quote("VIX", index=True) == pytest.approx(15.2)
