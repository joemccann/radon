"""Shared CTA / MenthorQ vision cascade.

Thin wrapper over ``clients.model_ladder`` — one rung-order source of truth.
"""
from __future__ import annotations

from typing import Any, Callable, Mapping

from clients.model_ladder import (
    MODEL_LADDER_ORDER as VISION_CASCADE_ORDER,
    MODEL_LADDER_TIERS as VISION_CASCADE_TIERS,
    ModelLadderExhausted,
    VisionExtractResult as VisionResult,
    parse_json_rows,
    run_vision_extract_ladder,
    wired_providers as wired_vision_providers,
)


class VisionCascadeExhausted(RuntimeError):
    """Every keyed cascade provider failed or none were keyed."""


def extract_via_vision(
    png_bytes: bytes,
    prompt: str,
    *,
    env: Mapping[str, str] | None = None,
    post: Callable[..., Any] | None = None,
) -> VisionResult:
    try:
        return run_vision_extract_ladder(
            png_bytes, prompt, env=env, post=post, log_prefix="CTA vision"
        )
    except ModelLadderExhausted as exc:
        msg = str(exc).replace("Model ladder exhausted", "Vision cascade exhausted", 1)
        raise VisionCascadeExhausted(msg) from None


__all__ = [
    "VISION_CASCADE_ORDER",
    "VISION_CASCADE_TIERS",
    "VisionCascadeExhausted",
    "VisionResult",
    "extract_via_vision",
    "parse_json_rows",
    "wired_vision_providers",
]
