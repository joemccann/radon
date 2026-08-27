"""The edge must state its own behaviour rather than inherit defaults.

R-219: neither `reverse_proxy` block sets `dial_timeout`,
`response_header_timeout` or `read_timeout`, and the global options block has
no `servers { timeouts { ... } }`. Caddy's defaults for all three are 0
(unlimited). `lb_try_duration` only retries when the CONNECTION ATTEMPT fails;
it does nothing for an upstream that accepts the socket and never writes a
response header — the dominant `radon-api` failure mode in this repo (R-014's
data-role lock held by a wedged gateway, R-060's zombie pool client). In that
state the edge emits no 5xx at all, so the `@edge_health_status` mapping never
trips and `/edge-health/status` keeps answering 200: a total front-end hang is
indistinguishable at the edge from a healthy idle system.

R-220: the final `handle` block serves every browser request not matching
`/ws*`, `/api/ib/*` or `/edge-health/*` — including `POST /api/orders/place`,
`/cancel` and `/modify` — and enables a 15 s retry loop with no `retry_match`.
Whether Caddy replays a POST severed mid-flight depends on an unpinned
third-party default; relying on that default to protect order non-duplication
is itself the defect, since there is no idempotency key anywhere on this path
(R-009, R-022) and the operator's only evidence would be two fills.

R-258: `grace_period 10s` is shorter than the `lb_try_duration 15s` it has to
accommodate, so on `systemctl reload caddy` — which the deploy runs whenever
the Caddyfile changes, i.e. concurrently with the restarts the retry absorbs —
an in-flight retry loop is hard-cut before its window closes.

R-218: the `/api/ib/*` ride-out is 15 s but the caller the comment names,
`getWsTicket()`, aborts at 8 s. The client is gone seven seconds before the
retry loop would have reached the restarted upstream.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

# The shipped-config helpers live beside this file; pytest's rootdir is the
# repo, so the directory is not on sys.path by default.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_caddyfile import (  # noqa: E402
    free_port,
    read_caddyfile,
    retry_window_seconds,
    reverse_proxy_block,
    wait_for_listener,
)


REPO = Path(__file__).resolve().parents[2]
WS_TICKET = REPO / "web" / "lib" / "wsTicket.ts"

APP_UPSTREAM = "localhost:3000"
IB_UPSTREAM = "localhost:8321"


def _directive_seconds(block: str, name: str) -> float | None:
    match = re.search(rf"\b{name}\s+(\d+(?:\.\d+)?)(s|ms)\b", block)
    if not match:
        return None
    value = float(match.group(1))
    return value / 1000 if match.group(2) == "ms" else value


def _grace_period_seconds(content: str) -> float | None:
    return _directive_seconds(content, "grace_period")


class TestUpstreamHangIsBounded:
    """A hung upstream must produce a 5xx the health mapping can see."""

    @pytest.mark.parametrize("upstream", [APP_UPSTREAM, IB_UPSTREAM])
    @pytest.mark.parametrize(
        "directive", ["dial_timeout", "response_header_timeout", "read_timeout"]
    )
    def test_each_proxy_states_its_timeouts(self, caddy_dir, upstream, directive):
        block = reverse_proxy_block(read_caddyfile(caddy_dir), upstream)
        assert _directive_seconds(block, directive) is not None, (
            f"{upstream} inherits Caddy's unlimited {directive}; an upstream "
            "that accepts the socket and never answers hangs forever and the "
            "edge emits no 5xx at all"
        )

    @pytest.mark.parametrize("upstream", [APP_UPSTREAM, IB_UPSTREAM])
    def test_the_header_timeout_is_short_enough_to_surface(self, caddy_dir, upstream):
        block = reverse_proxy_block(read_caddyfile(caddy_dir), upstream)
        seconds = _directive_seconds(block, "response_header_timeout")
        assert seconds is not None and seconds <= 60, (
            f"{upstream} response_header_timeout {seconds}s is too long to "
            "turn a wedged upstream into an observable 5xx"
        )


class TestNonIdempotentRetryIsRestricted:
    def test_the_app_proxy_states_its_retry_restriction(self, caddy_dir):
        content = read_caddyfile(caddy_dir)
        block = reverse_proxy_block(content, APP_UPSTREAM)
        assert "retry_match" in block, (
            "POST /api/orders/place rides this block with a 15s retry loop and "
            "no stated restriction; order non-duplication must not rest on an "
            "unpinned upstream default"
        )

    def test_the_restriction_names_only_safe_methods(self, caddy_dir):
        block = reverse_proxy_block(read_caddyfile(caddy_dir), APP_UPSTREAM)
        match = re.search(r"retry_match\s*\{([^}]*)\}", block) or re.search(
            r"retry_match\s+(.+)", block
        )
        assert match, block
        body = match.group(1)
        assert "POST" not in body.upper(), f"retry_match admits POST: {body}"


class TestReloadCanAccommodateTheRetry:
    def test_grace_period_covers_the_longest_retry_window(self, caddy_dir):
        content = read_caddyfile(caddy_dir)
        grace = _grace_period_seconds(content)
        assert grace is not None
        windows = [
            retry_window_seconds(reverse_proxy_block(content, upstream))
            for upstream in (APP_UPSTREAM, IB_UPSTREAM)
        ]
        assert grace >= max(windows), (
            f"grace_period {grace}s hard-cuts an in-flight {max(windows)}s retry "
            "loop during the very event it exists to absorb"
        )


class TestRideOutMatchesItsNamedClient:
    def test_the_ib_window_is_not_longer_than_getwsticket_waits(self, caddy_dir):
        block = reverse_proxy_block(read_caddyfile(caddy_dir), IB_UPSTREAM)
        window = retry_window_seconds(block)
        source = WS_TICKET.read_text(encoding="utf-8")
        match = re.search(r"AbortSignal\.timeout\((\d[\d_]*)\)", source)
        assert match, "getWsTicket no longer states a client deadline"
        client_secs = int(match.group(1).replace("_", "")) / 1000
        assert window <= client_secs, (
            f"the edge rides out {window}s for a client that aborts at "
            f"{client_secs}s — the ticket fetch still fails, and the abandoned "
            "retry loop keeps dialling a connection nobody is reading"
        )


# ── Mechanism tests ──────────────────────────────────────────────────────
#
# The assertions above prove the config STATES the right behaviour. These drive
# a real caddy with the shipped proxy block, which is what the existing harness
# in test_caddyfile.py does for the restart-gap case — that one drives only a
# GET against a dead port, so neither the hung-upstream nor the non-idempotent
# case was covered in either direction (R-219, R-220).

import http.server
import os
import shutil
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request



CADDY_BIN = os.environ.get("RADON_CADDY_BIN", "caddy")


class _NeverAnswers(http.server.BaseHTTPRequestHandler):
    """Accepts the socket and never writes a response header.

    This is the dominant radon-api failure mode in this repo: R-014's
    data-role lock held by a wedged gateway, R-060's zombie pool client. It is
    exactly the case `lb_try_duration` cannot help with, because the connection
    attempt SUCCEEDS.
    """

    protocol_version = "HTTP/1.1"

    def do_GET(self):
        time.sleep(120)

    def do_POST(self):
        time.sleep(120)

    def log_message(self, *args):
        pass


class _CountsPosts(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    seen: list = []

    def do_POST(self):
        type(self).seen.append(self.path)
        # Sever the connection mid-flight without writing a response — what
        # boundedShutdown's exit(143) -> process.exit() does to every
        # still-draining connection during a deploy.
        self.close_connection = True
        self.wfile.close()

    def log_message(self, *args):
        pass


def _run_caddy(tmp_path, proxy_port, upstream_port, block):
    config = tmp_path / "Caddyfile"
    config.write_text(
        "{\n\tadmin off\n\tgrace_period 20s\n}\n\n"
        f"http://127.0.0.1:{proxy_port} {{\n"
        "\thandle {\n"
        f"\t\treverse_proxy 127.0.0.1:{upstream_port} {block}\n"
        "\t}\n}\n"
    )
    return subprocess.Popen(
        [CADDY_BIN, "run", "--config", str(config), "--adapter", "caddyfile"],
        env={
            **os.environ,
            "XDG_DATA_HOME": str(tmp_path / "data"),
            "XDG_CONFIG_HOME": str(tmp_path / "config"),
        },
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


@pytest.mark.skipif(
    shutil.which(CADDY_BIN) is None,
    reason="needs a caddy binary; set RADON_CADDY_BIN to run the edge mechanism tests",
)
class TestEdgeMechanism:
    def test_a_hung_upstream_becomes_a_5xx_within_a_bound(self, caddy_dir, tmp_path):
        block = reverse_proxy_block(read_caddyfile(caddy_dir), APP_UPSTREAM)
        header_timeout = _directive_seconds(block, "response_header_timeout")
        assert header_timeout is not None

        upstream_port, proxy_port = free_port(), free_port()
        server = http.server.ThreadingHTTPServer(("127.0.0.1", upstream_port), _NeverAnswers)
        threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05},
                         daemon=True).start()
        caddy = _run_caddy(tmp_path, proxy_port, upstream_port, block)
        try:
            assert wait_for_listener(proxy_port, time.monotonic() + 10)
            started = time.monotonic()
            try:
                urllib.request.urlopen(
                    f"http://127.0.0.1:{proxy_port}/admin",
                    timeout=header_timeout + 30,
                )
                pytest.fail("a hung upstream produced a normal response")
            except urllib.error.HTTPError as exc:
                status = exc.code
            waited = time.monotonic() - started
            assert 500 <= status < 600, (
                f"the edge answered {status}; @edge_health_status only maps "
                "502/503/504, so a total front-end hang stayed invisible"
            )
            assert waited < header_timeout + 20, f"took {waited:.1f}s"
        finally:
            server.shutdown()
            caddy.terminate()
            caddy.wait(timeout=10)

    def test_a_severed_post_is_not_replayed(self, caddy_dir, tmp_path):
        block = reverse_proxy_block(read_caddyfile(caddy_dir), APP_UPSTREAM)
        _CountsPosts.seen = []

        upstream_port, proxy_port = free_port(), free_port()
        server = http.server.ThreadingHTTPServer(("127.0.0.1", upstream_port), _CountsPosts)
        threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05},
                         daemon=True).start()
        caddy = _run_caddy(tmp_path, proxy_port, upstream_port, block)
        try:
            assert wait_for_listener(proxy_port, time.monotonic() + 10)
            request = urllib.request.Request(
                f"http://127.0.0.1:{proxy_port}/api/orders/place",
                data=b'{"symbol":"SPY"}',
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with pytest.raises(Exception):
                urllib.request.urlopen(request, timeout=40)
            assert len(_CountsPosts.seen) == 1, (
                f"the edge replayed a severed order POST {len(_CountsPosts.seen)}x; "
                "there is no idempotency key on this path, so the operator's "
                "only evidence would be two fills"
            )
        finally:
            server.shutdown()
            caddy.terminate()
            caddy.wait(timeout=10)
