"""P2: page-driven Flex POSTs are 404. GET blotter still reads journal."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))


@pytest.fixture
def client(monkeypatch):
    from scripts.api import server
    from scripts.api import auth

    monkeypatch.setattr(auth, "is_trusted_local_request", lambda request: True)
    monkeypatch.setattr(server, "is_trusted_local_request", lambda request: True)
    return TestClient(server.app)


def test_post_performance_is_404(client):
    res = client.post("/performance")
    assert res.status_code == 404



@pytest.mark.parametrize("path", ["/journal/rehydrate", "/blotter", "/performance/background"])
def test_removed_flex_post_stays_unroutable(client, path):
    res = client.post(path)
    assert res.status_code in (404, 405)
