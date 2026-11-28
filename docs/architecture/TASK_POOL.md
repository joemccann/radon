# Optimization task pool

Ranked by how much duplicated behavior a safe deletion removes.

1. Intraday flow clock. `fetch_flow.get_trading_day_progress` still uses a
   fixed 09:30-16:00 window, so a half day is interpolated as a partial full
   session. See pending confirmation: this changes a live signal.
2. Session-completion boundary. `last_completed_session_date` waits for
   16:00 even on an early close. Same pending item. RV-ratio, CRI, and the
   scan cache gate read it.
3. TRIN hour buckets. `fetch_trin.py` bins samples on a fixed 09:30-16:00
   window. Changing it rewrites stored buckets. Pending.
4. `server._is_market_open_now_et` and the handler `_is_trading_day_et`
   one-liners. They are patch seams with one body line. Low value until a
   caller can patch `is_market_open_et` / `is_trading_day_et` without
   widening the blast radius.

Not a task: UTC timestamp helpers. The copies do not share a format. Some
emit `Z`, some `+00:00`, some drop microseconds, one keeps milliseconds.
Merging them would change stored stamps.

Not a task: atomic writers that `os.replace` a temp file. `atomic_save`
embeds `_checksum`. Moving a writer onto it changes the file shape unless
that writer already verifies the checksum. Leave the others.
