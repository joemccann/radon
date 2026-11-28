# Optimization task pool

Ranked by how much duplicated behavior a safe deletion removes.

1. Identical Cboe daily CSV parsers. `fetch_straddle.parse_index_csv` and
   `fetch_vixts.parse_index_csv` are the same function. `fetch_cor` and
   `fetch_vixcor` hardcode the CLOSE column. `fetch_vix_model_spread` still
   needs a look before it joins them.
2. Scanner historical vol. `calculate_hv` matches in the strength and theta
   scanners. `leap_scanner_uw.calculate_hv` skips a non-positive close that
   those two reject. Do not fold the leap copy in without a yes.
3. ET calendar day. `_et_today(now)` is the same function in earnings,
   catalysts, and informed flow. `market_calendar` does not expose it yet.
4. Intraday flow clock. `fetch_flow.get_trading_day_progress` still uses a
   fixed 09:30-16:00 window, so a half day is interpolated as a partial full
   session. See pending confirmation: this changes a live signal.
5. Session-completion boundary. `last_completed_session_date` waits for
   16:00 even on an early close. Same pending item. RV-ratio, CRI, and the
   scan cache gate read it.
6. TRIN hour buckets. `fetch_trin.py` bins samples on a fixed 09:30-16:00
   window. Changing it rewrites stored buckets. Pending.
7. Atomic state writes that open their own temp file instead of
   `utils.atomic_io.atomic_save`. Migrate only the copies that match the
   helper's contract (replace, mode, checksum). Leave writers with a
   different durability rule.
8. `server._is_market_open_now_et` and the handler `_is_trading_day_et`
   one-liners. They are patch seams with one body line. Low value until a
   caller can patch `is_market_open_et` / `is_trading_day_et` without
   widening the blast radius.
