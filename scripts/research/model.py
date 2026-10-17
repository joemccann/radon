"""Bounded, tool-free multimodal review via the shared model ladder."""
from __future__ import annotations

import os
from pathlib import Path

from clients.model_ladder import (
    ModelLadderExhausted,
    ModelResponseError,
    complete_multimodal_json,
    complete_text_json,
    safe_error_message,
    wired_providers,
)


class ModelError(RuntimeError):
    """Research reviewer failure with a safe operator-facing message."""

    def __init__(self, message: str):
        super().__init__(message)


def _policy_text() -> str:
    return Path(__file__).with_name("policy.md").read_text()


def intake_v2_enabled(env=None) -> bool:
    """RADON_RESEARCH_PIPELINE=v2 selects research.intake; anything else keeps research.pipeline."""
    return ((os.environ if env is None else env).get("RADON_RESEARCH_PIPELINE") or "").strip().lower() == "v2"


def build_pipeline(root, publisher, env=None, **kwargs):
    if intake_v2_enabled(env):
        from research.intake import Pipeline
    else:
        from research.pipeline import Pipeline
    return Pipeline(root, Reviewer() if env is None else Reviewer(env=env), publisher, **kwargs)


class Reviewer:
    def __init__(self, api_key=None, model=None, session=None, env=None):
        self.env = dict(os.environ if env is None else env)
        if api_key:
            self.env["CLAUDE_CODE_OAUTH_TOKEN"] = api_key
        self.model = model or self.env.get("RADON_RESEARCH_MODEL")
        self.session = session
        self.begin_document()
        if not wired_providers(self.env):
            raise ModelError("No keyed model provider is configured for research review")

    def begin_document(self):
        """Retry every configured provider for each PDF, including reused pipelines."""
        self._document_timeouts = {False: set(), True: set()}

    def _providers(self, has_images):
        providers = tuple(name for name in wired_providers(self.env)
                          if name not in self._document_timeouts[has_images])
        if not providers:
            # The shared ladder treats an empty tuple as its full default order.
            raise ModelError("Research reviewer unavailable: no eligible provider remains for this document")
        return providers

    def _remember_timeouts(self, result, has_images):
        timeout_classes = {"ReadTimeout", "ConnectTimeout", "WriteTimeout", "PoolTimeout", "Timeout", "TimeoutError"}
        for attempt in result.attempted:
            parts = attempt.split(":")
            if (len(parts) == 3 and parts[1] == "network" and parts[2] in timeout_classes
                    and parts[0] != result.provider):
                self._document_timeouts[has_images].add(parts[0])

    def ask_text(self, instruction):
        """Text-only JSON completion (intake v2 SELECT): no images, larger output budget, cheapest text rung."""
        try:
            result = complete_text_json(
                instruction,
                providers=self._providers(False),
                system=_policy_text(),
                env=self.env,
                post=self.session.post if self.session is not None else None,
                model_override=self.env.get("RADON_RESEARCH_TEXT_MODEL") or self.model,
                max_tokens=10000,
                read_timeout=120.0,
                max_response_bytes=2_000_000,
                require_end_turn=True,
            )
        except ModelResponseError as exc:
            raise ModelError(str(exc)) from None
        except ModelLadderExhausted as exc:
            raise ModelError(safe_error_message(exc)) from None
        except (ValueError, TypeError, KeyError):
            raise ModelError("Research reviewer unavailable or malformed response") from None
        self._remember_timeouts(result, False)
        return result.data

    def ask(self, instruction, images=()):
        for _label, path in images:
            raw = Path(path).read_bytes()
            if len(raw) > 5_000_000:
                raise ModelError("Source image exceeds review byte limit")
        try:
            result = complete_multimodal_json(
                instruction,
                images,
                providers=self._providers(bool(images)),
                system=_policy_text(),
                env=self.env,
                post=self.session.post if self.session is not None else None,
                model_override=self.model,
                stream_anthropic=True,
                read_timeout=120.0,
                max_response_bytes=2_000_000,
                require_end_turn=True,
            )
        except ModelResponseError as exc:
            raise ModelError(str(exc)) from None
        except ModelLadderExhausted as exc:
            raise ModelError(safe_error_message(exc)) from None
        except (ValueError, TypeError, KeyError):
            raise ModelError("Research reviewer unavailable or malformed response") from None
        self._remember_timeouts(result, bool(images))
        return result.data


# Subscription quotas refill on rolling windows; parked work waits this long before its next try.
PROVIDER_PARK_SECS = 15 * 60

_OUTAGE_MARKERS = ("ladder exhausted", "quota", "rate limit", "rate_limit", "http_429", "http_5",
                   "overloaded", "capacity", "timeout", "timed out", "unavailable")


def is_provider_outage(error: Exception) -> bool:
    """True when the failure is the provider's, not the document's; such work is parked, never held."""
    if not isinstance(error, ModelError):
        return False
    message = str(error).lower()
    return any(marker in message for marker in _OUTAGE_MARKERS)


def classify_error(error: Exception) -> str:
    """Safe error string for queue persistence and health reporting."""
    if isinstance(error, ModelError):
        return safe_error_message(error)
    from research.pipeline import EXTRACT_MESSAGE_MAX, EvidenceError
    if isinstance(error, EvidenceError):
        message = str(error).strip()
        if not message:
            return type(error).__name__
        if len(message) > EXTRACT_MESSAGE_MAX:
            return message[:EXTRACT_MESSAGE_MAX - 3] + '...'
        return message
    return type(error).__name__
