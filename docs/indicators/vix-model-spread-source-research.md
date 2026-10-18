# VIX model spread: source research

Researched 2026-10-18. This is source diligence, not an implementation or release.

## Verified definition and units

The requested descriptive approximation is:

`model_vix = sqrt(cor1m / 100) * vixeq`

`spread = vix - model_vix`

VIX and VIXEQ are annualized volatility percentage points; COR1M is quoted in
percentage index points, so 10.07 means correlation 0.1007. Dividing COR1M by
100 is mandatory before taking its square root. This model is the requested
heuristic, not an official Cboe fair-value calculation or forecast.

Cboe's [launch announcement](https://ir.cboe.com/news/news-details/2024/Cboe-Global-Markets-and-SP-Dow-Jones-Indices-Plan-to-Launch-New-Cboe-SP-500-Constituent-Volatility-Index-VIXEQ/default.aspx)
identifies VIXEQ as a market-cap-weighted 30-day single-stock implied-volatility
measure using the S&P 500 Dispersion Basket. S&P DJI's
[primary explanation](https://www.indexologyblog.com/2024/11/05/a-vix-for-single-stocks-is-alive-and-ticking/)
specifies a capitalization-weighted root-mean-square construction. It is not
equal-weight VIX, realized volatility, an ETF IV, or IV from one stock.

The COR1M and VIXEQ constituent sets and volatility constructions differ, so
the positive model basis is not an arbitrage identity. The model must retain
its historical spread baseline rather than classify every positive spread as
an overshoot. Confidence: high on index identity and numerical reproduction;
the predictive interpretation remains unestablished.

## Exact transport and schema

Three official Cboe HTTPS CSVs were fetched successfully with honest
`User-Agent: radon/2.0`, no authentication and no browser impersonation:

| Symbol | Exact source | Schema | Rows | First date | Last date |
| --- | --- | --- | ---: | --- | --- |
| VIX | https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv | `DATE,OPEN,HIGH,LOW,CLOSE` | 9296 | 1990-01-02 | 2026-10-16 |
| COR1M | https://cdn.cboe.com/api/global/us_indices/daily_prices/COR1M_History.csv | `DATE,OPEN,HIGH,LOW,CLOSE` | 5230 | 2006-01-03 | 2026-10-16 |
| VIXEQ | https://cdn.cboe.com/api/global/us_indices/daily_prices/VIXEQ_History.csv | `DATE,VIXEQ` | 3101 | 2014-06-19 | 2026-10-16 |

All returned HTTP 200. Dates are `MM/DD/YYYY`; normalize to ISO calendar dates.
Read CLOSE for VIX/COR1M, and the VIXEQ column for VIXEQ. Join only identical
session dates, with no forward-filling, mismatched-day current values, or proxy
substitution. The observed common intersection is 3101 sessions beginning
2014-06-19. A chart starting in 2017 is possible from these official files.

Captured raw files and HTTP metadata are transient research evidence under
`/tmp/radon-vix-model-source-research-20261018/`; no raw provider fixture was
added to Git while use rights remain unresolved.

### Historical seams

VIXEQ launched on 2024-11-04. S&P DJI expressly describes its earlier history
back to 2014 as hypothetical/back-tested. COR1M's official family is backfilled
to 2006; the existing COR documentation notes pre-2022 synthetic OHLC equal to
the close. Use closes only; disclose back-tested history rather than imply
live publication existed in 2014. No old annual ICJ/JCJ symbol splice is needed
for the observed COR1M CSV.

## Reproducing the screenshot and the window mismatch

The official 2026-10-16 closes are VIX 19.99, VIXEQ 41.71 and COR1M 10.07.
They produce model VIX **13.235944124617633** and spread
**6.754055875382365**, matching the screenshot's rounded 13.24 and 6.75.

Rolling statistics below include the latest session and use population
standard deviation (`statistics.pstdev`, ddof=0). Values were calculated from
the captured same-date intersection, not estimated from the image.

| Sessions | Mean spread | Population SD | Mean + 2 SD | Mean - 2 SD | Z-score |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 20 | 4.327848928602837 | 0.7021606855738206 | 5.732170299750478 | 2.9235275574551958 | 3.4553443344621044 |
| 252 | 3.9495507725205607 | 0.9161620918088602 | 5.781874956138282 | 2.1172265889028403 | 3.061145105146864 |

The screenshot's mean 3.95 and bands 5.78/2.12 match **252 sessions**, while the
user's text requests a **20-day** moving average. Do not label the 3.95 mean
20-day. An implementation can honor the explicit 20-session default and offer
a clearly labeled 252-session comparison; the specification must record that
choice. Compute the two z-scores independently and identify each window.

An overshoot state can be defined as spread strictly greater than its selected
window mean plus two SDs, with null statistics during warmup or zero SD.
Do not describe the state as proof of future VIX decline or a trade signal.

## Publication cadence and honest sampling

These are daily session histories, not an intraday combined indicator. Cboe's
[VIX historical-data page](https://www.cboe.com/tradable_products/vix/vix_historical_data)
labels the VIX file updated daily. No exact publication-time SLA for all three
CSV files was located in the checked primary pages.

Observed response metadata on 2026-10-18:

- VIX Last-Modified: `Sat, 17 Oct 2026 01:11:02 GMT`.
- VIXEQ Last-Modified: `Sat, 17 Oct 2026 01:11:02 GMT`.
- COR1M Last-Modified: `Fri, 16 Oct 2026 21:31:02 GMT`.
- All three: `Cache-Control: max-age=60, s-maxage=900, stale-while-revalidate=900`.

These timestamps are observations, not guaranteed publication times.
`scripts/clients/cboe_client.py` already supports conditional GET and records
that Cboe can touch Last-Modified without appending a session. A successful
sample or HTTP 304 must heartbeat the job without making the data date newer.
The freshness rail should show the common data date separately from collection
time and the exact next configured sampling slot. A daily timer after 01:11 UTC
with slack is plausible; its final slot must be checked against existing timers
and must not be advertised as a guaranteed source release time.

## Existing provider checks and priority

The applicable hierarchy is IB, Robinhood, UW, specialized official Cboe,
Yahoo as a last resort. Existing source-specific evidence:

- `scripts/cri_scan.py` attempts IB before fallbacks and implements the
  official COR1M dashboard history anchor. Its `official_cor1m_previous_close`
  explains observed stale IB COR1M bars/tick-9 close; `docs/indicators/cor.md`
  records this established official-source exception.
- `scripts/clients/cboe_client.py` serves the exact static CSV transport needed
  here. `scripts/fetch_vixts.py` and `scripts/fetch_cor.py` establish daily
  same-provider history patterns. No new HTTP client is necessary.
- Robinhood's equity historicals do not provide this Cboe index family; no
  option-chain IV from Robinhood should substitute for VIXEQ.
- A read-only production `/health` preflight reported Gateway
  `auth_state=authenticated` and a connected data pool. Three bounded pooled
  `/historical/bars` requests, for IND/CBOE VIXEQ, COR1M and VIX, duration 10 D,
  all returned HTTP 500 immediately. **IB coverage and entitlement are unknown**
  from this failed API probe; this is not evidence the broker lacks VIXEQ.
  No Gateway restart, new broker socket, or order operation was used.
- Existing UW credentials were tested with requests, no retries, against
  `GET /api/stock/VIXEQ/ohlc/1d`, COR1M and VIX. VIXEQ and VIX returned HTTP
  422, code `cboe_index_access_missing`, saying provisioned access and Cboe
  approval are required. COR1M returned HTTP 422 invalid path input. The
  present UW entitlement therefore does not supply the needed VIXEQ history.
  Credentials and account identifiers are not included in this document.
- No Yahoo request was made. A Yahoo price proxy cannot establish Cboe usage
  rights or replace the exact requested single-stock volatility series.

## Licensing decision: personal, non-commercial use confirmed

Cboe's [website terms, section 2](https://www.cboe.com/terms/) permit a copy for
personal non-commercial use, while reserving other storage, display and
derived-work uses absent consent or an applicable exception. Its
[use-of-content page](https://www.cboe.com/use-of-content/) provides the consent
route. The [official feed licensing page](https://www.cboe.com/data/global-indices-feed/)
provides an index-data license request and subscriber agreements. Public HTTP
200 access is not evidence of storage/display permission.

Existing `docs/indicators/cor.md`, `straddle.md` and `vixts.md` record an adopted
internal single-operator position. `vixts.md` itself acknowledges no explicit
public grant covering storage or derived works. No Cboe consent or executed
license was found in the checked repository documentation/config paths.
This is not proof no agreement exists outside the repository.

On 2026-10-18 the user explicitly confirmed personal, non-commercial use only. Production data must be operator-only, never live-data demo fixtures or public sharing. Only three-session factual parser samples are committed, with attribution; bulk source captures stay outside Git.

The initial research phase held implementation pending scope clarification.
The user's explicit personal, non-commercial confirmation resolves that task
gate. The implementation will enforce operator-only access and omit public
share/demo data and bulk source fixtures. This does not assert a commercial
or redistribution license.

Confidence: high on published terms and current UW response; unknown on any
off-repository Radon agreement and on IB's own index entitlement.

## Implementation readiness after the source decision

Proposed identifiers: slug/service `vix-model-spread`, component
`VixModelSpread`, tab label `VIX MODEL`. The next free migration on
the inspected base is 0098; recheck before implementation. This research task did not create indicator code, schema, timer or production rows.
Three-session parser samples were added by the parent implementation after scope confirmation.

Any later implementation needs finite/nonnegative input validation, same-date
joins, full warmup, zero-SD handling, population-SD tests, both real CSV schemas,
missing-date tests, conditional-GET/no-change heartbeat tests, and an explicit
selected window in the payload and UI. Avoid rounding model/spread before
computing rolling statistics.
