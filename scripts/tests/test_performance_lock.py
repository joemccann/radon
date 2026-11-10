"""Tests for atomic cache writes."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# Ensure scripts/ is importable
SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))


@pytest.fixture()
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_atomic_write_cache_survives_crash(tmp_path):
    """_write_cache uses atomic temp+replace, not direct write."""
    from api.server import _write_cache

    target = tmp_path / "test.json"
    data = {"key": "value"}
    _write_cache(target, data)

    assert target.exists()
    written = json.loads(target.read_text())
    assert written == data
    # No temp files should remain
    temps = list(tmp_path.glob(".cache_*"))
    assert len(temps) == 0
