"""Shared model ladder: Joe order, failover, text JSON and vision paths."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

# T-498: file-backed subscriptions are opt-in synthetic fixtures.
pytestmark = pytest.mark.usefixtures("isolated_model_credentials")

from clients import model_ladder
from clients.model_ladder import (
    MODEL_LADDER_ORDER,
    MODEL_LADDER_TIERS,
    ModelLadderExhausted,
    ModelResponseError,
    accept_distill_payload,
    accept_tags_payload,
    complete_multimodal_json,
    complete_text_json,
    extract_via_vision,
    wired_providers,
)


@pytest.fixture(autouse=True)
def _hermetic_auth_home(monkeypatch, tmp_path):
    # Ladder auth discovery falls back to Path.home(); keep host auth files
    # (~/.claude, ~/.codex, ~/.grok) out of these hermetic-env tests.
    monkeypatch.setenv("HOME", str(tmp_path / "hermetic-home"))
    monkeypatch.setattr(
        Path, "home", classmethod(lambda cls: tmp_path / "hermetic-home")
    )


ROWS = [{"underlying": "E-Mini S&P 500 Index", "position_today": 0.45}]
OBJ = {"candidates": [{"title": "test"}]}
PROMPT = "extract"
PNG = b"\x89PNG-fake"


class _Resp:
    def __init__(self, status_code: int, payload):
        self.status_code = status_code
        if isinstance(payload, str):
            self.text = payload
            self._json = None
        else:
            self._json = payload
            self.text = json.dumps(payload)

    def json(self):
        if self._json is not None:
            return self._json
        return json.loads(self.text)


def _anthropic_ok(rows=None):
    return _Resp(200, {"content": [{"type": "text", "text": json.dumps(rows or ROWS)}]})


def _anthropic_obj_ok(obj=None):
    return _Resp(
        200,
        {
            "stop_reason": "end_turn",
            "content": [{"type": "text", "text": json.dumps(obj or OBJ)}],
        },
    )


def _openai_ok(rows=None):
    return _Resp(200, {"choices": [{"message": {"content": json.dumps(rows or ROWS)}}]})


def _openai_obj_ok(obj=None):
    return _Resp(200, {"choices": [{"message": {"content": json.dumps(obj or OBJ)}}]})


def _credit_low():
    return _Resp(
        400,
        {
            "error": {
                "message": (
                    "Your credit balance is too low to access the Anthropic API. "
                    "Please go to Plans & Billing to upgrade or purchase credits."
                ),
            }
        },
    )


ALL_KEYS = {
    "ANTHROPIC_API_KEY": "sk-ant-test",
    "XAI_API_KEY": "xai-test",
    "OPENAI_API_KEY": "sk-openai-test",
    "GEMINI_API_KEY": "gem-test",
    "NVIDIA_API_KEY": "nvapi-test",
    "CEREBRAS_API_KEY": "csk-test",
    # Explicit escape hatch so prepaid-path failover tests keep exercising HTTP.
    "RADON_LADDER_ALLOW_PREPAID": "1",
}


class _Router:
    def __init__(self, routes: dict):
        self.routes = routes
        self.calls: list[str] = []

    def __call__(self, url, *, headers=None, json=None, timeout=None, stream=False):
        self.calls.append(url)
        for needle, resp in self.routes.items():
            if needle in url:
                return resp() if callable(resp) else resp
        return _Resp(599, {"error": {"message": f"unmocked {url}"}})


class _StreamingResponse:
    """Production-shaped response: parsing before streaming is a test failure."""

    def __init__(self, payload: dict, *, chunks: list[bytes] | None = None):
        self.status_code = 200
        self._chunks = chunks or [json.dumps(payload).encode()]
        self.closed = False

    def iter_content(self, _chunk_size):
        return iter(self._chunks)

    @property
    def text(self):
        pytest.fail("response text was materialized before enforcing the byte cap")

    def json(self):
        pytest.fail("response JSON was materialized before enforcing the byte cap")

    def close(self):
        self.closed = True


class TestLadderContract:
    def test_order_is_joes_exact_bands(self):
        assert MODEL_LADDER_ORDER == (
            "anthropic",
            "grok",
            "cursor",
            "codex",
            "gemini",
            "nvidia",
            "cerebras",
        )
        assert MODEL_LADDER_TIERS["nvidia"] == "nvidia"
        assert MODEL_LADDER_TIERS["cerebras"] == "cerebras"

    def test_cursor_is_never_wired(self):
        assert "cursor" not in wired_providers(ALL_KEYS)


class TestVisionPath:
    def test_anthropic_credit_falls_to_grok(self):
        router = _Router(
            {"api.anthropic.com": _credit_low(), "api.x.ai": _openai_ok()}
        )
        result = extract_via_vision(PNG, PROMPT, env=ALL_KEYS, post=router)
        assert result.provider == "grok"
        assert result.rows == ROWS


class TestTextJsonPath:
    def test_anthropic_credit_falls_to_grok_for_json(self):
        router = _Router(
            {"api.anthropic.com": _credit_low(), "api.x.ai": _openai_obj_ok()}
        )
        result = complete_multimodal_json(
            "evaluate",
            env=ALL_KEYS,
            post=router,
            stream_anthropic=False,
        )
        assert result.provider == "grok"
        assert result.data == OBJ

    def test_incomplete_anthropic_raises_response_error(self):
        router = _Router(
            {
                "api.anthropic.com": _Resp(
                    200,
                    {
                        "stop_reason": "max_tokens",
                        "content": [{"type": "text", "text": "{}"}],
                    },
                )
            }
        )
        with pytest.raises(ModelResponseError, match="complete"):
            complete_multimodal_json(
                "evaluate",
                env={"ANTHROPIC_API_KEY": "a", "RADON_LADDER_ALLOW_PREPAID": "1"},
                post=router,
                stream_anthropic=False,
            )

    @pytest.mark.parametrize(
        ("env", "needle", "payload"),
        [
            ({"ANTHROPIC_API_KEY": "a", "RADON_LADDER_ALLOW_PREPAID": "1"}, "api.anthropic.com", {"stop_reason": "end_turn", "content": [{"type": "text", "text": json.dumps(OBJ)}]}),
            ({"XAI_API_KEY": "x", "RADON_LADDER_ALLOW_PREPAID": "1"}, "api.x.ai", {"choices": [{"message": {"content": json.dumps(OBJ)}}]}),
            ({"OPENAI_API_KEY": "o", "RADON_LADDER_ALLOW_PREPAID": "1"}, "api.openai.com", {"choices": [{"message": {"content": json.dumps(OBJ)}}]}),
            ({"NVIDIA_API_KEY": "n"}, "integrate.api.nvidia.com", {"choices": [{"message": {"content": json.dumps(OBJ)}}]}),
            ({"CEREBRAS_API_KEY": "c"}, "api.cerebras.ai", {"choices": [{"message": {"content": json.dumps(OBJ)}}]}),
        ],
    )
    def test_every_text_provider_streams_and_closes_before_parsing(self, env, needle, payload):
        response = _StreamingResponse(payload)
        calls = []

        def post(url, **kwargs):
            calls.append((url, kwargs))
            assert needle in url
            return response

        assert complete_multimodal_json("evaluate", env=env, post=post).data == OBJ
        assert calls[0][1]["stream"] is True
        assert response.closed

    def test_oversized_streaming_response_never_materializes_text_or_json(self):
        response = _StreamingResponse(
            {}, chunks=[b"x" * 17, b"x" * 17]
        )

        with pytest.raises(ModelResponseError, match="exceeds limit"):
            complete_multimodal_json(
                "evaluate",
                env={"ANTHROPIC_API_KEY": "a", "RADON_LADDER_ALLOW_PREPAID": "1"},
                post=lambda *_args, **_kwargs: response,
                max_response_bytes=32,
            )
        assert response.closed


class TestResearchReviewerFailover:
    def test_reviewer_falls_through_anthropic_credit(self):
        from research.model import Reviewer

        router = _Router(
            {"api.anthropic.com": _credit_low(), "api.x.ai": _openai_obj_ok()}
        )
        from types import SimpleNamespace

        reviewer = Reviewer(
            env={"ANTHROPIC_API_KEY": "a", "XAI_API_KEY": "x", "RADON_LADDER_ALLOW_PREPAID": "1"},
            session=SimpleNamespace(post=router),
        )
        assert reviewer.ask("evaluate") == OBJ

    def test_exhausted_message_is_safe(self):
        router = _Router({"api.anthropic.com": _credit_low()})
        with pytest.raises(ModelLadderExhausted) as exc:
            complete_multimodal_json(
                "evaluate",
                env={"ANTHROPIC_API_KEY": "a", "RADON_LADDER_ALLOW_PREPAID": "1"},
                post=router,
                stream_anthropic=False,
            )
        assert "credit_balance" in str(exc.value)
        assert "top up" not in str(exc.value).lower()


class TestAnthropicHttpxStream:
    def test_httpx_post_rejects_stream_kwarg(self):
        import httpx

        with pytest.raises(TypeError, match="unexpected keyword argument 'stream'"):
            httpx.post("https://example.invalid", json={}, timeout=0.1, stream=True)

    def test_default_adapter_streams_via_client_send_not_httpx_post(self, monkeypatch):
        """Production Reviewer() has no session; _default_post must not use httpx.post(stream=)."""
        import httpx

        from research.model import Reviewer

        closed: list[str] = []
        post_calls: list[dict] = []

        def guarded_post(*_args, **kwargs):
            post_calls.append(kwargs)
            if "stream" in kwargs:
                raise TypeError("post() got an unexpected keyword argument 'stream'")
            raise AssertionError("non-stream httpx.post should not run on the Reviewer path")

        class Response:
            status_code = 200

            def iter_bytes(self, _chunk_size):
                yield json.dumps(
                    {
                        "stop_reason": "end_turn",
                        "content": [{"type": "text", "text": json.dumps(OBJ)}],
                    }
                ).encode()

            def close(self):
                closed.append("response")

        class Client:
            def __init__(self, *, timeout):
                assert timeout == (10.0, 120.0)

            def build_request(self, method, url, *, headers, json):
                assert method == "POST"
                assert "api.anthropic.com" in url
                return object()

            def send(self, request, *, stream):
                assert stream is True
                return Response()

            def close(self):
                closed.append("client")

        monkeypatch.setattr(httpx, "post", guarded_post)
        monkeypatch.setattr(httpx, "Client", Client)
        reviewer = Reviewer(env={"ANTHROPIC_API_KEY": "a", "RADON_LADDER_ALLOW_PREPAID": "1"})
        assert reviewer.ask("evaluate") == OBJ
        assert post_calls == []
        assert closed == ["response", "client"]


class TestCodexTokenParam:
    def test_gpt55_chat_uses_max_completion_tokens(self):
        captured: list[dict] = []

        def post(url, *, headers=None, json=None, timeout=None, stream=False):
            captured.append(json)
            return _openai_obj_ok()

        result = complete_multimodal_json(
            "evaluate",
            env={"OPENAI_API_KEY": "sk-openai-test", "RADON_LADDER_ALLOW_PREPAID": "1"},
            post=post,
            stream_anthropic=False,
        )
        assert result.provider == "codex"
        assert captured[0]["model"] == "gpt-5.5"
        assert captured[0]["max_completion_tokens"] == 6000
        assert "max_tokens" not in captured[0]

    def test_gpt55_vision_uses_max_completion_tokens(self):
        captured: list[dict] = []

        def post(url, *, headers=None, json=None, timeout=None, stream=False):
            captured.append(json)
            return _openai_ok()

        result = extract_via_vision(
            PNG, PROMPT, env={"OPENAI_API_KEY": "sk-openai-test", "RADON_LADDER_ALLOW_PREPAID": "1"}, post=post
        )
        assert result.provider == "codex"
        assert captured[0]["model"] == "gpt-5.5"
        assert captured[0]["max_completion_tokens"] == 4096
        assert "max_tokens" not in captured[0]

    def test_grok_compat_path_keeps_max_tokens(self):
        captured: list[dict] = []

        def post(url, *, headers=None, json=None, timeout=None, stream=False):
            captured.append(json)
            return _openai_obj_ok()

        result = complete_multimodal_json(
            "evaluate",
            env={"XAI_API_KEY": "xai-test", "RADON_LADDER_ALLOW_PREPAID": "1"},
            post=post,
            stream_anthropic=False,
        )
        assert result.provider == "grok"
        assert captured[0]["max_tokens"] == 6000
        assert "max_completion_tokens" not in captured[0]


class TestCompleteTextJson:
    def test_cerebras_is_not_attempted_when_anthropic_wins(self):
        router = _Router(
            {
                "api.anthropic.com": _anthropic_obj_ok({"tags": ["PUTS", "OPTIONS", "POSITIONING"]}),
                "api.cerebras.ai": _openai_obj_ok({"tags": ["SHOULD", "NOT", "WIN"]}),
            }
        )
        result = complete_text_json(
            "tag this",
            env=ALL_KEYS,
            post=router,
            accept=accept_tags_payload,
        )
        assert result.provider == "anthropic"
        assert result.data["tags"] == ["PUTS", "OPTIONS", "POSITIONING"]
        assert not any("cerebras" in url for url in router.calls)
        assert any("api.anthropic.com" in url for url in router.calls)

    def test_cerebras_is_last_after_earlier_keyed_failures(self):
        router = _Router(
            {
                "api.anthropic.com": _credit_low(),
                "api.x.ai": _Resp(429, {"error": {"message": "rate limit"}}),
                "api.openai.com": _Resp(500, {"error": {"message": "overloaded"}}),
                "generativelanguage.googleapis.com": _Resp(403, {"error": {"message": "quota"}}),
                "integrate.api.nvidia.com": _Resp(401, {"error": {"message": "auth"}}),
                "api.cerebras.ai": _openai_obj_ok(
                    {"summary": "What fixed the relay?", "tickers": ["spy"]}
                ),
            }
        )
        result = complete_text_json(
            "distill this",
            env=ALL_KEYS,
            post=router,
            accept=accept_distill_payload,
        )
        assert result.provider == "cerebras"
        assert [url for url in router.calls if "cerebras" in url]
        first_cerebras = next(i for i, url in enumerate(router.calls) if "cerebras" in url)
        assert first_cerebras == len(router.calls) - 1

    def test_no_keyed_provider_raises_exhausted(self):
        def must_not_post(*_args, **_kwargs):
            raise AssertionError("ladder posted with no keys")

        with pytest.raises(ModelLadderExhausted, match="no keyed provider"):
            complete_text_json("tag this", env={}, post=must_not_post)

    def test_accept_rejects_short_tag_list_and_walks_on(self):
        router = _Router(
            {
                "api.anthropic.com": _anthropic_obj_ok({"tags": ["ONLY", "TWO"]}),
                "api.x.ai": _openai_obj_ok({"tags": ["MACRO", "FED", "RATES"]}),
            }
        )
        result = complete_text_json(
            "tag this",
            env={
                "ANTHROPIC_API_KEY": "a",
                "XAI_API_KEY": "x",
                "RADON_LADDER_ALLOW_PREPAID": "1",
            },
            post=router,
            accept=accept_tags_payload,
        )
        assert result.provider == "grok"
        assert result.data["tags"] == ["MACRO", "FED", "RATES"]


class TestCerebrasModelId:
    def test_default_is_not_archived_scout(self):
        from clients.model_ladder import _model_for

        vision = _model_for("cerebras", {}, kind="vision")
        text = _model_for("cerebras", {}, kind="text")
        assert "llama-4-scout" not in vision
        assert "llama-4-scout" not in text
        assert vision == "qwen-3.8-27b"
        assert text == "qwen-3.8-27b"

    def test_reviewer_path_uses_public_multimodal_id(self):
        captured: list[dict] = []

        def post(url, *, headers=None, json=None, timeout=None, stream=False):
            captured.append(json)
            assert "api.cerebras.ai" in url
            return _openai_obj_ok()

        result = complete_multimodal_json(
            "evaluate",
            env={"CEREBRAS_API_KEY": "csk-test"},
            post=post,
            stream_anthropic=False,
        )
        assert result.provider == "cerebras"
        assert captured[0]["model"] == "qwen-3.8-27b"
        assert "llama-4-scout" not in captured[0]["model"]
        assert captured[0]["reasoning_effort"] == "none"


class TestSubscriptionAuthPreference:
    def test_anthropic_oauth_preferred_over_prepaid_api_key(self):
        from clients.model_ladder import _auth_for

        auth = _auth_for(
            "anthropic",
            {
                "CLAUDE_CODE_OAUTH_TOKEN": "oauth-sub-token",
                "ANTHROPIC_API_KEY": "sk-ant-prepaid",
            },
        )
        assert auth is not None
        assert auth.kind == "subscription"
        assert auth.token == "oauth-sub-token"
        assert auth.mechanism == "CLAUDE_CODE_OAUTH_TOKEN"

    def test_subscription_token_wires_anthropic_without_prepaid(self):
        assert "anthropic" in wired_providers({"CLAUDE_CODE_OAUTH_TOKEN": "oauth"})

    def test_codex_auth_json_preferred_over_openai_api_key(self, tmp_path):
        from clients.model_ladder import _auth_for

        codex_home = tmp_path / "codex"
        codex_home.mkdir()
        (codex_home / "auth.json").write_text(
            json.dumps({"tokens": {"access_token": "codex-sub-token"}}),
            encoding="utf-8",
        )
        auth = _auth_for(
            "codex",
            {"CODEX_HOME": str(codex_home), "OPENAI_API_KEY": "sk-openai-prepaid", "RADON_LADDER_ALLOW_PREPAID": "1"},
        )
        assert auth is not None
        assert auth.kind == "subscription"
        assert auth.token == "codex-sub-token"

    def test_grok_auth_json_preferred_over_xai_api_key(self, tmp_path):
        from clients.model_ladder import _auth_for

        home = tmp_path / "home"
        (home / ".grok").mkdir(parents=True)
        (home / ".grok" / "auth.json").write_text(
            json.dumps({"access_token": "grok-sub-token"}),
            encoding="utf-8",
        )
        auth = _auth_for(
            "grok",
            {"HOME": str(home), "XAI_API_KEY": "xai-prepaid", "RADON_LADDER_ALLOW_PREPAID": "1"},
        )
        assert auth is not None
        assert auth.kind == "subscription"
        assert auth.token == "grok-sub-token"

    def test_google_is_antigravity_only_no_key_no_oauth_token_under_any_flag(self):
        # Operator 2026-09-18: only Antigravity, never Gemini API keys or tokens.
        assert "gemini" not in wired_providers({"GOOGLE_API_KEY": "goog-test"})
        assert "gemini" not in wired_providers(
            {"GEMINI_API_KEY": "g", "GOOGLE_API_KEY": "goog-test", "RADON_LADDER_ALLOW_PREPAID": "1"}
        )
        assert "gemini" not in wired_providers({"GEMINI_OAUTH_TOKEN": "gem-oauth"})

    def test_anthropic_subscription_sends_oauth_beta_header(self):
        captured: list[dict] = []

        def post(url, *, headers=None, json=None, timeout=None, stream=False):
            captured.append(headers or {})
            return _anthropic_obj_ok()

        result = complete_multimodal_json(
            "evaluate",
            env={"CLAUDE_CODE_OAUTH_TOKEN": "oauth-sub"},
            post=post,
            stream_anthropic=False,
        )
        assert result.provider == "anthropic"
        assert captured[0].get("anthropic-beta") == "oauth-2025-04-20"
        # The grant is a Bearer, never an x-api-key (401 live on 2026-09-18).
        assert captured[0].get("authorization") == "Bearer oauth-sub"
        assert "x-api-key" not in captured[0]
        assert captured[0].get("user-agent", "").startswith("claude-cli/")

    def test_anthropic_subscription_leads_system_with_the_claude_code_identity(self):
        # The Messages API answers a bare 429 to a Claude Max grant unless the
        # system prompt opens with the Claude Code identity block (live probe
        # 2026-09-18: 200 with it, 429 without, same token).
        bodies: list[dict] = []

        def post(url, *, headers=None, json=None, timeout=None, stream=False):
            bodies.append(json or {})
            return _anthropic_obj_ok()

        complete_multimodal_json(
            "evaluate",
            system="You are Radon.",
            env={"CLAUDE_CODE_OAUTH_TOKEN": "oauth-sub"},
            post=post,
            stream_anthropic=False,
        )
        system = bodies[0]["system"]
        assert isinstance(system, list)
        assert system[0]["text"].startswith("You are Claude Code, Anthropic's official CLI for Claude.")
        assert system[1] == {"type": "text", "text": "You are Radon."}

    def test_prepaid_anthropic_keeps_x_api_key_and_plain_system(self):
        captured: list[tuple[dict, dict]] = []

        def post(url, *, headers=None, json=None, timeout=None, stream=False):
            captured.append((headers or {}, json or {}))
            return _anthropic_obj_ok()

        complete_multimodal_json(
            "evaluate",
            system="You are Radon.",
            env={"ANTHROPIC_API_KEY": "sk-ant", "RADON_LADDER_ALLOW_PREPAID": "1"},
            post=post,
            stream_anthropic=False,
        )
        headers, body = captured[0]
        assert headers.get("x-api-key") == "sk-ant"
        assert "authorization" not in headers
        assert body["system"] == "You are Radon."


class TestGrokGrantLiveShape:
    """~/.grok/auth.json as the grok CLI writes it: entries keyed by
    "<issuer>::<client_id>" with the OIDC token under `key` (the flat
    access_token shape older tests used never matched production)."""

    def _write(self, tmp_path, expires_at: str):
        home = tmp_path / "home"
        (home / ".grok").mkdir(parents=True)
        (home / ".grok" / "auth.json").write_text(json.dumps({
            "https://auth.x.ai::b1a00492-0000-0000-0000-000000000000": {
                "key": "grok-oidc-grant", "auth_mode": "oidc", "refresh_token": "r",
                "expires_at": expires_at, "oidc_issuer": "https://auth.x.ai",
            }
        }))
        return home

    def test_reads_the_key_of_a_live_entry(self, tmp_path):
        auth = model_ladder._auth_for("grok", {"HOME": str(self._write(tmp_path, "2099-01-01T00:00:00Z"))})
        assert auth is not None and auth.kind == "subscription" and auth.token == "grok-oidc-grant"

    def test_skips_an_expired_entry(self, tmp_path):
        assert model_ladder._auth_for("grok", {"HOME": str(self._write(tmp_path, "2020-01-01T00:00:00Z"))}) is None


class TestCodexSubscriptionGoesThroughChatGpt:
    """api.openai.com meters the prepaid wallet and rejects the ChatGPT grant
    ("no credits remaining", live 2026-09-18); chatgpt.com's codex Responses
    endpoint accepts it with the account id, streaming only."""

    def _codex_home(self, tmp_path):
        codex_home = tmp_path / "codex"
        codex_home.mkdir()
        (codex_home / "auth.json").write_text(
            json.dumps({"auth_mode": "chatgpt", "tokens": {"access_token": "codex-grant", "account_id": "acct-1"}})
        )
        return codex_home

    def test_auth_carries_the_account_id(self, tmp_path):
        auth = model_ladder._auth_for("codex", {"CODEX_HOME": str(self._codex_home(tmp_path))})
        assert auth is not None
        assert auth.kind == "subscription"
        assert auth.account_id == "acct-1"

    def test_text_completion_streams_from_chatgpt_with_the_account_id(self, tmp_path):
        seen: list[tuple[str, dict, dict]] = []
        sse = b"".join(
            [
                b'data: {"type":"response.created","response":{"id":"r1"}}\n\n',
                b'data: {"type":"response.output_text.delta","delta":"{\\"ok\\": "}\n\n',
                b'data: {"type":"response.output_text.delta","delta":"true}"}\n\n',
                b'data: {"type":"response.completed","response":{"status":"completed","usage":{"input_tokens":3,"output_tokens":2}}}\n\n',
            ]
        )

        def post(url, *, headers=None, json=None, timeout=None, stream=False):
            seen.append((url, headers or {}, json or {}))
            return _StreamingResponse({}, chunks=[sse])

        result = complete_text_json(
            "evaluate",
            system="You are Radon.",
            env={"CODEX_HOME": str(self._codex_home(tmp_path)), "RADON_LADDER_NO_AUTH_FILES": "0"},
            post=post,
        )
        assert result.provider == "codex"
        assert result.data == {"ok": True}
        url, headers, body = seen[0]
        assert url == "https://chatgpt.com/backend-api/codex/responses"
        assert headers["authorization"] == "Bearer codex-grant"
        assert headers["chatgpt-account-id"] == "acct-1"
        assert body["stream"] is True
        assert body["instructions"] == "You are Radon."
        assert body["input"][0]["content"][-1] == {"type": "input_text", "text": "evaluate"}

    def test_prepaid_openai_still_uses_api_openai_com(self):
        router = _Router({"api.openai.com": _openai_obj_ok()})
        result = complete_text_json(
            "evaluate",
            env={"OPENAI_API_KEY": "sk-openai", "RADON_LADDER_ALLOW_PREPAID": "1"},
            post=router,
        )
        assert result.provider == "codex"
        assert any("api.openai.com" in u for u in router.calls)


class TestGeminiRunsThroughTheAntigravityCli:
    """Google retired the Gemini CLI OAuth client for individuals on 2026-09-18
    and the Antigravity grant lacks the generativelanguage scope (403 live), so
    the gemini rung shells out to `agy -p` and never calls HTTP."""

    def _home(self, tmp_path):
        home = tmp_path / "home"
        (home / ".gemini" / "antigravity-cli").mkdir(parents=True)
        (home / ".gemini" / "antigravity-cli" / "antigravity-oauth-token").write_text(
            json.dumps({"token": {"access_token": "a", "refresh_token": "r", "expiry": "2099-01-01T00:00:00Z"}})
        )
        (home / ".local" / "bin").mkdir(parents=True)
        agy = home / ".local" / "bin" / "agy"
        agy.write_text("#!/bin/sh\nexit 0\n")
        agy.chmod(0o755)
        return home

    def test_wired_when_the_cli_and_grant_are_present(self, tmp_path):
        env = {"HOME": str(self._home(tmp_path))}
        auth = model_ladder._auth_for("gemini", env)
        assert auth is not None
        assert auth.kind == "subscription"
        assert auth.mechanism == "antigravity_cli"
        assert "gemini" in wired_providers(env)

    def test_not_wired_without_the_cli(self, tmp_path):
        home = self._home(tmp_path)
        (home / ".local" / "bin" / "agy").unlink()
        assert "gemini" not in wired_providers({"HOME": str(home)})

    def test_text_completion_shells_out_to_agy_print_mode(self, tmp_path, monkeypatch):
        home = self._home(tmp_path)
        calls: list[list[str]] = []

        def fake_run(argv, **kwargs):
            calls.append(list(argv))
            import subprocess as sp
            return sp.CompletedProcess(argv, 0, stdout=json.dumps({"status": "SUCCESS", "response": '{"ok": true}\n'}), stderr="")

        monkeypatch.setattr(model_ladder.subprocess, "run", fake_run)
        router = _Router({})  # any HTTP call is an unmocked 599
        result = complete_text_json(
            "evaluate",
            system="You are Radon.",
            env={"HOME": str(home), "GEMINI_MODEL": "gemini-3.8-flash-low"},
            post=router,
        )
        assert result.provider == "gemini"
        assert result.data == {"ok": True}
        assert router.calls == []
        argv = calls[0]
        assert argv[0] == str(home / ".local" / "bin" / "agy")
        assert "-p" in argv and "--output-format" in argv and "json" in argv
        assert argv[argv.index("--model") + 1] == "gemini-3.8-flash-low"
        prompt = argv[argv.index("-p") + 1]
        assert "You are Radon." in prompt and "evaluate" in prompt

    def test_images_skip_the_rung(self, tmp_path, monkeypatch):
        home = self._home(tmp_path)
        monkeypatch.setattr(model_ladder.subprocess, "run", lambda *a, **k: pytest.fail("agy must not run for images"))
        router = _Router({"integrate.api.nvidia.com": _openai_obj_ok()})
        result = complete_multimodal_json(
            "evaluate",
            images=[("chart", b"png")],
            env={"HOME": str(home), "NVIDIA_API_KEY": "nv"},
            post=router,
        )
        assert result.provider == "nvidia"


class TestPrepaidMissDoesNotExhaustWhenAlternatesExist:
    def test_prepaid_anthropic_skipped_nvidia_wins_by_default(self):
        """Hetzner prepaid-only: skip sub rungs; do not burn ANTHROPIC_API_KEY."""
        router = _Router(
            {
                "api.anthropic.com": _credit_low(),
                "integrate.api.nvidia.com": _openai_obj_ok(),
            }
        )
        result = complete_multimodal_json(
            "evaluate",
            env={"ANTHROPIC_API_KEY": "sk-ant-empty", "NVIDIA_API_KEY": "nvapi-test"},
            post=router,
            stream_anthropic=False,
        )
        assert result.provider == "nvidia"
        assert not any("api.anthropic.com" in url for url in router.calls)

    def test_allow_prepaid_credit_falls_to_nvidia_when_keyed(self):
        router = _Router(
            {
                "api.anthropic.com": _credit_low(),
                "integrate.api.nvidia.com": _openai_obj_ok(),
            }
        )
        result = complete_multimodal_json(
            "evaluate",
            env={
                "ANTHROPIC_API_KEY": "sk-ant-empty",
                "NVIDIA_API_KEY": "nvapi-test",
                "RADON_LADDER_ALLOW_PREPAID": "1",
            },
            post=router,
            stream_anthropic=False,
        )
        assert result.provider == "nvidia"
        assert "credit_balance" in " ".join(result.attempted)

    def test_only_nvidia_keyed_is_attempted(self):
        router = _Router({"integrate.api.nvidia.com": _openai_obj_ok()})
        result = complete_multimodal_json(
            "evaluate",
            images=[("page", PNG)],
            env={"NVIDIA_API_KEY": "nvapi-test"},
            post=router,
            stream_anthropic=False,
        )
        assert result.provider == "nvidia"
        assert result.model == "meta/llama-3.2-90b-vision-instruct"

    def test_subscription_anthropic_wins_without_prepaid(self):
        router = _Router({"api.anthropic.com": _anthropic_obj_ok()})
        result = complete_multimodal_json(
            "evaluate",
            env={"CLAUDE_CODE_OAUTH_TOKEN": "oauth-sub"},
            post=router,
            stream_anthropic=False,
        )
        assert result.provider == "anthropic"


class TestNvidiaVisionDefaultAndFallback:
    def test_default_vision_model_is_90b(self):
        from clients.model_ladder import _model_for, _nvidia_vision_models

        assert _model_for("nvidia", {}, kind="vision") == (
            "meta/llama-3.2-90b-vision-instruct"
        )
        assert _nvidia_vision_models({}) == (
            "meta/llama-3.2-90b-vision-instruct",
            "meta/llama-3.2-11b-vision-instruct",
        )

    def test_nvidia_vision_model_override_honored(self):
        from clients.model_ladder import _nvidia_vision_models

        models = _nvidia_vision_models(
            {"NVIDIA_VISION_MODEL": "meta/llama-3.2-11b-vision-instruct"}
        )
        assert models == ("meta/llama-3.2-11b-vision-instruct",)

    def test_multimodal_falls_from_90b_to_11b_on_error(self):
        calls: list[str] = []

        def post(url, *, headers=None, json=None, timeout=None, stream=False):
            model = (json or {}).get("model", "")
            calls.append(model)
            if model.endswith("90b-vision-instruct"):
                return _Resp(500, {"error": {"message": "timeout"}})
            return _openai_obj_ok()

        result = complete_multimodal_json(
            "evaluate",
            images=[("page", PNG)],
            env={"NVIDIA_API_KEY": "nvapi-test"},
            post=post,
            stream_anthropic=False,
        )
        assert result.provider == "nvidia"
        assert result.model == "meta/llama-3.2-11b-vision-instruct"
        assert calls == [
            "meta/llama-3.2-90b-vision-instruct",
            "meta/llama-3.2-11b-vision-instruct",
        ]

    def test_vision_extract_uses_90b_then_11b(self):
        calls: list[str] = []

        def post(url, *, headers=None, json=None, timeout=None, stream=False):
            model = (json or {}).get("model", "")
            calls.append(model)
            if "90b" in model:
                return _Resp(503, {"error": {"message": "capacity"}})
            return _openai_ok()

        result = extract_via_vision(
            PNG, PROMPT, env={"NVIDIA_API_KEY": "nvapi-test"}, post=post
        )
        assert result.provider == "nvidia"
        assert result.model == "meta/llama-3.2-11b-vision-instruct"
        assert calls[0] == "meta/llama-3.2-90b-vision-instruct"


class TestNoPrepaidByDefault:
    """Subscription rungs must not meter prepaid wallets unless opted in."""

    PREPAID_ONLY = {
        "ANTHROPIC_API_KEY": "sk-ant-prepaid",
        "XAI_API_KEY": "xai-prepaid",
        "OPENAI_API_KEY": "sk-openai-prepaid",
        "GEMINI_API_KEY": "gem-prepaid",
        "NVIDIA_API_KEY": "nvapi-test",
    }

    def test_prepaid_only_does_not_wire_subscription_rungs(self):
        wired = wired_providers(self.PREPAID_ONLY)
        assert "anthropic" not in wired
        assert "grok" not in wired
        assert "codex" not in wired
        assert "gemini" not in wired
        assert "nvidia" in wired

    def test_prepaid_only_text_path_uses_nvidia_without_sub_http(self):
        router = _Router({"integrate.api.nvidia.com": _openai_obj_ok({"tags": ["A", "B", "C"]})})

        def refuse_sub(url, *, headers=None, json=None, timeout=None, stream=False):
            for forbidden in (
                "api.anthropic.com",
                "api.x.ai",
                "api.openai.com",
                "generativelanguage.googleapis.com",
            ):
                if forbidden in url:
                    raise AssertionError(f"prepaid-only must not call {forbidden}")
            return router(url, headers=headers, json=json, timeout=timeout, stream=stream)

        result = complete_text_json(
            "tag this",
            env=self.PREPAID_ONLY,
            post=refuse_sub,
            accept=accept_tags_payload,
        )
        assert result.provider == "nvidia"
        assert result.model == "nvidia/nemotron-3-super-120b-a12b"
        assert any("integrate.api.nvidia.com" in url for url in router.calls)

    def test_allow_prepaid_restores_prepaid_wiring(self):
        env = {**self.PREPAID_ONLY, "RADON_LADDER_ALLOW_PREPAID": "1"}
        wired = wired_providers(env)
        assert "anthropic" in wired
        assert "grok" in wired
        assert "codex" in wired
        assert "gemini" not in wired  # Antigravity only; no prepaid Gemini path exists

    def test_subscription_still_preferred_when_present_with_allow_prepaid(self):
        from clients.model_ladder import _auth_for

        auth = _auth_for(
            "anthropic",
            {
                "CLAUDE_CODE_OAUTH_TOKEN": "oauth-sub-token",
                "ANTHROPIC_API_KEY": "sk-ant-prepaid",
                "RADON_LADDER_ALLOW_PREPAID": "1",
            },
        )
        assert auth is not None
        assert auth.kind == "subscription"
        assert auth.token == "oauth-sub-token"


class TestNvidiaTextDefault:
    def test_default_text_model_is_nemotron_super_not_stale_llama(self):
        from clients.model_ladder import _DEFAULT_TEXT_MODELS, _model_for

        assert "llama-3.3-70b" not in _DEFAULT_TEXT_MODELS["nvidia"]
        assert _model_for("nvidia", {}, kind="text") == (
            "nvidia/nemotron-3-super-120b-a12b"
        )

    def test_nvidia_text_model_override_honored(self):
        from clients.model_ladder import _model_for

        assert (
            _model_for(
                "nvidia",
                {"NVIDIA_TEXT_MODEL": "nvidia/llama-3.1-nemotron-70b-instruct"},
                kind="text",
            )
            == "nvidia/llama-3.1-nemotron-70b-instruct"
        )
        assert (
            _model_for(
                "nvidia",
                {"NVIDIA_MODEL": "nvidia/llama-3.1-nemotron-70b-instruct"},
                kind="text",
            )
            == "nvidia/llama-3.1-nemotron-70b-instruct"
        )

    def test_vision_defaults_unchanged_from_90b(self):
        from clients.model_ladder import _model_for, _nvidia_vision_models

        assert _model_for("nvidia", {}, kind="vision") == (
            "meta/llama-3.2-90b-vision-instruct"
        )
        assert _nvidia_vision_models({}) == (
            "meta/llama-3.2-90b-vision-instruct",
            "meta/llama-3.2-11b-vision-instruct",
        )


class TestNoAuthFilesFlag:
    """RADON_LADDER_NO_AUTH_FILES=1 disables every file-based auth discovery.

    Credential-free loops pass a scrubbed env, but the implicit Path.home()
    fallback still discovered host auth files; the flag makes discovery
    env-var-only so their children stay keyless.
    """

    def _seeded_home(self, tmp_path):
        home = tmp_path / "home"
        (home / ".claude").mkdir(parents=True)
        (home / ".claude" / ".credentials.json").write_text(
            json.dumps({"claudeAiOauth": {"accessToken": "claude-file-token"}}),
            encoding="utf-8",
        )
        (home / ".codex").mkdir()
        (home / ".codex" / "auth.json").write_text(
            json.dumps({"tokens": {"access_token": "codex-file-token"}}),
            encoding="utf-8",
        )
        (home / ".grok").mkdir()
        (home / ".grok" / "auth.json").write_text(
            json.dumps({"access_token": "grok-file-token"}),
            encoding="utf-8",
        )
        return home

    def test_flag_blocks_all_home_auth_file_discovery(self, tmp_path):
        from clients.model_ladder import _auth_for

        home = self._seeded_home(tmp_path)
        env = {"HOME": str(home), "RADON_LADDER_NO_AUTH_FILES": "1"}
        assert _auth_for("anthropic", env) is None
        assert _auth_for("codex", env) is None
        assert _auth_for("grok", env) is None
        assert wired_providers(env) == ()

    def test_flag_blocks_explicit_file_paths_too(self, tmp_path):
        from clients.model_ladder import _auth_for

        home = self._seeded_home(tmp_path)
        token_file = tmp_path / "oauth.txt"
        token_file.write_text("claude-file-oauth", encoding="utf-8")
        env = {
            "RADON_LADDER_NO_AUTH_FILES": "1",
            "CLAUDE_CODE_OAUTH_TOKEN_FILE": str(token_file),
            "CLAUDE_CONFIG_DIR": str(home / ".claude"),
            "CODEX_HOME": str(home / ".codex"),
        }
        assert _auth_for("anthropic", env) is None
        assert _auth_for("codex", env) is None

    def test_env_var_tokens_still_wire_under_flag(self, tmp_path):
        home = self._seeded_home(tmp_path)
        env = {
            "HOME": str(home),
            "RADON_LADDER_NO_AUTH_FILES": "1",
            "CLAUDE_CODE_OAUTH_TOKEN": "oauth-env",
        }
        wired = wired_providers(env)
        assert "anthropic" in wired

    def test_without_flag_file_discovery_unchanged(self, tmp_path):
        from clients.model_ladder import _auth_for

        home = self._seeded_home(tmp_path)
        auth = _auth_for("grok", {"HOME": str(home)})
        assert auth is not None
        assert auth.token == "grok-file-token"


class TestSlmTaggerRung:
    TAXONOMY = ["GAMMA", "SPX", "VOL", "VIX"]

    def _env(self, **extra):
        return {
            "RADON_SLM_TAGGER_URL": "http://127.0.0.1:8331",
            "RADON_SLM_TAGGER_MODE": "primary",
            "RADON_SLM_TAXONOMY_JSON": json.dumps(self.TAXONOMY),
            "ANTHROPIC_API_KEY": "a",
            "RADON_LADDER_ALLOW_PREPAID": "1",
            **extra,
        }

    def test_absent_from_order_and_wired_providers(self):
        assert "slm-tagger" not in MODEL_LADDER_ORDER
        wired = wired_providers(self._env())
        assert "slm-tagger" not in wired

    def test_complete_without_providers_never_attempts_slm(self, monkeypatch):
        from clients import model_ladder as ml

        monkeypatch.setattr(ml, "_slm_health_ok", lambda _url: True)
        calls = []

        def post(url, **kwargs):
            calls.append(url)
            return _anthropic_obj_ok({"tags": ["GAMMA", "SPX", "VOL"]})

        complete_text_json(
            "Title: x\nBody: y",
            env=self._env(),
            post=post,
            accept=accept_tags_payload,
        )
        assert all("8331" not in url for url in calls)

    def test_providers_post_carries_contract(self, monkeypatch):
        from clients import model_ladder as ml
        from newsfeed.slm.contract import SLM_SYSTEM

        monkeypatch.setattr(ml, "_slm_health_ok", lambda _url: True)
        captured = {}

        def post(url, **kwargs):
            captured["url"] = url
            captured["body"] = kwargs.get("json")
            return _openai_obj_ok({"tags": ["GAMMA", "SPX", "VOL"]})

        result = complete_text_json(
            "Title: x\nBody: y",
            system="CALLER TAXONOMY PROMPT",
            env=self._env(),
            post=post,
            accept=accept_tags_payload,
            providers=["slm-tagger"],
        )
        assert captured["url"] == "http://127.0.0.1:8331/v1/chat/completions"
        body = captured["body"]
        assert body["temperature"] == 0
        assert body["max_tokens"] == 64
        assert body["messages"][0]["content"] == SLM_SYSTEM
        assert body["response_format"]["json_schema"]["schema"]["required"] == ["tags"]
        assert result.provider == "slm-tagger"

    def test_http_503_falls_through(self, monkeypatch):
        from clients import model_ladder as ml

        monkeypatch.setattr(ml, "_slm_health_ok", lambda _url: True)

        def post(url, **kwargs):
            if "8331" in url:
                return _Resp(503, {"error": "down"})
            return _anthropic_obj_ok({"tags": ["GAMMA", "SPX", "VOL"]})

        result = complete_text_json(
            "Title: x\nBody: y",
            env=self._env(),
            post=post,
            accept=accept_tags_payload,
            providers=["slm-tagger", "anthropic"],
        )
        assert result.provider == "anthropic"
        assert result.attempted == ("slm-tagger:http_503", "anthropic:ok")

    def test_accept_slm_tags_payload_and_fallthrough(self, monkeypatch):
        from clients.model_ladder import accept_slm_tags_payload
        from newsfeed.slm.contract import classify_slm_tags

        tax = self.TAXONOMY
        assert accept_slm_tags_payload({"tags": ["GAMMA", "SPX", "VOL"]}, tax)
        assert classify_slm_tags({"tags": ["GAMMA", "SPX"]}, tax)[0] == "abstain:count"
        assert classify_slm_tags({"tags": ["GAMMA", "SPX", "VOL", "VIX"]}, tax)[0] == "abstain:count"
        assert classify_slm_tags({"tags": []}, tax)[0] == "abstain:empty"
        assert classify_slm_tags("prose", tax)[0] == "unparseable"
        assert classify_slm_tags({"tags": ["gamma", "GAMMA", "spx"]}, tax)[0] == "abstain:count"
        assert classify_slm_tags({"tags": ["GAMMA", "SPX", "NEW"]}, tax)[0] == "abstain:out_of_taxonomy"

        from clients import model_ladder as ml

        monkeypatch.setattr(ml, "_slm_health_ok", lambda _url: True)

        def post(url, **kwargs):
            if "8331" in url:
                return _openai_obj_ok({"tags": ["GAMMA", "SPX"]})
            return _anthropic_obj_ok({"tags": ["GAMMA", "SPX", "VOL"]})

        result = complete_text_json(
            "Title: x\nBody: y",
            env=self._env(),
            post=post,
            accept=accept_tags_payload,
            providers=["slm-tagger", "anthropic"],
        )
        assert result.provider == "anthropic"
        assert result.data == {"tags": ["GAMMA", "SPX", "VOL"]}
        assert result.attempted[0] == "slm-tagger:abstain:count"
