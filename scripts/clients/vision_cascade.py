"""Thin wrapper around the shared model ladder for CTA / MenthorQ vision.

Joe's exact order lives in ``clients.model_ladder``; this module keeps the
historical import path and re-exports the vision-specific API.
"""
from __future__ import annotations

from clients.model_ladder import (
    MODEL_LADDER_ORDER,
    MODEL_LADDER_TIERS,
    ModelLadderExhausted,
    VisionResult,
    extract_via_vision,
    parse_json_rows,
    wired_providers,
)

VISION_CASCADE_ORDER = MODEL_LADDER_ORDER
VISION_CASCADE_TIERS = MODEL_LADDER_TIERS
VisionCascadeExhausted = ModelLadderExhausted


def wired_vision_providers(env=None):
    return wired_providers(env)


__all__ = [
    "VISION_CASCADE_ORDER",
    "VISION_CASCADE_TIERS",
    "VisionCascadeExhausted",
    "VisionResult",
    "extract_via_vision",
    "parse_json_rows",
    "wired_vision_providers",
]
