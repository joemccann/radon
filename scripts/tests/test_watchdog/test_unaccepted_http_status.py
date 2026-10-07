"""REL-322 / R-733: an unaccepted HTTP reply cannot silence a real page."""
from datetime import datetime, timezone
import json
from unittest.mock import MagicMock
from urllib.error import HTTPError

import pytest

from watchdog import notify
from watchdog.check import CheckOutcome


@pytest.fixture
def refused_http(monkeypatch):
    # Run the real HTTP seam's HTTPError path with a fake opener. No credential
    # source is consulted, no database write or Pushover request can escape.
    monkeypatch.setattr(notify, "_pushover_creds", lambda: ("synthetic-user", "synthetic-token"))
    opener = MagicMock()
    monkeypatch.setattr(notify.urllib_request, "urlopen", opener)
    health, cooldown, page = MagicMock(), MagicMock(), MagicMock()
    monkeypatch.setattr(notify, "_write_dispatcher_health", health)
    monkeypatch.setattr(notify, "_mark_notified_best_effort", cooldown)
    monkeypatch.setattr(notify, "_enqueue_grok_page", page)
    return opener, health, cooldown, page


@pytest.mark.parametrize("status", [300, 307, 308])
def test_p1_unaccepted_response_never_arms_cooldown(refused_http, status):
    opener, health, cooldown, page = refused_http
    opener.side_effect = HTTPError("https://fake.invalid", status, "fake refusal", {}, None)
    outcome = CheckOutcome(service="fake-service", kind="error", status="error", severity="P1",
                           fired=True, message="synthetic fault", consecutive_failures=2,
                           now=datetime(2026, 10, 7, tzinfo=timezone.utc))
    error = notify.dispatch(outcome)
    assert error is not None
    assert str(status) in error
    assert health.call_args.kwargs["dispatcher_error"] == error
    cooldown.assert_not_called()
    page.assert_not_called()
    assert opener.call_count == 1
    request = opener.call_args.args[0]
    assert request.get_method() == "POST"
    assert request.full_url == notify.PUSHOVER_API_URL
    assert json.loads(request.data)["priority"] == 2


@pytest.mark.parametrize("status", [300, 307, 308])
def test_unaccepted_cancel_never_reports_emergency_cleared(refused_http, status):
    opener, _health, cooldown, page = refused_http
    opener.side_effect = HTTPError("https://fake.invalid", status, "fake refusal", {}, None)
    error = notify.cancel_emergency("synthetic-tag")
    assert error is not None
    assert str(status) in error
    cooldown.assert_not_called()
    page.assert_not_called()
    assert opener.call_count == 1
    request = opener.call_args.args[0]
    assert request.get_method() == "POST"
    assert request.full_url == "https://api.pushover.net/1/receipts/cancel_by_tag/synthetic-tag.json"
    assert json.loads(request.data) == {"token": "synthetic-token"}
