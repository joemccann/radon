"""Production Flex Web Service call sites must be an explicit allowlist."""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCRIPTS = REPO / "scripts"

GDCDYN_ALLOW = {
    "cash_flow_sync.py",
    "perf_twr_builder.py",
    "trade_blotter/flex_query.py",
    "trade_blotter/blotter_service.py",
    "portfolio_performance.py",
}

FLEXREPORT_ALLOW = {
    "clients/ib_client.py",
}


def _py_files():
    for path in SCRIPTS.rglob("*.py"):
        rel = path.relative_to(SCRIPTS).as_posix()
        if "/tests/" in f"/{rel}" or rel.startswith("tests/"):
            continue
        yield rel, path


def test_gdcdyn_only_on_allowlisted_modules():
    hits = []
    for rel, path in _py_files():
        text = path.read_text(encoding="utf-8")
        if "gdcdyn" in text and rel not in GDCDYN_ALLOW:
            hits.append(rel)
    assert hits == [], hits


def test_flexreport_ctor_only_on_allowlisted_modules():
    hits = []
    for rel, path in _py_files():
        text = path.read_text(encoding="utf-8")
        if "FlexReport(" in text and rel not in FLEXREPORT_ALLOW:
            hits.append(rel)
    assert hits == [], hits


def test_fastapi_has_no_gdcdyn():
    text = (SCRIPTS / "api" / "server.py").read_text(encoding="utf-8")
    assert "gdcdyn" not in text
    assert "FlexReport(" not in text
