"""Shared-tmp scenario JSON must be owned by the reader, and report fields escaped."""
from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_SCRIPT = _REPO / "scripts" / "scenario_report.py"


def _load_scenario_json():
    """Call the loader without executing the report script."""
    tree = ast.parse(_SCRIPT.read_text(encoding="utf-8"))
    keep = []
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom, ast.FunctionDef)):
            if isinstance(node, ast.FunctionDef) and node.name != "load_scenario_json":
                continue
            keep.append(node)
    module = ast.Module(body=keep, type_ignores=[])
    ast.fix_missing_locations(module)
    namespace: dict = {}
    exec(compile(module, str(_SCRIPT), "exec"), namespace)
    return namespace["load_scenario_json"]


def test_symlink_is_refused(tmp_path: Path) -> None:
    target = tmp_path / "real.json"
    target.write_text("{}", encoding="utf-8")
    link = tmp_path / "link.json"
    link.symlink_to(target)
    with pytest.raises(SystemExit, match="refusing scenario json"):
        _load_scenario_json()(str(link))


def test_world_writable_file_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "open.json"
    path.write_text("{}", encoding="utf-8")
    path.chmod(0o666)
    with pytest.raises(SystemExit, match="untrusted"):
        _load_scenario_json()(str(path))


def test_group_writable_owned_file_loads(tmp_path: Path) -> None:
    path = tmp_path / "group.json"
    path.write_text('{"ok": 1}', encoding="utf-8")
    path.chmod(0o664)
    assert _load_scenario_json()(str(path)) == {"ok": 1}


def test_foreign_owner_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "owned.json"
    path.write_text("{}", encoding="utf-8")
    path.chmod(0o644)
    real = os.fstat

    def foreign(fd: int):
        st = real(fd)
        return os.stat_result(
            (
                st.st_mode,
                st.st_ino,
                st.st_dev,
                st.st_nlink,
                st.st_uid + 1,
                st.st_gid,
                st.st_size,
                st.st_atime,
                st.st_mtime,
                st.st_ctime,
            )
        )

    monkeypatch.setattr(os, "fstat", foreign)
    with pytest.raises(SystemExit, match="untrusted"):
        _load_scenario_json()(str(path))


def _row(ticker: str, structure: str, pnl: float) -> dict:
    return {
        "ticker": ticker,
        "structure_type": structure,
        "pnl": pnl,
        "pnl_pct": 1.0,
        "risk_profile": "equity",
        "dte": 30,
        "entry_cost": 1000.0,
        "stock_move_pct": -1.0,
        "scenario_price": 10.0,
        "beta_component": -1.0,
        "oil_component": 0.0,
        "vix_stress": 0.0,
    }


def test_report_escapes_ticker_and_structure(tmp_path: Path) -> None:
    payload_ticker = "</td><script>alert(1)</script>"
    payload_structure = '"><img src=x onerror=alert(1)>'
    base = [
        _row(payload_ticker, payload_structure, -50.0),
        _row("SPXU", "shares", 10.0),
        _row("BKD", "put spread", 5.0),
    ]
    scenario = {
        "results": {"base": base, "bear": base, "bull": base},
        "totals": {
            name: {
                "bankroll": 1000.0,
                "total_pnl": -35.0,
                "total_pnl_pct": -3.5,
                "net_liq_after": 965.0,
            }
            for name in ("bear", "base", "bull")
        },
        "parameters": {},
        "current_prices": {},
        "betas": {},
        "oil_sensitivity": {},
        "vix_multipliers": {},
    }
    source = tmp_path / "scenario.json"
    source.write_text(json.dumps(scenario), encoding="utf-8")
    source.chmod(0o644)
    template = tmp_path / "template.html"
    template.write_text(
        "<html><head><style></style></head><body>{{TITLE}}{{BODY}}</body></html>",
        encoding="utf-8",
    )
    output = tmp_path / "out.html"
    proc = subprocess.run(
        [sys.executable, str(_SCRIPT)],
        cwd=_REPO,
        env={
            **os.environ,
            "RADON_SCENARIO_JSON": str(source),
            "RADON_SCENARIO_TEMPLATE": str(template),
            "RADON_SCENARIO_HTML": str(output),
        },
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    html = output.read_text(encoding="utf-8")
    assert "<script>alert(1)</script>" not in html
    assert "<img" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "&lt;img" in html
    assert "SPXU" in html
