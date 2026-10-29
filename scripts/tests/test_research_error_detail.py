"""classify_error and stage health keep a class name and a redacted detail."""
import json

import pytest

from research.ingestion import stage_health
from research.model import classify_error, error_detail


class _StatusError(RuntimeError):
    def __init__(self, message, status):
        super().__init__(message)
        self.status = status


def test_generic_error_keeps_class_name_and_records_detail():
    error = _StatusError("read failed", 504)
    assert classify_error(error) == "_StatusError"
    detail = error_detail(error)
    assert detail["class"] == "_StatusError"
    assert detail["message"] == "read failed"
    assert detail["http_status"] == 504
    assert detail["kind"] == "other"


def test_timeout_kind_covers_timeout_type_and_wrapped_read_timeout():
    from api.db_http import DbHttpError

    direct = error_detail(TimeoutError("The read operation timed out"))
    assert direct["class"] == "TimeoutError"
    assert direct["kind"] == "timeout"
    assert direct["http_status"] is None

    wrapped = DbHttpError("TimeoutError: The read operation timed out")
    detail = error_detail(wrapped)
    assert classify_error(wrapped) == "DbHttpError"
    assert detail["class"] == "DbHttpError"
    assert detail["kind"] == "timeout"
    assert "timed out" in detail["message"]


def test_stage_health_stores_detail_beside_the_class_name(tmp_path):
    from api.db_http import DbHttpError

    error = DbHttpError("TimeoutError: The read operation timed out")
    stage_health(tmp_path, "review", "error", error)
    payload = json.loads((tmp_path / "health-review.json").read_text())
    assert payload["error"] == "DbHttpError"
    assert payload["error_detail"]["class"] == "DbHttpError"
    assert payload["error_detail"]["kind"] == "timeout"
    assert payload["error_detail"]["http_status"] is None
    assert len(payload["error_detail"]["message"]) <= 240


def test_error_detail_redacts_secrets_tokens_credential_urls_and_auth_headers(tmp_path):
    token = "sk-ant-test1234567890abcdef"
    bearer = "eyJhbGciOiJub25lIn0.eyJzdWIiOiJvcCJ9.signature"
    message = (
        "DbHttpError: TimeoutError: read failed "
        f"Authorization: Bearer {bearer} "
        f"token={token} "
        "https://operator:s3cret-pass@radon-db.turso.io/v2/pipeline "
        "libsql://radon-db.turso.io?authToken=jwt-token-value"
    )
    error = TimeoutError(message)
    detail = error_detail(error)
    stage_health(tmp_path, "review", "error", error)
    stored = (tmp_path / "health-review.json").read_text()
    blob = detail["message"] + stored
    assert detail["class"] == "TimeoutError"
    assert detail["kind"] == "timeout"
    assert classify_error(error) == "TimeoutError"
    for secret in (token, bearer, "s3cret-pass", "jwt-token-value", "authToken="):
        assert secret not in blob
    assert len(detail["message"]) <= 240


def test_error_detail_truncates_long_messages():
    error = RuntimeError("x" * 2000)
    message = error_detail(error)["message"]
    assert message.endswith("...")
    assert len(message) <= 240


@pytest.mark.parametrize("status", [429, 503])
def test_http_status_comes_from_the_exception(status):
    detail = error_detail(_StatusError("nope", status))
    assert detail["http_status"] == status
    assert detail["kind"] == "other"
