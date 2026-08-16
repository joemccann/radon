"""REL-036 / R-062: shared UW budget counting for Next.js-originated fetches.

Six web route handlers fetch UW directly and used to increment nothing —
browsing-driven UW traffic was invisible to /uw/usage and the universe-scan
brake, so the fleet could pass the real 40k cap while the counter read low.
POST /uw/usage/record is the counted path web/lib/uwCountedFetch.ts mirrors
its hits into (same flock-shared budget file UWClient writes).
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from utils.uw_budget import quota_date, record_hits, used  # noqa: E402

ET = ZoneInfo("America/New_York")
NOW = datetime(2026, 8, 14, 12, 0, tzinfo=ET)
AT_RESET = datetime(2026, 8, 14, 20, 0, tzinfo=ET)


def test_record_hits_increments_by_count_in_one_call(tmp_path: Path) -> None:
    path = tmp_path / "uw_budget.json"
    assert record_hits(3, path=path, now=NOW) == 3
    assert record_hits(2, path=path, now=NOW) == 5
    payload = json.loads(path.read_text())
    assert payload == {"date": quota_date(NOW), "count": 5}
    assert used(path=path, now=NOW) == 5


def test_record_hits_rolls_quota_day_at_2000_et(tmp_path: Path) -> None:
    path = tmp_path / "uw_budget.json"
    record_hits(7, path=path, now=NOW)
    assert record_hits(4, path=path, now=AT_RESET) == 4
    assert used(path=path, now=AT_RESET) == 4


@pytest.fixture
def app_client(monkeypatch):
    """Late-imported FastAPI TestClient via the trusted-local auth bypass."""
    from fastapi.testclient import TestClient
    from api import server  # noqa: WPS433 — import-after-path
    from api import auth

    monkeypatch.setattr(auth, "is_trusted_local_request", lambda request: True)
    monkeypatch.setattr(server, "is_trusted_local_request", lambda request: True)
    monkeypatch.setattr(server, "test_mode", False)

    return TestClient(server.app), server


def test_post_uw_usage_record_moves_usage_snapshot(app_client, tmp_path, monkeypatch) -> None:
    client, _server = app_client
    import utils.uw_budget as uw_budget

    monkeypatch.setattr(uw_budget, "BUDGET_PATH", tmp_path / "uw_budget.json")

    resp = client.post("/uw/usage/record", params={"count": 3})
    assert resp.status_code == 200
    assert resp.json()["used"] == 3

    snapshot = client.get("/uw/usage").json()
    assert snapshot["used"] == 3

    resp = client.post("/uw/usage/record")
    assert resp.status_code == 200
    assert resp.json()["used"] == 4


def test_post_uw_usage_record_rejects_out_of_range_count(app_client, tmp_path, monkeypatch) -> None:
    client, _server = app_client
    import utils.uw_budget as uw_budget

    monkeypatch.setattr(uw_budget, "BUDGET_PATH", tmp_path / "uw_budget.json")

    assert client.post("/uw/usage/record", params={"count": 0}).status_code == 400
    assert client.post("/uw/usage/record", params={"count": 501}).status_code == 400
    assert used(path=tmp_path / "uw_budget.json", now=None) == 0
