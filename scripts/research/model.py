"""Bounded, tool-free multimodal review via the shared HTTP model ladder."""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

import requests

from clients.model_ladder import (
    LadderAbort,
    ModelLadderExhausted,
    run_json_multimodal_ladder,
    safe_failure_message,
    wired_providers,
)

logger = logging.getLogger(__name__)

MAX_IMAGE_BYTES = 5_000_000
MAX_RESPONSE_BYTES = 2_000_000


class ModelError(RuntimeError):
    """Safe, operator-readable model failure."""


def safe_error_message(error: BaseException) -> str:
    if isinstance(error, ModelError):
        msg = str(error).strip()
        return msg[:200] if msg else "ModelError"
    return type(error).__name__


class _Response:
    def __init__(self, status_code: int, payload: Any = None, text: str = ""):
        self.status_code = status_code
        self._payload = payload
        self.text = text or (json.dumps(payload) if payload is not None else "")

    def json(self):
        if self._payload is None:
            raise ValueError("no json payload")
        return self._payload


class Reviewer:
    def __init__(self, api_key=None, model=None, session=None, env=None):
        self.env = dict(env if env is not None else os.environ)
        if api_key:
            self.env.setdefault("ANTHROPIC_API_KEY", api_key)
        if model:
            self.env["RADON_RESEARCH_MODEL"] = model
        self.session = session or requests.Session()
        self.provider = "anthropic"
        self.model = self.env.get("RADON_RESEARCH_MODEL", "claude-sonnet-4-6")
        if not wired_providers(self.env):
            raise ModelError("No keyed model provider for research evidence review")

    def ask(self, instruction, images=()):
        for _label, path in images:
            if Path(path).stat().st_size > MAX_IMAGE_BYTES:
                raise ModelError("Source image exceeds review byte limit")

        system = Path(__file__).with_name("policy.md").read_text()

        def post(url, *, headers=None, json=None, timeout=None):
            import json as json_mod

            req_timeout = (10, int(timeout)) if isinstance(timeout, (int, float)) else (
                timeout if timeout is not None else (10, 120)
            )
            try:
                response = self.session.post(
                    url,
                    headers=headers,
                    json=json,
                    timeout=req_timeout,
                    stream=True,
                )
            except requests.RequestException as exc:
                raise RuntimeError(f"network:{type(exc).__name__}") from exc
            try:
                raw = bytearray()
                for chunk in response.iter_content(65536):
                    raw.extend(chunk)
                    if len(raw) > MAX_RESPONSE_BYTES:
                        raise LadderAbort("Research reviewer response exceeds limit")
                text = raw.decode("utf-8", errors="replace")
                try:
                    payload = json_mod.loads(raw)
                except json_mod.JSONDecodeError:
                    payload = None
                return _Response(response.status_code, payload, text=text)
            finally:
                response.close()

        def validate_anthropic_stop(payload: dict[str, Any]) -> str | None:
            if not isinstance(payload.get("content"), list) or any(
                not isinstance(x, dict) for x in payload["content"]
            ):
                return "invalid_envelope"
            if payload.get("stop_reason") != "end_turn":
                return "incomplete_response"
            return None

        try:
            result = run_json_multimodal_ladder(
                instruction,
                images,
                system=system,
                env=self.env,
                post=post,
                max_tokens=6000,
                timeout=120.0,
                validate_anthropic_stop=validate_anthropic_stop,
                log_prefix="research reviewer",
            )
        except LadderAbort as exc:
            raise ModelError(str(exc)) from None
        except ModelLadderExhausted as exc:
            raise ModelError(str(exc)) from None
        except (ValueError, KeyError, TypeError):
            raise ModelError("Research reviewer unavailable or malformed response") from None

        self.provider = result.provider
        self.model = result.model
        logger.info(
            "Research reviewer won provider=%s model=%s", result.provider, result.model
        )
        return result.value
