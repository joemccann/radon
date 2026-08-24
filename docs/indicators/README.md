# Indicators

Owner specs for regime tabs and the cheap-wing scanner. Add a row here when a spec ships. Do not copy signal math into the root README.

| Slug | Route | Service | Spec |
|---|---|---|---|
| vol-cone | `/scanner?mode=vol-cone` | `vol-cone`, `vol-cone-intraday` | [vol-cone.md](vol-cone.md) |
| skew | `/regime/skew` | `skew` | [skew.md](skew.md) |
| skew2d | `/regime/skew2d` | `skew2d` | [skew2d.md](skew2d.md) |
| straddle | `/regime/straddle` | `straddle` | [straddle.md](straddle.md) |
| cor | `/regime/cor` | `cor` | [cor.md](cor.md) |
| vixcor | `/regime/vixcor` | `vixcor` | [vixcor.md](vixcor.md) |
| ivrank | `/regime/ivrank` | `ivrank` | [ivrank.md](ivrank.md) |
| curve | `/regime/curve` | `yield-curve` | [curve.md](curve.md) |
| credit | `/regime/credit` | `credit-spread` | [credit.md](credit.md) |
| iei-hyg | `/regime/iei-hyg` | `iei-hyg` | [iei-hyg.md](iei-hyg.md) |
| trin | `/regime/trin` | `trin` | [trin.md](trin.md) (spec; build in flight) |
| divyield | `/regime/divyield` | `div-yield` | [divyield.md](divyield.md) |
| hyad | `/regime/hyad` | `hy-ad` | [hyad.md](hyad.md) |

`vol-cone` is the only indicator with two writers: an EOD run that stores the completed session and a 15m live pass that re-ranks today's chain against it (`is_intraday`), so the tab is tradeable during the session rather than a day stale.

`/regime/vol-cone` redirects to the scanner. Pattern for a new indicator: `.claude/skills/new-indicator/SKILL.md`.

`vol-cone` is the only indicator that deep-links into the ORDER BUILDER: a
`CHEAP_WINGS` / `CHEAP_ATM` row links to
`/{TICKER}?deck=c&expiry=…&src=vol-cone&legs=…`, and the chain labels the
builder `PREFILLED FROM VOL CONE` off that `src`. Any other `src` falls back to
`PREFILLED FROM THETA HARVESTER`, so a new indicator that prefills the builder
must add its own `src` value rather than reuse one.

A price-series indicator whose every source (IB, UW, Yahoo) fails must re-serve
its cache as `status: "stale_source"` with an `error` heartbeat, never a fresh `ok`
over unconfirmed data: the watchdog gates purely on the 26h heartbeat window, so an
`ok` here pins it open through a permanent outage. Pattern: `fetch_ivrank._serve_cached`,
mirrored by [ivrank.md](ivrank.md), [credit.md](credit.md) and [iei-hyg.md](iei-hyg.md).

A derived indicator must tell an explained parent lag apart from a broken
parent, or a spent UW daily cap fails its unit and pages a P1 every run. See
[skew2d.md](skew2d.md) "Parent lag — stale vs embargoed".

Every UW-backed indicator shares one daily-cap breaker,
`scripts/utils/uw_embargo.py`. Give a new writer its own sidecar file through
`UwEmbargo(service, path_source)`; do not re-implement the reset arithmetic.
