"""Every pooled hop must have a server idle timeout above its client's.

A client that reuses a keep-alive socket the server already closed gets a
reset before any response, and the route turns it into a 502. Two hops had
the ordering backwards:

* Next -> FastAPI: Node's fetch (undici) pools sockets for 4s; uvicorn closes
  idle ones at its 5s default. One second of margin, and undici's idle timer
  is a JS timer that slips whenever the Next event loop is busy. Reproduced
  locally: 3.8s idle + 1.5s loop block gave 6/6 ECONNRESET; with uvicorn at
  75s, 0/24.
* Caddy -> Next: Caddy's reverse_proxy keeps idle upstream connections for
  2m by default; `next start` closes them at Node's 5s default. Go replays
  only idempotent requests on a dead pooled connection, so a POST
  (/api/portfolio, /api/orders/*) can surface as a raw edge 502.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

UNDICI_KEEP_ALIVE_S = 4
CADDY_DEFAULT_KEEPALIVE_S = 120

UVICORN_LAUNCHERS = (
    REPO / "cloud/services/radon-api.service",
    REPO / "cloud/scripts/radon-app-runtime.sh",
    REPO / "docker/app/Dockerfile.python",
)


def _uvicorn_keep_alive_s(text: str) -> int | None:
    match = re.search(r'--timeout-keep-alive"?,?\s*"?(\d+)', text)
    return int(match.group(1)) if match else None


def test_every_uvicorn_launch_outlasts_the_node_fetch_pool():
    for launcher in UVICORN_LAUNCHERS:
        text = launcher.read_text()
        assert "uvicorn" in text, launcher
        keep_alive = _uvicorn_keep_alive_s(text)
        assert keep_alive is not None, f"{launcher.name}: uvicorn launched without --timeout-keep-alive"
        assert keep_alive >= 15 * UNDICI_KEEP_ALIVE_S, f"{launcher.name}: {keep_alive}s"


def test_next_start_outlasts_the_caddy_upstream_pool():
    caddyfile = (REPO / "cloud/caddy/Caddyfile").read_text()
    assert not re.search(r"^\s*keepalive\s", caddyfile, re.MULTILINE), (
        "Caddyfile now sets keepalive; derive the Next bound from it"
    )
    start = json.loads((REPO / "web/package.json").read_text())["scripts"]["start"]
    match = re.search(r"--keepAliveTimeout\s+(\d+)", start)
    assert match, f"next start without --keepAliveTimeout: {start!r}"
    assert int(match.group(1)) > CADDY_DEFAULT_KEEPALIVE_S * 1_000
