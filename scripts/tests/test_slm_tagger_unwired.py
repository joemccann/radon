"""The SLM tagger experiment is unwired from every live request path.

Newsfeed tagging, the model gateway and the ladder CLI must work with
``newsfeed.slm`` unimportable, whatever ``RADON_SLM_TAGGER_MODE`` says, and
must never route to the ``slm-tagger`` rung or return a shadow row.
"""
from __future__ import annotations

import io
import json
import sys

import pytest

from model_gateway.client import GatewayError
from model_gateway.contracts import LadderResult

TAGS = {"tags": ["GAMMA", "SPX", "VOL"]}


@pytest.fixture
def slm_unimportable(monkeypatch):
    for name in list(sys.modules):
        if name == "newsfeed.slm" or name.startswith("newsfeed.slm.") or name in {
            "model_gateway.slm",
            "clients.model_ladder_cli",
        }:
            monkeypatch.delitem(sys.modules, name)
    monkeypatch.setitem(sys.modules, "newsfeed.slm", None)
    monkeypatch.setenv("RADON_SLM_TAGGER_MODE", "shadow")
    monkeypatch.setenv("RADON_SLM_TAGGER_URL", "http://127.0.0.1:8331")
    with pytest.raises(ImportError):
        __import__("newsfeed.slm.modes")


def _answer(name):
    text = json.dumps(TAGS)
    if name == "anthropic":
        return 200, "", {"content": [{"type": "text", "text": text}], "stop_reason": "end_turn"}
    return 200, "", {"choices": [{"message": {"content": text}}]}


def _gateway(monkeypatch, calls):
    from model_gateway import engine
    from model_gateway.server import Gateway

    monkeypatch.setattr(engine, "_auth_for", lambda name, env: engine.AuthMaterial("fixture", "subscription", "test"))
    monkeypatch.setattr(engine, "_model_for", lambda name, env, **kw: name + "-model")

    def call(name, *args, **kwargs):
        calls.append(name)
        return _answer(name)

    monkeypatch.setattr(engine, "_call_text_provider", call)
    return Gateway()


def test_newsfeed_tags_request_uses_only_the_ladder(monkeypatch, slm_unimportable):
    calls = []
    gateway = _gateway(monkeypatch, calls)
    reply = gateway.handle("json", {
        "instruction": "Title: x\nBody: y", "system": "tagger", "accept": "tags",
        "purpose": "newsfeed-tags", "post_id": "post-1", "taxonomy": ["GAMMA", "SPX", "VOL"],
    })
    assert reply["data"] == TAGS
    assert "shadow" not in reply
    assert "slm-tagger" not in calls
    assert calls[0] == "antigravity"


def test_gateway_refuses_the_slm_rung(monkeypatch, slm_unimportable):
    gateway = _gateway(monkeypatch, [])
    with pytest.raises(GatewayError) as error:
        gateway.handle("json", {"instruction": "facts", "providers": ["slm-tagger"]})
    assert error.value.status == 400


def test_engine_never_calls_the_slm_rung(monkeypatch, slm_unimportable):
    from model_gateway import engine

    calls = []
    monkeypatch.setattr(engine, "_slm_health_ok", lambda _url: True, raising=False)
    monkeypatch.setattr(engine, "_call_text_provider", lambda name, *a, **kw: calls.append(name) or _answer(name))
    env = {"RADON_SLM_TAGGER_URL": "http://127.0.0.1:8331", "CLAUDE_CODE_OAUTH_TOKEN": "fixture"}
    result = engine.complete_text_json("Title: x", env=env, providers=["slm-tagger", "anthropic"],
                                       accept=engine.accept_tags_payload)
    assert result.provider == "anthropic"
    assert calls == ["anthropic"]


def test_ladder_cli_runs_the_plain_ladder(slm_unimportable):
    from clients.model_ladder_cli import main

    captured = []

    def complete(instruction, **kwargs):
        captured.append(kwargs)
        return LadderResult(data=TAGS, text=json.dumps(TAGS), provider="anthropic", model="m")

    out = io.StringIO()
    payload = {"instruction": "Title: x", "accept": "tags", "post_id": "post-1"}
    assert main(stdin=io.StringIO(json.dumps(payload)), stdout=out, complete=complete) == 0
    assert json.loads(out.getvalue()) == {"ok": True, "data": TAGS, "provider": "anthropic", "model": "m"}
    assert len(captured) == 1 and "providers" not in captured[0]
