"""SLM tagger ladder modes. Default is off; MODEL_LADDER_ORDER is unchanged."""
from __future__ import annotations

from typing import Sequence

from clients.model_ladder import MODEL_LADDER_ORDER

from newsfeed.slm.contract import SLM_TAGGER_RUNG

SLM_MODE_OFF = "off"
SLM_MODE_SHADOW = "shadow"
SLM_MODE_PREFER = "prefer"
SLM_MODE_PRIMARY = "primary"

# H.2 order strings. Tests pin these exactly.
SLM_PREFER_ORDER: tuple[str, ...] = (SLM_TAGGER_RUNG, *MODEL_LADDER_ORDER)
SLM_PRIMARY_ORDER: tuple[str, ...] = (SLM_TAGGER_RUNG, "nvidia", "cerebras")
SLM_SHADOW_PROBE_ORDER: tuple[str, ...] = (SLM_TAGGER_RUNG,)


def normalise_mode(raw: str | None) -> str:
    mode = (raw or SLM_MODE_OFF).strip().lower()
    if mode not in {SLM_MODE_OFF, SLM_MODE_SHADOW, SLM_MODE_PREFER, SLM_MODE_PRIMARY}:
        return SLM_MODE_OFF
    return mode


def providers_for_mode(mode: str) -> Sequence[str] | None:
    """Return providers= for prefer/primary. off/shadow/unset: None (today's order)."""
    mode = normalise_mode(mode)
    if mode == SLM_MODE_PREFER:
        return SLM_PREFER_ORDER
    if mode == SLM_MODE_PRIMARY:
        return SLM_PRIMARY_ORDER
    return None
