"""REL-052 group B / R-120, R-122, R-123 (all P2) — storage hygiene for the
five tables this delta added.

R-120: none of `credit_spread_history`, `ivrank_history`, `iei_hyg_history`,
`trin_samples` or `trin_daily` has a retention policy, so the R-076 sweep
that bounds every other history table skips them entirely. `trin_samples`
grows at ~78 rows a weekday forever.

R-122: `trin` carries `closed: 24h` against a Mon-Fri-only timer with
`Persistent=false`, so the last heartbeat is Friday ~21:57 UTC and the row is
"stale" from Saturday evening until Monday's first fire — a guaranteed
weekend page, the same shape the 3-day windows elsewhere exist to prevent.

R-123: `credit_spread_history` and `iei_hyg_history` are WRITE-ONLY — no
SELECT anywhere in the repo. Both fetchers rehydrate from host-local JSON
only, so losing `data/credit_spread.json` makes the next run treat IB's 1-year
window as the whole series and republish a 1-year snapshot where an 18-year
one stood, while the 2007+ rows sit unreadable in Turso. `fetch_ivrank.py`
and `fetch_trin.py` do it right.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

from db import retention


class TestNewHistoryTablesHaveRetention:
    def test_every_delta_table_is_swept(self):
        covered = {policy.table for policy in retention.SNAPSHOT_RETENTION_POLICIES}
        missing = sorted(
            {
                "credit_spread_history",
                "ivrank_history",
                "iei_hyg_history",
                "trin_samples",
                "trin_daily",
            }
            - covered
        )
        assert missing == [], (
            f"the R-076 sweep bounds every other history table but not these: {missing}"
        )

    def test_the_horizons_sit_above_their_deepest_reader(self):
        by_table = {p.table: p for p in retention.SNAPSHOT_RETENTION_POLICIES}
        # iei-hyg and ivrank rank against 252 sessions; credit-spread charts
        # its full series; trin's MA(10) needs only the current session but
        # the daily series backs the chart.
        assert by_table["ivrank_history"].keep >= 252
        assert by_table["iei_hyg_history"].keep >= 252
        assert by_table["trin_daily"].keep >= 252

    def test_trin_samples_is_bounded_tighter_than_the_daily_series(self):
        by_table = {p.table: p for p in retention.SNAPSHOT_RETENTION_POLICIES}
        # The two are counted in different units: `trin_daily` keeps SESSIONS,
        # `trin_samples` keeps ROWS at ~78 a weekday. Compare in sessions.
        samples_sessions = by_table["trin_samples"].keep / 78
        assert samples_sessions < by_table["trin_daily"].keep
        assert samples_sessions >= 20, "the hourly chart needs a month of bars"


class TestTrinClosedWindowCoversTheWeekend:
    def test_both_catalogs_use_a_multi_day_closed_window(self):
        from watchdog import services as svc

        assert svc.SCHEDULED_SERVICES["trin"]["closed"] >= 3 * 24 * 60 * 60

        windows = (REPO / "web" / "lib" / "serviceHealthWindows.ts").read_text()
        line = next(l for l in windows.splitlines() if l.strip().startswith('"trin":'))
        assert "3 * DAY" in line, line

    def test_the_open_window_is_unchanged(self):
        from watchdog import services as svc

        assert svc.SCHEDULED_SERVICES["trin"]["open"] == 15 * 60


class TestTursoFirstRehydration:
    def test_credit_spread_reads_its_history_table(self):
        source = (REPO / "scripts" / "fetch_credit_spread.py").read_text()
        assert "credit_spread_history" in source, (
            "the table is write-only: losing data/credit_spread.json republishes "
            "a 1-year series where an 18-year one stood"
        )

    def test_iei_hyg_reads_its_history_table(self):
        source = (REPO / "scripts" / "fetch_iei_hyg.py").read_text()
        assert "iei_hyg_history" in source

    def test_credit_spread_prefers_turso_and_falls_back_to_json(self, monkeypatch):
        import fetch_credit_spread as cs

        monkeypatch.setattr(
            cs, "_turso_series",
            lambda: [{"date": "2007-01-03", "hyg_close": 1.0, "spx_close": 2.0}],
        )
        assert cs._read_cached_series()[0]["date"] == "2007-01-03"

        def _boom():
            raise RuntimeError("hrana 502")

        monkeypatch.setattr(cs, "_turso_series", _boom)
        monkeypatch.setattr(cs, "_json_series", lambda: [{"date": "2026-08-01"}])
        assert cs._read_cached_series()[0]["date"] == "2026-08-01"

    def test_iei_hyg_prefers_turso_and_falls_back_to_json(self, monkeypatch):
        import fetch_iei_hyg as iei

        monkeypatch.setattr(iei, "_turso_series", lambda: [{"date": "2020-01-02"}])
        assert iei.load_cached_series()[0]["date"] == "2020-01-02"

        def _boom():
            raise RuntimeError("hrana 502")

        monkeypatch.setattr(iei, "_turso_series", _boom)
        monkeypatch.setattr(iei, "_json_series", lambda: [{"date": "2026-08-01"}])
        assert iei.load_cached_series()[0]["date"] == "2026-08-01"
