"""Refuse production Turso writes from a developer machine in cloud mode.

Laptop and VPS share one Turso database. ``RADON_MODE=hetzner`` means the
production schedulers run on the VPS, so a process on a developer host that
still carries that mode (a launchd agent that survived ``cloud.sh``, a stray
``--once`` run) must not write rows the VPS writers own. Production is Linux;
every macOS process in cloud mode is such a stray.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from pathlib import Path

CLOUD_MODE = "hetzner"
PRODUCTION_PLATFORM_PREFIX = "linux"
EXIT_REFUSED = 78  # EX_CONFIG
_IB_MODE_FILE = Path(__file__).resolve().parents[2] / ".env.ib-mode"


class ProductionWriterRefused(RuntimeError):
    """A developer host in cloud mode tried to write production rows."""


def radon_mode(env: Mapping[str, str] | None = None, ib_mode_file: Path | None = None) -> str:
    """``RADON_MODE`` from the environment, else from the ``.env.ib-mode`` overlay."""
    env = os.environ if env is None else env
    value = env.get("RADON_MODE", "")
    if not value:
        value = _mode_from_file(_IB_MODE_FILE if ib_mode_file is None else ib_mode_file)
    return value.strip().strip("'\"").lower()


def refusal_reason(
    env: Mapping[str, str] | None = None,
    platform: str | None = None,
    ib_mode_file: Path | None = None,
) -> str | None:
    """Why this process may not write production rows, or ``None``."""
    platform = sys.platform if platform is None else platform
    if platform.startswith(PRODUCTION_PLATFORM_PREFIX):
        return None
    if radon_mode(env, ib_mode_file) != CLOUD_MODE:
        return None
    return (
        f"RADON_MODE={CLOUD_MODE} on {platform}: production writers run on the "
        "VPS; this host must not write shared Turso rows (run scripts/local.sh "
        "to take the writers back)"
    )


def refuse_developer_host_in_cloud_mode(
    env: Mapping[str, str] | None = None,
    platform: str | None = None,
) -> None:
    """Raise :class:`ProductionWriterRefused` when a write must not happen here."""
    reason = refusal_reason(env, platform)
    if reason:
        raise ProductionWriterRefused(reason)


def _mode_from_file(path: Path) -> str:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return ""
    for line in lines:
        key, sep, value = line.strip().partition("=")
        if sep and key == "RADON_MODE":
            return value
    return ""
