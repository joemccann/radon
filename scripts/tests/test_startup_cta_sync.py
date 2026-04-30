"""
Regression: the dev/web-server startup must kick off a CTA cache refresh.

Without this wiring, /cta surfaces a "CTA CACHE STALE" banner whenever the
launchd job missed a window (machine asleep, etc.), because nothing else
backfills today's MenthorQ pull.

The check is deliberately filesystem-level — if anyone deletes the wrapper
or removes the call from cloud.sh/local.sh, this fails.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = REPO_ROOT / "scripts"
WRAPPER = SCRIPTS / "_post_start_cta.sh"
CLOUD_SH = SCRIPTS / "cloud.sh"
LOCAL_SH = SCRIPTS / "local.sh"
CTA_SERVICE = SCRIPTS / "cta_sync_service.py"


def test_post_start_cta_wrapper_exists() -> None:
    assert WRAPPER.is_file(), f"{WRAPPER} must exist so cloud.sh / local.sh can invoke it"


def test_post_start_cta_wrapper_is_executable() -> None:
    mode = WRAPPER.stat().st_mode
    assert mode & stat.S_IXUSR, "_post_start_cta.sh must be chmod +x to run from cloud.sh / local.sh"


def test_post_start_cta_wrapper_invokes_cta_sync_service() -> None:
    """The wrapper must actually call the CTA sync runner, not just sit there."""
    src = WRAPPER.read_text()
    assert "cta_sync_service" in src, (
        "_post_start_cta.sh must invoke scripts/cta_sync_service.py "
        "(directly, via run_cta_sync.sh, or via a FastAPI endpoint that wraps it)"
    )


def test_post_start_cta_wrapper_logs_to_dedicated_file() -> None:
    """A dedicated log file means failures are findable. Mirrors the journal pattern."""
    src = WRAPPER.read_text()
    assert "logs/cta-startup-sync.log" in src or "cta-startup-sync.log" in src, (
        "_post_start_cta.sh must log to logs/cta-startup-sync.log"
    )


def test_cta_sync_service_still_present() -> None:
    """Keep the wrapper test honest — bail loudly if the runner moves."""
    assert CTA_SERVICE.is_file(), f"{CTA_SERVICE} is the runner the wrapper depends on"


@pytest.mark.parametrize("script", [CLOUD_SH, LOCAL_SH], ids=lambda p: p.name)
def test_startup_script_invokes_post_start_cta(script: Path) -> None:
    """Both startup scripts must background _post_start_cta.sh."""
    assert script.is_file(), f"{script} missing"
    src = script.read_text()
    assert "_post_start_cta.sh" in src, (
        f"{script.name} must invoke scripts/_post_start_cta.sh so a fresh "
        f"dev/web-server startup refreshes the CTA cache"
    )


@pytest.mark.parametrize("script", [CLOUD_SH, LOCAL_SH], ids=lambda p: p.name)
def test_startup_script_backgrounds_cta_sync(script: Path) -> None:
    """Backgrounded — CTA fetch takes ~80s and must not block dev startup."""
    src = script.read_text()
    # Look for the same `( ... & )` subshell pattern as _post_start_journal.sh.
    assert "_post_start_cta.sh &" in src or "( \"$SCRIPT_DIR/_post_start_cta.sh\" &" in src, (
        f"{script.name} must background _post_start_cta.sh so npm run dev isn't blocked"
    )


@pytest.mark.parametrize("script", [CLOUD_SH, LOCAL_SH], ids=lambda p: p.name)
def test_startup_script_invokes_cta_sync_before_exec(script: Path) -> None:
    """The wrapper call must precede `exec npm run dev` — otherwise it never runs."""
    src = script.read_text()
    cta_idx = src.find("_post_start_cta.sh")
    exec_idx = src.find("exec npm run dev")
    assert cta_idx > -1 and exec_idx > -1, "both markers required"
    assert cta_idx < exec_idx, (
        f"{script.name}: _post_start_cta.sh invocation must come before "
        f"`exec npm run dev` (otherwise the exec replaces the shell and the "
        f"backgrounded subshell never fires)"
    )
