"""The api test subtree must not write into the repo's real data/ directory.

T-275: `test_flow_report_capacity_shed.py` POSTs /flow-analysis/JOBY three
times without redirecting `server._FLOW_REPORTS_DIR`, so running the gate
left `?? data/flow_reports/JOBY.json` in the working tree. A dirty tree is
the precondition that aborted a weekend run at pytest COLLECTION (2026-08-16),
it trips deploy.sh's tracked-drift guard, and the stub entry is then served
for a live ticker by both the FastAPI GET handler and the Next.js route.

This file owns the CONTRACT rather than the individual case: it drives the
cache-writing route for real and asserts the repo `data/` tree is unchanged
byte for byte. Deleting `_isolate_flow_reports_dir` from conftest.py must
red it.
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

SCRIPTS_DIR = Path(__file__).resolve().parent.parent.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from api.subprocess import ScriptResult  # noqa: E402


@pytest.fixture(autouse=True)
def localhost_bypass(monkeypatch):
    from scripts.api import server, auth

    monkeypatch.setattr(auth, "is_trusted_local_request", lambda request: True)
    monkeypatch.setattr(server, "is_trusted_local_request", lambda request: True)
    monkeypatch.setattr(server, "test_mode", False)
    yield


def _snapshot(root: Path) -> dict[str, str]:
    """Relative path -> content digest for every file under root."""
    if not root.exists():
        return {}
    out: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            out[str(path.relative_to(root))] = hashlib.sha256(
                path.read_bytes()
            ).hexdigest()
    return out


def _cacheable_report(ticker: str) -> ScriptResult:
    """A report that passes `_flow_report_is_cacheable`, so the write fires."""
    return ScriptResult(
        ok=True,
        data={
            "ticker": ticker,
            "analysis": {"num_prints": 12},
            "dark_pool": {"daily": [{"date": "2026-08-26", "num_prints": 12}]},
        },
    )


def test_flow_analysis_post_writes_nothing_into_the_repo_data_dir():
    from scripts.api import server

    before = _snapshot(server.DATA_DIR)

    async def ok(script, args=None, timeout=30, **_kwargs):
        assert script == "flow_report.py"
        return _cacheable_report("JOBY")

    with patch("scripts.api.server.run_script", side_effect=ok):
        resp = TestClient(server.app).post("/flow-analysis/JOBY")

    assert resp.status_code == 200, resp.text

    # The route really did write — otherwise the assertion below is vacuous
    # and would keep passing if the cache write were removed entirely.
    assert (server._FLOW_REPORTS_DIR / "JOBY.json").is_file()
    assert server.DATA_DIR not in server._FLOW_REPORTS_DIR.parents

    after = _snapshot(server.DATA_DIR)
    assert after == before, (
        "the api test subtree wrote into the repo's real data/ directory: "
        f"{sorted(set(after) - set(before)) or 'existing files changed'}"
    )
