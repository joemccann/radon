"""Refuse production Turso writes from a developer machine in cloud mode.

Laptop and VPS share one Turso database. ``RADON_MODE=hetzner`` means the
production schedulers run on the VPS, so a process on a developer host that
still carries that mode (a leftover launchd agent, a stray ``--once`` run with
the VPS environment) must not write rows the VPS writers own. Production is
Linux; every macOS process in cloud mode is such a stray. The laptop has no
local mode: ``RADON_MODE`` comes from the environment only.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping

CLOUD_MODE = "hetzner"
PRODUCTION_PLATFORM_PREFIX = "linux"
EXIT_REFUSED = 78  # EX_CONFIG


class ProductionWriterRefused(RuntimeError):
    """A developer host in cloud mode tried to write production rows."""


def radon_mode(env: Mapping[str, str] | None = None) -> str:
    """``RADON_MODE`` from the environment (empty when unset)."""
    env = os.environ if env is None else env
    return env.get("RADON_MODE", "").strip().strip("'\"").lower()


def refusal_reason(
    env: Mapping[str, str] | None = None,
    platform: str | None = None,
) -> str | None:
    """Why this process may not write production rows, or ``None``."""
    platform = sys.platform if platform is None else platform
    if platform.startswith(PRODUCTION_PLATFORM_PREFIX):
        return None
    if radon_mode(env) != CLOUD_MODE:
        return None
    return (
        f"RADON_MODE={CLOUD_MODE} on {platform}: production writers run on the "
        "VPS; this host must not write shared Turso rows (unset RADON_MODE "
        "in this shell; the laptop owns no writers)"
    )


def refuse_developer_host_in_cloud_mode(
    env: Mapping[str, str] | None = None,
    platform: str | None = None,
) -> None:
    """Raise :class:`ProductionWriterRefused` when a write must not happen here."""
    reason = refusal_reason(env, platform)
    if reason:
        raise ProductionWriterRefused(reason)
