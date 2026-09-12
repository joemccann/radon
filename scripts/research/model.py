"""Bounded, tool-free multimodal review via the shared model ladder."""
from __future__ import annotations

import os
from pathlib import Path

from clients.model_ladder import (
    ModelLadderExhausted,
    ModelResponseError,
    complete_multimodal_json,
    safe_error_message,
    wired_providers,
)


class ModelError(RuntimeError):
    """Research reviewer failure with a safe operator-facing message."""

    def __init__(self, message: str):
        super().__init__(message)


def _policy_text() -> str:
    return Path(__file__).with_name("policy.md").read_text()


class Reviewer:
    def __init__(self, api_key=None, model=None, session=None, env=None):
        self.env = dict(env or os.environ)
        if api_key:
            self.env["ANTHROPIC_API_KEY"] = api_key
        self.model = model or self.env.get("RADON_RESEARCH_MODEL")
        self.session = session
        if not wired_providers(self.env):
            raise ModelError("No keyed model provider is configured for research review")

    def ask(self, instruction, images=()):
        for _label, path in images:
            raw = Path(path).read_bytes()
            if len(raw) > 5_000_000:
                raise ModelError("Source image exceeds review byte limit")
        try:
            result = complete_multimodal_json(
                instruction,
                images,
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
        return result.data


def classify_error(error: Exception) -> str:
    """Safe error string for queue persistence and health reporting."""
    if isinstance(error, ModelError):
        return safe_error_message(error)
    return type(error).__name__
