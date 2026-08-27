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


def test_journal_rehydrate_is_404(client):
    res = client.post("/journal/rehydrate")
    assert res.status_code == 404
    assert "file-ingest" in res.json()["detail"].lower() or "file-ingest" in str(res.json()).lower()


def test_post_blotter_is_404(client):
    res = client.post("/blotter")
    assert res.status_code == 404


def test_post_performance_is_404(client):
    res = client.post("/performance")
    assert res.status_code == 404


def test_post_performance_background_is_404(client):
    res = client.post("/performance/background")
    assert res.status_code == 404
