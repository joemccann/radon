# Pending confirmation

Not started. Each item changes a contract, a deploy, or a security boundary.

1. Half-day session clock. `fetch_flow.get_trading_day_progress` and
   `market_calendar.last_completed_session_date` treat 16:00 ET as the only
   close. On an early close the flow interpolator still scales the tape as
   a partial full day until 16:00, and "last completed session" stays on the
   prior day until 16:00. Aligning both with the calendar close changes
   interpolated dark-pool figures and the session date RV-ratio, CRI, and
   the scan cache use. Needs an explicit yes before the signal moves.
2. TRIN RTH buckets. `scripts/fetch_trin.py` drops samples outside
   09:30-16:00 ET and does not consult the early-close table. Using the
   calendar close would drop half-day afternoon samples from a stored
   series. Needs a yes before historical rows change shape.
3. Split `scripts/api/server.py` and `web/components/WorkspaceSections.tsx`.
   Both are wide on purpose (route table, workspace shell). A directory
   split is a major move with no behavior win until a seam is already
   stable. Not started.
4. Security runners, branch protection, secrets, auth policy, deploys, and
   production hosts. Out of scope for this branch.
