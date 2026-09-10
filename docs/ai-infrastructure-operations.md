# AI infrastructure collection operations

The `/regime/llm` AI infrastructure view reads the compact `ai_cycle_api_snapshot` row written by collection. Browser requests never scrape providers, scan observation history, or place trades. Rebuild that snapshot with `python3.13 -m scripts.ai_cycle --persist-snapshot`. All automatic source collection runs through `python3.13 -m scripts.ai_cycle` from the repository root. `radon-ai-cycle-backfill.timer` resumes historical coverage at 05:30 UTC, `radon-aa-frontier-refresh.timer` refreshes the model cohort at 07:00 UTC, then `radon-ai-cycle.timer` collects current observations at 07:15 UTC.

## Collection and verification

```sh
# Public-source verification; raw snapshots only, no database writes.
python3.13 -m scripts.ai_cycle --verify --sources vercel,gpu-rental

# Isolated local ingestion for research/UI verification.
python3.13 -m scripts.ai_cycle --record --database /tmp/ai-cycle.sqlite --sources vercel,gpu-rental

# Production ingestion; explicit write mode, default configured sources.
python3.13 -m scripts.ai_cycle --record
```

Omitting both modes is equivalent to `--verify`. `--record` without `--database` uses the production store, unless `RADON_AI_CYCLE_DB_PATH` selects an isolated SQLite file. Production cycles emit bounded service-health heartbeats, including enabled-source failures and unchanged cycles. Verification and isolated SQLite cycles never write production health state. Missing credentials are visible unavailable states; HTTP failures and schema errors remain explicit. Failed source observations are never replaced by zero. Raw evidence is staged and fsynced in its archive directory, digest-verified, then atomically replaced; a truncated content-addressed artifact is repaired by the next matching collection.

`--env-file PATH` reads only `OPENROUTER_API_KEY`, `ARTIFICIAL_ANALYSIS_API_KEY`, `SEC_USER_AGENT`, `EIA_API_KEY`, `VAST_API_KEY`, and `RADON_AI_CYCLE_AA_BASKET`. Environment variables take precedence over that file. When `RADON_SECRET_STORE_PATH` is configured, values saved under **Profile → Credentials → LLM Regime Sources** take final precedence. The scheduled service loads the same encrypted store and master key as the API, so Profile rotations apply on its next run without editing `/etc/radon/env`. Credentials are never copied into source URLs, status reasons or logs. `SEC_USER_AGENT` must identify the application and a real contact email. Production EIA collection requires its own registered key; `DEMO_KEY` was used only for the isolated public-access probe.

Artificial Analysis additionally requires `RADON_AI_CYCLE_AA_BASKET` or `--basket slug1,slug2`. The daily frontier refresher maintains the encrypted Profile value with one scored, priced text model from each configured major provider. It accepts only a complete catalog, retains a frontier incumbent until a strictly newer qualifying release appears, excludes previews and specialist models, and preserves the last-known-good basket on every upstream or schema failure. Stable model and creator IDs define cohort identity; mutable slugs are only the current API lookup projection. State is checksum-verified and written atomically under `~/.radon/ai-cycle/`. Any missing member suppresses the entire basket. The fixed bundle is one million input plus one million output tokens, uncached, at published list prices. Confirm appropriate product redistribution rights before exposing licensed AA data outside permitted internal use. The old 70/30 price index remains legacy history.

## History and request bounds

```sh
python3.13 -m scripts.ai_cycle --record --database /tmp/ai-cycle.sqlite \
  --sources vercel --backfill --start 2025-10-01 --end 2026-09-06 \
  --checkpoint /tmp/ai-cycle-backfill.json --max-requests 100
```

Daily runs reconcile the trailing seven completed UTC days. SEC defaults to 800 days of fiscal facts for quarter/TTM reconstruction. `--start` and `--end` override this window; `--end` must precede today UTC. Backfill clamps each source to its verified publisher floor and splits it into bounded windows: seven days for OpenRouter, 90 days for Vercel, EIA and NOAA, and publisher snapshots for GPU Rental Prices. Snapshot-only feeds run once and never fabricate historical observations. The checkpoint records only successful committed source windows. Use a checkpoint specific to the database and source selection; never reuse it against a fresh database. Daily revision collection should omit historical checkpoints.

| Source | Historical floor | Authoritative location | Access and implementation limit |
|---|---:|---|---|
| SEC EDGAR | 2009-01-01 | [Companyfacts API](https://www.sec.gov/search-filings/edgar-application-programming-interfaces) | Public; requires a descriptive user agent and fair-access pacing. U.S. GAAP 10-K/10-Q and TSM IFRS 20-F facts are retained. |
| NOAA NCEI | 2018-07-01 | [Daily Summaries API](https://www.ncei.noaa.gov/access/services/data/v1) | Public U.S. government data. A fixed four-station DOM-area weather cohort is collected; incomplete station-days are skipped and visible in source coverage. |
| EIA | 2019-01-01 | [EIA-930 API](https://www.eia.gov/opendata/browser/electricity/rto/region-sub-ba-data) | Free API key required. Hourly DOM load is compacted to daily average and peak values so the complete API history fits the snapshot budget. |
| OpenRouter | 2025-01-01 | [Data API](https://openrouter.ai/docs/cookbook/administration/data-api) | API key required; rankings data is CC BY 4.0. Model history uses bounded ranges; app history needs one call per completed day and is limited to the returned public top-100 cohort. |
| Vercel AI Gateway | 2025-10-01 | [Leaderboard export](https://vercel.com/docs/ai-gateway/leaderboards) | Public CC BY 4.0 export. Model and lab token/request/spend shares are retained separately. |
| GPU Rental Prices | 2026-07-05 | [Historical snapshots](https://github.com/adriannutiu/gpu-rental-prices/tree/main/data/snapshots) | Public CC BY 4.0 snapshots. Upstream provider URLs remain attached; sparse bundle metadata can keep matched-cohort transforms incomplete. |
| Artificial Analysis | current observations only | [Data API](https://artificialanalysis.ai/data-api) | API key and fixed basket required. The documented endpoint returns the current catalog, so no historical prices are inferred. |
| Vast.ai | current observations only | [Search offers API](https://docs.vast.ai/api-reference/search/search-offers) | API key required. Search results are current availability, not historical fleet utilization. |
| Lambda | current observations only | [Pricing](https://lambda.ai/pricing) | Values arrive through the attributed GPU Rental Prices dataset; no unsupported HTML history is manufactured. |
| Portkey | none | [Daily rankings](https://portkey.ai/rankings/daily) | No documented licensed historical export is available; the source stays unavailable instead of being scraped. |

The scheduled backfill requests every implementable history above and resumes from `/home/radon/.radon/ai-cycle/backfill-checkpoint.json`. Source cards on every LLM chart report provider status, linked location, retained observation count and actual first/last observation dates. A configured credential proves access only after a successful collection; unavailable and restricted statuses remain visible.

The transport caps each run at 500 seconds and each response at 20 MB. `--max-requests` allows 1–400 requests, default100. OpenRouter uses a locked local UTC request ledger at `~/.radon/ai-cycle/openrouter-budget.json`, spaces requests by2.1seconds, and reserves50of500daily account calls. Other applications' account usage is not visible locally; OpenRouter's own quota remains authoritative. A corrupted local quota ledger fails closed. App history uses one completed-day request at a time, with top100truncation explicitly preserved.

Raw successful JSON responses are archived by SHA256 under `~/.radon/ai-cycle/raw`, overrideable with `--archive PATH`. Provider errors are never archived as measurements. Preserve raw archives alongside the database for reproducibility; no automatic deletion is implemented. The transport bounds each fetch, not the lifetime size of this archive. Observation revisions append rather than overwrite; historical replay uses first-seen availability, not a backdated publication timestamp.

## Reviewed issuer disclosures

Unattended NVIDIA and Dell release pages returned HTTP403 during verification; primary web retrieval worked. Hardware semantic figures therefore use a reviewed import, not a misleading claim of live release parsing. Daily SEC checks refresh available filing facts for NVDA, DELL, SMCI, MU and TSM, but do not semantically extract NVIDIA segment guidance or Dell AI orders/backlog. Review new issuer releases each reporting event and import only values whose period, definition and source have been checked. A daily run without `--import-disclosures` skips this event-only source entirely: it preserves the previous reviewed status, observation vintage and check timestamp. If no import has ever succeeded, the registry continues to show unavailable without inventing a new check. An explicitly requested malformed import records a failure; it does not masquerade as a successful refresh. Portkey remains explicitly unavailable.

```sh
python3.13 -m scripts.ai_cycle --record --database /tmp/ai-cycle.sqlite \
  --sources issuer-disclosures --import-disclosures /path/to/reviewed-disclosures.json
```

The JSON object contains `observations`, an array with these required fields:

```json
{
  "observations": [{
    "indicator_id": "H1",
    "series_id": "ISSUER.metric",
    "entity": "ISSUER",
    "label": "Explicit metric and horizon",
    "value": 123,
    "unit": "USD",
    "period_start": "2026-01-01",
    "period_end": "2026-03-31",
    "published_at": "2026-05-01T23:59:59Z",
    "source_url": "https://issuer.example/earnings",
    "source_excerpt": "Short exact supporting excerpt",
    "definition": "Exact fiscal horizon and measurement definition",
    "verified": true,
    "verified_by": "Reviewer and review date",
    "measurement": "observed"
  }]
}
```

The numbers above are schema examples, not a data seed. Use `estimated` for guidance and preserve the future target period. Accepted indicator IDs are H1,H2,F1,F2,P2. Missing verification, excerpt, definition or known past publication time rejects the import. Date-only publication evidence should use conservative end-of-day UTC and note that precision in metadata. Keep revisions distinct. The import's raw hash identifies the reviewed import artifact; include `source_raw_hash` only when actual publisher bytes have been archived. Do not pretend a manually transcribed excerpt is a raw publisher response.

## Current source gates

- Vercel models/labs and GPU ask JSON passed actual public HTTP/schema probes. GPU offers lack enough bundle/region/interconnect terms for the matched-price index; visible asking prices do not imply scarcity or utilization.
- All ten SEC companyfacts endpoints passed. Five issuer cashflow mappings were reconciled against current primary statements; subsequent typed observations disclose mapping validation rather than claiming individual manual audits. Amazon productive-asset purchases are gross; they are not issuer-net capex or AWS-only.
- EIA DOM hourly load passed with the configured production key back to 2019-01-01. It is observed regional grid load, not AI MW. NOAA's fixed station cohort is collected as a weather control; weather-adjusted residuals remain disabled until the control-region method is validated.
- OpenRouter, AA and EIA keys are accepted only after authenticated collection succeeds. OpenRouter app/model series share one host lineage. Vast's read-only search parser is fixture-tested; provider schema or entitlement failures remain visible rather than creating observations.
- Portkey automated access and completed-day methodology remain unverified. Direct Lambda pricing requires a reviewed cohort import; no unsupported HTML parser runs automatically. Both remain unavailable rather than seeded with memo values.
- H1 reviewed disclosures are event snapshots. Future release detection/semantic extraction is not claimed. Review the last publication date before using them; an old report is not refreshed by a new fetch timestamp.
- Quality-constrained task economics, grid residuals, power milestones and independent shadow episodes remain gated by actual data/methodology requirements. The dashboard does not claim validated trading edge or enable trade execution.

### EIA interval convention

EIA's current [Form EIA-930 instructions](https://www.eia.gov/survey/form/eia_930/instructions.pdf), General Instructions (page3), define timestamps as hour-ending UTC; the daily-file section includes sub-region demand. The [sub-BA API metadata](https://api.eia.gov/v2/electricity/rto/region-sub-ba-data/) identifies this form as its source and `hourly` as UTC frequency. An API period `2026-09-06T04` therefore describes 03:00–04:00UTC, not 04:00–05:00UTC. Completed-day queries run from the first day's01:00 hour-ending label through00:00 on the day after the final requested day.

The historical backfill uses `methodology_version=eia-daily-hour-ending-v3`. It assigns each hour-ending value to the preceding interval's UTC date, then records the daily average and peak plus the contributing-hour count. Version1 and version2 rows remain audit evidence and must not be mixed into version3 analyses. Reprocessing archived responses appends a new method vintage with a new first-seen timestamp; it never edits an older row or pretends the corrected method was historically available.

The systemd service invokes `python -m scripts.ai_cycle.collect --record` directly so the watchdog contract can identify its bounded heartbeat writer. The public `python -m scripts.ai_cycle` entry point delegates to the same implementation.
