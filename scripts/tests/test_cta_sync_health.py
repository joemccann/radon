"""Tests for CTA sync health ledger and payload validation."""

import json
import os
from pathlib import Path

from utils.cta_sync_health import (
    classify_sync_error,
    latest_available_cta_date,
    load_cta_sync_status,
    retry_backoffs_for_error,
    validate_cta_payload,
    write_cta_sync_status,
)


def test_classify_auth_rejection():
    stderr = "ERROR: Login failed — still on login page after submit. page_excerpt=Your username or password was incorrect"
    error_type, message = classify_sync_error(stderr)
    assert error_type == "auth_rejected"
    assert "username or password was incorrect" in message


def test_classify_unauthorized_page():
    """Detect 'You are unauthorized' MenthorQ page as auth_rejected."""
    stderr = "No img src found for card slug: cta_table You are unauthorized to view this page"
    error_type, message = classify_sync_error(stderr)
    assert error_type == "auth_rejected"
    assert "unauthorized" in message.lower()


def test_classify_unauthorized_short():
    """Even short 'unauthorized' substring should trigger auth_rejected."""
    stderr = "page_excerpt=You are unauthorized to view this page. Email Password"
    error_type, message = classify_sync_error(stderr)
    assert error_type == "auth_rejected"


def test_classify_timeout():
    stderr = "Timeout 30000ms exceeded while waiting for networkidle"
    error_type, message = classify_sync_error(stderr)
    assert error_type == "timeout"
    assert "Timeout" in message


def test_classify_subprocess_timeout_expired():
    stderr = (
        "Command '['/home/radon/radon/.venv/bin/python3.13', "
        "'/home/radon/radon/scripts/fetch_menthorq_cta.py', '--json', "
        "'--date', '2026-09-17']' timed out after 300 seconds"
    )
    error_type, message = classify_sync_error(stderr)
    assert error_type == "timeout"
    assert "timed out" in message.lower()


def test_retry_backoffs_for_retryable_error():
    assert retry_backoffs_for_error("auth_rejected") == [0, 120, 600]


def test_retry_backoffs_for_timeout_fit_unit_start():
    assert retry_backoffs_for_error("timeout") == [0, 120]


def test_retry_backoffs_for_non_retryable_error():
    assert retry_backoffs_for_error("selector_failure") == [0]


def test_write_and_load_status(tmp_path: Path):
    status_path = tmp_path / "cta_sync_status.json"
    payload = {
        "state": "healthy",
        "target_date": "2026-03-12",
        "last_successful_date": "2026-03-12",
        "last_error": None,
    }

    write_cta_sync_status(payload, status_path)
    loaded = load_cta_sync_status(status_path)

    assert loaded is not None
    assert loaded["state"] == "healthy"
    assert loaded["last_successful_date"] == "2026-03-12"


def test_validate_cta_payload_accepts_expected_tables():
    payload = {
        "date": "2026-03-12",
        "tables": {
            "main": [{"underlying": "SPX"}],
            "index": [],
            "commodity": [],
            "currency": [],
        },
    }
    ok, reason = validate_cta_payload(payload, "2026-03-12")
    assert ok is True
    assert reason is None


def test_validate_cta_payload_rejects_target_mismatch():
    payload = {
        "date": "2026-03-11",
        "tables": {
            "main": [{"underlying": "SPX"}],
            "index": [],
            "commodity": [],
            "currency": [],
        },
    }
    ok, reason = validate_cta_payload(payload, "2026-03-12")
    assert ok is False
    assert reason == "target_date_mismatch"


def test_validate_cta_payload_rejects_empty_tables():
    payload = {
        "date": "2026-03-12",
        "tables": {
            "main": [],
            "index": [],
            "commodity": [],
            "currency": [],
        },
    }
    ok, reason = validate_cta_payload(payload, "2026-03-12")
    assert ok is False
    assert reason == "empty_tables"


# ── latest_available_cta_date ─────────────────────────────────────────────────


def test_latest_available_cta_date_returns_none_for_empty_dir(tmp_path: Path):
    assert latest_available_cta_date(tmp_path) is None


def test_latest_available_cta_date_returns_correct_date(tmp_path: Path):
    (tmp_path / "cta_2026-03-17.json").write_text("{}")
    (tmp_path / "cta_2026-03-18.json").write_text("{}")
    result = latest_available_cta_date(tmp_path)
    assert result == "2026-03-18"


def test_latest_available_cta_date_ignores_non_date_json(tmp_path: Path):
    """cta_sync_status.json must NOT be treated as a CTA data file."""
    (tmp_path / "cta_2026-03-17.json").write_text("{}")
    (tmp_path / "cta_sync_status.json").write_text('{"state": "syncing"}')
    result = latest_available_cta_date(tmp_path)
    # Bug: currently returns "sync_status" because sorted() puts 's' after digits
    assert result == "2026-03-17"


def test_latest_available_cta_date_only_non_date_json_returns_none(tmp_path: Path):
    """If only non-date JSON files are present, return None."""
    (tmp_path / "cta_sync_status.json").write_text('{"state": "syncing"}')
    result = latest_available_cta_date(tmp_path)
    assert result is None


# ── stale lock detection ──────────────────────────────────────────────────────


def test_cta_sync_lock_clears_stale_lock_and_acquires(tmp_path: Path):
    """Lock held by a dead PID must be cleared so a new run can acquire it."""
    from cta_sync_service import CtaSyncLockError, cta_sync_lock

    lock_dir = tmp_path / "cta-sync.lock"
    lock_dir.mkdir()
    dead_pid = 99999  # almost certainly not a real process
    (lock_dir / "lock.json").write_text(
        json.dumps({"pid": dead_pid, "run_id": "old", "target_date": "2026-03-18", "started_at": "2026-03-18T03:00:00Z"})
    )
    assert lock_dir.exists()

    # Should NOT raise — stale lock should be cleared automatically
    acquired = False
    with cta_sync_lock(target_date="2026-03-19", run_id="new-run", lock_dir=lock_dir):
        acquired = True
    assert acquired
    assert not lock_dir.exists()  # cleaned up after context exit


def test_cta_sync_lock_raises_when_live_pid_holds_lock(tmp_path: Path):
    """Lock held by the current (live) process must not be stolen."""
    from cta_sync_service import CtaSyncLockError, cta_sync_lock

    lock_dir = tmp_path / "cta-sync.lock"
    lock_dir.mkdir()
    live_pid = os.getpid()
    (lock_dir / "lock.json").write_text(
        json.dumps({"pid": live_pid, "run_id": "active", "target_date": "2026-03-19", "started_at": "2026-03-19T03:00:00Z"})
    )

    import pytest
    with pytest.raises(CtaSyncLockError):
        with cta_sync_lock(target_date="2026-03-19", run_id="interloper", lock_dir=lock_dir):
            pass
