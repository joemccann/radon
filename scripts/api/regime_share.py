"""Regime/CRI share generation with the page's live reading.

The /regime page recomputes CRI from live VIX/VVIX/SPY/COR1M ticks between
scans. The share cards must show that reading, so the page posts it and the
generator lays it over the last scan snapshot (`--live-overlay`).
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from api.subprocess import ScriptResult, run_script
from utils.share_cards import ensure_share_cards_dir

GENERATOR = "generate_regime_share.py"
GENERATOR_TIMEOUT_S = 120

# Bounds are sanity limits on a browser-supplied payload, not market limits.
Quote = Field(default=None, ge=-1000, le=100_000, allow_inf_nan=False)
Component = Field(ge=0, le=25, allow_inf_nan=False)


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CriComponents(_Strict):
    vix: float = Component
    vvix: float = Component
    correlation: float = Component
    momentum: float = Component


class CriReading(_Strict):
    score: float = Field(ge=0, le=100, allow_inf_nan=False)
    level: Literal["LOW", "ELEVATED", "HIGH", "CRITICAL"]
    components: CriComponents


class CrashTriggerConditions(_Strict):
    spx_below_100d_ma: Optional[bool] = None
    realized_vol_gt_25: Optional[bool] = None
    cor1m_gt_60: Optional[bool] = None


class CrashTrigger(_Strict):
    conditions: CrashTriggerConditions


class RegimeLiveOverlay(_Strict):
    vix: Optional[float] = Quote
    vvix: Optional[float] = Quote
    spy: Optional[float] = Quote
    cor1m: Optional[float] = Quote
    cor1m_5d_change: Optional[float] = Quote
    vvix_vix_ratio: Optional[float] = Quote
    spx_distance_pct: Optional[float] = Quote
    realized_vol: Optional[float] = Quote
    cri: Optional[CriReading] = None
    crash_trigger: Optional[CrashTrigger] = None

    def to_generator_json(self) -> str:
        return self.model_dump_json(exclude_none=True)


def overlay_dir() -> Path:
    return ensure_share_cards_dir()


async def generate(
    overlay: Optional[RegimeLiveOverlay],
    card_type: Literal["regime", "internals"],
) -> ScriptResult:
    args = ["--json", "--no-open"]
    if card_type != "regime":
        args += ["--card-type", card_type]
    if overlay is None:
        return await run_script(GENERATOR, args, timeout=GENERATOR_TIMEOUT_S)
    path = _write_overlay(overlay)
    try:
        return await run_script(
            GENERATOR, args[:2] + ["--live-overlay", str(path)] + args[2:],
            timeout=GENERATOR_TIMEOUT_S,
        )
    finally:
        path.unlink(missing_ok=True)


def _write_overlay(overlay: RegimeLiveOverlay) -> Path:
    fd, name = tempfile.mkstemp(prefix="regime-live-", suffix=".json", dir=overlay_dir())
    with os.fdopen(fd, "w") as f:
        f.write(overlay.to_generator_json())
    return Path(name)
