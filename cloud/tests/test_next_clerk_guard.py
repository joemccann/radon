"""Fail closed: Clerk publishableKey must be in env AND the client bundle.

An empty bake ships /sign-in as Missing publishableKey. --env-file cannot
repair NEXT_PUBLIC_* after `next build`. The image must refuse to boot.
"""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

CLOUD = Path(__file__).resolve().parents[1]
REPO = CLOUD.parent
GUARD = REPO / "docker" / "app" / "next-clerk-guard.sh"
KEY = "pk_live_" + "fixture" * 4


def _run(env: dict[str, str], static: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(GUARD)],
        env={
            **os.environ,
            "NEXT_CLERK_GUARD_TEST": "1",
            "NEXT_CLERK_STATIC_DIR": str(static),
            **env,
        },
        capture_output=True,
        text=True,
        timeout=5,
    )


def test_guard_script_is_executable() -> None:
    assert GUARD.is_file()
    assert stat.S_IMODE(GUARD.stat().st_mode) & 0o111


def test_empty_key_is_refused(tmp_path: Path) -> None:
    static = tmp_path / "static"
    static.mkdir()
    (static / "chunk.js").write_text("no key here\n", encoding="utf-8")
    result = _run({"NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY": ""}, static)
    assert result.returncode == 78
    assert "publishableKey" in result.stderr or "CLERK" in result.stderr


def test_unbaked_bundle_is_refused(tmp_path: Path) -> None:
    static = tmp_path / "static"
    static.mkdir()
    (static / "chunk.js").write_text("pk_live_" + "other" * 4 + "\n", encoding="utf-8")
    result = _run({"NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY": KEY}, static)
    assert result.returncode == 78
    assert "bundle" in result.stderr.lower() or "static" in result.stderr.lower()


def test_baked_key_is_accepted(tmp_path: Path) -> None:
    static = tmp_path / "static"
    static.mkdir()
    (static / "chunk.js").write_text(f"clerk:{KEY}:end\n", encoding="utf-8")
    result = _run({"NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY": KEY}, static)
    assert result.returncode == 0, result.stderr
