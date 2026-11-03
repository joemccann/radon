"""2026-10-15: an expired subscription OAuth token cost a research call a 401.

Two guards: the timer refreshes a run early (skew > cadence), and the ladder
refreshes in-band and retries once when a provider still answers 401 expired.
"""
from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

import pytest

pytestmark = pytest.mark.usefixtures("isolated_model_credentials")

import subscription_tokens as st
from model_gateway import engine as model_ladder
from model_gateway.engine import complete_multimodal_json

EXPIRED_BODY = json.dumps(
    {
        "type": "error",
        "error": {
            "type": "authentication_error",
            "message": "OAuth access token has expired. Re-authenticate to continue.",
        },
    }
)


class _Resp:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        if isinstance(payload, str):
            self.text = payload
            self._json = None
        else:
            self._json = payload
            self.text = json.dumps(payload)

    def json(self):
        return self._json if self._json is not None else json.loads(self.text)


def _ok():
    return _Resp(200, {"stop_reason": "end_turn", "content": [{"type": "text", "text": json.dumps({"candidates": [{"title": "t"}]})}]})


@pytest.fixture(autouse=True)
def _hermetic_home(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home"))


def test_skew_exceeds_timer_cadence_plus_jitter():
    # Timer: every 30min + RandomizedDelaySec=60. A token must never be judged
    # "live" at one fire and be expired before the next one starts.
    assert st.EXPIRY_SKEW_SECONDS > 30 * 60 + 60


def test_token_with_31_minutes_left_is_refreshed_not_left_live(tmp_path):
    # Mirrors 13:30Z on 2026-10-15: 30min left was judged live under 600s skew.
    from tests.test_subscription_tokens import (  # type: ignore
        NOW, FakeHttp, anthropic_doc, make_runtime,
    )

    rt = make_runtime(tmp_path, http=FakeHttp())
    run = st._Run(rt, "check", False)
    doc = anthropic_doc(NOW + timedelta(minutes=31))
    assert st._is_usable(run, st.PROVIDERS["anthropic"], doc) is False


def test_expired_401_refreshes_reloads_and_retries_once(tmp_path, monkeypatch):
    token_file = tmp_path / "claude-token"
    token_file.write_text("old-token")
    sent: list[str] = []
    refreshed: list[str] = []

    def post(url, *, headers=None, json=None, timeout=None, stream=False):
        sent.append((headers or {}).get("authorization", ""))
        if "old-token" in sent[-1]:
            return _Resp(401, EXPIRED_BODY)
        return _ok()

    def fake_refresh(name):
        refreshed.append(name)
        token_file.write_text("new-token")

    monkeypatch.setattr(model_ladder, "_refresh_subscription_token", fake_refresh)
    result = complete_multimodal_json(
        "evaluate",
        env={"CLAUDE_CODE_OAUTH_TOKEN_FILE": str(token_file)},
        post=post,
        stream_anthropic=False,
        providers=("anthropic",),
    )

    assert result.provider == "anthropic"
    assert refreshed == ["anthropic"]
    assert sent == ["Bearer old-token", "Bearer new-token"]


def test_refresh_is_attempted_only_once_per_provider(tmp_path, monkeypatch):
    token_file = tmp_path / "claude-token"
    token_file.write_text("old-token")
    refreshed: list[str] = []
    calls = {"n": 0}

    def post(url, *, headers=None, json=None, timeout=None, stream=False):
        calls["n"] += 1
        return _Resp(401, EXPIRED_BODY)

    monkeypatch.setattr(
        model_ladder, "_refresh_subscription_token", lambda name: refreshed.append(name)
    )
    with pytest.raises(model_ladder.ModelLadderExhausted):
        complete_multimodal_json(
            "evaluate",
            env={"CLAUDE_CODE_OAUTH_TOKEN_FILE": str(token_file)},
            post=post,
            stream_anthropic=False,
            providers=("anthropic",),
        )
    assert refreshed == ["anthropic"]


def test_plain_401_without_expiry_text_does_not_refresh(tmp_path, monkeypatch):
    token_file = tmp_path / "claude-token"
    token_file.write_text("tok")
    refreshed: list[str] = []

    def post(url, *, headers=None, json=None, timeout=None, stream=False):
        return _Resp(401, {"error": {"message": "invalid x-api-key"}})

    monkeypatch.setattr(
        model_ladder, "_refresh_subscription_token", lambda name: refreshed.append(name)
    )
    with pytest.raises(model_ladder.ModelLadderExhausted):
        complete_multimodal_json(
            "evaluate",
            env={"CLAUDE_CODE_OAUTH_TOKEN_FILE": str(token_file)},
            post=post,
            stream_anthropic=False,
            providers=("anthropic",),
        )
    assert refreshed == []


def test_expired_marker_requires_subscription_auth_and_refreshable_provider():
    sub = model_ladder.AuthMaterial("t", "subscription", "x")
    key = model_ladder.AuthMaterial("t", "api_key", "x")
    assert model_ladder._is_expired_subscription_token("anthropic", sub, 401, EXPIRED_BODY)
    assert model_ladder._is_expired_subscription_token("antigravity", sub, 401, EXPIRED_BODY)
    assert not model_ladder._is_expired_subscription_token("anthropic", key, 401, EXPIRED_BODY)
    assert not model_ladder._is_expired_subscription_token("cerebras", sub, 401, EXPIRED_BODY)
    assert not model_ladder._is_expired_subscription_token("anthropic", sub, 403, EXPIRED_BODY)


def test_in_band_refresh_never_runs_a_push_login(monkeypatch):
    seen = {}

    def fake_run(mode, names, runtime=None, **_k):
        seen["mode"], seen["names"], seen["rt"] = mode, names, runtime
        return {}

    monkeypatch.setattr(st, "run", fake_run)
    model_ladder._refresh_subscription_token("anthropic")
    assert seen["mode"] == "once" and seen["names"] == ["anthropic"]
    assert seen["rt"].login() is False


def test_in_band_refresh_swallows_errors(monkeypatch):
    def boom(*_a, **_k):
        raise RuntimeError("vault down")

    monkeypatch.setattr(st, "run", boom)
    model_ladder._refresh_subscription_token("anthropic")  # does not raise
