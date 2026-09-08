# AI infrastructure collection operations

The `/regime/llm` AI infrastructure view reads cached observations. Browser requests never scrape providers or place trades. All automatic source collection runs through `python3.13 -m scripts.ai_cycle` from the repository root. The daily systemd service is `radon-ai-cycle.service`, scheduled by `radon-ai-cycle.timer` at 07:15 UTC.

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

Artificial Analysis additionally requires `RADON_AI_CYCLE_AA_BASKET` or `--basket slug1,slug2`. These must be verified current API slugs, not guessed legacy IDs. The immutable cohort hashes sorted membership; any missing member suppresses the entire basket. The fixed bundle is one million input plus one million output tokens, uncached, at published list prices. Confirm appropriate product redistribution rights before exposing licensed AA data outside permitted internal use. The old 70/30 price index remains legacy history.

## History and request bounds

```sh
python3.13 -m scripts.ai_cycle --record --database /tmp/ai-cycle.sqlite \
  --sources vercel --backfill --start 2025-10-01 --end 2026-09-06 \
  --checkpoint /tmp/ai-cycle-backfill.json --max-requests 100
```

Daily runs reconcile the trailing seven completed UTC days. SEC defaults to 800 days of fiscal facts for quarter/TTM reconstruction. `--start` and `--end` override this window; `--end` must precede today UTC. Backfill splits historical OpenRouter, Vercel and EIA requests into 28-day windows. Snapshot-only feeds run once and never fabricate historical observations. The checkpoint records only successful committed source windows. Use a checkpoint specific to the database and source selection; never reuse it against a fresh database. Daily revision collection should omit historical checkpoints.

The transport caps each run at 500 seconds and each response at 20 MB. `--max-requests` allows 1–400 requests, default100. OpenRouter uses a locked local UTC request ledger at `~/.radon/ai-cycle/openrouter-budget.json`, spaces requests by2.1seconds, and reserves50of500daily account calls. Other applications' account usage is not visible locally; OpenRouter's own quota remains authoritative. A corrupted local quota ledger fails closed. App history uses one completed-day request at a time, with top100truncation explicitly preserved.

Raw successful JSON responses are archived by SHA256 under `~/.radon/ai-cycle/raw`, overrideable with `--archive PATH`. Provider errors are never archived as measurements. Preserve raw archives alongside the database for reproducibility; no automatic deletion is implemented. The transport bounds each fetch, not the lifetime size of this archive. Observation revisions append rather than overwrite; historical replay uses first-seen availability, not a backdated publication timestamp.

## Reviewed issuer disclosures

Unattended NVIDIA and Dell release pages returned HTTP403 during verification; primary web retrieval worked. Hardware semantic figures therefore use a reviewed import, not a misleading claim of live release parsing. Daily SEC checks refresh available filing facts, but do not semantically extract NVIDIA segment guidance or Dell AI orders/backlog. Review new issuer releases each reporting event and import only values whose period, definition and source have been checked. A daily run without `--import-disclosures` skips this event-only source entirely: it preserves the previous reviewed status, observation vintage and check timestamp. If no import has ever succeeded, the registry continues to show unavailable without inventing a new check. An explicitly requested malformed import records a failure; it does not masquerade as a successful refresh. NOAA and Portkey remain explicitly reported as unsupported source checks.

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
- All seven SEC companyfacts endpoints passed. Five issuer cashflow mappings were reconciled against current primary statements; subsequent typed observations disclose mapping validation rather than claiming individual manual audits. Amazon productive-asset purchases are gross; they are not issuer-net capex or AWS-only.
- EIA DOM hourly load passed with a public demonstration key. It is observed regional grid load, not AI MW. NOAA weather coverage remains unverified, so weather-adjusted residuals are disabled.
- No OpenRouter, AA, Vast or production EIA credentials were configured in the reviewed local environment. OpenRouter/AA unauthenticated probes returned401; no authenticated entitlement is claimed. OpenRouter app/model series share one host lineage. Vast's read-only search parser is implemented and fixture-tested; authenticated schema validation remains required.
- Portkey automated access and completed-day methodology remain unverified. Direct Lambda pricing requires a reviewed cohort import; no unsupported HTML parser runs automatically. Both remain unavailable rather than seeded with memo values.
- H1 reviewed disclosures are event snapshots. Future release detection/semantic extraction is not claimed. Review the last publication date before using them; an old report is not refreshed by a new fetch timestamp.
- Quality-constrained task economics, grid residuals, power milestones and independent shadow episodes remain gated by actual data/methodology requirements. The dashboard does not claim validated trading edge or enable trade execution.

### EIA interval convention

EIA's current [Form EIA-930 instructions](https://www.eia.gov/survey/form/eia_930/instructions.pdf), General Instructions (page3), define timestamps as hour-ending UTC; the daily-file section includes sub-region demand. The [sub-BA API metadata](https://api.eia.gov/v2/electricity/rto/region-sub-ba-data/) identifies this form as its source and `hourly` as UTC frequency. An API period `2026-09-06T04` therefore describes 03:00–04:00UTC, not 04:00–05:00UTC. Completed-day queries run from the first day's01:00 hour-ending label through00:00 on the day after the final requested day.

The corrected mapping is `methodology_version=eia-hour-ending-v2`. Any pre-release version1 rows remain audit evidence of the previous interval assignment and must not be mixed into version2 analyses. Reprocessing the archived original response appends a new method vintage with a new first-seen timestamp; it never edits the original row or pretends the correction was available historically. The isolated verification database `ai-cycle-hour-ending-v2.sqlite` preserves149original observations and appends149corrected observations without production writes.

For rendered verification, `implementation/ai-cycle-verified.sqlite` is the fresh corrected cache: it includes only EIA version2 plus every other source's observations. `implementation/ai-cycle-hour-ending-v2.sqlite` is the audit ledger retaining both versions, and the original `ai-cycle.sqlite` remains untouched. Use the verified cache for UI screenshots; do not feed the audit ledger's superseded interval method into charts.

The systemd service invokes `python -m scripts.ai_cycle.collect --record` directly so the watchdog contract can identify its bounded heartbeat writer. The public `python -m scripts.ai_cycle` entry point delegates to the same implementation.
