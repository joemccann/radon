from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def _source(name: str) -> str:
    return (ROOT / "scripts" / name).read_text()


def test_failed_garch_fallback_preserves_child_status():
    source = _source("run_garch_refresh.sh")
    assert "else" in source[source.index("garch_convergence.py") :]
    assert "EXIT_CODE=$?" in source[source.index("garch_convergence.py") :]


def test_failed_leap_fallback_preserves_child_status():
    source = _source("run_leap_refresh.sh")
    assert "else" in source[source.index("leap_scanner_uw.py") :]
    assert "EXIT_CODE=$?" in source[source.index("leap_scanner_uw.py") :]


def test_failed_oi_refresh_preserves_child_status():
    source = _source("run_oi_changes_refresh.sh")
    assert "else" in source[source.index("fetch_oi_changes.py") :]
    assert "EXIT_CODE=$?" in source[source.index("fetch_oi_changes.py") :]
