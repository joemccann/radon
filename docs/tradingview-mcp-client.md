# TradingView MCP research client

## Scope and contract

Radon owns a private, read-only research client for the official
`https://mcp.tradingview.com/mcp` Streamable HTTP server. Existing Ultimate access
is sufficient. This is separate from the webhook receiver and does not replace
IB execution or the existing price-provider order.

Use the already pinned Python `mcp==1.28.1` SDK for protocol negotiation and
OAuth discovery, PKCE, dynamic client registration, refresh and session framing.
Do not copy Robinhood's provider-specific token endpoint or scrape browser tokens.
HTTP operations and complete research calls have finite deadlines.

### Authentication

`scripts/tradingview_mcp.py auth` is an explicit local operator operation.
It opens official browser consent and accepts the OAuth callback only on a
temporary 127.0.0.1 listener. SDK state validation and PKCE are mandatory.
The loopback callback avoids adding a public auth-exempt endpoint to Radon.
This replaces the speculative app.radon.run callback requirement for this
single-operator first release. Production receives its own protected token file
through the existing operator credential deployment mechanism.

Persist OAuth tokens, expiry and registered client metadata atomically in a
0600 regular file configured by `TRADINGVIEW_MCP_TOKEN_FILE`, defaulting to
`data/tradingview_mcp_token.json` (gitignored). Never log credentials, callback
codes, state, authorization URLs or provider exception bodies. Normal data calls
never open browser consent: missing credentials are an explicit unconfigured
state; expired access tokens refresh automatically when a valid refresh grant
exists. Expired credentials without refresh or rejected grants require explicit
operator reauthorization.

### Client interface

`TradingViewClient` is an async client with `call_tool(name, arguments)`,
`list_tools()`, `search_symbols(query, type_filter="all")` and
`get_ohlcv(symbol, interval="1D", count=300)`. It uses the authentication module's
`build_oauth_provider(token_file=None, interactive=False)`.
Client and auth modules must import without initiating network, consent or SDK
initialization. Missing optional SDK is a coded dependency error on use.

Deny every tool except the explicit, documented research allowlist. Exclude all
alert and watchlist mutations and `get_active_watchlist` (can create/activate
lists despite its read-only label). Discover available tools and honor their
input schemas without widening the allowlist. No news story bodies or alert fire
messages in this first release. Structured responses or JSON text content are
accepted; `isError`, partial provider errors and malformed payloads stay errors.
No fabricated zeroes or swallowed auth failures. Empty search/calendar arrays
remain legitimate empty results; empty OHLCV is unusable history and stays an error.

OHLCV output has `source="tradingview"`, exact `symbol`, `interval`,
`fetched_at`, `requested_count`, `returned_count`, `history_complete=false`
and sorted `bars` of `{timestamp, open, high, low, close, volume}`.
Reject nonfinite numbers, boolean numeric values, inconsistent OHLC, duplicates
and malformed timestamps. Volume may be null. Unix seconds remain the source
timestamp; never invent an exchange session date from a UTC midnight.
Bound count to 1..5000; the vendor documents no historical date cursor, so
truncated windows never claim all-time coverage or a confirmed SPX all-time high.

### Radon surfaces

The operator CLI exposes `status`, `auth`, `tools`, `search`, `ohlcv`,
and `call` with JSON stdout and safe diagnostics. It is directly usable by
ingestion scripts and other private operator tools. Client helpers and a
protected FastAPI research router provide integration without Next subprocesses:
`GET /research/tradingview/status`, `GET /research/tradingview/symbols`,
`GET /research/tradingview/ohlcv`, `POST /research/tradingview/call`.
All retain existing JWT middleware, are excluded from public share surfaces,
and use private/no-store caching. Remote data calls are classified read.spawn
for the assistant request budget; status is read. Unconfigured responses are
HTTP 200 with `missing=true`; rate limits/auth/network errors have distinct
bounded error codes. Credentials are never returned.

TradingView serves explicit research reads for unique coverage. Existing
commodity price priority remains IB > Robinhood > UW > specialized official >
Yahoo. This release does not insert TradingView into automatic price failover;
the integration roadmap's proposed position remains future work.

## Verification plan

Record failing tests before modules exist. Test OAuth file permissions, atomic
updates and expiry, blocked interactive consent in unattended calls, protocol
initialization/tool calls, allowlist rejection before network, schema validation,
structured/text/error payloads, timeouts/rate limits, and OHLCV normalization.
Exercise actual SDK against an in-process fake server or mock HTTP transport.
Test CLI and API usable paths, missing credentials and private caching/auth
registration. Luna runs serial focused checks followed by relevant broader gates.
Live operator consent and authenticated symbol/bar probes are separate evidence;
mocked tests do not claim live data. Capture only sanitized source payloads.

## Record-high breadth dependency

The requested indicator still needs validated paired NYSE high/low universes,
source finalization, legal storage/display scope and historical continuity.
TradingView exports presently reach 2001 or 2005 for candidate breadth pairs;
they do not establish 1999/1929 events or match the supplied chart counts.
The MCP client makes direct probes possible without treating missing history as
valid negative signal days. Indicator UI/timers/storage follow the indicator
skills after these source gates pass.

## Primary reference

[TradingView official MCP documentation](https://www.tradingview.com/mcp/docs),
checked 2026-10-07. Vendor beta schemas may evolve; live tool discovery is required.

## Verification evidence, 2026-10-07

Sol 6.1 implemented the client and authentication modules. Luna 6 independently
exercised SDK discovery, registration, PKCE, state rejection, credential refresh,
and the protected CLI/API surfaces. Final focused verification passes 186 tests;
combined statement/branch coverage is 99% (client 100%, auth 99%, API 100%, CLI 96%).
Input and output schemas reject remote references before SDK validation, keeping
schema resolution inside the client's network boundary. Regression tests proved
remote output references attempted retrieval before the repair.

Complete Python roots: scripts 13,062 passed, 2 skipped; API 1,212 passed; trade
blotter 25 passed; cloud 2,692 passed, 7 skipped, 3 failed. Two cloud setgid-mode
assertions reproduce unchanged at base `1a0b888c4` on macOS. The Caddy startup
timeout passes in both targeted base runs. These remain separate from the focused
green client verification. Full JavaScript verification passes 10,437 tests across
1,054 files, with 21 tests and one file skipped. Upstream exact-head CI receipts
are recorded on the pull request before delivery.

Live official OAuth discovery reaches TradingView's consent page. A missing
User-Agent on SDK-created metadata requests caused HTTP 403; stamped
`User-Agent: radon/2.0` requests reach the official metadata endpoint (HTTP 200).
No authenticated tool call is claimed until operator consent and live probes pass.

## Operator setup and research

Install the pinned project dependencies with Python 3.13. Authenticate locally
with the existing TradingView account:

```sh
python3.13 scripts/tradingview_mcp.py status
python3.13 scripts/tradingview_mcp.py auth
python3.13 scripts/tradingview_mcp.py tools
python3.13 scripts/tradingview_mcp.py search "NYSE 52 week highs" --type-filter index
python3.13 scripts/tradingview_mcp.py ohlcv INDEX:MAHN --count 5000
python3.13 scripts/tradingview_mcp.py call get_economic_symbols --arguments '{"country":"US","search":"inflation"}'
```

These are private research reads. Exact breadth candidates must be resolved and
compared as pairs before choosing a series. The CLI does not store research data
automatically. For production, configure `TRADINGVIEW_MCP_TOKEN_FILE` outside the
checkout, keep its directory private and its file owned by the service user with
mode 0600; transfer through the operator's secure credential deployment channel.
Do not paste credentials into shell arguments, tickets, Git, screenshots or logs.
The installed client refreshes credentials; a rejected grant requires running
explicit consent again. Status reports configuration and expiry, not successful
upstream access. A live symbol/bar read is the connection check.

Private API examples:

- `GET /research/tradingview/status`
- `GET /research/tradingview/symbols?query=SPX&type_filter=index`
- `GET /research/tradingview/ohlcv?symbol=SP:SPX&interval=1D&count=300`
- `POST /research/tradingview/call` with
  `{"tool":"get_economic_symbols","arguments":{"country":"US"}}`

They retain Radon's JWT perimeter and return `Cache-Control: private, no-store`.
Source-specific responses include permission/licensing metadata as provided;
availability through MCP does not authorize public redistribution.
