"""Tests for caddy/Caddyfile configuration."""

import contextlib
import errno
import http.server
import os
import re
import shutil
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request

import pytest


def read_caddyfile(caddy_dir):
    return (caddy_dir / "Caddyfile").read_text()


class TestCaddyfileExists:
    def test_file_exists_and_nonempty(self, caddy_dir):
        caddyfile = caddy_dir / "Caddyfile"
        assert caddyfile.exists(), "Caddyfile must exist"
        assert caddyfile.stat().st_size > 0, "Caddyfile must not be empty"


class TestSiteAddress:
    def test_uses_domain_env_var_with_default(self, caddy_dir):
        content = read_caddyfile(caddy_dir)
        assert "{$DOMAIN:app.radon.run}" in content


class TestCompression:
    def test_has_encode_directive(self, caddy_dir):
        content = read_caddyfile(caddy_dir)
        assert re.search(r"^\s*encode\b", content, re.MULTILINE)


class TestSecurityHeaders:
    def test_x_content_type_options(self, caddy_dir):
        content = read_caddyfile(caddy_dir)
        assert "X-Content-Type-Options" in content

    def test_x_frame_options(self, caddy_dir):
        content = read_caddyfile(caddy_dir)
        assert "X-Frame-Options" in content

    def test_referrer_policy(self, caddy_dir):
        content = read_caddyfile(caddy_dir)
        assert "Referrer-Policy" in content

    def test_removes_server_header(self, caddy_dir):
        content = read_caddyfile(caddy_dir)
        assert "-Server" in content


class TestLogging:
    def test_has_log_block(self, caddy_dir):
        content = read_caddyfile(caddy_dir)
        assert re.search(r"^\s*log\s*\{", content, re.MULTILINE)

    def test_json_format(self, caddy_dir):
        content = read_caddyfile(caddy_dir)
        assert re.search(r"^\s*format\s+json\b", content, re.MULTILINE)


class TestRouting:
    def test_websocket_route_to_8765(self, caddy_dir):
        content = read_caddyfile(caddy_dir)
        assert re.search(r"handle\s+/ws\*", content)
        assert "127.0.0.1:8765" in content

    def test_api_ib_route_to_8321(self, caddy_dir):
        content = read_caddyfile(caddy_dir)
        assert re.search(r"handle_path\s+/api/ib/\*", content)
        assert "127.0.0.1:8321" in content

    def test_default_route_to_3000(self, caddy_dir):
        content = read_caddyfile(caddy_dir)
        assert re.search(r"handle\s*\{", content, re.MULTILINE)
        assert "127.0.0.1:3000" in content

    def test_handle_path_used_for_api_ib(self, caddy_dir):
        content = read_caddyfile(caddy_dir)
        match = re.search(r"(handle_path|handle)\s+/api/ib/\*", content)
        assert match is not None, "/api/ib/* route must exist"
        assert match.group(1) == "handle_path", (
            "/api/ib/* must use handle_path (not handle) for prefix stripping"
        )

    def test_reverse_proxy_does_not_dial_localhost_hostname(self, caddy_dir):
        # `localhost` prefers ::1. Relay binds 127.0.0.1:8765 only, so
        # reverse_proxy localhost:8765 502s with connection refused. Same
        # trap hit [::1]:3000 during nextjs restarts (2026-08-25).
        content = read_caddyfile(caddy_dir)
        stripped = re.sub(r"(?m)^\s*#.*$", "", content)
        assert not re.search(r"reverse_proxy\s+localhost:", stripped), (
            "reverse_proxy must dial 127.0.0.1, not localhost"
        )

    def test_edge_health_status_maps_dial_errors_to_200(self, caddy_dir):
        content = read_caddyfile(caddy_dir)
        block = reverse_proxy_block(content, "127.0.0.1:8330")
        assert "handle_response" in block
        assert "handle_errors" in content
        assert "@edge_health_err" in content
        ws_block = content.split("handle /ws*", 1)[1].split("handle_path", 1)[0]
        assert "handle_errors" not in ws_block
        assert '{"reachable":false,"observer":"caddy"}' in content


class TestRetiredBetaStack:
    def test_no_beta_hostname_or_ports(self, caddy_dir):
        content = read_caddyfile(caddy_dir)
        assert "beta.radon.run" not in content
        assert "localhost:3001" not in content
        assert "127.0.0.1:8322" not in content
        assert "127.0.0.1:8331" not in content

    def test_websocket_route_before_api_route(self, caddy_dir):
        content = read_caddyfile(caddy_dir)
        ws_pos = content.find("handle /ws")
        api_pos = content.find("handle_path /api/ib")
        assert ws_pos < api_pos, "WebSocket route must come before API route"


class TestTLS:
    def test_no_explicit_tls_config(self, caddy_dir):
        content = read_caddyfile(caddy_dir)
        assert not re.search(r"^\s*tls\b", content, re.MULTILINE), (
            "No explicit TLS config needed; Caddy auto-provisions certificates"
        )


class TestReverseProxyTargets:
    def test_all_reverse_proxy_targets_use_loopback(self, caddy_dir):
        content = read_caddyfile(caddy_dir)
        active_config = "\n".join(
            line for line in content.splitlines()
            if not line.lstrip().startswith("#")
        )
        targets = re.findall(r"reverse_proxy\s+(\S+)", active_config)
        assert len(targets) > 0, "Must have at least one reverse_proxy directive"
        for target in targets:
            assert target.startswith(("localhost:", "127.0.0.1:")), (
                f"reverse_proxy target '{target}' must use loopback, not a public bind"
            )


class TestReloadCompletes:
    """A reload that never finishes wedges the edge's control plane.

    Caddy's default grace period is infinite: on reload it keeps the OLD
    servers alive until every connection closes. This Caddyfile proxies the
    WebSocket relay (`handle /ws*`), so one open browser socket is enough to
    keep them alive forever. Under Type=notify the unit therefore never
    re-signals READY, systemd sits in `reloading`, and the admin endpoint on
    :2019 times out shutting down -- observed 2026-08-11 while publishing edge
    config, which then looked like a failed reload and triggered a rollback.
    """

    def test_global_options_block_is_first(self, caddy_dir):
        content = read_caddyfile(caddy_dir)
        active = [
            line for line in content.splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]
        assert active and active[0].strip() == "{", (
            "the global options block must be the first block in a Caddyfile; "
            f"found: {active[0] if active else '<empty>'}"
        )

    def test_grace_period_is_bounded(self, caddy_dir):
        content = read_caddyfile(caddy_dir)
        match = re.search(r"^\s*grace_period\s+(\d+)s\s*$", content, re.MULTILINE)
        assert match, (
            "no bounded grace_period: reload will hang forever while any "
            "long-lived connection (the /ws* relay proxy) stays open"
        )
        assert 1 <= int(match.group(1)) <= 30, (
            f"grace_period {match.group(1)}s is outside the bound the publish "
            "action's reload timeout is sized for"
        )


# The bounded Next.js SIGTERM drain (web/lib/boundedShutdown.ts
# SHUTDOWN_GRACE_MS) caps how long radon-nextjs may take to go away, and
# `next start` is listening ~1s later. A retry window has to outlast the whole
# gap or a deploy restart still reaches the browser as a 502.
SHUTDOWN_GRACE_SECONDS = 10
MIN_RETRY_WINDOW_SECONDS = SHUTDOWN_GRACE_SECONDS + 2


# Caddy's default when lb_try_interval is omitted.
CADDY_DEFAULT_TRY_INTERVAL_SECONDS = 0.25
# A retry loop that re-dials less often than this turns a restart gap into a
# visibly hung request instead of an invisible one, and only makes a handful of
# attempts across the window.
MAX_RETRY_INTERVAL_SECONDS = 1.0


def strip_comments(content):
    """Drop Caddyfile `#` comments, leaving quoted and backticked literals alone.

    The Caddyfile carries a WARNING COMMENT naming `fail_duration`, so any
    assertion about proxy settings has to read the active config only.
    """
    kept = []
    for line in content.splitlines():
        quote = None
        cut = None
        for index, char in enumerate(line):
            if quote is not None:
                if char == quote:
                    quote = None
            elif char in "\"`":
                quote = char
            elif char == "#" and (index == 0 or line[index - 1].isspace()):
                cut = index
                break
        kept.append(line if cut is None else line[:cut])
    return "\n".join(kept)


def reverse_proxy_block(content, upstream):
    """Return the body of the `reverse_proxy <upstream>` block, '' if inline.

    Brace-balanced (the health block nests `handle_response`) and
    quote-aware (it responds with a JSON literal full of braces), so a setting
    can only satisfy or trip an assertion from inside the block it belongs to.
    """
    active = strip_comments(content)
    directive = re.search(r"reverse_proxy\s+" + re.escape(upstream) + r"\b", active)
    assert directive, f"no reverse_proxy directive for {upstream}"
    rest = active[directive.end():]
    opener = re.match(r"[^\S\n]*\{", rest)
    if not opener:
        return ""
    start = opener.end() - 1
    depth = 0
    quote = None
    for index in range(start, len(rest)):
        char = rest[index]
        if quote is not None:
            if char == quote:
                quote = None
        elif char in "\"`":
            quote = char
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return rest[start:index + 1]
    raise AssertionError(f"unbalanced braces in the reverse_proxy {upstream} block")


def retry_window_seconds(block):
    match = re.search(r"lb_try_duration\s+(\d+)s", block)
    return int(match.group(1)) if match else 0


def retry_interval_seconds(block):
    match = re.search(r"lb_try_interval\s+(\d+(?:\.\d+)?)(ms|s)\b", block)
    if not match:
        return CADDY_DEFAULT_TRY_INTERVAL_SECONDS
    value = float(match.group(1))
    return value / 1000 if match.group(2) == "ms" else value


class TestUpstreamRestartWindow:
    """A deploy restart of an upstream must not surface as a 502.

    2026-08-25: during the #89 promote, Caddy logged `dial tcp [::1]:3000:
    connect: connection refused` for ~7s and every request in that window --
    /admin included -- got a raw 502. `reverse_proxy` defaults to zero retries,
    so the edge forwards the restart gap straight to the browser instead of
    riding over it.
    """

    def test_app_proxy_retries_across_the_restart_gap(self, caddy_dir):
        block = reverse_proxy_block(read_caddyfile(caddy_dir), "127.0.0.1:3000")
        assert retry_window_seconds(block) >= MIN_RETRY_WINDOW_SECONDS, (
            "the app upstream has no retry window that outlasts a radon-nextjs "
            "restart; every request during the deploy gap 502s"
        )

    def test_api_proxy_retries_across_the_restart_gap(self, caddy_dir):
        block = reverse_proxy_block(read_caddyfile(caddy_dir), "127.0.0.1:8321")
        assert retry_window_seconds(block) >= MIN_RETRY_WINDOW_SECONDS, (
            "the /api/ib/* upstream has no retry window that outlasts a "
            "radon-api restart; ws-ticket calls 502 through every deploy"
        )

    @pytest.mark.parametrize("upstream", ["127.0.0.1:3000", "127.0.0.1:8321"])
    def test_retry_interval_re_dials_across_the_gap(self, caddy_dir, upstream):
        block = reverse_proxy_block(read_caddyfile(caddy_dir), upstream)
        interval = retry_interval_seconds(block)
        assert 0 < interval <= MAX_RETRY_INTERVAL_SECONDS, (
            f"lb_try_interval {interval}s on {upstream} is too coarse: the "
            "retry loop barely re-dials inside the window, so a restart gap "
            "still reaches the browser as a failure"
        )
        assert interval < retry_window_seconds(block), (
            f"lb_try_interval {interval}s on {upstream} outlasts "
            "lb_try_duration; the proxy gives up after a single dial"
        )

    def test_edge_health_status_stays_fast(self, caddy_dir):
        block = reverse_proxy_block(read_caddyfile(caddy_dir), "127.0.0.1:8330")
        assert retry_window_seconds(block) == 0, (
            "the edge health floor must answer immediately; a retry window "
            "would make the probe hang instead of reporting unreachable"
        )


class TestSingleUpstreamStaysInThePool:
    """The retry window only helps while the upstream is still in the pool.

    `fail_duration` switches on passive health checking. With exactly one
    upstream behind the proxy, a single refused dial during a restart marks
    that upstream down and `lb_try_duration` has nothing left to retry
    against -- Caddy 502s immediately, which is the incident 717e8d5d fixed.
    The Caddyfile says so in a comment; a comment is not a test. Scoped to the
    proxy block so the warning comment itself neither satisfies nor trips it.
    """

    @pytest.mark.parametrize("upstream", ["127.0.0.1:3000", "127.0.0.1:8321"])
    def test_no_fail_duration_on_the_single_upstream_blocks(self, caddy_dir, upstream):
        block = reverse_proxy_block(read_caddyfile(caddy_dir), upstream)
        assert "fail_duration" not in block, (
            f"reverse_proxy {upstream} sets fail_duration: the only upstream "
            "gets marked down on the first refused dial of a deploy restart, "
            "the lb_try_duration retry loop never runs, and every request in "
            "the gap 502s again"
        )


class _RestoredUpstream(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Length", "5")
        self.end_headers()
        self.wfile.write(b"admin")

    def log_message(self, *args):
        pass


@contextlib.contextmanager
def held_port():
    """Reserve an ephemeral port and KEEP it bound for the caller's lifetime.

    Binding, closing and returning the bare number leaves a TOCTOU window: the
    number is unowned from that moment until the test finally uses it, and the
    kernel hands it to whatever binds next. Two concurrent edge tests then draw
    the same port and one of them dies on EADDRINUSE. Holding the socket keeps
    the reservation for as long as the test needs it.

    A test that needs the port to REFUSE connections (a stopped unit, not a
    wedged one) has to give the reservation up first -- on darwin a
    bound-but-unlistening socket swallows the SYN instead of resetting it -- so
    use `release_to_peer` at the point the gap has to start, not at draw time.
    """
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    try:
        yield sock
    finally:
        sock.close()


def port_of(sock):
    return sock.getsockname()[1]


def release_to_peer(sock):
    """Give the reservation up at the last possible moment.

    For a peer that has to bind the port itself (caddy), which cannot be handed
    an already-bound socket.
    """
    port = port_of(sock)
    sock.close()
    return port


def serve_on(sock, handler):
    """Start a ThreadingHTTPServer on the ALREADY-BOUND socket from `held_port`.

    The reservation is handed straight to the server, so the port is never
    unowned between the draw and the listen.
    """
    server = http.server.ThreadingHTTPServer(
        sock.getsockname()[:2], handler, bind_and_activate=False
    )
    server.socket.close()
    server.socket = sock
    server.server_name, server.server_port = sock.getsockname()
    server.server_activate()
    threading.Thread(
        target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True
    ).start()
    return server


def stop_serving(server):
    """Stop the loop AND close the listener.

    `shutdown()` alone only ends `serve_forever`; the listening socket stays
    open for the rest of the pytest process.
    """
    with contextlib.suppress(Exception):
        server.shutdown()
    with contextlib.suppress(Exception):
        server.server_close()


def wait_for_listener(port, deadline):
    while time.monotonic() < deadline:
        with socket.socket() as probe:
            probe.settimeout(0.2)
            if probe.connect_ex(("127.0.0.1", port)) == 0:
                return True
        time.sleep(0.05)
    return False


CADDY_BIN = os.environ.get("RADON_CADDY_BIN", "caddy")


@pytest.mark.skipif(
    shutil.which(CADDY_BIN) is None,
    reason="needs a caddy binary; set RADON_CADDY_BIN to run the edge mechanism test",
)
class TestRestartWindowMechanism:
    """Drive the shipped proxy block with a real caddy against a real dead port.

    The assertions above only prove the retry window is spelled correctly.
    This one reproduces what production did: a request that arrives while the
    upstream is refusing connections must still be answered once the upstream
    is back, not turned into a 502 the instant the dial fails.
    """

    def test_request_during_an_upstream_gap_is_served_not_502ed(
        self, caddy_dir, tmp_path
    ):
        proxied = reverse_proxy_block(read_caddyfile(caddy_dir), "127.0.0.1:3000")
        order = []
        request_issued = threading.Event()

        with held_port() as upstream_sock, held_port() as proxy_sock:
            # Both ports stay reserved across the slow setup below; each is
            # released only at the instant its owner needs it.
            upstream_port = port_of(upstream_sock)
            proxy_port = release_to_peer(proxy_sock)
            config = tmp_path / "Caddyfile"
            config.write_text(
                "{\n\tadmin off\n\tgrace_period 10s\n}\n\n"
                f"http://127.0.0.1:{proxy_port} {{\n"
                "\thandle {\n"
                f"\t\treverse_proxy 127.0.0.1:{upstream_port} {proxied}\n"
                "\t}\n}\n"
            )

            caddy = subprocess.Popen(
                [CADDY_BIN, "run", "--config", str(config), "--adapter", "caddyfile"],
                env={
                    **os.environ,
                    "XDG_DATA_HOME": str(tmp_path / "data"),
                    "XDG_CONFIG_HOME": str(tmp_path / "config"),
                },
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            upstream = []
            try:
                assert wait_for_listener(proxy_port, time.monotonic() + 10), (
                    "caddy never started listening"
                )

                # The gap starts here: the reservation is given up so the port
                # REFUSES connections, exactly what radon-nextjs looks like
                # between systemd's stop and `next start`.
                release_to_peer(upstream_sock)
                with socket.socket() as probe:
                    probe.settimeout(0.5)
                    refusal = probe.connect_ex(("127.0.0.1", upstream_port))
                assert refusal == errno.ECONNREFUSED, (
                    f"the upstream port answered connect_ex with {refusal} "
                    f"({errno.errorcode.get(refusal, '?')}), not ECONNREFUSED: "
                    "this is a wedged upstream, not the restart gap, and a SYN "
                    "the kernel merely swallows gets retransmitted onto the "
                    "restored listener with no retry loop involved"
                )

                def serve_after_the_gap():
                    request_issued.wait(30)
                    # Hold the gap open across several lb_try_interval re-dials
                    # so the retry loop must actually run. A stimulus, not a
                    # bound on the behaviour being asserted.
                    time.sleep(retry_interval_seconds(proxied) * 4)
                    server = http.server.ThreadingHTTPServer(
                        ("127.0.0.1", upstream_port), _RestoredUpstream
                    )
                    upstream.append(server)
                    order.append("upstream-listening")
                    server.serve_forever(poll_interval=0.05)

                threading.Thread(target=serve_after_the_gap, daemon=True).start()

                order.append("request-issued")
                request_issued.set()
                try:
                    with urllib.request.urlopen(
                        f"http://127.0.0.1:{proxy_port}/admin",
                        # Safety net only, and derived: the edge's own retry
                        # window plus the drain it exists to cover.
                        timeout=retry_window_seconds(proxied)
                        + SHUTDOWN_GRACE_SECONDS,
                    ) as response:
                        status, body = response.status, response.read()
                except urllib.error.HTTPError as exc:
                    pytest.fail(
                        f"the upstream gap surfaced as HTTP {exc.code}: the "
                        "edge forwarded the restart straight to the browser "
                        "instead of riding it out"
                    )
                order.append("response")

                assert status == 200, f"upstream gap surfaced as HTTP {status}"
                assert body == b"admin", body
                # The ordering fact, not an elapsed float: the request was in
                # flight while the port refused connections, and it was
                # answered only after the upstream came back. An elapsed bound
                # is calibrated to the stub's sleep and to host load, so it
                # reds on a slow machine and greens on a fast wrong one.
                assert order == [
                    "request-issued",
                    "upstream-listening",
                    "response",
                ], f"the 200 did not come from the retry path: {order}"
            finally:
                for server in upstream:
                    stop_serving(server)
                caddy.terminate()
                caddy.wait(timeout=10)
