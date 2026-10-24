"""Refuse production Turso writes from a developer machine.

Laptop and VPS share one Turso database. Production writers run on Linux.
A non-Linux process must not write rows the VPS owns. ``RADON_MODE`` is not
a permit: the launchd agent that overwrote ``service_health`` on 2026-10-22
sets no mode (its plist carries PATH and PYTHONPATH only), and nothing in
this tree loads ``.env.ib-mode`` anymore (REL-330 / R-741).
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
    """Why this process may not write production rows, or ``None``.

    Linux is the VPS. Every other platform refuses, whatever ``RADON_MODE``
    says. ``PYTEST_CURRENT_TEST`` is set only by pytest, never by launchd or
    a service unit, so the suite can still exercise the transport on a
    developer machine. A test of the production decision deletes that
    variable (or passes an env mapping that lacks it).
    """
    env = os.environ if env is None else env
    platform = sys.platform if platform is None else platform
    if platform.startswith(PRODUCTION_PLATFORM_PREFIX):
        return None
    if env.get("PYTEST_CURRENT_TEST"):
        return None
    mode = radon_mode(env) or "unset"
    return (
        f"production writers run on Linux; refusing shared Turso writes from "
        f"{platform} (RADON_MODE={mode} is not a permit)"
    )


def refuse_developer_host_in_cloud_mode(
    env: Mapping[str, str] | None = None,
    platform: str | None = None,
) -> None:
    """Raise :class:`ProductionWriterRefused` when a write must not happen here."""
    reason = refusal_reason(env, platform)
    if reason:
        raise ProductionWriterRefused(reason)
