"""An expired Grok grant is renewed, not silently dropped.

grok's auth reader skips an entry whose `expires_at` has passed, so the ladder
saw "no_key" and never sent a request; the in-band refresh only fires on a 401,
so nothing ever renewed it. The VPS hides this behind its 30-minute timer; the
Mac gateway has no timer, and Grok vanished from /health hours after sign-in
(2026-11-11). The ladder now asks for a renewal when a grant is present but
expired, then re-reads it.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from model_gateway import engine
from model_gateway.engine import complete_multimodal_json

ISSUER = "https://auth.x.ai::client"


class _Resp:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self.text = json.dumps(payload)

    def json(self):
        return json.loads(self.text)


OK = {"choices": [{"message": {"content": '{"ok": true}'}}]}


def write_grant(home: Path, key: str, expires_at: str) -> None:
    path = home / ".grok" / "auth.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({ISSUER: {"key": key, "refresh_token": "r", "expires_at": expires_at}}))


@pytest.fixture
def home(monkeypatch, tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    return home


def run(home, monkeypatch, refresh):
    sent = []

    def post(url, *, headers=None, json=None, timeout=None, stream=False):
        sent.append((url, (headers or {}).get("authorization") or (headers or {}).get("Authorization")))
        return _Resp(200, OK)

    monkeypatch.setattr(engine, "_refresh_subscription_token", refresh)
    try:
        result = complete_multimodal_json("evaluate", env={"HOME": str(home)}, post=post, providers=("grok",))
    except engine.ModelLadderExhausted as exc:
        return None, sent, str(exc)
    return result, sent, ""


def test_expired_grant_is_renewed_then_used(home, monkeypatch):
    write_grant(home, "old", "2000-01-01T00:00:00Z")
    renewed = []

    def refresh(name):
        renewed.append(name)
        write_grant(home, "fresh", "2999-01-01T00:00:00Z")

    result, sent, _ = run(home, monkeypatch, refresh)
    assert renewed == ["grok"]
    assert result is not None and result.provider == "grok"
    assert sent == [("https://api.x.ai/v1/chat/completions", "Bearer fresh")]


def test_pending_renewal_sends_nothing_and_says_so(home, monkeypatch):
    write_grant(home, "old", "2000-01-01T00:00:00Z")
    renewed = []
    # The gateway's hook only queues a renewal job; the file is still expired.
    result, sent, error = run(home, monkeypatch, renewed.append)
    assert renewed == ["grok"]
    assert result is None and sent == []
    assert "grok:token_refresh_pending" in error


@pytest.mark.parametrize("grant", [None, "live"])
def test_missing_or_live_grant_does_not_ask_for_renewal(home, monkeypatch, grant):
    if grant:
        write_grant(home, "live", "2999-01-01T00:00:00Z")
    renewed = []
    _, sent, _ = run(home, monkeypatch, renewed.append)
    assert renewed == []
    assert sent == ([("https://api.x.ai/v1/chat/completions", "Bearer live")] if grant else [])
