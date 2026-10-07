"""Shared pytest configuration and fixtures for scripts tests."""
import json
import os
import sys
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _stub_bankroll_snapshot(monkeypatch):
    """NF-1: the placement funnel's Gate 3 check reads Turso.

    Order-path tests exercise their own concern, so the gate admits by
    default. test_bankroll_admission.py restores the real check.
    """
    try:
        import bankroll_guard
    except Exception:
        yield
        return
    monkeypatch.setattr(bankroll_guard, "check_bankroll_admission", lambda *a, **k: None)
    yield


@pytest.fixture(autouse=True)
def _isolate_model_ladder_auth_files(tmp_path, monkeypatch):
    """Credential discovery may read only this test's explicit auth fixtures."""
    from clients import model_ladder

    for name in ("_json_load_object", "_read_secret_file"):
        original = getattr(model_ladder, name)
        empty = None if name == "_json_load_object" else ""

        def isolated(path, _read=original, _empty=empty):
            if not Path(path).resolve().is_relative_to(tmp_path.resolve()):
                return _empty
            return _read(path)

        monkeypatch.setattr(model_ladder, name, isolated)

# Captured at import, before any test can monkeypatch HOME or Path.home.
_REAL_HOME = Path(os.path.expanduser("~"))
_REAL_AGENT_BINS = tuple(
    _REAL_HOME / rel for rel in (".grok/bin/grok", ".local/bin/grok")
)


def _link_state(path: Path):
    try:
        st = path.lstat()
    except FileNotFoundError:
        return None
    target = os.readlink(path) if path.is_symlink() else None
    return (st.st_mode, st.st_ino, st.st_mtime_ns, target)


@pytest.fixture(autouse=True)
def _real_agent_cli_is_untouched():
    """No test may relink or rewrite the host's real agent CLI entries.

    2026-09-29: a scripts test run inside the VPS responder clone repointed
    the operator's ~/.grok/bin/grok at a pytest tmp candidate, and the
    responder and upgrade units then failed on a dangling binary. Tests must
    fake HOME and pass explicit paths; this guard fails the test that did it.
    """
    before = [_link_state(p) for p in _REAL_AGENT_BINS]
    yield
    after = [_link_state(p) for p in _REAL_AGENT_BINS]
    for path, old, new in zip(_REAL_AGENT_BINS, before, after):
        assert old == new, f"test modified the real {path}: {old} -> {new}"


# Add scripts/ and scripts/trade_blotter/ to sys.path so tests can import modules
SCRIPTS_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(SCRIPTS_DIR))
sys.path.insert(0, str(SCRIPTS_DIR / "trade_blotter"))
# scripts/tests itself, so test modules can import shared non-test helpers
# instead of exec_module-ing sibling test files (T-481).
sys.path.insert(0, str(SCRIPTS_DIR / "tests"))

# Captured before any test can patch it, so `real_orphan_confirm_interval`
# hands back the production value rather than the zeroed one.
try:
    from utils import ib_2fa_lock as _ib_2fa_lock
except Exception:  # pragma: no cover - module is stdlib-only, import can't fail
    _ib_2fa_lock = None
REAL_ORPHAN_CONFIRM_INTERVAL_SECS = (
    _ib_2fa_lock.ORPHAN_CONFIRM_INTERVAL_SECS if _ib_2fa_lock is not None else 0.0
)


@pytest.fixture(autouse=True)
def _isolate_ib_2fa_lock_orphan_state(monkeypatch):
    """Reset the process-wide orphan-confirmation memory around every test.

    R-210 put two mutable module globals (`_orphan_reported`, `_orphan_seen_up`)
    and a real inter-probe `time.sleep` in `ib_2fa_lock`. Eleven test files
    import the module; only two reset the globals, so inside one xdist worker a
    revocation outcome depended on file order and every unguarded revocation
    paid the real sleep. Owned here so every importer gets it. Mirrored in
    scripts/api/tests/conftest.py, which is a separate rootdir subtree. T-226.
    """
    if _ib_2fa_lock is None:
        yield
        return
    _ib_2fa_lock.reset_orphan_state()
    monkeypatch.setattr(_ib_2fa_lock, "ORPHAN_CONFIRM_INTERVAL_SECS", 0.0)
    yield
    _ib_2fa_lock.reset_orphan_state()


@pytest.fixture
def real_orphan_confirm_interval(monkeypatch):
    """Restore the production inter-probe interval for tests that measure cost."""
    monkeypatch.setattr(
        _ib_2fa_lock,
        "ORPHAN_CONFIRM_INTERVAL_SECS",
        REAL_ORPHAN_CONFIRM_INTERVAL_SECS,
    )
    return REAL_ORPHAN_CONFIRM_INTERVAL_SECS


@pytest.fixture(autouse=True)
def _reset_menthorq_auth_embargo():
    """Auth-failure embargo is process-wide; do not leak across tests."""
    try:
        from clients.menthorq_dashboard_client import _reset_auth_embargo_for_tests
    except Exception:
        yield
        return
    _reset_auth_embargo_for_tests()
    yield
    _reset_auth_embargo_for_tests()


@pytest.fixture(autouse=True)
def _isolate_uw_budget(tmp_path, monkeypatch):
    """Point the process-wide UW budget file at a per-test tmp path."""
    try:
        import utils.uw_budget as _uwb
    except Exception:
        return
    monkeypatch.setattr(_uwb, "BUDGET_PATH", tmp_path / "uw_budget.json")


@pytest.fixture(autouse=True)
def _isolate_uw_http_cache(tmp_path, monkeypatch):
    """Point the UW HTTP disk cache at a per-test tmp dir."""
    try:
        import utils.uw_cache as _uwc
    except Exception:
        return
    if not hasattr(_uwc, "CACHE_DIR"):
        return
    monkeypatch.setattr(_uwc, "CACHE_DIR", tmp_path / "uw_http_cache")


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


@pytest.fixture(autouse=True)
def _neutralize_quiet_windows(monkeypatch):
    """The DUR-10 scheduled-restart quiet windows default to wall-clock UTC
    ranges (23:40-00:15, 09:00-09:30), which made watchdog ladder tests fail
    when the suite happened to run inside a window. Tests are deterministic
    by default; quiet-window behavior tests set the env explicitly.
    """
    monkeypatch.setenv("RADON_GW_RESTART_QUIET_WINDOWS_UTC", "")


@pytest.fixture
def index_preset_dir(tmp_path, monkeypatch):
    """Provide deterministic file-backed index presets in clean checkouts.

    Production preset files are runtime-owned and intentionally gitignored.
    Unit tests therefore build equivalent master-file shapes instead of
    depending on a developer or VPS data directory.
    """
    import utils.presets as presets

    specs = {
        "ndx100": ("Nasdaq-100", "N", 100, 40),
        "sp500": ("S&P 500", "S", 500, 250),
        "r2k": ("Russell 2000", "R", 2000, 1000),
    }
    for slug, (description, prefix, ticker_count, pair_count) in specs.items():
        tickers = [f"{prefix}{i:04d}" for i in range(ticker_count)]
        if slug == "ndx100":
            tickers[:2] = ["NVDA", "AAPL"]
        pairs = [
            [tickers[0], tickers[i + 1]]
            for i in range(pair_count)
        ]
        (tmp_path / f"{slug}.json").write_text(
            json.dumps(
                {
                    "name": slug,
                    "description": description,
                    "tickers": tickers,
                    "pairs": pairs,
                    "vol_driver": "GICS/sector-curated index pairs",
                }
            )
        )

    monkeypatch.setattr(presets, "PRESETS_DIR", tmp_path)
    return tmp_path


# The T-317 Turso strip (`_strip_turso_credentials`) was hoisted to
# scripts/conftest.py so scripts/api/tests gets it too. T-368.


@pytest.fixture(autouse=True)
def _reset_flex_cash_flow_error_latch():
    """REL-210's duplicate-ok suppression latch is process-lifetime by design
    (one sftp batch = one process); tests share a process, so reset it."""
    module = sys.modules.get("flex_delivery_ingest")
    if module is not None and hasattr(module, "_CASH_FLOW_ERROR_LATCHED"):
        module._CASH_FLOW_ERROR_LATCHED = False
    yield


@pytest.fixture
def isolated_model_credentials(tmp_path, monkeypatch):
    """T-498: subscription discovery must never read the operator's home.

    Keep the real file readers and provider selection: subscription tests
    opt in by writing their own files and passing HOME/CODEX_HOME explicitly.
    Both defaults matter: env={} falls back to Path.home(), while Reviewer
    copies os.environ. Environment-only isolation misses the former.
    """
    home = tmp_path / "model-home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setenv("HOME", str(home))
    for key in (
        "CODEX_HOME", "CLAUDE_CONFIG_DIR", "CLAUDE_CODE_OAUTH_TOKEN_FILE",
        "CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY", "CLAUDE_CODE_API_KEY",
        "CLAUDE_API_KEY", "XAI_API_KEY", "GROK_API_KEY", "OPENAI_API_KEY",
        "ANTIGRAVITY_CLI", "ANTIGRAVITY_MODEL", "NVIDIA_API_KEY",
        "CEREBRAS_API_KEY", "RADON_LADDER_ALLOW_PREPAID",
    ):
        monkeypatch.delenv(key, raising=False)
    return home


@pytest.fixture(autouse=True)
def _isolate_order_rejection_digest(tmp_path, monkeypatch):
    """R-025 / REL-310: API rejection tests cannot write the runtime digest."""
    from watchdog import notify

    monkeypatch.setattr(notify, "DIGEST_STATE_PATH", tmp_path / "rejection-digest.json")


@pytest.fixture(autouse=True)
def _isolate_health_dependency_probe(monkeypatch):
    """R-028 / REL-316: endpoint tests never query the runtime health DB."""
    async def unknown():
        return {"database": "unknown", "market_data": "unknown"}
    for name in ("api.server", "scripts.api.server"):
        module = sys.modules.get(name)
        if module is not None:
            monkeypatch.setattr(module, "health_dependencies", unknown)
