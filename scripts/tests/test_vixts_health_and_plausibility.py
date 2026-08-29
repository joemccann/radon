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

import json
import sys
from datetime import date, datetime, timedelta, timezone
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
        # T-264: the writes must be COUNTED, not swallowed. Stubbing these as
        # bare `pass` is what let "a rejected value writes no row" go
        # unasserted for the whole life of this file.
        self.snapshots: list[tuple] = []
        self.row_upserts: list[tuple] = []

    def record_service_health(self, service, state, **kwargs):
        self.rows.append((service, state, kwargs))

    def ensure_no_replica_for_writers(self):
        pass

    def upsert_scan_snapshot(self, *args, **kwargs):
        self.snapshots.append((args, kwargs))

    def upsert_vixts_rows(self, *args, **kwargs):
        self.row_upserts.append((args, kwargs))


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


# ── T-263 / T-264: the CHANGED-source path is held to the same contract ────

_SYNTH_ROWS = vixts_math.MIN_SERIES_ROWS + 100
# Clock-pinned, never "today": a Thursday 19:00 ET, i.e. after the close, so
# `last_completed_session_date` resolves to that same Thursday. Every date in
# these tests is derived from it, so the suite reads identically on a weekend.
_NOW = datetime(2026, 8, 20, 23, 0, tzinfo=timezone.utc)


def _expected_session() -> str:
    from utils.market_calendar import last_completed_session_date

    return last_completed_session_date(_NOW)


def _cboe_csv(
    end: str,
    *,
    value: float,
    column: str = "CLOSE",
    overrides: dict | None = None,
) -> str:
    """A Cboe daily-prices CSV of `_SYNTH_ROWS` consecutive days ending on `end`."""
    last = date.fromisoformat(end)
    lines = [f"DATE,OPEN,HIGH,LOW,{column}"]
    for offset in range(_SYNTH_ROWS - 1, -1, -1):
        day = last - timedelta(days=offset)
        close = (overrides or {}).get(day.isoformat(), value)
        lines.append(f"{day.month:02d}/{day.day:02d}/{day.year},0,0,0,{close}")
    return "\n".join(lines) + "\n"


class _ChangedSourceClient:
    """All three files answer 200 with fresh bytes — the full-rebuild branch.

    Cboe re-touches Last-Modified intraday WITHOUT appending the session row
    (clients/cboe_client.py), so this is exactly the shape that republishes a
    two-week-old series through the branch R-333 never covered.
    """

    def __init__(self, end_date: str, *, vix_overrides: dict | None = None):
        self._texts = {
            "VIX": _cboe_csv(end_date, value=18.0, overrides=vix_overrides),
            "VIX3M": _cboe_csv(end_date, value=20.0),
            "SPX": _cboe_csv(end_date, value=4000.0, column="SPX"),
        }

    def fetch_history(self, symbol, if_modified_since=None):
        return self._texts[symbol], f"stamp-{symbol}"


class TestChangedSourcePathReAgesTheVerdict:
    """T-263: only the 304 branch aged its own data_date.

    The rebuild branch called `build_payload`, which emits no `status`, no
    `lag_days` and no `expected_session`, and then heartbeat a bare `ok`. The
    defence R-333 built lived on the branch that CANNOT have new data and was
    missing from the branch that can.
    """

    @pytest.fixture
    def recorder(self, monkeypatch, tmp_path):
        rec = _Recorder()
        monkeypatch.setattr(fetch_vixts, "writer", rec)
        monkeypatch.setattr(fetch_vixts, "VIXTS_JSON", tmp_path / "vixts.json")
        return rec

    def test_a_ten_day_old_rebuild_does_not_heartbeat_ok(self, recorder):
        expected = _expected_session()
        end = (date.fromisoformat(expected) - timedelta(days=10)).isoformat()

        payload = fetch_vixts.run(client=_ChangedSourceClient(end), now=_NOW)

        assert payload["data_date"] == end
        assert payload.get("expected_session") == expected
        assert payload.get("lag_days") == 10
        assert payload.get("status") == "stale_source", (
            "a 200 + full rebuild of an unmoved series is republished as "
            f"current: {payload.get('status')!r}"
        )
        assert "ok" not in [r[1] for r in recorder.rows], (
            "Cboe re-touching Last-Modified without appending a session row "
            "rebuilt 4,252 unchanged rows every night, passed the row-count "
            f"and ratio guards, and heartbeat ok; {recorder.rows}"
        )
        errors = [r for r in recorder.rows if r[1] == "error"]
        assert errors, "the stale rebuild must leave a health_error behind"
        assert end in str(errors[0][2].get("error"))

    def test_a_current_rebuild_still_heartbeats_ok(self, recorder):
        expected = _expected_session()

        payload = fetch_vixts.run(client=_ChangedSourceClient(expected), now=_NOW)

        assert payload.get("status") == "ok"
        assert payload.get("expected_session") == expected
        assert payload.get("lag_days") == 0
        assert [r[1] for r in recorder.rows] == ["ok"]

    def test_both_branches_emit_the_same_freshness_keys(self, recorder, monkeypatch):
        """The contract cannot be present on one branch and absent on the other."""
        expected = _expected_session()
        rebuilt = fetch_vixts.run(client=_ChangedSourceClient(expected), now=_NOW)

        monkeypatch.setattr(
            fetch_vixts,
            "_fetch_all",
            lambda *_a, **_k: ({s: None for s in fetch_vixts._SYMBOLS}, {}),
        )
        reused = fetch_vixts.run(client=object(), now=_NOW)

        keys = {"status", "lag_days", "expected_session"}
        assert keys <= set(rebuilt), f"rebuild branch is missing {keys - set(rebuilt)}"
        assert keys <= set(reused), f"304 branch is missing {keys - set(reused)}"
        assert {k: reused[k] for k in keys} == {k: rebuilt[k] for k in keys}


class TestARejectedValueWritesNoRow:
    """T-264: the suite only ever asserted "an error row exists".

    `_Recorder` stubbed `upsert_vixts_rows` / `upsert_scan_snapshot` as no-op
    `pass` and `_write_json_cache` was never intercepted, so reordering
    `_run_cycle` to "persist what we pulled, then flag it" would push a
    corrupt 4,252-row series into `vixts_history`, overwrite `data/vixts.json`
    AND still emit the error heartbeat the old test asserts on — green suite,
    ratio of 50 in the canonical store.
    """

    PRIOR = json.dumps(
        {
            "scan_time": "2026-08-11T02:45:00Z",
            "source_last_modified": {},
            "data_date": "2026-08-10",
            "count": 4252,
            "current": {"ratio": 0.9, "regime": "CONTANGO"},
            "stats": {},
            "series": [],
        },
        indent=2,
    )

    @pytest.fixture
    def rig(self, monkeypatch, tmp_path):
        rec = _Recorder()
        monkeypatch.setattr(fetch_vixts, "writer", rec)
        cache = tmp_path / "vixts.json"
        cache.write_text(self.PRIOR)
        monkeypatch.setattr(fetch_vixts, "VIXTS_JSON", cache)
        json_writes: list[dict] = []
        real_write = fetch_vixts._write_json_cache

        def _spy(payload):
            json_writes.append(payload)
            return real_write(payload)

        monkeypatch.setattr(fetch_vixts, "_write_json_cache", _spy)
        return rec, cache, json_writes

    def test_a_corrupt_ratio_persists_nothing_anywhere(self, rig):
        recorder, cache, json_writes = rig
        end = _expected_session()
        # The REAL ensure_plausible_series against a genuinely corrupt row:
        # VIX 1000 over VIX3M 20 is a ratio of 50, far outside the sane band.
        corrupt_day = (date.fromisoformat(end) - timedelta(days=5)).isoformat()
        client = _ChangedSourceClient(end, vix_overrides={corrupt_day: 1000.0})

        with pytest.raises(ValueError, match="sane band"):
            fetch_vixts.run(client=client, now=_NOW)

        assert recorder.row_upserts == [], (
            "a rejected value must not reach vixts_history; "
            f"{len(recorder.row_upserts)} upsert_vixts_rows call(s) landed"
        )
        assert recorder.snapshots == [], (
            "a rejected payload must not become the scan snapshot the API "
            f"serves; {len(recorder.snapshots)} snapshot(s) landed"
        )
        assert json_writes == [], "the JSON fallback must not be overwritten"
        assert cache.read_bytes() == self.PRIOR.encode(), (
            "data/vixts.json changed on a rejected pull"
        )
        assert [r[1] for r in recorder.rows] == ["error"]

    def test_a_plausible_pull_does_write_all_three(self, rig):
        """Pairs the negative: the assertions above are not vacuous."""
        recorder, cache, json_writes = rig

        fetch_vixts.run(client=_ChangedSourceClient(_expected_session()), now=_NOW)

        assert len(recorder.row_upserts) == 1
        assert len(recorder.snapshots) == 1
        assert len(json_writes) == 1
        assert cache.read_bytes() != self.PRIOR.encode()
