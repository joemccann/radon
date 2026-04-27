from datetime import datetime, timedelta, timezone

from scripts.api.server import _is_gex_cache_stale


def test_gex_cache_stale_when_missing():
    assert _is_gex_cache_stale(None, today_et="2026-04-22", current_market_open=True, now_ts=1_000) is True


def test_gex_cache_stale_when_market_open_and_scan_too_old():
    scan_dt = datetime(2026, 4, 22, 14, 0, 0, tzinfo=timezone.utc)
    data = {"scan_time": scan_dt.isoformat()}
    assert _is_gex_cache_stale(data, today_et="2026-04-22", current_market_open=True, now_ts=(scan_dt + timedelta(minutes=2)).timestamp()) is True


def test_gex_cache_fresh_when_market_open_and_recent():
    scan_dt = datetime(2026, 4, 22, 14, 0, 30, tzinfo=timezone.utc)
    data = {"scan_time": scan_dt.isoformat()}
    assert _is_gex_cache_stale(data, today_et="2026-04-22", current_market_open=True, now_ts=(scan_dt + timedelta(seconds=20)).timestamp()) is False


def test_gex_cache_fresh_same_day_after_close():
    scan_dt = datetime(2026, 4, 22, 20, 5, 0, tzinfo=timezone.utc)
    data = {"scan_time": scan_dt.isoformat()}
    assert _is_gex_cache_stale(data, today_et="2026-04-22", current_market_open=False, now_ts=(scan_dt + timedelta(hours=2)).timestamp()) is False
