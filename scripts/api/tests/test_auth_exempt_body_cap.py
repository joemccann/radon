"""Auth-exempt POST handlers must bound the request body BEFORE parsing.

/ws-ticket/validate and /demo/trial-expiry are reachable without a Clerk JWT
and call request.json(); without a size cap an anonymous caller can buffer an
arbitrarily large body into the trading API process. The handlers must reject
oversized (or length-less) bodies without reading them.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

# Must be set before importing server (test_mode is read at import time).
os.environ.setdefault("RADON_API_TEST_MODE", "1")

SCRIPTS_DIR = Path(__file__).resolve().parent.parent.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))


@pytest.fixture()
def client():
    from scripts.api import server

    return TestClient(server.app)


@pytest.mark.parametrize("path", ["/ws-ticket/validate", "/demo/trial-expiry"])
def test_oversized_body_rejected_413_without_auth(client, path):
    from scripts.api import server

    oversized = b"{" + b" " * (server.AUTH_EXEMPT_BODY_MAX_BYTES + 1) + b"}"
    resp = client.post(
        path, content=oversized, headers={"Content-Type": "application/json"}
    )
    assert resp.status_code == 413, resp.text


@pytest.mark.parametrize("path", ["/ws-ticket/validate", "/demo/trial-expiry"])
def test_missing_content_length_rejected_without_reading_body(client, path):
    def gen():
        yield b'{"ticket": "x"}'

    # A chunked body has no Content-Length, so the size is unknowable before
    # reading — the cap must fail closed instead of buffering.
    resp = client.post(
        path, content=gen(), headers={"Content-Type": "application/json"}
    )
    assert resp.status_code == 411, resp.text


def test_normal_body_still_works(client):
    resp = client.post(
        "/demo/trial-expiry",
        json={"start_iso_et": "2026-06-24T09:30:00-04:00"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["expires_at"].startswith("2026-06-25T16:00:00")


def test_normal_ticket_validation_still_reaches_handler(client):
    resp = client.post("/ws-ticket/validate", json={"ticket": "bogus"})
    # Body was parsed and the bogus ticket rejected by the handler itself.
    assert resp.status_code == 401, resp.text
