from datetime import datetime, timedelta, timezone

from scripts.api.server import _is_cri_cache_stale


def test_cri_cache_stale_when_missing():
    assert _is_cri_cache_stale(None, today_et="2026-04-22", current_market_open=True, now_ts=1_000) is True


def test_cri_cache_stale_when_market_open_and_mtime_too_old():
    mtime_dt = datetime(2026, 4, 22, 14, 0, 0, tzinfo=timezone.utc)
    data = {"date": "2026-04-22", "market_open": True, "scan_time": mtime_dt.isoformat()}
    assert _is_cri_cache_stale(
        data,
        mtime_ms=mtime_dt.timestamp() * 1000,
        today_et="2026-04-22",
        current_market_open=True,
        now_ts=(mtime_dt + timedelta(minutes=2)).timestamp(),
    ) is True


def test_cri_cache_fresh_when_market_open_and_recent():
    mtime_dt = datetime(2026, 4, 22, 14, 0, 30, tzinfo=timezone.utc)
    data = {"date": "2026-04-22", "market_open": True, "scan_time": mtime_dt.isoformat()}
    assert _is_cri_cache_stale(
        data,
        mtime_ms=mtime_dt.timestamp() * 1000,
        today_et="2026-04-22",
        current_market_open=True,
        now_ts=(mtime_dt + timedelta(seconds=20)).timestamp(),
    ) is False


def test_cri_cache_fresh_same_day_after_close():
    mtime_dt = datetime(2026, 4, 22, 20, 5, 0, tzinfo=timezone.utc)
    data = {"date": "2026-04-22", "market_open": False, "scan_time": mtime_dt.isoformat()}
    assert _is_cri_cache_stale(
        data,
        mtime_ms=mtime_dt.timestamp() * 1000,
        today_et="2026-04-22",
        current_market_open=False,
        now_ts=(mtime_dt + timedelta(hours=2)).timestamp(),
    ) is False
