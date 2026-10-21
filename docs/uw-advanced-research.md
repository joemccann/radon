# UW Advanced research snapshots

The research adapter adds provider context without changing Interactive Brokers
quotes, execution, position marks, risk capital or evaluation gates. All REST
requests use `UWAdvancedClient`, a subclass of the shared governed `UWClient`.
The route names and parameter contracts were checked against the official
[OpenAPI document](https://api.unusualwhales.com/api/openapi) on 2026-10-21.

## Collection and reads

- `python3.13 scripts/uw_research.py --ticker SPY --json` refreshes five default
  sections: multi-leg prints, ticker Options Pulse, volatility context, company
  profile and machine-readable seasonality. Fresh sections skip collection.
- `--sections` selects one to six registered names. `SECTION_NAMES` is the
  authoritative allowlist. An invocation has a twelve-adapter-call budget,
  configurable by the Python caller within a hard maximum of 24. This count is
  not the provider's charged HTTP count; the shared governor owns that evidence.
- `--scheduled --json` reads a validated CSV `RADON_UW_RESEARCH_TICKERS`, default
  `SPY,QQQ`, maximum ten tickers. The managed timer may run hourly; sections retain
  their individual TTLs, so issuer and historical datasets are not polled hourly.
- Transcript collection requires `--quarter YYYYQ1` through `YYYYQ4`; the issuer's
  fiscal quarter is never guessed. Futures requires both `--futures-contract ESZ6`
  and a verified numeric `--security-id` to bind the security definition to the
  dated contract. These optional sections do not activate themselves.
- `read_research(ticker)` returns the latest atomic snapshot without provider
  calls. Each section ages independently; refreshing company data cannot make an
  old flow observation fresh. Public/demo clients must not receive this data
  under the operator's personal subscription.

`collect_research` and `read_research` return schema 1 with `ticker`, `source`,
`generated_at`, `status`, `sections` and `limitations`. Every section contains
`state`, `data`, `source`, `observed_at`, `as_of`, `expires_at` and its request
identity. The identity is versioned and includes ticker, section and its parameters
(fiscal quarter, dated CME contract/security ID, or fixed analytics/macro
settings). Legacy observations without the exact identity cannot satisfy TTL
reuse or be retained as stale for another request. `observed_at` is ingestion time. `as_of` is unavailable unless backed by
an explicit adapter contract; provider timestamps remain unchanged in the payload.

Authentication, entitlement denial, rate limiting, malformed schema and exhausted
request budget have distinct safe `error_code` values. A failed refresh retains
matching old data as stale with its original observation/expiry timestamps. It
never replaces missing data with zero or a successful empty observation. Provider
exception strings, URLs and response error bodies do not enter snapshots or stdout.

## Integrity and bounded storage

Snapshots live at `data/uw_research/TICKER.json`, written by `atomic_io` with
checksums and fsync. A per-ticker advisory lock serializes refresh/read/modify/write;
a competing refresh waits at most one second. There is a 2 MiB per-response limit
and 4 MiB aggregate snapshot limit. Successful refresh attempts retain first-seen
snapshots under `history/TICKER/`, capped at 64 snapshots per ticker and 128 MiB for
the entire research archive. These are retention bounds, not promises of a fixed
historical lookback. No future-availability assumption may be inferred from a
refetched historical provider date.

## Dataset contracts

- Multi-leg queries use explicit ordered UTC windows no longer than 24 hours,
  two 50-row pages, and enrich at most three strategy IDs with legs. `coverage`
  reports truncation and range. Debit/credit derives only from signed decimal
  `net_price`; leg bid/ask is aggressor context, not a held buy/sell direction.
  Vendor spreads are observed market classifications, not executable combo quotes.
- Options Pulse maintains the provider's daily/interval distinction and Nasdaq
  scope. It does not replace required next-session cleared OI corroboration.
- Volatility context/anomaly/character and stance retain raw units and component
  scores. Descriptive fit scores do not become Kelly probabilities.
- Seasonality is numeric UW context. Population, corporate-action adjustment,
  sample period and month-return definition must be compared before replacing
  an EquityClock vision-derived win rate.
- Issuer estimates/transcripts, corporate actions, bounded fixed/sliding analytics,
  and economic series are optional low-cadence sections. The adapter also exposes
  explicit listing, commodity and FX-history methods for source-ordered callers.
- CME display factors multiply **raw integer stream prices** only. REST block prices
  are already displayed. A factor is not applied twice; block NBBO may be stale or
  unavailable and is never used to fabricate an aggressor direction. Broker prices
  and executions remain IB-owned.

## Validation

`test_uw_advanced_research.py` covers API query bounds, path confinement, exact signed
decimals, source/entitlement states, stale retention, dataset TTLs, request limits,
explicit fiscal/contract identity, checksum rejection, archival bounds and competing
refreshes. Suites run on GitHub runners; local work uses static compilation only.
