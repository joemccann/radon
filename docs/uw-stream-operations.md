# UW Advanced research stream operations

This collector supplies research evidence only. IB remains authoritative for executions, positions, order prices and risk checks. The stream never submits orders or populates executable quotes.

## Ownership and activation

`radon-uw-stream.service` runs immutable Python image code through the root-owned app runtime. The fixed shared `data/uw_stream/collector.lock` admits one local owner; deploy teardown/resume preserves an already-active optional collector. UW permits one machine per API token, so do not enable a second host with that token. No global token lease is claimed.

Bootstrap installs the collector, `radon-uw-research.service` and hourly `radon-uw-research.timer`; setup does not activate them. Set verified capabilities first, then explicitly start the collector. `RADON_UW_STREAM_ENABLED=0` is an idle heartbeat with no vendor socket; set 1 only after a redacted channel ACK receipt. `RADON_UW_STREAM_CHANNELS` is an explicit validated CSV manifest, maximum64 channels. The four defaults are flow-alerts,off_lit_trades,market_tide,interval_flow. Add selected `net_flow:SPY`, `gex:SPY`, `gex_strike_expiry:SPY`, `iv_term_structure:SPY`, `interpolated_iv:SPY`, `greek_flow:SPY` and `periscope:SPX` after observed access. Optional news/SEC and dated futures block channels are supported; unscoped option/quote/Greek firehoses are rejected.

Canonical Profile settings under the unusual_whales registry override environment values. The daemon and scheduled enrichment explicitly hydrate only that service's registered settings. A configured but unreadable store fails startup. Changing settings in the Profile requires restarting the managed collector to reread its manifest/token. The research timer rereads canonical settings every run.

### Key boundary

Both new UW processes are fully trusted app-plane image processes, like the API: the existing root-owned runtime grants them the encrypted store master key through `root:radon-secrets` staging, never a host radon process. The key can decrypt other stored services; it is not a provider-scoped cryptographic grant. Code reads/exports only UW registry fields, but that allowlist is an implementation boundary, not cryptographic isolation. The stream shares the app data directory, including the same governor SQLite WAL/SHM and legacy budget as API/oneshot callers; its state grant is broad and fully trusted. It has no media, broker lease/control or provider CLI grant mounts. A future scoped broker/export would reduce this trust. Never describe the current master-key grant as UW-only access.

## Persistence and channel semantics

Raw frames batch at most once per second or500 records/2MB, with an8MB/1024-frame input queue. SQLite WAL uses FULL synchronous commits, one-day/100k raw-event retention, a1GB disk watermark and512MB free-space floor. Local transaction rollback restores materialized additive state. The atomic checksummed snapshot publishes only after a durable checkpoint commit. Restart resumes local latest values but records an unknown upstream gap; websocket replay is unavailable. Turso receives compact metadata/current health every60 seconds, not per-event trades. Object archival is intentionally a later capacity decision; no lossless provider replay or complete raw historical archive is claimed.

`read_snapshot()` is read-only and never creates files. Missing/corrupt data returns structured missing/unavailable. A publisher heartbeat older than15 seconds marks the stream stale. Channel last observation age is independent; trade-driven quiet channels are not failed solely because no event arrived.

- Greek-flow per-second partials add by ticker/minute; source frames have no IDs. Identical partials are real inputs, not hash-deduplicated. Local ingest IDs are for local replay only. Reconnect/drop intervals are explicitly incomplete; A separate bounded worker automatically requests governed REST totals for observed completed minutes, at most10 requests per60-second cycle, only after a60-second settling interval following minute close. Invalid/mismatched/current-minute rows cannot certify a bucket. Confirmed REST totals replace whole completed buckets and retain gap provenance. Late partials retain the last confirmed baseline, become incomplete and trigger another REST confirmation; they never add on top of a REST total.
- Interval-flow repeats replace a five-minute bucket. Market tide already includes OTM fields, so there is no duplicate OTM subscription.
- GEX strike/expiry and interpolated IV days-horizon entries retain independent identities and provider units. Risk-reversal skew keys include ticker+expiry+delta (10 or25), with put-IV-minus-call-IV fractional-difference units; they are not expected-move percentages. Never mix Periscope net market-maker Greeks with REST dollars per1% move.
- Periscope publishes only a complete, row-identity-unique multipart board with final has_more=false and matching total_rows. An incomplete rollover retains old observations and records a gap.
- Futures blocks use exchange+trade_date+trade_id identity and reported_at order; update/delete supersede old states. Current documented block messages already contain displayed decimal prices. Preserve raw factor-based conversion for raw CME integer feeds only; do not double-convert block or REST prices. Do not infer direction from stale/null block NBBO.

## Health and rollback

Watchdog tracks uw-stream after first snapshot and uw-research after its state directory exists. Unit failures are also covered by the existing radon-* unit watchdog. DB mirror outages do not discard the local journal. Input/disk overflow records a gap; storage transaction failure stops the collector rather than continuing unpersisted.

After CI exact-head approval, measure channel coverage, received/persisted bytes, queue depth/bytes, drop counters and source/ingest lag on one watchlist before widening. Full-market option tape is prohibited by this manifest until a separate capacity/replay decision; Kafka is separately entitled.

Rollback: set stream enabled0/restart or stop the optional UW service and disable research timer; retained snapshots are research-only stale evidence. Reverting code must stop the new units before running an older image that lacks the collector. Existing REST scans and IB execution continue independently.

Provider references: https://api.unusualwhales.com/docs/websocket ; https://api.unusualwhales.com/docs/websocket/greek-flow.md ; https://api.unusualwhales.com/docs/websocket/ticker-interval-flow.md ; https://api.unusualwhales.com/docs/websocket/periscope.md ; https://api.unusualwhales.com/docs/websocket/futures-block-trades.md
