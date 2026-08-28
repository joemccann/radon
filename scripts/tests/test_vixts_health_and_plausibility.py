"""R-331 / R-333 / R-363 / R-364 / REL-117: the VIX TS producer cannot exit
without a health row, and cannot restamp a frozen source as fresh.

R-331 (NF-9, three-for-three): `run()`'s only try/except covered the
`CboeClient` CONSTRUCTOR. Everything after it — `_fetch_all`, `parse_index_csv`,
`join_series`, `ensure_plausible_series`, `_write_db` — was uncovered, so any
raise exited the oneshot with NO `service_health` row and yesterday's `ok` left
standing. `vixts_math.py`'s own docstring claimed the opposite. Shipped the
same week R-276 was fixed in `fetch_vixcor` for the identical shape.

R-333: the all-304 path restamped unchanged data with a fresh `scan_time` and
heartbeat `ok` without checking `cached['data_date']` against the current
session, so a stuck CDN edge answering 304 indefinitely held the 26h window
green while the ratio was frozen at an old session.

R-363: the `bad_leg` non-positive-VIX3M guard was unreachable — `join_series`
already `continue`d past every such row, so a corrupt zero VIX3M day was
silently DROPPED and the hole passed every guard.

R-364: the ratio sanity band was applied to `series[-1]` only, so a corrupt
value anywhere in the ~4,250-row history flowed into `compute_stats`, the
chart, and the "percent of sessions" copy with `service_health` green.
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import fetch_vixts  # noqa: E402
from lib import vixts_math  # noqa: E402


def _series(n: int, *, ratio: float = 0.9) -> list[dict]:
    return [
        {
            "date": f"20{10 + i // 250:02d}-01-{(i % 28) + 1:02d}",
            "vix": 18.0,
            "vix3m": 20.0,
            "ratio": ratio,
            "spx": 4000.0,
        }
        for i in range(n)
    ]


# ── R-363 / R-364: the plausibility guards reach the whole series ───────────

class TestPlausibilityCoversEveryRow:
    def test_a_zero_vix3m_day_raises_rather_than_being_dropped(self):
        vix = [{"date": f"2026-01-{d:02d}", "value": 18.0} for d in (1, 2, 3, 4)]
        vix3m = [
            {"date": "2026-01-01", "value": 20.0},
            {"date": "2026-01-02", "value": 0.0},
            {"date": "2026-01-03", "value": 0.0},
            {"date": "2026-01-04", "value": 0.0},
        ]
        with pytest.raises(ValueError, match="non-positive"):
            vixts_math.join_series(vix, vix3m, [])

    def test_a_healthy_join_is_unchanged(self):
        vix = [{"date": f"2026-01-{d:02d}", "value": 18.0} for d in (1, 2)]
        vix3m = [{"date": f"2026-01-{d:02d}", "value": 20.0} for d in (1, 2)]
        series = vixts_math.join_series(vix, vix3m, [])
        assert [r["date"] for r in series] == ["2026-01-01", "2026-01-02"]
        assert series[0]["ratio"] == 0.9

    def test_a_corrupt_ratio_mid_history_is_caught_and_named(self):
        series = _series(vixts_math.MIN_SERIES_ROWS + 10)
        series[1200]["ratio"] = 999.0
        offender = series[1200]["date"]
        with pytest.raises(ValueError) as exc:
            vixts_math.ensure_plausible_series(series)
        assert offender in str(exc.value), (
            "the failure must name the FIRST offending date, not just the "
            f"last row: {exc.value}"
        )
        assert "999.0" in str(exc.value)

    def test_a_corrupt_latest_ratio_is_still_caught(self):
        series = _series(vixts_math.MIN_SERIES_ROWS + 10)
        series[-1]["ratio"] = 3.0
        with pytest.raises(ValueError, match="sane band"):
            vixts_math.ensure_plausible_series(series)

    def test_a_healthy_series_passes(self):
        vixts_math.ensure_plausible_series(_series(vixts_math.MIN_SERIES_ROWS + 10))


# ── R-331: every failure path writes an error heartbeat ────────────────────

class _Recorder:
    def __init__(self):
        self.rows: list[tuple] = []

    def record_service_health(self, service, state, **kwargs):
        self.rows.append((service, state, kwargs))

    def ensure_no_replica_for_writers(self):
        pass

    def upsert_scan_snapshot(self, *_args, **_kwargs):
        pass

    def upsert_vixts_rows(self, *_args, **_kwargs):
        pass


class TestEveryFailurePathHeartbeats:
    @pytest.fixture
    def recorder(self, monkeypatch):
        rec = _Recorder()
        monkeypatch.setattr(fetch_vixts, "writer", rec)
        return rec

    def test_a_fetch_failure_writes_an_error_row(self, recorder, monkeypatch):
        monkeypatch.setattr(fetch_vixts, "_read_json_cache", lambda: None)

        def _boom(*_a, **_k):
            raise RuntimeError("cboe 503")

        monkeypatch.setattr(fetch_vixts, "_fetch_all", _boom)
        with pytest.raises(RuntimeError, match="cboe 503"):
            fetch_vixts.run(client=object())

        errors = [r for r in recorder.rows if r[1] == "error"]
        assert errors, (
            "a raise anywhere past the client constructor exited the oneshot "
            "with NO service_health row and yesterday's ok left standing"
        )
        assert errors[0][0] == fetch_vixts.SERVICE
        assert "cboe 503" in str(errors[0][2].get("error"))

    def test_a_plausibility_failure_writes_an_error_row(self, recorder, monkeypatch):
        monkeypatch.setattr(fetch_vixts, "_read_json_cache", lambda: None)
        monkeypatch.setattr(
            fetch_vixts,
            "_fetch_all",
            lambda *_a, **_k: ({s: "csv" for s in fetch_vixts._SYMBOLS}, {}),
        )
        monkeypatch.setattr(fetch_vixts, "_refetch_unchanged", lambda *_a, **_k: None)
        monkeypatch.setattr(fetch_vixts, "parse_index_csv", lambda *_a, **_k: [])
        monkeypatch.setattr(fetch_vixts, "join_series", lambda *_a, **_k: [])

        def _implausible(_series):
            raise ValueError("vixts series has 0 rows")

        monkeypatch.setattr(fetch_vixts, "ensure_plausible_series", _implausible)
        with pytest.raises(ValueError, match="0 rows"):
            fetch_vixts.run(client=object())

        assert [r for r in recorder.rows if r[1] == "error"], (
            "ensure_plausible_series raising is the DESIGNED failure signal; "
            "it must leave an error heartbeat behind"
        )

    def test_the_client_constructor_failure_still_heartbeats(self, recorder, monkeypatch):
        """The one path that already worked must keep working."""
        import builtins

        real_import = builtins.__import__

        def _fake(name, *args, **kwargs):
            if name == "clients.cboe_client":
                raise RuntimeError("no cboe client")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", _fake)
        with pytest.raises(RuntimeError):
            fetch_vixts.run()
        assert any("client init failed" in str(r[2].get("error")) for r in recorder.rows)


# ── R-333: a frozen source cannot restamp as fresh ─────────────────────────

class TestAll304PathReAgesTheVerdict:
    @pytest.fixture
    def recorder(self, monkeypatch):
        rec = _Recorder()
        monkeypatch.setattr(fetch_vixts, "writer", rec)
        monkeypatch.setattr(fetch_vixts, "_write_json_cache", lambda _p: None)
        monkeypatch.setattr(
            fetch_vixts, "_fetch_all",
            lambda *_a, **_k: ({s: None for s in fetch_vixts._SYMBOLS}, {}),
        )
        return rec

    def _cached(self, data_date: str) -> dict:
        return {
            "data_date": data_date,
            "count": 4200,
            "series": [],
            "current": {"ratio": 0.9, "regime": "contango"},
            "stats": {},
            "source_last_modified": {},
            "scan_time": "2026-08-01T00:00:00Z",
        }

    def test_a_two_week_old_cache_does_not_heartbeat_ok(self, recorder, monkeypatch):
        monkeypatch.setattr(
            fetch_vixts, "_read_json_cache", lambda: self._cached("2026-08-06")
        )
        now = datetime(2026, 8, 20, 23, 0, tzinfo=timezone.utc)
        payload = fetch_vixts.run(client=object(), now=now)

        states = [r[1] for r in recorder.rows]
        assert "ok" not in states, (
            "a stuck CDN edge answering 304 indefinitely kept scan_time minutes "
            f"old and the 26h window green while the ratio was frozen; {recorder.rows}"
        )
        assert payload.get("status") not in (None, "ok")

    def test_a_current_cache_still_heartbeats_ok(self, recorder, monkeypatch):
        from utils.market_calendar import last_completed_session_date

        now = datetime(2026, 8, 20, 23, 0, tzinfo=timezone.utc)
        current_session = last_completed_session_date(now)
        monkeypatch.setattr(
            fetch_vixts, "_read_json_cache", lambda: self._cached(current_session)
        )
        fetch_vixts.run(client=object(), now=now)
        assert "ok" in [r[1] for r in recorder.rows]
