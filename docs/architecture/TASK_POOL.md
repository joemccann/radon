# Optimization task pool

Ranked by how much duplicated behavior a safe deletion removes.

1. Knowledge query policy. `scripts/knowledge/embed.py` and
   `scripts/model_gateway/_embedding_backend.py` each define
   `embedding_text`, `embed_backend`, `dual_write_enabled`,
   `v2_coverage_ready`, and `resolve_query_vector`. The app copy calls the
   gateway. The backend copy calls NVIDIA. The decision of which vector to
   query is the same text in both. Next safe extraction: one rules module,
   two transports.
2. Intraday flow clock. `fetch_flow.get_trading_day_progress` still uses a
   fixed 09:30-16:00 window, so a half day is interpolated as a partial full
   session. See pending confirmation: this changes a live signal.
3. Session-completion boundary. `last_completed_session_date` waits for
   16:00 even on an early close. Same pending item. RV-ratio, CRI, and the
   scan cache gate read it.
4. TRIN hour buckets. `fetch_trin.py` bins samples on a fixed 09:30-16:00
   window. Changing it rewrites stored buckets. Pending.
5. Identical scanner cache reads. `_read_cache_file` is the same body in
   `bounce_setup_scanner`, `garch_convergence`, `strength_confirmation_scanner`,
   `theta_harvester_scanner`, and `vol_skew_mr_scanner`. Worth one helper
   only if the callers keep the same miss/corrupt contract.
6. `server._is_market_open_now_et` and the handler `_is_trading_day_et`
   one-liners. They are patch seams with one body line. Low value until a
   caller can patch `is_market_open_et` / `is_trading_day_et` without
   widening the blast radius.

Not a task: UTC timestamp helpers. The copies do not share a format. Some
emit `Z`, some `+00:00`, some drop microseconds, one keeps milliseconds.
Merging them would change stored stamps.

Not a task: atomic writers that `os.replace` a temp file. `atomic_save`
embeds `_checksum`. Moving a writer onto it changes the file shape unless
that writer already verifies the checksum. Leave the others.
