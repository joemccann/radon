"""Regression tests for breadth_scan.py (NYSE advance/decline breadth)."""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

import breadth_scan


def _session_dates(n: int) -> list[str]:
    """Window-relative session dates ending yesterday (oldest -> newest)."""
    today = datetime.now(timezone.utc)
    return [(today - timedelta(days=n - i)).strftime("%Y-%m-%d") for i in range(n)]


def _bars(dates: list[str], closes: list[float]) -> list[SimpleNamespace]:
    return [SimpleNamespace(date=d, close=c) for d, c in zip(dates, closes)]


# ── build_daily_net_ad ────────────────────────────────────────────


def test_build_daily_net_ad_uses_bar_closes() -> None:
    dates = _session_dates(3)
    daily = breadth_scan.build_daily_net_ad(_bars(dates, [512.0, -238.0, 91.0]))

    assert daily == [(dates[0], 512.0), (dates[1], -238.0), (dates[2], 91.0)]


def test_build_daily_net_ad_skips_non_finite_closes() -> None:
    dates = _session_dates(3)
    daily = breadth_scan.build_daily_net_ad(_bars(dates, [100.0, float("nan"), -50.0]))

    assert daily == [(dates[0], 100.0), (dates[2], -50.0)]


# ── build_cumulative_ad ───────────────────────────────────────────


def test_build_cumulative_ad_is_running_sum() -> None:
    dates = _session_dates(3)
    cum = breadth_scan.build_cumulative_ad(
        [(dates[0], 100.0), (dates[1], -40.0), (dates[2], 10.0)]
    )

    assert cum == [(dates[0], 100.0), (dates[1], 60.0), (dates[2], 70.0)]


# ── count_sessions_positive ───────────────────────────────────────


def test_count_sessions_positive_counts_last_n() -> None:
    dates = _session_dates(22)
    net = [-1.0, -1.0] + [50.0] * 12 + [-25.0] * 8
    daily = list(zip(dates, net))

    assert breadth_scan.count_sessions_positive(daily, n=20) == 12


def test_count_sessions_positive_returns_none_when_short() -> None:
    dates = _session_dates(5)
    daily = [(d, 10.0) for d in dates]

    assert breadth_scan.count_sessions_positive(daily, n=20) is None


# ── compute_divergence ────────────────────────────────────────────


def test_compute_divergence_bearish_when_spy_up_and_cum_ad_down() -> None:
    assert breadth_scan.compute_divergence(2.5, -1500.0) == "bearish"


def test_compute_divergence_bullish_when_spy_down_and_cum_ad_up() -> None:
    assert breadth_scan.compute_divergence(-3.0, 900.0) == "bullish"


def test_compute_divergence_none_inside_thresholds() -> None:
    assert breadth_scan.compute_divergence(0.5, -1500.0) == "none"
    assert breadth_scan.compute_divergence(2.5, 1500.0) == "none"
    assert breadth_scan.compute_divergence(1.0, -1500.0) == "none"
    assert breadth_scan.compute_divergence(-1.0, 1500.0) == "none"


def test_compute_divergence_null_when_inputs_missing() -> None:
    assert breadth_scan.compute_divergence(None, -1500.0) is None
    assert breadth_scan.compute_divergence(2.5, None) is None
    assert breadth_scan.compute_divergence(None, None) is None


# ── build_output ──────────────────────────────────────────────────


def test_build_output_matches_contract_payload_shape() -> None:
    n = 25
    dates = _session_dates(n)
    # Rising SPY + net-negative breadth over the trailing 20 sessions.
    net_ad = [200.0 if i % 2 == 0 else -350.0 for i in range(n)]
    spy_closes = [500.0 + i for i in range(n)]
    ad_daily = list(zip(dates, net_ad))
    spy_daily = list(zip(dates, spy_closes))

    result = breadth_scan.build_output(
        ad_daily=ad_daily,
        spy_daily=spy_daily,
        intraday=[],
        tick=250.0,
        market_open=False,
        source="ib",
    )

    assert set(result) == {
        "scan_time", "market_open", "source", "latest", "history", "intraday",
    }
    assert result["market_open"] is False
    assert result["source"] == "ib"
    assert result["intraday"] == []

    cum = breadth_scan.build_cumulative_ad(ad_daily)
    expected_cum_change = cum[-1][1] - cum[-21][1]
    expected_spy_change = (spy_closes[-1] / spy_closes[-21] - 1) * 100
    expected_positive = sum(1 for v in net_ad[-20:] if v > 0)

    latest = result["latest"]
    assert set(latest) == {
        "session_date", "net_ad", "net_ad_prev", "tick", "cum_ad",
        "cum_ad_change_20d", "spy_change_20d_pct", "sessions_positive_20",
        "divergence",
    }
    assert latest["session_date"] == dates[-1]
    assert latest["net_ad"] == pytest.approx(net_ad[-1])
    assert latest["net_ad_prev"] == pytest.approx(net_ad[-2])
    assert latest["tick"] == pytest.approx(250.0)
    assert latest["cum_ad"] == pytest.approx(cum[-1][1])
    assert latest["cum_ad_change_20d"] == pytest.approx(expected_cum_change, abs=0.01)
    assert latest["spy_change_20d_pct"] == pytest.approx(expected_spy_change, abs=0.01)
    assert latest["sessions_positive_20"] == expected_positive
    assert latest["divergence"] == "bearish"

    assert len(result["history"]) == n
    first = result["history"][0]
    assert set(first) == {"date", "net_ad", "cum_ad", "spy_close"}
    assert first["date"] == dates[0]
    assert first["net_ad"] == pytest.approx(net_ad[0])
    assert first["cum_ad"] == pytest.approx(cum[0][1])
    assert first["spy_close"] == pytest.approx(spy_closes[0])

    scan_dt = datetime.fromisoformat(result["scan_time"].replace("Z", "+00:00"))
    assert scan_dt.tzinfo is not None


def test_build_output_history_spy_close_null_when_date_missing() -> None:
    dates = _session_dates(3)
    ad_daily = [(d, 10.0) for d in dates]
    spy_daily = [(dates[0], 500.0), (dates[2], 502.0)]

    result = breadth_scan.build_output(
        ad_daily=ad_daily,
        spy_daily=spy_daily,
        intraday=[],
        tick=None,
        market_open=True,
        source="ib",
    )

    assert result["history"][1]["spy_close"] is None


def test_build_output_intraday_only_degrades_gracefully() -> None:
    now = datetime.now(timezone.utc)
    times = [(now - timedelta(minutes=5 * (3 - i))).isoformat() for i in range(3)]
    intraday = list(zip(times, [120.0, -80.0, 310.0]))

    result = breadth_scan.build_output(
        ad_daily=[],
        spy_daily=[],
        intraday=intraday,
        tick=None,
        market_open=True,
        source="ib",
    )

    latest = result["latest"]
    assert result["history"] == []
    assert result["intraday"] == [
        {"time": t, "net_ad": pytest.approx(v)} for t, v in intraday
    ]
    assert latest["net_ad"] == pytest.approx(310.0)
    assert latest["session_date"] == breadth_scan._session_date_from_intraday(intraday)
    assert latest["net_ad_prev"] is None
    assert latest["cum_ad"] is None
    assert latest["cum_ad_change_20d"] is None
    assert latest["spy_change_20d_pct"] is None
    assert latest["sessions_positive_20"] is None
    assert latest["divergence"] is None


def test_build_output_latest_net_ad_prefers_intraday_over_daily() -> None:
    dates = _session_dates(2)
    now = datetime.now(timezone.utc)
    intraday = [(now.isoformat(), 999.0)]

    result = breadth_scan.build_output(
        ad_daily=[(dates[0], 5.0), (dates[1], -5.0)],
        spy_daily=[],
        intraday=intraday,
        tick=None,
        market_open=True,
        source="ib",
    )

    assert result["latest"]["net_ad"] == pytest.approx(999.0)


# ── persist guard ─────────────────────────────────────────────────


def test_persist_result_skips_empty_payload(monkeypatch, tmp_path) -> None:
    calls: list[str] = []
    monkeypatch.setattr(breadth_scan, "atomic_save", lambda *a, **k: calls.append("save"))
    monkeypatch.setattr(
        breadth_scan, "mirror_scan_snapshot", lambda *a, **k: calls.append("mirror")
    )

    empty = breadth_scan.build_output(
        ad_daily=[], spy_daily=[], intraday=[], tick=None,
        market_open=False, source="ib",
    )
    persisted = breadth_scan.persist_result(empty, cache_path=tmp_path / "breadth.json")

    assert persisted is False
    assert calls == []


def test_persist_result_writes_cache_and_mirrors_nonempty(monkeypatch, tmp_path) -> None:
    calls: list[tuple] = []
    monkeypatch.setattr(
        breadth_scan, "atomic_save", lambda path, data: calls.append(("save", path))
    )
    monkeypatch.setattr(
        breadth_scan,
        "mirror_scan_snapshot",
        lambda service, payload: calls.append(("mirror", service)),
    )

    dates = _session_dates(2)
    result = breadth_scan.build_output(
        ad_daily=[(dates[0], 10.0), (dates[1], -4.0)],
        spy_daily=[(dates[0], 500.0), (dates[1], 501.0)],
        intraday=[],
        tick=100.0,
        market_open=False,
        source="ib",
    )
    cache_path = tmp_path / "breadth.json"
    persisted = breadth_scan.persist_result(result, cache_path=cache_path)

    assert persisted is True
    assert calls == [("save", str(cache_path)), ("mirror", "breadth-scan")]


def test_carry_forward_history_preserves_cached_daily_on_degraded_scan() -> None:
    """An intraday-only scan (daily bars timed out) must not clobber the
    cached daily history — the cumulative A/D stats derive from it."""
    dates = _session_dates(25)
    ad_daily = [(d, 100.0 if i % 2 == 0 else -40.0) for i, d in enumerate(dates)]
    spy_daily = [(d, 500.0 + i) for i, d in enumerate(dates)]
    cached = breadth_scan.build_output(
        ad_daily=ad_daily, spy_daily=spy_daily, intraday=[], tick=50.0,
        market_open=False, source="ib",
    )

    now = datetime.now(timezone.utc)
    intraday = [((now - timedelta(minutes=5)).isoformat(), 210.0), (now.isoformat(), 330.0)]
    degraded = breadth_scan.build_output(
        ad_daily=[], spy_daily=[], intraday=intraday, tick=75.0,
        market_open=True, source="ib",
    )

    merged = breadth_scan.carry_forward_history(degraded, cached)

    assert merged["history"] == cached["history"]
    assert merged["intraday"] == degraded["intraday"]
    assert merged["latest"]["net_ad"] == pytest.approx(330.0)
    assert merged["latest"]["tick"] == pytest.approx(75.0)
    assert merged["latest"]["cum_ad"] == cached["latest"]["cum_ad"]
    assert merged["latest"]["cum_ad_change_20d"] == cached["latest"]["cum_ad_change_20d"]
    assert merged["latest"]["sessions_positive_20"] == cached["latest"]["sessions_positive_20"]
    assert merged["source"] == "ib"
    assert merged["market_open"] is True


def test_carry_forward_history_noop_when_fresh_history_present() -> None:
    dates = _session_dates(3)
    fresh = breadth_scan.build_output(
        ad_daily=[(d, 10.0) for d in dates], spy_daily=[],
        intraday=[], tick=None, market_open=True, source="ib",
    )
    cached = {"history": [{"date": dates[0], "net_ad": 999.0, "cum_ad": 999.0, "spy_close": None}]}

    assert breadth_scan.carry_forward_history(fresh, cached) is fresh


def test_carry_forward_history_noop_when_cache_empty() -> None:
    now = datetime.now(timezone.utc)
    degraded = breadth_scan.build_output(
        ad_daily=[], spy_daily=[], intraday=[(now.isoformat(), 5.0)],
        tick=None, market_open=True, source="ib",
    )

    assert breadth_scan.carry_forward_history(degraded, None) is degraded
    assert breadth_scan.carry_forward_history(degraded, {"history": []}) is degraded


# ── scan_mirror registration ──────────────────────────────────────


# ── live AD-NYSE snapshot (bid − ask = advancers − decliners) ─────


def test_extract_ad_net_is_bid_minus_ask() -> None:
    """IB publishes advancing issues in the bid and declining in the ask for
    AD-NYSE; the live net advance/decline is bid − ask."""
    ticker = SimpleNamespace(last=float("nan"), bid=1752.0, ask=1019.0, close=0.0)
    assert breadth_scan._extract_ad_net(ticker) == pytest.approx(733.0)


def test_extract_ad_net_none_when_side_missing() -> None:
    assert breadth_scan._extract_ad_net(SimpleNamespace(bid=1752.0, ask=None)) is None
    assert breadth_scan._extract_ad_net(SimpleNamespace(bid=None, ask=1019.0)) is None
    assert breadth_scan._extract_ad_net(SimpleNamespace(bid=float("nan"), ask=1019.0)) is None


# ── accumulate_session_tape (live intraday tape) ──────────────────

_ET = timezone.utc  # placeholder; real ET conversion exercised via aware datetimes


def _et_now(hour_et: int, minute_et: int, day: str = "2026-07-09") -> datetime:
    """An ET wall-clock time as an aware UTC datetime (ET = UTC−4 in July)."""
    import zoneinfo
    et = zoneinfo.ZoneInfo("America/New_York")
    y, m, d = (int(x) for x in day.split("-"))
    return datetime(y, m, d, hour_et, minute_et, tzinfo=et).astimezone(timezone.utc)


def test_accumulate_session_tape_appends_first_live_point() -> None:
    now = _et_now(10, 0)
    tape = breadth_scan.accumulate_session_tape(None, 733.0, now)
    assert len(tape) == 1
    assert tape[0][1] == pytest.approx(733.0)


def test_accumulate_session_tape_appends_new_bucket() -> None:
    t0 = _et_now(10, 0)
    first = breadth_scan.accumulate_session_tape(None, 733.0, t0)
    cached = {"intraday": [{"time": t, "net_ad": v} for t, v in first]}
    t1 = _et_now(10, 5)
    tape = breadth_scan.accumulate_session_tape(cached, 810.0, t1)
    assert len(tape) == 2
    assert tape[0][1] == pytest.approx(733.0)
    assert tape[1][1] == pytest.approx(810.0)


def test_accumulate_session_tape_replaces_same_bucket() -> None:
    t0 = _et_now(10, 0)
    first = breadth_scan.accumulate_session_tape(None, 733.0, t0)
    cached = {"intraday": [{"time": t, "net_ad": v} for t, v in first]}
    # A second scan within the SAME bucket updates that bucket in place.
    same_bucket = _et_now(10, 0)
    tape = breadth_scan.accumulate_session_tape(cached, 900.0, same_bucket)
    assert len(tape) == 1
    assert tape[0][1] == pytest.approx(900.0)


def test_accumulate_session_tape_drops_prior_session_points() -> None:
    yesterday = {"intraday": [{"time": _et_now(15, 0, "2026-07-08").isoformat(), "net_ad": 111.0}]}
    now = _et_now(10, 0, "2026-07-09")
    tape = breadth_scan.accumulate_session_tape(yesterday, 733.0, now)
    assert len(tape) == 1
    assert tape[0][1] == pytest.approx(733.0)


def test_accumulate_session_tape_keeps_tape_when_live_missing() -> None:
    t0 = _et_now(10, 0)
    first = breadth_scan.accumulate_session_tape(None, 733.0, t0)
    cached = {"intraday": [{"time": t, "net_ad": v} for t, v in first]}
    tape = breadth_scan.accumulate_session_tape(cached, None, _et_now(10, 5))
    assert len(tape) == 1
    assert tape[0][1] == pytest.approx(733.0)


def test_scan_mirror_registers_breadth_scan_with_date_keyed_upsert(monkeypatch) -> None:
    """mirror_scan_snapshot must dispatch the (date, taken_at, payload) arity
    that upsert_breadth_snapshot requires. Never touches a real DB — writer
    functions are monkeypatched per test_db_client_pytest_guard.py."""
    from db import scan_mirror, writer

    assert scan_mirror.SNAPSHOT_UPSERTS["breadth-scan"] == "upsert_breadth_snapshot"

    upserts: list[tuple] = []
    heartbeats: list[tuple] = []
    monkeypatch.setattr(writer, "ensure_no_replica_for_writers", lambda: None)
    monkeypatch.setattr(
        writer,
        "upsert_breadth_snapshot",
        lambda date_str, taken_at, payload: upserts.append((date_str, taken_at)),
    )
    monkeypatch.setattr(
        writer,
        "record_service_health",
        lambda service, state, **kwargs: heartbeats.append((service, state)),
    )

    session_date = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
    scan_time = datetime.now(timezone.utc).isoformat()
    payload = {
        "scan_time": scan_time,
        "latest": {"session_date": session_date},
        "history": [{"date": session_date, "net_ad": 1.0, "cum_ad": 1.0, "spy_close": None}],
        "intraday": [],
    }
    scan_mirror.mirror_scan_snapshot("breadth-scan", payload)

    assert upserts == [(session_date, scan_time)]
    assert heartbeats == [("breadth-scan", "ok")]
