"""Regression tests for `fetch_cash_transactions` IBKR Flex error handling.

Production incidents:

  2026-05-09 (#1): the service-health banner showed "Flex SendRequest
  did not return a ReferenceCode" — a generic message that hid the
  real IBKR error. Now we surface IBKR's ErrorCode + ErrorMessage so
  an operator can tell transient from auth from config.

  2026-05-09 (#2): the daemon's 4h cadence with internal 3-attempt
  retries (12 hits/day) perpetuated a Flex sliding-window throttle
  for ~24h. Every retry on a throttle code (1001/1018/1019) pushes
  the reset further out — so the script must NOT retry internally
  on those codes. It raises FlexThrottleError on the first hit and
  the handler decides when to try again (typically tomorrow at
  17:00 ET via the throttle-aware circuit breaker).

  Other transient failures (network blip, parse error) still allow
  ONE bounded retry within the call.
"""
from __future__ import annotations

import io
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

# Make scripts/ importable.
SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import xml.etree.ElementTree as ET  # noqa: E402

import cash_flow_sync  # noqa: E402
from cash_flow_sync import fetch_cash_transactions  # noqa: E402
from monitor_daemon.handlers._throttle_backoff import FlexThrottleError  # noqa: E402


def _xml_response(body: str) -> MagicMock:
    """Mimic the file-like object urlopen returns."""
    resp = MagicMock()
    resp.read.return_value = body.encode("utf-8")
    return resp


FAIL_1001 = (
    "<FlexStatementResponse timestamp='09 May, 2026 04:21 PM EDT'>"
    "<Status>Fail</Status>"
    "<ErrorCode>1001</ErrorCode>"
    "<ErrorMessage>Statement could not be generated at this time. "
    "Please try again shortly.</ErrorMessage>"
    "</FlexStatementResponse>"
)

FAIL_1019 = (
    "<FlexStatementResponse>"
    "<Status>Warn</Status>"
    "<ErrorCode>1019</ErrorCode>"
    "<ErrorMessage>Statement generation in progress. Please try "
    "again shortly.</ErrorMessage>"
    "</FlexStatementResponse>"
)

FAIL_AUTH = (
    "<FlexStatementResponse>"
    "<Status>Fail</Status>"
    "<ErrorCode>1012</ErrorCode>"
    "<ErrorMessage>Token has expired.</ErrorMessage>"
    "</FlexStatementResponse>"
)

SUCCESS_REF = (
    "<FlexStatementResponse>"
    "<Status>Success</Status>"
    "<ReferenceCode>1234567890</ReferenceCode>"
    "<Url>https://example.com</Url>"
    "</FlexStatementResponse>"
)

SUCCESS_STMT = (
    "<FlexQueryResponse>"
    "<FlexStatements count='1'>"
    "<FlexStatement accountId='U123'>"
    "<CashTransactions>"
    "<CashTransaction transactionID='42' amount='100.00' "
    "type='Deposit' reportDate='20260509' currency='USD' "
    "description='ACH Deposit' />"
    "</CashTransactions>"
    "</FlexStatement>"
    "</FlexStatements>"
    "</FlexQueryResponse>"
)


FAIL_1018 = (
    "<FlexStatementResponse>"
    "<Status>Fail</Status>"
    "<ErrorCode>1018</ErrorCode>"
    "<ErrorMessage>Too many requests have been made from this token. "
    "Please try again shortly.</ErrorMessage>"
    "</FlexStatementResponse>"
)


class TestFlexErrorSurface:
    """Surface IBKR's actual error code + message instead of a generic."""

    def test_includes_error_code_in_exception(self):
        with patch("cash_flow_sync.urlopen") as mock_urlopen, \
             patch("cash_flow_sync.time.sleep"):
            mock_urlopen.return_value = _xml_response(FAIL_1001)
            # Type-agnostic: this class asserts the MESSAGE surface, not the
            # classification. 1001 is transient, not a throttle.
            with pytest.raises(RuntimeError) as excinfo:
                fetch_cash_transactions("tok", "qid")
            msg = str(excinfo.value)
            assert "1001" in msg, f"error code 1001 missing: {msg!r}"

    def test_includes_error_message_in_exception(self):
        with patch("cash_flow_sync.urlopen") as mock_urlopen, \
             patch("cash_flow_sync.time.sleep"):
            mock_urlopen.return_value = _xml_response(FAIL_1001)
            # Type-agnostic: this class asserts the MESSAGE surface, not the
            # classification. 1001 is transient, not a throttle.
            with pytest.raises(RuntimeError) as excinfo:
                fetch_cash_transactions("tok", "qid")
            msg = str(excinfo.value)
            assert "Statement could not be generated" in msg, (
                f"IBKR error message missing: {msg!r}"
            )

    def test_does_not_emit_generic_reference_code_message(self):
        """Regression: the old generic message hid the real IBKR error."""
        with patch("cash_flow_sync.urlopen") as mock_urlopen, \
             patch("cash_flow_sync.time.sleep"):
            mock_urlopen.return_value = _xml_response(FAIL_1001)
            # Type-agnostic: this class asserts the MESSAGE surface, not the
            # classification. 1001 is transient, not a throttle.
            with pytest.raises(RuntimeError) as excinfo:
                fetch_cash_transactions("tok", "qid")
            msg = str(excinfo.value)
            assert "did not return a ReferenceCode" not in msg, (
                f"generic message regressed: {msg!r}"
            )

    def test_auth_failure_surfaces_real_message(self):
        with patch("cash_flow_sync.urlopen") as mock_urlopen, \
             patch("cash_flow_sync.time.sleep"):
            mock_urlopen.return_value = _xml_response(FAIL_AUTH)
            with pytest.raises(RuntimeError) as excinfo:
                fetch_cash_transactions("tok", "qid")
            msg = str(excinfo.value)
            assert "1012" in msg
            assert "Token has expired" in msg


class TestThrottleNoInternalRetry:
    """Only 1018 is a rate limit, and it must NOT trigger an internal retry.

    This class used to assert that 1001 and 1019 were throttles too. They are
    not, per IBKR's published error table:

        1001  Statement could not be generated at this time. Try again shortly.
        1018  Too many requests have been made from this token. Try again
              shortly.  One request per second, 10 per minute (per token).
        1019  Statement generation in progress. Try again shortly.

    Treating 1019 as a throttle meant the ordinary "still generating" response
    bought a 24-hour backoff, and treating 1001 as one escalated a ladder toward
    a week-long embargo over a transient server-side failure. Those two rows are
    now pinned to the CORRECT classification below rather than deleted, so the
    old behaviour cannot come back.
    """

    def test_1001_is_transient_not_a_throttle(self):
        """IBKR says try again shortly. It must not touch the breaker ladder."""
        with patch("cash_flow_sync.urlopen") as mock_urlopen, \
             patch("cash_flow_sync.time.sleep"):
            mock_urlopen.return_value = _xml_response(FAIL_1001)
            with pytest.raises(cash_flow_sync._FlexTransientError):
                fetch_cash_transactions("tok", "qid")
            # Explicitly NOT the throttle type — that is the whole point.
            mock_urlopen.return_value = _xml_response(FAIL_1001)
            with pytest.raises(Exception) as excinfo:
                fetch_cash_transactions("tok", "qid")
            assert not isinstance(excinfo.value, FlexThrottleError)

    def test_1019_on_a_poll_is_not_ready_and_keeps_polling(self):
        """"Generation in progress" is the normal not-ready response. Aborting
        the poll loop on it is what turned a few seconds into 24 hours."""
        root = ET.fromstring(FAIL_1019)
        assert cash_flow_sync._flex_error_from(root, "GetStatement") is None

    def test_1019_on_a_send_is_transient_not_permanent(self):
        """A generation already in flight is retryable. It must not fall through
        to the "no ReferenceCode" hard error, which is classified never-retry."""
        root = ET.fromstring(FAIL_1019)
        exc = cash_flow_sync._flex_error_from(root, "SendRequest")
        assert isinstance(exc, cash_flow_sync._FlexTransientError)
        assert not isinstance(exc, cash_flow_sync._FlexAppError)

    def test_1018_raises_flex_throttle_error_immediately(self):
        with patch("cash_flow_sync.urlopen") as mock_urlopen, \
             patch("cash_flow_sync.time.sleep") as mock_sleep:
            mock_urlopen.return_value = _xml_response(FAIL_1018)
            with pytest.raises(FlexThrottleError) as excinfo:
                fetch_cash_transactions("tok", "qid")
            assert excinfo.value.code == "1018"
            assert mock_urlopen.call_count == 1
            mock_sleep.assert_not_called()

    def test_throttle_error_is_runtime_error_subclass(self):
        """Existing callers that catch RuntimeError still work."""
        with patch("cash_flow_sync.urlopen") as mock_urlopen, \
             patch("cash_flow_sync.time.sleep"):
            mock_urlopen.return_value = _xml_response(FAIL_1018)
            with pytest.raises(RuntimeError):
                fetch_cash_transactions("tok", "qid")


class TestNonThrottleFailures:
    """Non-throttle errors keep their existing semantics."""

    def test_does_not_retry_on_auth_failure(self):
        """Auth/permission errors are NOT transient — fail fast."""
        with patch("cash_flow_sync.urlopen") as mock_urlopen, \
             patch("cash_flow_sync.time.sleep"):
            mock_urlopen.return_value = _xml_response(FAIL_AUTH)
            with pytest.raises(RuntimeError) as excinfo:
                fetch_cash_transactions("tok", "qid")
            # Auth failures must NOT be FlexThrottleError — that would
            # advance the circuit breaker incorrectly.
            assert not isinstance(excinfo.value, FlexThrottleError)
            assert mock_urlopen.call_count == 1

    def test_network_error_retried_at_most_once(self):
        """A network blip is NOT a throttle — bounded single retry is fine."""
        from urllib.error import URLError

        side_effects = [
            URLError("connection reset"),
            _xml_response(SUCCESS_REF),
            _xml_response(SUCCESS_STMT),
        ]
        with patch("cash_flow_sync.urlopen") as mock_urlopen, \
             patch("cash_flow_sync.time.sleep"):
            mock_urlopen.side_effect = side_effects
            rows = fetch_cash_transactions("tok", "qid", max_polls=5, poll_sleep=0)
            assert len(rows) == 1
            # Exactly 3: 1 failed + 1 retry SendRequest + 1 GetStatement.
            assert mock_urlopen.call_count == 3

    def test_persistent_network_error_fails_after_one_retry(self):
        """If both attempts fail with network errors, raise — don't loop."""
        from urllib.error import URLError

        with patch("cash_flow_sync.urlopen") as mock_urlopen, \
             patch("cash_flow_sync.time.sleep"):
            mock_urlopen.side_effect = URLError("connection reset")
            with pytest.raises(Exception):
                fetch_cash_transactions("tok", "qid")
            # 2 attempts total (initial + 1 retry).
            assert mock_urlopen.call_count == 2
