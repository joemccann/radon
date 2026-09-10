"""CTA vision cascade: Joe's exact provider order and credit fallthrough.

Order (do not leave a band early):
  subscription: anthropic -> grok -> cursor -> codex -> gemini
  nvidia (free endpoints)
  cerebras (cheap paid, last)

Cursor has no vision HTTP path in Radon; it is skipped as unavailable,
not treated as a reason to leave the subscription band.
"""
from __future__ import annotations

import json
import os
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def mock_playwright():
    with patch("clients.menthorq_client.sync_playwright") as mock_sp, patch(
        "clients.menthorq_client.time.sleep"
    ):
        mock_pw = MagicMock()
        mock_sp.return_value.__enter__ = MagicMock(return_value=mock_pw)
        mock_sp.return_value.__exit__ = MagicMock(return_value=False)
        mock_browser = MagicMock()
        mock_pw.chromium.launch.return_value = mock_browser
        mock_context = MagicMock()
        mock_browser.new_context.return_value = mock_context
        mock_page = MagicMock()
        mock_context.new_page.return_value = mock_page
        mock_page.url = "https://menthorq.com/account/"
        yield {
            "sync_playwright": mock_sp,
            "playwright": mock_pw,
            "browser": mock_browser,
            "context": mock_context,
            "page": mock_page,
        }

from clients.vision_cascade import (
    VISION_CASCADE_ORDER,
    VISION_CASCADE_TIERS,
    VisionCascadeExhausted,
    VisionResult,
    extract_via_vision,
    wired_vision_providers,
)


ROWS = [{"underlying": "E-Mini S&P 500 Index", "position_today": 0.45}]
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


def _openai_ok(rows=None):
    return _Resp(200, {"choices": [{"message": {"content": json.dumps(rows or ROWS)}}]})


def _gemini_ok(rows=None):
    return _Resp(
        200,
        {"candidates": [{"content": {"parts": [{"text": json.dumps(rows or ROWS)}]}}]},
    )


def _credit_low():
    return _Resp(
        400,
        {
            "error": {
                "type": "invalid_request_error",
                "message": (
                    "Your credit balance is too low to access the Anthropic API. "
                    "Please go to Plans & Billing to upgrade or purchase credits."
                ),
            }
        },
    )


def _quota():
    return _Resp(429, {"error": {"message": "You exceeded your current quota"}})


def _http_401():
    return _Resp(401, {"error": {"message": "invalid api key"}})


ALL_KEYS = {
    "ANTHROPIC_API_KEY": "sk-ant-test",
    "XAI_API_KEY": "xai-test",
    "OPENAI_API_KEY": "sk-openai-test",
    "GEMINI_API_KEY": "gem-test",
    "NVIDIA_API_KEY": "nvapi-test",
    "CEREBRAS_API_KEY": "csk-test",
}


class _Router:
    def __init__(self, routes: dict):
        self.routes = routes
        self.calls: list[str] = []

    def __call__(self, url, *, headers=None, json=None, timeout=None):
        self.calls.append(url)
        for needle, resp in self.routes.items():
            if needle in url:
                return resp() if callable(resp) else resp
        return _Resp(599, {"error": {"message": f"unmocked {url}"}})


class TestCascadeContract:
    def test_order_is_joes_exact_bands(self):
        assert VISION_CASCADE_ORDER == (
            "anthropic",
            "grok",
            "cursor",
            "codex",
            "gemini",
            "nvidia",
            "cerebras",
        )
        assert VISION_CASCADE_TIERS["anthropic"] == "subscription"
        assert VISION_CASCADE_TIERS["grok"] == "subscription"
        assert VISION_CASCADE_TIERS["cursor"] == "subscription"
        assert VISION_CASCADE_TIERS["codex"] == "subscription"
        assert VISION_CASCADE_TIERS["gemini"] == "subscription"
        assert VISION_CASCADE_TIERS["nvidia"] == "nvidia"
        assert VISION_CASCADE_TIERS["cerebras"] == "cerebras"

    def test_cursor_is_never_wired(self):
        assert "cursor" not in wired_vision_providers(ALL_KEYS)

    def test_wired_follows_env_keys(self):
        assert wired_vision_providers({"XAI_API_KEY": "x"}) == ("grok",)
        assert wired_vision_providers({"GROK_API_KEY": "g"}) == ("grok",)
        assert wired_vision_providers({"OPENAI_API_KEY": "o"}) == ("codex",)
        assert wired_vision_providers({"GEMINI_API_KEY": "g"}) == ("gemini",)
        assert wired_vision_providers({"NVIDIA_API_KEY": "n"}) == ("nvidia",)
        assert wired_vision_providers({"CEREBRAS_API_KEY": "c"}) == ("cerebras",)
        assert wired_vision_providers({}) == ()


class TestCreditFallthrough:
    def test_anthropic_credit_falls_to_grok_before_nvidia(self):
        router = _Router(
            {
                "api.anthropic.com": _credit_low(),
                "api.x.ai": _openai_ok(),
                "integrate.api.nvidia.com": _openai_ok(
                    [{"underlying": "SHOULD_NOT_WIN"}]
                ),
            }
        )
        result = extract_via_vision(
            PNG, PROMPT, env=ALL_KEYS, post=router
        )
        assert isinstance(result, VisionResult)
        assert result.provider == "grok"
        assert result.rows == ROWS
        assert any("api.anthropic.com" in u for u in router.calls)
        assert any("api.x.ai" in u for u in router.calls)
        assert not any("nvidia" in u for u in router.calls)

    def test_anthropic_quota_falls_to_grok(self):
        router = _Router(
            {"api.anthropic.com": _quota(), "api.x.ai": _openai_ok()}
        )
        result = extract_via_vision(
            PNG, PROMPT, env={"ANTHROPIC_API_KEY": "a", "XAI_API_KEY": "x"}, post=router
        )
        assert result.provider == "grok"

    def test_anthropic_401_falls_to_grok(self):
        router = _Router(
            {"api.anthropic.com": _http_401(), "api.x.ai": _openai_ok()}
        )
        result = extract_via_vision(
            PNG, PROMPT, env={"ANTHROPIC_API_KEY": "a", "XAI_API_KEY": "x"}, post=router
        )
        assert result.provider == "grok"

    def test_subscription_band_is_exhausted_before_nvidia(self):
        router = _Router(
            {
                "api.anthropic.com": _credit_low(),
                "api.x.ai": _http_401(),
                "api.openai.com": _http_401(),
                "generativelanguage.googleapis.com": _http_401(),
                "integrate.api.nvidia.com": _openai_ok(),
                "api.cerebras.ai": _openai_ok([{"underlying": "CEREBRAS_SHOULD_WAIT"}]),
            }
        )
        result = extract_via_vision(PNG, PROMPT, env=ALL_KEYS, post=router)
        assert result.provider == "nvidia"
        assert any("api.openai.com" in u for u in router.calls)
        assert any("generativelanguage.googleapis.com" in u for u in router.calls)
        assert not any("cerebras" in u for u in router.calls)

    def test_nvidia_is_tried_before_cerebras(self):
        router = _Router(
            {
                "integrate.api.nvidia.com": _http_401(),
                "api.cerebras.ai": _openai_ok(),
            }
        )
        env = {"NVIDIA_API_KEY": "n", "CEREBRAS_API_KEY": "c"}
        result = extract_via_vision(PNG, PROMPT, env=env, post=router)
        assert result.provider == "cerebras"
        assert any("nvidia" in u for u in router.calls)

    def test_gemini_wins_inside_subscription_band(self):
        router = _Router(
            {
                "api.anthropic.com": _credit_low(),
                "api.x.ai": _http_401(),
                "api.openai.com": _http_401(),
                "generativelanguage.googleapis.com": _gemini_ok(),
                "integrate.api.nvidia.com": _openai_ok(
                    [{"underlying": "NVIDIA_TOO_LATE"}]
                ),
            }
        )
        result = extract_via_vision(PNG, PROMPT, env=ALL_KEYS, post=router)
        assert result.provider == "gemini"
        assert not any("nvidia" in u for u in router.calls)

    def test_codex_uses_openai_key(self):
        router = _Router({"api.openai.com": _openai_ok()})
        result = extract_via_vision(
            PNG, PROMPT, env={"OPENAI_API_KEY": "sk-o"}, post=router
        )
        assert result.provider == "codex"


class TestExhaustedMessaging:
    def test_exhausted_does_not_tell_ops_to_top_up_anthropic_only(self):
        router = _Router(
            {
                "api.anthropic.com": _credit_low(),
                "api.x.ai": _http_401(),
            }
        )
        with pytest.raises(VisionCascadeExhausted) as exc:
            extract_via_vision(
                PNG,
                PROMPT,
                env={"ANTHROPIC_API_KEY": "a", "XAI_API_KEY": "x"},
                post=router,
            )
        msg = str(exc.value).lower()
        assert "vision cascade exhausted" in msg
        assert "anthropic" in msg
        assert "grok" in msg
        assert "top up" not in msg
        assert "billing then re-run" not in msg
        assert "anthropic only" not in msg

    def test_exhausted_names_remaining_untried_keys(self):
        router = _Router({"api.anthropic.com": _credit_low()})
        with pytest.raises(VisionCascadeExhausted) as exc:
            extract_via_vision(
                PNG,
                PROMPT,
                env={"ANTHROPIC_API_KEY": "a", "NVIDIA_API_KEY": "n"},
                post=router,
            )
        # NVIDIA must have been tried (subscription keys exhausted) — not
        # left sitting while we tell ops to top up Anthropic.
        assert any("nvidia" in u for u in router.calls)
        assert "nvidia" in str(exc.value).lower()

    def test_no_keys_is_unavailable_not_anthropic_billing(self):
        with pytest.raises(VisionCascadeExhausted) as exc:
            extract_via_vision(PNG, PROMPT, env={}, post=_Router({}))
        msg = str(exc.value).lower()
        assert "no keyed" in msg or "no cta vision provider" in msg
        assert "top up" not in msg


class TestMenthorQClientWiring:
    def test_get_cta_does_not_require_anthropic_when_grok_is_keyed(
        self, mock_playwright
    ):
        from clients.menthorq_client import MenthorQClient, MenthorQExtractionError

        env = {
            "MENTHORQ_USER": "u@example.com",
            "MENTHORQ_PASS": "pw",
            "XAI_API_KEY": "xai-test",
        }
        for key in (
            "ANTHROPIC_API_KEY",
            "CLAUDE_CODE_API_KEY",
            "CLAUDE_API_KEY",
        ):
            env.setdefault(key, "")
        with patch.dict(os.environ, env, clear=False):
            for key in ("ANTHROPIC_API_KEY", "CLAUDE_CODE_API_KEY", "CLAUDE_API_KEY"):
                os.environ.pop(key, None)
            client = MenthorQClient(headless=True)
            try:
                page = mock_playwright["page"]
                page.evaluate.return_value = 4
                with patch.object(
                    client, "_download_card_images", return_value={"main": PNG}
                ), patch.object(
                    client, "_extract_via_vision", return_value=ROWS
                ) as vision:
                    tables = client.get_cta("2026-03-06")
                assert tables["main"] == ROWS
                assert vision.call_count == 1
            finally:
                client.close()

    def test_get_cta_surfaces_cascade_exhausted_not_anthropic_topup(
        self, mock_playwright
    ):
        from clients.menthorq_client import MenthorQClient, MenthorQExtractionError

        with patch.dict(
            os.environ,
            {
                "MENTHORQ_USER": "u@example.com",
                "MENTHORQ_PASS": "pw",
                "ANTHROPIC_API_KEY": "sk-ant-test",
                "XAI_API_KEY": "xai-test",
            },
            clear=False,
        ):
            client = MenthorQClient(headless=True)
            try:
                page = mock_playwright["page"]
                page.evaluate.return_value = 4
                with patch.object(
                    client, "_download_card_images", return_value={"main": PNG}
                ), patch(
                    "clients.menthorq_client.extract_via_vision",
                    side_effect=VisionCascadeExhausted(
                        "Vision cascade exhausted. tried=anthropic:credit_balance grok:http_401"
                    ),
                ):
                    with pytest.raises(MenthorQExtractionError) as exc:
                        client.get_cta("2026-03-06")
                msg = str(exc.value).lower()
                assert "vision cascade exhausted" in msg
                assert "top up" not in msg
            finally:
                client.close()


class TestHealthClassifier:
    def test_cascade_exhausted_is_not_menthorq_auth_and_not_retryable(self):
        from utils.cta_sync_health import classify_sync_error, retry_backoffs_for_error

        error_type, message = classify_sync_error(
            "ERROR: Vision cascade exhausted. tried=anthropic:credit_balance grok:http_401"
        )
        assert error_type == "vision_cascade_exhausted"
        assert retry_backoffs_for_error(error_type) == [0]
        assert "top up" not in message.lower()
