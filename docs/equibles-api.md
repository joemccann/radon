# Equibles API - observed contract

Ground truth for Radon's Equibles integration. Every shape and unit below was **probed live**
against `https://api.equibles.com/v1` on 2026-08-11 from the Hetzner host, not read off the
docs prose. Where the published docs and the wire disagree, the wire wins and the disagreement
is called out.

Companion to `docs/unusual_whales_api.md`.

---

## Transport

| | |
|---|---|
| Base | `https://api.equibles.com/v1` |
| Auth | `Authorization: Bearer $EQUIBLES_API_KEY` |
| Key | repo-root `.env` (laptop) and `/etc/radon/env` mode `0640` root:radon (Hetzner; `~/radon-cloud/.env` is a compatibility symlink) |
| Plan | Pro. `X-RateLimit-Limit: 100000` / day, shared REST + MCP, resets 00:00 UTC |
| Methods | GET only |

Rate-limit headers on every counted response: `X-RateLimit-Limit`, `X-RateLimit-Remaining`,
`X-RateLimit-Reset` (unix seconds). `Retry-After` only on 429.

### Unknown query parameters are a hard 400

This is the single most important transport behaviour and it is not obvious from the docs:

```
GET /v1/cftc/positions/latest?limit=1
-> 400 {"error":{"code":"invalid_parameter",
        "message":"Unknown query parameter 'limit'. Supported query parameters: category.",
        "status":400}}
```

Unsupported params are **rejected**, not ignored. Never pass `limit`/`offset` speculatively.
Only send parameters confirmed in the endpoint table below. The error message helpfully
enumerates the supported set, so a 400 here is self-diagnosing.

### Error envelope

```json
{ "error": { "code": "invalid_parameter", "message": "...", "status": 400 } }
```

`400 invalid_parameter` · `401 unauthorized` · `404 not_found` · `429 rate_limited` ·
`500 internal_error` · `503 service_unavailable`

All GETs are safe to retry. Honor `Retry-After` on 429; exponential backoff on 5xx.

### Pagination

Endpoints that paginate return `meta: {limit, offset, count, hasMore}`. Advance `offset` by
`limit` while `hasMore`. **Each page is a separately billed request.** Endpoints that do *not*
return a `meta` block do not accept `limit`/`offset` at all (see the 400 rule above).

---

## ⛔ Units: the API mixes fractions and percents

The published docs claim ratio fields "come over the wire as fractions". **That is only true
for a subset.** The same underlying quantity is encoded differently depending on the endpoint.
Getting this wrong is a silent 100x error in either direction.

Confirmed by computing ground truth independently as `shortPosition / sharesOutstanding`:

| ticker | `squeeze.shortInterestPercentOfShares` | computed `shortPosition/sharesOutstanding` | ratio |
|---|---|---|---|
| NVDA | 0.01339 | 0.013391 (1.339%) | 1.000 |
| AAPL | 0.01004 | 0.009978 (0.998%) | 1.006 |

So `shortInterestPercentOfShares` is a **fraction**, while `/screener/stocks` reports the same
concept as `shortInterestPercent: 1.34`, a **percent**. Both describe ~1.34%.

### Fraction (0 to 1) - multiply by 100 to display

Currently all from `/short-squeeze-scores`:

- `shortInterestPercentOfShares`
- `shortInterestChangePercent`
- `shortVolumeShareTrend`
- `failsToDeliverPercentOfShares`
- `priceAboveVwap`

### Percent (0 to 100) - already display-ready, do NOT scale

- `shortVolumePercent` (`/stocks/{t}/short-volume`, `/short-volume/largest`) - observed 39.37 for NVDA.
  Scaling this would render 3937%.
- `percentOfTotal` (`/stocks/{t}/institutional-holders`)
- `changePercent` (`/stocks/{t}/institutional-ownership`)
- `percentOfUniverse` (`/13f/most-held`)
- `qoqChangePercent` (`/super-investors`)
- `shortInterestPercent`, `dividendYieldPercent`, `revenueGrowthYoYPercent`, `grossMarginPercent` (`/screener/stocks`)

### Percentile (0 to 100) - a rank, not a ratio. Never scale.

`shortInterestPercentile`, `daysToCoverPercentile`, `shortVolumeTrendPercentile`,
`shortInterestChangePercentile`, and the other `*Percentile` fields on `/short-squeeze-scores`.

### Unitless

`daysToCover` (a count of days; 2.47 for NVDA, agrees across both endpoints that report it),
`score`, `baseScore`, `catalystBoost`, all raw share and dollar counts.

**Rule for this codebase:** never infer a unit from a field name. Look it up in the table above.
A field ending in `Percent` proves nothing.

---

## Endpoints

Only parameters listed here are accepted. `meta` column indicates pagination support.

### Short data

| Endpoint | Params | meta | Response |
|---|---|---|---|
| `/stocks/{t}/off-exchange-volume` | `startDate`, `endDate`, `limit`, `offset` | yes | `data[]`: `weekStartDate`, `atsVolume`, `atsTradeCount`, `nonAtsOtcVolume`, `nonAtsOtcTradeCount`, `totalOffExchangeVolume` |
| `/stocks/{t}/short-volume` | `startDate`, `endDate`, `limit`, `offset` | yes | `data[]`: `date`, `shortVolume`, `shortExemptVolume`, `totalVolume`, `shortVolumePercent` |
| `/short-volume/largest` | liquidity filters, `sortBy` | no | `date`, `data[]`: `ticker`, `name`, `shortVolume`, `shortExemptVolume`, `totalVolume`, `shortVolumePercent` |
| `/stocks/{t}/short-interest` | `startDate`, `endDate`, `limit`, `offset` | yes | `data[]`: `settlementDate`, `shortPosition`, `changeInShortPosition`, `averageDailyVolume`, `daysToCover` |
| `/short-interest/snapshot` | liquidity filters, `sortBy`, `minDaysToCover` | no | `settlementDate`, `data[]`: `ticker`, `name`, `shortPosition`, `changeInShortPosition`, `averageDailyVolume`, `daysToCover` |
| `/short-squeeze-scores` | `ticker` (optional) | no | `settlementDate`, `scoredCount`, `total`, `data[]`: `ticker`, `rank`, `score`, `shortInterestPercentOfShares`, `daysToCover`, `shortVolumeShareTrend`, `shortInterestChangePercent`, `failsToDeliverPercentOfShares`, `priceAboveVwap`, `hasPriceSpikeCatalyst`, `hasVolumeSurgeCatalyst`, `hasEarningsProximityCatalyst`, `marketCapitalization`, `averageDailyDollarVolume`, `baseScore`, `catalystBoost`, `*Percentile` |

`off-exchange-volume` is weekly (FINRA ATS transparency). `short-interest` settles bi-monthly.
`short-volume` is daily.

The ATS venue-share sweep (`scripts/fetch_equibles_ats_venue_share.py`) bounds
each ticker fetch with an abandoned **daemon thread** (never a
ThreadPoolExecutor — CPython's atexit join on executor workers blocks
interpreter exit behind a tarpitted socket, REL-196/R-528). A
timeout/budget-dropped tail records a `state='error'` health row naming the
tickers, and their prior-snapshot series carry forward into the payload
(`carried_forward`) so a covered-only batch never silently replaces a fuller
snapshot (R-558).

### 13F institutional

| Endpoint | Params | meta | Response |
|---|---|---|---|
| `/stocks/{t}/institutional-holders` | `reportDate`, `limit`, `offset` | yes | `data[]`: `name`, `cik`, `shares`, `value`, `percentOfTotal`, `listedTicker`, `positionType`; `meta` also carries `reportDate`, `isCombinedQuarter`, `totalInstitutions`, `totalShares`, `totalValue` |
| `/stocks/{t}/institutional-ownership` | `periods` | no | `data[]`: `reportDate`, `institutions`, `totalShares`, `totalValue`, `changePercent`, `isCombinedQuarter` |
| `/stocks/{t}/institutional-activity` | `reportDate`, `limit` | no | `reportDate`, `previousReportDate`, `isFilingWindowOpen`, `buyers[]`/`sellers[]`: `name`, `cik`, `previousShares`, `currentShares`, `deltaShares`, `deltaValue`, `isNewPosition`, `isSoldOut`, `isFirst13F` |
| `/13f/most-held` | `sort`, `reportDate`, `limit` | no | `data[]`: `ticker`, `company`, `filerCount`, `deltaFilerCount`, `totalValue`, `deltaValue`, `percentOfUniverse` + coverage flags |
| `/13f/market-activity` | `bucket`, `reportDate`, `limit` | no | `data[]`: `ticker`, `company`, `deltaShares`, `deltaValue`, `filerCount` |
| `/super-investors` | none | no | `data[]`: `managerName`, `firmName`, `cik`, `portfolioValue`, `positionCount`, `qoqChangePercent`, `asOf`, `isStale` |

**Vintage flags matter.** `isFilingWindowOpen`, `comparisonAvailable`, `isRankingAvailable`,
`comparisonUnavailableReason`, `isWithinCoverage`, `isStale` are all returned so the consumer can
tell "no change" from "quarter not comparable yet". Surface them; do not silently render a
half-filed quarter as a real QoQ delta. 13F carries a ~45 day lag and fails Gate 2 on its own.

### CFTC COT

| Endpoint | Params | meta | Response |
|---|---|---|---|
| `/cftc/contracts` | query/alias | no | `data[]`: `marketCode`, `marketName`, `category` (35 contracts) |
| `/cftc/positions/latest` | `category` **only** | no | `data[]`: `marketCode`, `marketName`, `category`, `reportDate`, `openInterest`, `commNet`, `nonCommNet` |
| `/cftc/contracts/{marketCode}/positions` | `startDate`, `endDate`, `limit`, `offset` | yes | weekly COT history, newest first |

Categories: `Agriculture`, `Energy`, `Metals`, `EquityIndices`, `InterestRates`, `Currencies`.
`EquityIndices` returns 5 contracts. Note the field names are `commNet` / `nonCommNet`, not the
longer names the docs prose implies.

COT reports Tuesday positions and publishes Friday afternoon ET, so the freshest possible row is
structurally ~3 days old. Derive all freshness copy from `reportDate`.

### Filing forensics

| Endpoint | Params | meta | Response |
|---|---|---|---|
| `/stocks/{t}/proposed-sales` | `limit`, `offset` | yes | `data[]`: `filingDate`, `sellerName`, `relationshipToIssuer`, `sharesToBeSold`, `aggregateMarketValue`, `approxSaleDate`, `brokerName`, `sharesOutstanding`, `remarks` |
| `/stocks/{t}/atm-programs` | none | no | `ticker`, `companyName`, `programs[]` (empty for NVDA - see below) |
| `/stocks/{t}/buyback-programs` | none | no | `authorizedAmount`, `authorizedUnit`, `authorizedAsOf`, `remainingAuthorizedAmount`, `remainingAuthorizedUnit`, `programExpires`, `dollarsAreCombinedEquity`, `programs[]`, `yearToDate{}`, `annualHistory[]`, `quarterlyHistory[]` |
| `/stocks/{t}/executive-changes` | `action`, `startDate`, `endDate`, `limit`, `offset` | yes | `data[]`: `filedDate`, `personName`, `role`, `roleText`, `action`, `effectiveDate`, `disclosure`, `form`, `filingUrl`; plus `coverage{processedFilingCount, coverageStartDate, coverageEndDate}` |
| `/screener/stocks` | many filters, `limit` | page-based | `totalCount`, `count`, `page`, `totalPages`, `vintages{shortInterestSettlementDate, thirteenFQuarterEnd}`, `data[]` |

Form 144 (`proposed-sales`) is **forward looking**: filed before the sale. Form 4, which
`fetch_informed_flow.py` already covers via UW, is filed after. That distinction is the point of
the feature.

`remainingAuthorizedAmount` came back `null` for NVDA even though `authorizedAmount` was populated.
Null means "not disclosed in the latest filing", **not** zero remaining. Never coerce null to 0 -
that would render an exhausted buyback where an active one exists.

Empty `programs[]` on `atm-programs` means no ATM shelf on file, which is genuinely good news
(no dilution overhang). A failed request also produces no programs. These must never render the
same way. Distinguish "no filings found" from "fetch failed" explicitly.

`/screener/stocks` uses page-based paging (`page` / `totalPages`), not `meta.hasMore`.

---

## Cadence

Derive all freshness copy from the payload's own date field. Never hardcode.

| Data | Real cadence | Date field |
|---|---|---|
| Short volume | daily | `date` |
| Off-exchange / ATS | weekly | `weekStartDate` |
| Short interest, squeeze scores | bi-monthly settlement | `settlementDate` |
| COT | weekly, Tue data published Fri | `reportDate` |
| 13F | quarterly, ~45 day lag | `reportDate` / `vintages.thirteenFQuarterEnd` |
| Filings | as filed | `filedDate` / `filingDate` |

---

## Quota exhaustion is fatal to a cycle, not to one source

`EquiblesAuthError` and `EquiblesRateLimitError` make **every** subsequent call
fail, so both jobs re-raise them out of their per-source error handling rather
than degrading one section:

- `fetch_equibles_smart_money_13f._cycle_fatal_errors()` — `_safe_call` re-raises.
- `fetch_equibles_filing_forensics._cycle_fatal_errors()` — `_fetch_source` re-raises.

The two lists are asserted equal
(`scripts/tests/test_equibles_quota_and_completeness.py`). Swallowing them
turned one 429 into `STATUS_ERROR` for every source of every remaining ticker,
marked those tickers `skipped`, and still heartbeat `ok` because one ticker had
landed before the trip — exit 0 and a green banner over 29 of 30 stale
dossiers (R-226).

An ordinary endpoint fault still degrades only its own source. That
distinction is the whole point: a thin day and a dead integration must not
produce the same health row.

## Holder rows are written all-or-nothing

`_upsert_holder_rows` writes N chunked multi-row INSERTs under one commit. A
failure on chunk *k* used to leave chunks 0..*k*-1 applied and *k*..N absent —
and because the upsert is not preceded by a delete, that truncated set mixed
silently with the previous quarter's surviving rows while
`equibles_13f_snapshots` (which carries the full `holders` array and
`holder_count`) asserted completeness. The write now rolls back as a unit, and
the snapshot carries `holders_persisted` so a consumer can tell when the depth
rows are behind the summary (R-227).

---

## Reproducing this document

The probe scripts that produced these observations are not committed (they read the production
key). To re-derive: `GET` each endpoint above with no parameters, record the top-level keys and
the first element of each array, and re-run the fraction cross-check by comparing
`/short-squeeze-scores?ticker=T` against `shortPosition / sharesOutstanding`.
