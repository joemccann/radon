"""UW Advanced admission: concurrent workers share one account contract.

All requests are synthetic; these tests are intended for GitHub runners.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
import sqlite3

import pytest
import requests

from utils import uw_governor as gov
from utils import uw_budget
from clients.uw_client import UWClient, UWRateLimitError, UWAuthError, UWValidationError

TOKEN = "synthetic-uw-token"


def test_parallel_workers_cannot_overspend_one_minute(monkeypatch):
    monkeypatch.setenv("RADON_UW_REQUESTS_PER_MINUTE", "5")
    def attempt(_):
        try:
            gov.reserve(TOKEN, priority="interactive", timeout=0)
            return True
        except gov.AdmissionError:
            return False
    with ThreadPoolExecutor(max_workers=12) as pool:
        admitted = list(pool.map(attempt, range(20)))
    assert sum(admitted) == 5


def test_rotation_does_not_create_another_account_allowance(monkeypatch):
    monkeypatch.setenv("RADON_UW_REQUESTS_PER_MINUTE", "1")
    gov.reserve(TOKEN, priority="interactive", timeout=0)
    with pytest.raises(gov.AdmissionError):
        gov.reserve("rotated-synthetic-token", priority="interactive", timeout=0)


def test_background_leaves_interactive_minute_headroom(monkeypatch):
    monkeypatch.setenv("RADON_UW_REQUESTS_PER_MINUTE", "5")
    for _ in range(4):
        gov.reserve(TOKEN, priority="background", timeout=0)
    with pytest.raises(gov.AdmissionError):
        gov.reserve(TOKEN, priority="background", timeout=0)
    gov.reserve(TOKEN, priority="interactive", timeout=0)


def test_background_leaves_interactive_daily_headroom(monkeypatch):
    monkeypatch.setenv("RADON_UW_DAILY_LIMIT", "5")
    for _ in range(4):
        gov.reserve(TOKEN, priority="background", timeout=0)
    with pytest.raises(gov.AdmissionError):
        gov.reserve(TOKEN, priority="background", timeout=0)
    gov.reserve(TOKEN, priority="interactive", timeout=0)
    with pytest.raises(gov.AdmissionError):
        gov.reserve(TOKEN, priority="interactive", timeout=0)


@pytest.mark.parametrize("value", ["0", "-1", "nan", "inf", "wrong", "99999999999"])
def test_malformed_minute_policy_fails_closed(monkeypatch, value):
    monkeypatch.setenv("RADON_UW_REQUESTS_PER_MINUTE", value)
    with pytest.raises(gov.AdmissionError):
        gov.reserve(TOKEN, timeout=0)
    assert gov.snapshot()["available"] is False


def test_higher_minute_policy_needs_verified_receipt(monkeypatch):
    monkeypatch.setenv("RADON_UW_REQUESTS_PER_MINUTE", "1000")
    assert gov.snapshot()["requests_per_minute"] == 120
    monkeypatch.setenv("RADON_UW_LIMITS_VERIFIED", "1")
    assert gov.snapshot()["requests_per_minute"] == 1000


def test_unlimited_cannot_be_inferred_from_upgrade_label(monkeypatch):
    monkeypatch.setenv("RADON_UW_DAILY_LIMIT", "unlimited")
    with pytest.raises(gov.AdmissionError):
        gov.reserve(TOKEN, timeout=0)
    monkeypatch.setenv("RADON_UW_LIMITS_VERIFIED", "1")
    assert gov.snapshot()["effective_limit"] is None


def test_provider_receipt_reconciles_pending_reservations():
    a = gov.reserve(TOKEN, timeout=0)
    gov.reserve(TOKEN, timeout=0)
    state = gov.observe(TOKEN, 200, {"x-uw-token-req-limit": "1000", "x-uw-daily-req-count": "40"}, reservation=a)
    assert state["reserved_or_observed"] == 41
    assert state["effective_limit"] == 1000
    assert state["provider_count"] == 40
    assert state["quota_period"] == "unknown"


def test_missing_or_malformed_headers_do_not_infer_unlimited():
    state = gov.observe(TOKEN, 200, {"x-uw-token-req-limit": "oops", "x-uw-daily-req-count": "nan"})
    assert state["effective_limit"] == 40000
    assert state["provider_cap"] is None
    assert state["limits_verified"] is False


def test_old_response_cannot_reduce_observed_count():
    gov.observe(TOKEN, 200, {"x-uw-token-req-limit": "5000", "x-uw-daily-req-count": "100"})
    state = gov.observe(TOKEN, 200, {"x-uw-token-req-limit": "5000", "x-uw-daily-req-count": "10"})
    assert state["provider_count"] == 100


def test_unknown_period_does_not_reset_provider_count_at_et_rollover(monkeypatch):
    now = datetime(2026, 10, 21, 23, 59, tzinfo=timezone.utc).timestamp()
    monkeypatch.setattr(gov.time, "time", lambda: now)
    gov.observe(TOKEN, 200, {"x-uw-token-req-limit": "100", "x-uw-daily-req-count": "99"})
    now += 3600
    assert gov.snapshot()["reserved_or_observed"] == 99
    gov.reserve(TOKEN, priority="interactive", timeout=0)
    with pytest.raises(gov.AdmissionError):
        gov.reserve(TOKEN, priority="interactive", timeout=0)


def test_verified_daily_period_can_accept_next_day_receipt(monkeypatch):
    monkeypatch.setenv("RADON_UW_LIMITS_VERIFIED", "1")
    monkeypatch.setenv("RADON_UW_QUOTA_PERIOD", "daily")
    now = datetime(2026, 10, 21, 23, 59, tzinfo=timezone.utc).timestamp()
    monkeypatch.setattr(gov.time, "time", lambda: now)
    gov.observe(TOKEN, 200, {"x-uw-token-req-limit": "100", "x-uw-daily-req-count": "100"})
    now += 3600
    reservation = gov.reserve(TOKEN, priority="interactive", timeout=0)
    state = gov.observe(TOKEN, 200, {"x-uw-token-req-limit": "100", "x-uw-daily-req-count": "1"}, reservation=reservation)
    assert state["provider_count"] == 1


@pytest.mark.parametrize("retry_after", ["120", "Wed, 21 Oct 2026 16:02:00 GMT"])
def test_retry_after_is_shared_with_other_workers(monkeypatch, retry_after):
    now = datetime(2026, 10, 21, 16, tzinfo=timezone.utc).timestamp()
    monkeypatch.setattr(gov.time, "time", lambda: now)
    state = gov.observe(TOKEN, 429, {"Retry-After": retry_after})
    assert state["embargo_until"] == now + 120
    with pytest.raises(gov.AdmissionError):
        gov.reserve("another-account-token", timeout=0)


def test_daily_429_embargo_lasts_until_verified_reset(monkeypatch):
    now = datetime(2026, 10, 21, 16, tzinfo=timezone.utc).timestamp()
    monkeypatch.setattr(gov.time, "time", lambda: now)
    state = gov.observe(TOKEN, 429, {}, daily_exhausted=True)
    assert state["embargo_until"] > now + 60


def test_corrupt_database_is_not_replaced_with_empty_admission_state():
    target = gov._database_path()
    target.write_bytes(b"not a sqlite database")
    with pytest.raises(gov.AdmissionError):
        gov.reserve(TOKEN, timeout=0)
    assert target.read_bytes() == b"not a sqlite database"
    assert gov.snapshot()["available"] is False


def test_no_token_or_unapproved_header_is_persisted():
    gov.observe(TOKEN, 200, {"x-uw-token-req-limit": "1000", "x-uw-daily-req-count": "2", "authorization": TOKEN, "x-raw-token": TOKEN})
    assert all(TOKEN.encode() not in p.read_bytes() for p in gov._database_path().parent.glob("uw_governor.sqlite3*"))
    assert TOKEN not in json.dumps(gov.snapshot(TOKEN))


def response(code=200, payload=None, headers=None):
    result = requests.Response()
    result.status_code = code
    result._content = json.dumps(payload or {"data": []}).encode()
    result.headers.update(headers or {})
    return result


def test_client_reserves_each_attempt_and_returns_safe_provider_error(monkeypatch):
    calls = []
    def get(_session, *_args, **_kwargs):
        calls.append(True)
        return response(401, {"message": f"bad key {TOKEN}", "token": TOKEN})
    monkeypatch.setattr(requests.Session, "get", get)
    with UWClient(token=TOKEN) as client:
        with pytest.raises(UWAuthError) as raised:
            client.get("market/economic-calendar", use_cache=False)
    assert calls == [True]
    assert TOKEN not in str(raised.value)
    assert TOKEN not in json.dumps(raised.value.response_body)
    assert gov.snapshot()["reserved_or_observed"] == 1


def test_client_refuses_before_wire_when_no_capacity(monkeypatch):
    monkeypatch.setenv("RADON_UW_REQUESTS_PER_MINUTE", "1")
    gov.reserve(TOKEN, priority="interactive", timeout=0)
    def forbidden(*_args, **_kwargs):
        raise AssertionError("provider HTTP must not run after refusal")
    monkeypatch.setattr(requests.Session, "get", forbidden)
    with UWClient(token=TOKEN) as client:
        with pytest.raises(UWRateLimitError):
            client.get("market/economic-calendar", wait_timeout=0, use_cache=False)


@pytest.mark.parametrize("path", ["https://evil.invalid/", "../market", "stock/AAPL/../info", "market/tide?token=secret", "stock/%2e%2e/info"])
def test_public_get_refuses_urls_queries_and_traversal(path):
    with UWClient(token=TOKEN) as client:
        with pytest.raises(UWValidationError):
            client.get(path)


def test_first_activation_does_not_reset_existing_account_spend(monkeypatch):
    monkeypatch.setenv("RADON_UW_DAILY_LIMIT", "5")
    uw_budget.record_hits(5)
    with pytest.raises(gov.AdmissionError):
        gov.reserve(TOKEN, priority="interactive", timeout=0)
    assert gov.snapshot()["reserved_or_observed"] == 5


def test_verified_unlimited_removes_stale_legacy_scan_brake(monkeypatch):
    monkeypatch.setenv("RADON_UW_LIMITS_VERIFIED", "1")
    monkeypatch.setenv("RADON_UW_DAILY_LIMIT", "unlimited")
    uw_budget.record_hits(40000)
    assert uw_budget.should_block_universe_scan() is False
    assert uw_budget.remaining() == float("inf")
    assert uw_budget.usage_snapshot()["limit"] is None
    assert uw_budget.usage_snapshot()["remaining"] is None


def test_corrupt_legacy_state_still_blocks_under_unlimited_policy(monkeypatch):
    monkeypatch.setenv("RADON_UW_LIMITS_VERIFIED", "1")
    monkeypatch.setenv("RADON_UW_DAILY_LIMIT", "unlimited")
    uw_budget.BUDGET_PATH.write_text("broken")
    assert uw_budget.should_block_universe_scan() is True
    assert uw_budget.remaining() == 0


def test_client_does_not_follow_provider_redirects(monkeypatch):
    calls = []
    def get(_session, _url, **kwargs):
        calls.append(kwargs)
        return response(302, {"message": "redirect"}, {"Location": "http://127.0.0.1/private"})
    monkeypatch.setattr(requests.Session, "get", get)
    with UWClient(token=TOKEN) as client:
        from clients.uw_client import UWAPIError
        with pytest.raises(UWAPIError):
            client.get("market/economic-calendar", use_cache=False)
    assert calls[0]["allow_redirects"] is False
