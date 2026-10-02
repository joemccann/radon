"""research/nemotron_parse.py shares the NVIDIA key's host-wide pace (2026-09-30).

Unit-level on ``_post_with_retries`` so it runs without the PDF runtime.
"""
from __future__ import annotations

import pytest

from research import nemotron_parse
from research.nemotron_parse import PageParseError, _post_with_retries


class _Limiter:
    def __init__(self, allow=True):
        self.allow = allow
        self.events = []

    def acquire(self, max_wait):
        self.events.append("acquire")
        return self.allow

    def note_429(self, retry_after):
        self.events.append(("429", retry_after))

    def note_auth_failure(self, status):
        self.events.append(("auth", status))

    def note_success(self):
        self.events.append("ok")


class _Resp:
    def __init__(self, status, headers=None, text="{}"):
        self.status_code = status
        self.headers = headers or {}
        self.text = text

    def json(self):
        return {}


@pytest.fixture
def limiter(monkeypatch):
    import clients.model_ladder as ladder
    lim = _Limiter()
    monkeypatch.setattr(ladder, "_nvidia_limiter", lambda env, post: lim)
    monkeypatch.setenv("RADON_RESEARCH_PARSE_MAX_RETRIES", "0")
    return lim


def _call(post):
    return _post_with_retries(nemotron_parse.parse_models()[0], {"model": "m"}, post=post,
                              sleep=lambda _s: None, monotonic=lambda: 0.0,
                              jitter=lambda: 0.0, deadline=100.0)


def test_nothing_is_sent_when_the_budget_is_spent(limiter):
    limiter.allow = False
    calls = []
    with pytest.raises(PageParseError, match="http_429"):
        _call(lambda url, **kw: calls.append(url))
    assert calls == []


def test_retry_after_from_a_429_reaches_the_limiter(limiter):
    with pytest.raises(PageParseError):
        _call(lambda url, **kw: _Resp(429, {"retry-after": "7"}, "rate limit"))
    assert limiter.events == ["acquire", ("429", 7.0)]


def test_a_403_trips_the_limiter_and_is_not_retried(limiter, monkeypatch):
    monkeypatch.setenv("RADON_RESEARCH_PARSE_MAX_RETRIES", "2")
    calls = []

    def post(url, **kw):
        calls.append(url)
        return _Resp(403, text='{"detail":"Authorization failed"}')

    with pytest.raises(PageParseError):
        _call(post)
    assert ("auth", 403) in limiter.events
    assert len(calls) == 1
