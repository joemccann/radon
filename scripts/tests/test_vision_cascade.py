"""CTA vision cascade: Joe's exact provider order and credit fallthrough.

Order (do not leave a band early):
  subscription: anthropic -> grok -> cursor -> codex -> gemini
  nvidia (free endpoints)
  cerebras (cheap paid, last)

Cursor has no vision HTTP path in Radon; it is skipped as unavailable,
not treated as a reason to leave the subscription band.

Subscription-tier rungs require subscription credentials by default
(CLAUDE_CODE_OAUTH_TOKEN, ~/.grok/auth.json, CODEX_HOME/auth.json,
GEMINI_OAUTH_TOKEN). Prepaid console wallets do not wire those rungs
unless RADON_LADDER_ALLOW_PREPAID=1. NVIDIA / Cerebras API keys remain OK.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from clients.vision_cascade import (
    VISION_CASCADE_ORDER,
    VISION_CASCADE_TIERS,
    VisionCascadeExhausted,
    VisionResult,
    extract_via_vision,
    wired_vision_providers,
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
PROMPT = "extract"
PNG = b"\x89PNG-fake"

# Prepaid wallets alone — must NOT wire subscription rungs by default.
PREPAID_KEYS = {
    "ANTHROPIC_API_KEY": "sk-ant-test",
    "XAI_API_KEY": "xai-test",
    "OPENAI_API_KEY": "sk-openai-test",
    "GEMINI_API_KEY": "gem-test",
    "NVIDIA_API_KEY": "nvapi-test",
    "CEREBRAS_API_KEY": "csk-test",
}


def _subscription_env(tmp_path: Path, **extra: str) -> dict[str, str]:
    """Subscription-band credentials for anthropic/grok/codex/gemini + NVIDIA/Cerebras."""
    home = tmp_path / "home"
    (home / ".grok").mkdir(parents=True)
    (home / ".grok" / "auth.json").write_text(
        json.dumps({"access_token": "grok-sub-token"}),
        encoding="utf-8",
    )
    codex_home = tmp_path / "codex"
    codex_home.mkdir()
    (codex_home / "auth.json").write_text(
        json.dumps({"tokens": {"access_token": "codex-sub-token"}}),
        encoding="utf-8",
    )
    env = {
        "HOME": str(home),
        "CODEX_HOME": str(codex_home),
        "CLAUDE_CODE_OAUTH_TOKEN": "claude-sub-token",
        "GEMINI_OAUTH_TOKEN": "gemini-sub-token",
        "NVIDIA_API_KEY": "nvapi-test",
        "CEREBRAS_API_KEY": "csk-test",
    }
    env.update(extra)
    return env


@pytest.fixture
def mock_playwright():
    import clients.menthorq_client  # noqa: F401 — load module before patch target resolve
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

    def test_cursor_is_never_wired(self, tmp_path):
        assert "cursor" not in wired_vision_providers(_subscription_env(tmp_path))
        assert "cursor" not in wired_vision_providers(PREPAID_KEYS)

    def test_wired_follows_env_keys(self, tmp_path):
        # Prepaid console wallets do not wire subscription rungs by default.
        assert wired_vision_providers({"XAI_API_KEY": "x"}) == ()
        assert wired_vision_providers({"GROK_API_KEY": "g"}) == ()
        assert wired_vision_providers({"OPENAI_API_KEY": "o"}) == ()
        assert wired_vision_providers({"GEMINI_API_KEY": "g"}) == ()
        assert wired_vision_providers({"ANTHROPIC_API_KEY": "a"}) == ()
        # NVIDIA / Cerebras API keys remain first-class.
        assert wired_vision_providers({"NVIDIA_API_KEY": "n"}) == ("nvidia",)
        assert wired_vision_providers({"CEREBRAS_API_KEY": "c"}) == ("cerebras",)
        assert wired_vision_providers({}) == ()
        # Subscription credentials wire the matching rungs.
        home = tmp_path / "home"
        (home / ".grok").mkdir(parents=True)
        (home / ".grok" / "auth.json").write_text(
            json.dumps({"access_token": "grok-sub"}), encoding="utf-8"
        )
        assert wired_vision_providers({"HOME": str(home)}) == ("grok",)
        assert wired_vision_providers({"CLAUDE_CODE_OAUTH_TOKEN": "oauth"}) == (
            "anthropic",
        )
        assert wired_vision_providers({"GEMINI_OAUTH_TOKEN": "gem-oauth"}) == (
            "gemini",
        )
        codex_home = tmp_path / "codex"
        codex_home.mkdir()
        (codex_home / "auth.json").write_text(
            json.dumps({"tokens": {"access_token": "codex-sub"}}),
            encoding="utf-8",
        )
        assert wired_vision_providers({"CODEX_HOME": str(codex_home)}) == ("codex",)
        # Escape hatch restores prepaid wiring when explicitly opted in.
        assert wired_vision_providers(
            {"XAI_API_KEY": "x", "RADON_LADDER_ALLOW_PREPAID": "1"}
        ) == ("grok",)


class TestCreditFallthrough:
    def test_anthropic_credit_falls_to_grok_before_nvidia(self, tmp_path):
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
            PNG, PROMPT, env=_subscription_env(tmp_path), post=router
        )
        assert isinstance(result, VisionResult)
        assert result.provider == "grok"
        assert result.rows == ROWS
        assert any("api.anthropic.com" in u for u in router.calls)
        assert any("api.x.ai" in u for u in router.calls)
        assert not any("nvidia" in u for u in router.calls)

    def test_anthropic_quota_falls_to_grok(self, tmp_path):
        home = tmp_path / "home"
        (home / ".grok").mkdir(parents=True)
        (home / ".grok" / "auth.json").write_text(
            json.dumps({"access_token": "grok-sub"}), encoding="utf-8"
        )
        router = _Router(
            {"api.anthropic.com": _quota(), "api.x.ai": _openai_ok()}
        )
        result = extract_via_vision(
            PNG,
            PROMPT,
            env={
                "HOME": str(home),
                "CLAUDE_CODE_OAUTH_TOKEN": "claude-sub",
            },
            post=router,
        )
        assert result.provider == "grok"

    def test_anthropic_401_falls_to_grok(self, tmp_path):
        home = tmp_path / "home"
        (home / ".grok").mkdir(parents=True)
        (home / ".grok" / "auth.json").write_text(
            json.dumps({"access_token": "grok-sub"}), encoding="utf-8"
        )
        router = _Router(
            {"api.anthropic.com": _http_401(), "api.x.ai": _openai_ok()}
        )
        result = extract_via_vision(
            PNG,
            PROMPT,
            env={
                "HOME": str(home),
                "CLAUDE_CODE_OAUTH_TOKEN": "claude-sub",
            },
            post=router,
        )
        assert result.provider == "grok"

    def test_subscription_band_is_exhausted_before_nvidia(self, tmp_path):
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
        result = extract_via_vision(
            PNG, PROMPT, env=_subscription_env(tmp_path), post=router
        )
        assert result.provider == "nvidia"
        assert any("api.openai.com" in u for u in router.calls)
        assert any("generativelanguage.googleapis.com" in u for u in router.calls)
        assert not any("cerebras" in u for u in router.calls)

    def test_prepaid_only_skips_subscription_band_and_uses_nvidia(self):
        """Hetzner prepaid-only: do not burn ANTHROPIC/XAI/OpenAI/Gemini wallets."""
        router = _Router(
            {
                "api.anthropic.com": _credit_low(),
                "api.x.ai": _openai_ok(),
                "integrate.api.nvidia.com": _openai_ok(),
            }
        )
        result = extract_via_vision(PNG, PROMPT, env=PREPAID_KEYS, post=router)
        assert result.provider == "nvidia"
        assert not any("api.anthropic.com" in u for u in router.calls)
        assert not any("api.x.ai" in u for u in router.calls)

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

    def test_gemini_wins_inside_subscription_band(self, tmp_path):
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
        result = extract_via_vision(
            PNG, PROMPT, env=_subscription_env(tmp_path), post=router
        )
        assert result.provider == "gemini"
        assert not any("nvidia" in u for u in router.calls)

    def test_codex_uses_subscription_auth(self, tmp_path):
        codex_home = tmp_path / "codex"
        codex_home.mkdir()
        (codex_home / "auth.json").write_text(
            json.dumps({"tokens": {"access_token": "codex-sub"}}),
            encoding="utf-8",
        )
        router = _Router({"api.openai.com": _openai_ok()})
        result = extract_via_vision(
            PNG, PROMPT, env={"CODEX_HOME": str(codex_home)}, post=router
        )
        assert result.provider == "codex"

    def test_codex_prepaid_openai_key_skipped_without_allow(self):
        router = _Router({"api.openai.com": _openai_ok()})
        with pytest.raises(VisionCascadeExhausted):
            extract_via_vision(
                PNG, PROMPT, env={"OPENAI_API_KEY": "sk-o"}, post=router
            )
        assert router.calls == []


class TestExhaustedMessaging:
    def test_exhausted_does_not_tell_ops_to_top_up_anthropic_only(self, tmp_path):
        home = tmp_path / "home"
        (home / ".grok").mkdir(parents=True)
        (home / ".grok" / "auth.json").write_text(
            json.dumps({"access_token": "grok-sub"}), encoding="utf-8"
        )
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
                env={
                    "HOME": str(home),
                    "CLAUDE_CODE_OAUTH_TOKEN": "claude-sub",
                },
                post=router,
            )
        msg = str(exc.value).lower()
        assert "ladder exhausted" in msg or "exhausted" in msg
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
                env={
                    "CLAUDE_CODE_OAUTH_TOKEN": "claude-sub",
                    "NVIDIA_API_KEY": "n",
                },
                post=router,
            )
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
        self, mock_playwright, tmp_path
    ):
        from clients.menthorq_client import MenthorQClient, MenthorQExtractionError

        home = tmp_path / "home"
        (home / ".grok").mkdir(parents=True)
        (home / ".grok" / "auth.json").write_text(
            json.dumps({"access_token": "grok-sub"}), encoding="utf-8"
        )
        env = {
            "HOME": str(home),
            "MENTHORQ_USER": "u@example.com",
            "MENTHORQ_PASS": "pw",
        }
        clear_keys = (
            "ANTHROPIC_API_KEY",
            "CLAUDE_CODE_API_KEY",
            "CLAUDE_API_KEY",
            "CLAUDE_CODE_OAUTH_TOKEN",
            "XAI_API_KEY",
            "GROK_API_KEY",
        )
        for key in clear_keys:
            env[key] = ""
        with patch.dict(os.environ, env, clear=False):
            for key in clear_keys:
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
        self, mock_playwright, tmp_path
    ):
        from clients.menthorq_client import MenthorQClient, MenthorQExtractionError

        home = tmp_path / "home"
        (home / ".grok").mkdir(parents=True)
        (home / ".grok" / "auth.json").write_text(
            json.dumps({"access_token": "grok-sub"}), encoding="utf-8"
        )
        with patch.dict(
            os.environ,
            {
                "HOME": str(home),
                "MENTHORQ_USER": "u@example.com",
                "MENTHORQ_PASS": "pw",
                "CLAUDE_CODE_OAUTH_TOKEN": "claude-sub",
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
                        "Model ladder exhausted. tried=anthropic:credit_balance grok:http_401"
                    ),
                ):
                    with pytest.raises(MenthorQExtractionError) as exc:
                        client.get_cta("2026-03-06")
                msg = str(exc.value).lower()
                assert "ladder exhausted" in msg
                assert "top up" not in msg
            finally:
                client.close()


class TestHealthClassifier:
    def test_cascade_exhausted_is_not_menthorq_auth_and_not_retryable(self):
        from utils.cta_sync_health import classify_sync_error, retry_backoffs_for_error

        error_type, message = classify_sync_error(
            "ERROR: Model ladder exhausted. tried=anthropic:credit_balance grok:http_401"
        )
        assert error_type == "vision_cascade_exhausted"
        assert retry_backoffs_for_error(error_type) == [0]
        assert "top up" not in message.lower()
