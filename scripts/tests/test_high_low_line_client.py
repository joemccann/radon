"""Bounded HTTP contracts; every transport is fake and never contacts sources."""
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import requests
from clients import high_low_source_client as client
from test_high_low_line import NOW


class Response:
    def __init__(self, status=200, chunks=(b'{"history":{}}',)):
        self.status_code, self.chunks, self.closed = status, chunks, False
    def __enter__(self): return self
    def __exit__(self, *args): self.closed = True
    def iter_content(self, **kwargs): return iter(self.chunks)


def source(monkeypatch, outcomes, **kwargs):
    session = SimpleNamespace(headers={}, get=Mock(side_effect=outcomes), close=Mock())
    monkeypatch.setattr(client.requests, "Session", lambda: session)
    monkeypatch.setattr(client.time, "sleep", Mock())
    return client.HighLowSourceClient(**kwargs), session


def test_honest_verified_url_dates_and_context_cleanup(monkeypatch):
    response = Response()
    api, session = source(monkeypatch, [response])
    with api:
        assert api.fetch_history("!NEWHINYC", now=NOW) == b'{"history":{}}'
    args, kwargs = session.get.call_args
    assert args == (client.HISTORY_URL,)
    assert kwargs["params"] == {"symbol": "!NEWHINYC", "start": "20030101", "end": "20261021"}
    assert session.headers == {"User-Agent": "radon/2.0"}
    assert "verify" not in kwargs  # requests default verifies TLS
    assert kwargs["allow_redirects"] is False
    assert kwargs["stream"] is True and kwargs["timeout"] == (8, 25)
    assert response.closed and session.close.call_count == 1


@pytest.mark.parametrize("status", [304, 302, 403, 404])
def test_no_fake_conditional_or_redirect_fallback(monkeypatch, status):
    api, session = source(monkeypatch, [Response(status)])
    with pytest.raises(client.HighLowSourceError, match=str(status)):
        api.fetch_history("!NEWHINYC", now=NOW)
    assert session.get.call_count == 1


def test_transient_status_retry_is_bounded(monkeypatch):
    api, session = source(monkeypatch, [Response(503), Response(429), Response()])
    assert api.fetch_history("!NEWLONYC", now=NOW)
    assert session.get.call_count == 3
    assert [x.args[0] for x in client.time.sleep.call_args_list] == [1, 2]


@pytest.mark.parametrize("failure", [requests.Timeout, requests.ConnectionError])
def test_transport_retry_exhaustion(monkeypatch, failure):
    api, session = source(monkeypatch, [failure("down")] * 3)
    with pytest.raises(client.HighLowSourceError, match="retries exhausted"):
        api.fetch_history("!NEWHINYC", now=NOW)
    assert session.get.call_count == 3


def test_decoded_body_bound_empty_and_deadline(monkeypatch):
    monkeypatch.setattr(client, "MAX_RESPONSE_BYTES", 4)
    api, session = source(monkeypatch, [Response(chunks=[b"123", b"45"]), Response(chunks=[])])
    with pytest.raises(client.HighLowSourceError, match="8 MiB"):
        api.fetch_history("!NEWHINYC", now=NOW)
    with pytest.raises(client.HighLowSourceError, match="empty"):
        api.fetch_history("!NEWHINYC", now=NOW)
    api.expires = 0
    with pytest.raises(client.HighLowSourceError, match="deadline exceeded"):
        api.fetch_history("!NEWHINYC", now=NOW)


def test_invalid_source_clock_and_options_do_not_request(monkeypatch):
    api, session = source(monkeypatch, [])
    with pytest.raises(ValueError): api.fetch_history("$NYHGH", now=NOW)
    with pytest.raises(ValueError): api.fetch_history("!NEWHINYC", now=datetime(2026, 10, 20))
    with pytest.raises(ValueError): client.HighLowSourceClient(timeout=0)
    assert session.get.call_count == 0


def test_deadline_prevents_retry_sleep(monkeypatch):
    api, _ = source(monkeypatch, [Response(503)], backoff_factor=999)
    with pytest.raises(client.HighLowSourceError, match="retry exceeds"):
        api.fetch_history("!NEWHINYC", now=NOW)
    assert client.time.sleep.call_count == 0
