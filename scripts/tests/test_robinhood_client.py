"""Robinhood MCP client — read-only contract, unconfigured skip, token
refresh, and parsing.

Robinhood ranks BELOW IB / UW / Cboe and ABOVE Yahoo. These tests pin the
properties the ladder integration depends on:

  1. Unconfigured (no access AND no refresh token) is a clean, network-free
     no-op.
  2. The client is READ-ONLY: any tool outside the documented read
     allowlist — every place_* / cancel_* order-write — is refused
     client-side before any I/O. Execution stays on IB.
  3. Tokens never leak into reprs, logs, or exception text.
  4. Access tokens expire ~3 days: the client refreshes through the official
     token endpoint (grant_type=refresh_token, public client, no secret),
     persists rotated tokens to a 0600 file atomically, and a rejected
     grant degrades to the unconfigured skip instead of crashing ladders.
"""
from __future__ import annotations

import json
import os
import stat
import sys
import time
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
    TOKEN_ENDPOINT,
    RobinhoodAuthError,
    RobinhoodClient,
    RobinhoodClientError,
    RobinhoodNotConfiguredError,
    RobinhoodReadOnlyError,
    RobinhoodTokenStore,
    fetch_robinhood_closes,
    fetch_robinhood_quote,
    robinhood_configured,
)


@pytest.fixture(autouse=True)
def isolated_token_state(monkeypatch, tmp_path):
    """Every test starts with no env credentials, a tmp token file path,
    and the process-level refresh kill switch reset."""
    monkeypatch.setattr(rh, "_refresh_disabled", False)
    monkeypatch.setenv("ROBINHOOD_MCP_TOKEN_FILE", str(tmp_path / "rh-mcp.json"))
    monkeypatch.delenv("ROBINHOOD_MCP_TOKEN", raising=False)
    monkeypatch.delenv("ROBINHOOD_MCP_REFRESH_TOKEN", raising=False)
    monkeypatch.delenv("ROBINHOOD_MCP_CLIENT_ID", raising=False)
    monkeypatch.delenv("ROBINHOOD_MCP_URL", raising=False)
    return tmp_path / "rh-mcp.json"


@pytest.fixture
def unconfigured():
    """Alias for readability — the autouse fixture already cleared the env."""
    return None


@pytest.fixture
def no_network(monkeypatch):
    """Any HTTP attempt fails the test — unconfigured must never touch the wire."""
    def _boom(*_a, **_k):
        raise AssertionError("network I/O attempted while Robinhood is unconfigured")

    monkeypatch.setattr("requests.Session.post", _boom)
    monkeypatch.setattr("requests.Session.get", _boom)
    monkeypatch.setattr(rh.requests, "post", _boom)


def _write_token_file(path: Path, **fields) -> None:
    path.write_text(json.dumps(fields), encoding="utf-8")
    os.chmod(path, 0o600)


class _FakeResponse:
    def __init__(self, status_code=200, payload=None, text="", headers=None):
        self.status_code = status_code
        self._payload = payload
        self.text = text if text else (json.dumps(payload) if payload is not None else "")
        self.content = self.text.encode()
        self.headers = headers or ({"Content-Type": "application/json"} if payload is not None else {})

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


def _mcp_responder(expected_bearer, calls=None, reject_bearers=()):
    """A fake MCP `Session.post` that 401s stale bearers and answers
    initialize / notification / tools-call for the expected one."""
    def post(url, json=None, headers=None, timeout=None):  # noqa: A002 - requests kwarg
        if calls is not None:
            calls.append({"url": url, "payload": json, "headers": dict(headers or {})})
        bearer = (headers or {}).get("Authorization", "")
        if bearer in {f"Bearer {b}" for b in reject_bearers}:
            return _FakeResponse(status_code=401, text="expired_token")
        assert bearer == f"Bearer {expected_bearer}", bearer
        if json.get("method") == "initialize":
            return _FakeResponse(payload={"jsonrpc": "2.0", "id": json["id"], "result": {}})
        if json.get("method") == "notifications/initialized":
            return _FakeResponse(status_code=202, text="")
        return _FakeResponse(payload={
            "jsonrpc": "2.0", "id": json["id"],
            "result": {"structuredContent": {"results": [{"symbol": "SPY"}]}},
        })
    return post


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
    """Payload shapes are probed, never assumed — parse defensively."""

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


class TestTokenRefresh:
    """Access tokens expire ~3 days: refresh is mandatory in production.

    Official protocol only: POST https://api.robinhood.com/oauth2/token/ with
    grant_type=refresh_token + refresh_token + client_id, form-encoded,
    NO client secret (public client, token_endpoint_auth_method=none).
    """

    def _token_endpoint(self, monkeypatch, calls, *, status=200, payload=None, text=""):
        payload = payload if payload is not None else {
            "access_token": "fresh-access",
            "token_type": "Bearer",
            "expires_in": 259200,
            "refresh_token": "refresh-2",
        }

        def post(url, data=None, headers=None, timeout=None):
            assert url == TOKEN_ENDPOINT
            calls.append({"data": dict(data or {}), "headers": dict(headers or {})})
            if status >= 400:
                return _FakeResponse(status_code=status, text=text or json.dumps(payload))
            return _FakeResponse(payload=payload)

        monkeypatch.setattr(rh.requests, "post", post)

    def test_refresh_before_expiry_hits_the_official_endpoint(
        self, monkeypatch, isolated_token_state
    ):
        _write_token_file(
            isolated_token_state,
            access_token="stale-access", refresh_token="refresh-1",
            client_id="cid-public", token_type="Bearer",
            expires_at=time.time() - 10,
        )
        refreshes: list = []
        mcp_calls: list = []
        self._token_endpoint(monkeypatch, refreshes)

        client = RobinhoodClient()
        monkeypatch.setattr(client._session, "post", _mcp_responder("fresh-access", mcp_calls))
        rows = client.call_tool("get_equity_quotes", {"symbols": ["SPY"]})

        assert rows == {"results": [{"symbol": "SPY"}]}
        assert len(refreshes) == 1
        form = refreshes[0]["data"]
        assert form == {
            "grant_type": "refresh_token",
            "refresh_token": "refresh-1",
            "client_id": "cid-public",
        }
        assert "client_secret" not in form, "public client: no secret, ever"
        assert refreshes[0]["headers"]["Content-Type"] == "application/x-www-form-urlencoded"
        assert all(
            c["headers"]["Authorization"] == "Bearer fresh-access" for c in mcp_calls
        ), "the stale access token must never reach the MCP"

    def test_refresh_on_401_retries_once_with_the_new_token(
        self, monkeypatch, isolated_token_state
    ):
        # No expiry metadata: the token looks usable until the MCP 401s it.
        _write_token_file(
            isolated_token_state,
            access_token="revoked-access", refresh_token="refresh-1",
            client_id="cid-public", token_type="Bearer",
        )
        refreshes: list = []
        mcp_calls: list = []
        self._token_endpoint(monkeypatch, refreshes)

        client = RobinhoodClient()
        monkeypatch.setattr(
            client._session, "post",
            _mcp_responder("fresh-access", mcp_calls, reject_bearers=["revoked-access"]),
        )
        rows = client.call_tool("get_equity_quotes", {"symbols": ["SPY"]})

        assert rows == {"results": [{"symbol": "SPY"}]}
        assert len(refreshes) == 1
        bearers = [c["headers"]["Authorization"] for c in mcp_calls]
        assert bearers[0] == "Bearer revoked-access"
        assert set(bearers[1:]) == {"Bearer fresh-access"}

    def test_refresh_persists_rotated_tokens_atomically_at_0600(
        self, monkeypatch, isolated_token_state
    ):
        _write_token_file(
            isolated_token_state,
            access_token="stale-access", refresh_token="refresh-1",
            client_id="cid-public", token_type="Bearer",
            expires_at=time.time() - 10,
        )
        refreshes: list = []
        self._token_endpoint(monkeypatch, refreshes)

        before = time.time()
        assert RobinhoodTokenStore().refresh() == "fresh-access"

        state = json.loads(isolated_token_state.read_text())
        assert state["access_token"] == "fresh-access"
        assert state["refresh_token"] == "refresh-2", "rotated refresh must replace the old one"
        assert state["client_id"] == "cid-public"
        assert state["expires_at"] == pytest.approx(before + 259200, abs=30)
        mode = stat.S_IMODE(isolated_token_state.stat().st_mode)
        assert mode == 0o600, oct(mode)
        assert not isolated_token_state.with_name(
            isolated_token_state.name + ".tmp"
        ).exists(), "atomic write must not leave a temp file behind"

    def test_first_configured_run_bootstraps_the_file_from_env(
        self, monkeypatch, isolated_token_state
    ):
        monkeypatch.setenv("ROBINHOOD_MCP_TOKEN", "env-access")
        monkeypatch.setenv("ROBINHOOD_MCP_REFRESH_TOKEN", "env-refresh")
        monkeypatch.setenv("ROBINHOOD_MCP_CLIENT_ID", "cid-public")
        assert not isolated_token_state.exists()

        client = RobinhoodClient()
        assert client._resolve_token() == "env-access"

        state = json.loads(isolated_token_state.read_text())
        assert state["access_token"] == "env-access"
        assert state["refresh_token"] == "env-refresh"
        assert state["client_id"] == "cid-public"
        assert stat.S_IMODE(isolated_token_state.stat().st_mode) == 0o600

    def test_invalid_grant_degrades_to_the_unconfigured_skip(
        self, monkeypatch, isolated_token_state, capsys
    ):
        # Refresh-only credentials: the first use must refresh, get refused,
        # and every ladder falls through to Yahoo without crashing.
        _write_token_file(
            isolated_token_state,
            refresh_token="refresh-dead", client_id="cid-public", token_type="Bearer",
        )
        refreshes: list = []
        self._token_endpoint(
            monkeypatch, refreshes, status=400,
            text='{"error": "invalid_grant"}',
        )

        assert robinhood_configured() is True
        assert fetch_robinhood_closes(["SPY"]) == {}
        assert robinhood_configured() is False, (
            "a rejected grant must mark Robinhood unconfigured for this process"
        )
        # And the next helper never even builds a client.
        assert fetch_robinhood_closes(["SPY"]) == {}
        assert len(refreshes) == 1, "no refresh retry storm after invalid_grant"
        err = capsys.readouterr().err
        assert "refresh-dead" not in err, "token values must never be logged"

    def test_refresh_error_text_never_contains_tokens(
        self, monkeypatch, isolated_token_state
    ):
        _write_token_file(
            isolated_token_state,
            refresh_token="refresh-super-secret", client_id="cid-public",
        )
        refreshes: list = []
        self._token_endpoint(
            monkeypatch, refreshes, status=400,
            text='{"error": "invalid_grant", "echo": "refresh-super-secret"}',
        )

        with pytest.raises(RobinhoodAuthError) as excinfo:
            RobinhoodTokenStore().refresh()
        assert "refresh-super-secret" not in str(excinfo.value)
        assert "[REDACTED]" in str(excinfo.value)

    def test_transient_endpoint_error_does_not_disable_the_process(
        self, monkeypatch, isolated_token_state
    ):
        _write_token_file(
            isolated_token_state,
            refresh_token="refresh-1", client_id="cid-public",
        )
        refreshes: list = []
        self._token_endpoint(monkeypatch, refreshes, status=503, text="upstream sad")

        with pytest.raises(RobinhoodClientError):
            RobinhoodTokenStore().refresh()
        assert robinhood_configured() is True, (
            "a 5xx is transient — the next cycle must try again"
        )

    def test_refresh_only_credentials_count_as_configured(self, isolated_token_state):
        _write_token_file(
            isolated_token_state,
            refresh_token="refresh-1", client_id="cid-public",
        )
        assert robinhood_configured() is True

    def test_no_access_and_no_refresh_stays_unconfigured_no_network(
        self, no_network
    ):
        assert robinhood_configured() is False
        assert fetch_robinhood_closes(["SPY"]) == {}


class TestAdversarialHardening:
    """Pins for the attack surfaces found in review: truncation-bisected
    token leaks, refresh storms/races, and fabricated price values."""

    # ── redaction survives truncation ────────────────────────────

    def test_mcp_error_truncation_cannot_leak_a_bisected_token(self, monkeypatch):
        token = "ZZSECRETZZ" * 4
        client = RobinhoodClient(token=token)

        class _Resp:
            status_code = 500
            headers: dict = {}
            content = b"x"
            # Token starts at char 295: a naive [:300] slice BEFORE the
            # scrub keeps the first 5 chars of the secret and drops the
            # rest, so the exact-match replace no longer fires.
            text = "x" * 295 + token

        monkeypatch.setattr(client._session, "post", lambda *a, **k: _Resp())
        with pytest.raises(RobinhoodClientError) as excinfo:
            client._post({"jsonrpc": "2.0", "id": 1, "method": "x"})
        assert "ZZSECRET" not in str(excinfo.value)

    def test_refresh_error_truncation_cannot_leak_a_bisected_token(
        self, monkeypatch, isolated_token_state
    ):
        refresh = "RRSECRETRR" * 4
        _write_token_file(
            isolated_token_state, refresh_token=refresh, client_id="cid",
        )

        def post(url, data=None, headers=None, timeout=None):
            return _FakeResponse(status_code=400, text="y" * 195 + refresh)

        monkeypatch.setattr(rh.requests, "post", post)
        with pytest.raises(RobinhoodAuthError) as excinfo:
            RobinhoodTokenStore().refresh()
        assert "RRSECRET" not in str(excinfo.value)

    # ── fabricated prices ────────────────────────────────────────

    def test_boolean_and_nonfinite_prices_are_rejected(self):
        assert rh._to_price(True) is None, "float(True) == 1.0 fabricates a $1 print"
        assert rh._to_price("inf") is None
        assert rh._to_price(float("inf")) is None
        assert rh._to_price("nan") is None
        assert rh._to_price(-5) is None
        assert rh._to_price("641.10") == pytest.approx(641.10)

    def test_quote_helper_rejects_boolean_and_inf_payloads(self, monkeypatch):
        monkeypatch.setenv("ROBINHOOD_MCP_TOKEN", "tok")
        for poisoned in ({"last": True}, {"last": "inf"}, {"last": float("nan")}):
            monkeypatch.setattr(
                rh.RobinhoodClient, "get_equity_quotes",
                lambda self, symbols, _p=poisoned: [_p],
            )
            assert fetch_robinhood_quote("SPY") is None, poisoned

    def test_closes_parser_rejects_boolean_and_inf_closes(self):
        assert rh._closes_from_historicals(
            [{"date": "2026-08-27", "close": True}]
        ) == {}
        assert rh._closes_from_historicals(
            [{"date": "2026-08-27", "close": "inf"}]
        ) == {}

    # ── refresh storms and races ─────────────────────────────────

    def test_mcp_rejecting_a_fresh_token_disables_the_process(
        self, monkeypatch, isolated_token_state
    ):
        """A token minted seconds ago being 401'd is structural. Without the
        kill switch, a 500-ticker garch sweep would hit the token endpoint
        once per symbol."""
        _write_token_file(
            isolated_token_state,
            refresh_token="refresh-1", client_id="cid", token_type="Bearer",
        )
        refreshes: list = []
        monkeypatch.setattr(
            rh.requests, "post",
            lambda url, data=None, headers=None, timeout=None: (
                refreshes.append(dict(data)) or _FakeResponse(payload={
                    "access_token": "fresh-access", "expires_in": 259200,
                })
            ),
        )

        client = RobinhoodClient()
        monkeypatch.setattr(
            client._session, "post",
            lambda *a, **k: _FakeResponse(status_code=401, text="revoked"),
        )
        with pytest.raises(RobinhoodAuthError):
            client.call_tool("get_equity_quotes", {"symbols": ["SPY"]})

        assert len(refreshes) == 1, "exactly one refresh, no storm"
        assert robinhood_configured() is False, (
            "fresh-token rejection must degrade the process to the "
            "unconfigured skip"
        )
        assert fetch_robinhood_closes(["SPY"]) == {}

    def test_concurrent_refreshers_spend_the_refresh_token_once(
        self, monkeypatch, isolated_token_state
    ):
        """Rotation race: the loser must re-read under the lock and reuse
        the winner's rotated tokens instead of spending the (single-use)
        old refresh token a second time."""
        _write_token_file(
            isolated_token_state,
            access_token="stale", refresh_token="refresh-1",
            client_id="cid", expires_at=time.time() - 10,
        )
        endpoint_calls: list = []
        monkeypatch.setattr(
            rh.requests, "post",
            lambda url, data=None, headers=None, timeout=None: (
                endpoint_calls.append(dict(data)) or _FakeResponse(payload={
                    "access_token": "fresh-access", "expires_in": 259200,
                    "refresh_token": "refresh-2",
                })
            ),
        )

        loser = RobinhoodTokenStore()   # snapshots the stale state first
        winner = RobinhoodTokenStore()
        assert winner.refresh() == "fresh-access"
        assert loser.refresh() == "fresh-access"

        assert len(endpoint_calls) == 1, (
            "the second refresher must short-circuit on the re-read, "
            "never POST the already-spent refresh token"
        )

    def test_ladder_helpers_use_the_bounded_timeout(self, monkeypatch):
        """4 POSTs x LADDER_TIMEOUT_S must stay inside the ~30s per-rung
        worst case the portfolio_risk budget math documents."""
        assert rh.LADDER_TIMEOUT_S * 4 <= 30

        monkeypatch.setenv("ROBINHOOD_MCP_TOKEN", "tok")
        captured: dict = {}
        real_client = rh.RobinhoodClient

        class Spy(real_client):
            def __init__(self, *args, **kwargs):
                captured.update(kwargs)
                super().__init__(*args, **kwargs)

            def fetch_daily_closes(self, symbol):
                return {}

            def get_equity_quotes(self, symbols):
                return []

        monkeypatch.setattr(rh, "RobinhoodClient", Spy)
        fetch_robinhood_closes(["SPY"])
        assert captured.get("timeout") == rh.LADDER_TIMEOUT_S
        captured.clear()
        fetch_robinhood_quote("SPY")
        assert captured.get("timeout") == rh.LADDER_TIMEOUT_S

    # ── secret residue on disk ───────────────────────────────────

    def test_token_file_and_write_artifacts_are_gitignored(self):
        import subprocess

        repo_root = SCRIPTS_DIR.parent
        for artifact in (
            "data/rh_mcp_token.json",
            "data/rh_mcp_token.json.tmp",
            "data/rh_mcp_token.json.lock",
        ):
            result = subprocess.run(
                ["git", "check-ignore", "-q", artifact],
                cwd=repo_root,
            )
            assert result.returncode == 0, f"{artifact} is not gitignored"

    def test_loose_file_permissions_warn_without_leaking_values(
        self, isolated_token_state, capsys
    ):
        isolated_token_state.write_text(
            json.dumps({"access_token": "tok-open-perm", "refresh_token": "r"}),
            encoding="utf-8",
        )
        os.chmod(isolated_token_state, 0o644)

        RobinhoodTokenStore()

        err = capsys.readouterr().err
        assert "too open" in err
        assert "tok-open-perm" not in err
