"""Shared pytest configuration and fixtures for scripts tests."""
import sys
from pathlib import Path

import pytest

# Add scripts/ and scripts/trade_blotter/ to sys.path so tests can import modules
SCRIPTS_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(SCRIPTS_DIR))
sys.path.insert(0, str(SCRIPTS_DIR / "trade_blotter"))


@pytest.fixture(autouse=True)
def _isolate_darkpool_cache(tmp_path, monkeypatch):
    """Point the persistent dark-pool cache at a per-test tmp dir.

    The cache is disk-backed and keyed by (ticker, date); without isolation a
    test that fetches a prior day writes to the real data/darkpool_cache/ and a
    later test reading the same (ticker, date) gets the cached trades instead of
    its own mock — and prod data gets polluted (cf. feedback_test_pollution_to_production).
    """
    try:
        import utils.darkpool_cache as _dpc
    except Exception:
        return
    monkeypatch.setattr(_dpc, "CACHE_DIR", tmp_path / "darkpool_cache")


@pytest.fixture(autouse=True)
def _stub_jvm_forensics_capture(monkeypatch):
    """Stub the DUR-08 forensic capture so watchdog tests that trip the
    api-hang path don't exec real docker commands or write to the real
    data/jvm_forensics/ (cf. feedback_test_pollution_to_production).

    The watchdog hook resolves ``jvm_forensics.capture_jvm_forensics`` by
    attribute lookup at call time, so this stub intercepts it. Tests in
    test_jvm_forensics.py that exercise the REAL capture are unaffected:
    they bind the function at module import time, before this patch.
    """
    try:
        import jvm_forensics
    except Exception:
        return
    monkeypatch.setattr(
        jvm_forensics,
        "capture_jvm_forensics",
        lambda **kwargs: jvm_forensics.CaptureResult(steps={"stubbed": "conftest"}),
    )
