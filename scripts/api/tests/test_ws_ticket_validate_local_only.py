"""POST /ws-ticket/validate serves the loopback relay only.

The route is exempt from the Clerk JWT middleware because its sole legitimate
caller is the Node WebSocket relay on the same host, which holds no user JWT.
Caddy proxies the whole FastAPI surface under /api/ib/*, so "auth-exempt" also
means "reachable from the internet"; the handler itself must therefore apply
the same server-to-server discipline as the rest of the trusted-local bypass:
loopback peer, no forwarding headers, no browser fetch metadata. A denied call
must not consume the ticket, so a stray request cannot burn the operator's
pending WebSocket upgrade.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

SCRIPTS_DIR = Path(__file__).resolve().parent.parent.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))


def _client(peer: str = "127.0.0.1") -> TestClient:
    from scripts.api import server

    return TestClient(server.app, client=(peer, 40000))


def _mint() -> str:
    from api.ws_ticket import create_ticket

    return create_ticket("ws-validate-probe")


def _still_valid(ticket: str) -> bool:
    from api.ws_ticket import validate_ticket

    return validate_ticket(ticket) == "ws-validate-probe"


def test_loopback_relay_call_validates_and_consumes_ticket():
    ticket = _mint()
    resp = _client().post("/ws-ticket/validate", json={"ticket": ticket})
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"user_id": "ws-validate-probe"}
    assert not _still_valid(ticket), "a validated ticket must be single-use"


@pytest.mark.parametrize(
    "headers",
    [
        {"X-Forwarded-For": "203.0.113.9"},
        {"Forwarded": "for=203.0.113.9"},
        {"X-Real-IP": "203.0.113.9"},
        {"X-Forwarded-Host": "app.example.test"},
    ],
    ids=["x-forwarded-for", "forwarded", "x-real-ip", "x-forwarded-host"],
)
def test_proxied_caller_is_refused_and_ticket_survives(headers):
    ticket = _mint()
    resp = _client().post("/ws-ticket/validate", json={"ticket": ticket}, headers=headers)
    assert resp.status_code == 401, resp.text
    assert _still_valid(ticket), "a refused call must not consume the ticket"


def test_browser_page_on_loopback_is_refused():
    ticket = _mint()
    resp = _client().post(
        "/ws-ticket/validate",
        json={"ticket": ticket},
        headers={"Sec-Fetch-Site": "cross-site", "Origin": "https://evil.example"},
    )
    assert resp.status_code == 401, resp.text
    assert _still_valid(ticket)


def test_non_local_peer_is_refused():
    ticket = _mint()
    resp = _client(peer="198.51.100.7").post("/ws-ticket/validate", json={"ticket": ticket})
    assert resp.status_code == 401, resp.text
    assert _still_valid(ticket)


def test_loopback_bogus_ticket_still_401():
    resp = _client().post("/ws-ticket/validate", json={"ticket": "bogus"})
    assert resp.status_code == 401, resp.text
