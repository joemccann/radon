# Optimization task pool

Ranked by how much duplicated behavior a safe deletion removes.

1. ET calendar day. `_et_today(now)` is the same function in earnings,
   catalysts, and informed flow. `market_calendar` does not expose it yet.
2. Intraday flow clock. `fetch_flow.get_trading_day_progress` still uses a
   fixed 09:30-16:00 window, so a half day is interpolated as a partial full
   session. See pending confirmation: this changes a live signal.
3. Session-completion boundary. `last_completed_session_date` waits for
   16:00 even on an early close. Same pending item. RV-ratio, CRI, and the
   scan cache gate read it.
4. TRIN hour buckets. `fetch_trin.py` bins samples on a fixed 09:30-16:00
   window. Changing it rewrites stored buckets. Pending.
5. Atomic state writes that open their own temp file instead of
   `utils.atomic_io.atomic_save`. Migrate only the copies that match the
   helper's contract (replace, mode, checksum). Leave writers with a
   different durability rule.
6. `server._is_market_open_now_et` and the handler `_is_trading_day_et`
   one-liners. They are patch seams with one body line. Low value until a
   caller can patch `is_market_open_et` / `is_trading_day_et` without
   widening the blast radius.
