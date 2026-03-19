"""Tests for CTA sync health ledger and payload validation."""

from pathlib import Path

from utils.cta_sync_health import (
    classify_sync_error,
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


def test_retry_backoffs_for_retryable_error():
    assert retry_backoffs_for_error("auth_rejected") == [0, 120, 600]


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
