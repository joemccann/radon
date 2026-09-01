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
import yaml

# The shipped-config helpers live beside this file; pytest's rootdir is the
# repo, so the directory is not on sys.path by default.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_caddyfile import (  # noqa: E402
    APP_UPSTREAM,
    IB_UPSTREAM,
    handle_block,
    held_port,
    port_of,
    proxy_block,
    read_caddyfile,
    release_to_peer,
    retry_window_seconds,
    reverse_proxy_block,
    serve_on,
    stop_serving,
    strip_comments,
    wait_for_listener,
)


REPO = Path(__file__).resolve().parents[2]
WS_TICKET = REPO / "web" / "lib" / "wsTicket.ts"
ASSISTANT_ROUTE = REPO / "web" / "app" / "api" / "assistant" / "route.ts"

# The `handle` matcher that carries the assistant turn (R-262).
ASSISTANT_MATCHER = "/api/assistant*"


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
        block = proxy_block(read_caddyfile(caddy_dir), upstream)
        assert _directive_seconds(block, directive) is not None, (
            f"{upstream} inherits Caddy's unlimited {directive}; an upstream "
            "that accepts the socket and never answers hangs forever and the "
            "edge emits no 5xx at all"
        )

    @pytest.mark.parametrize("upstream", [APP_UPSTREAM, IB_UPSTREAM])
    def test_the_header_timeout_is_short_enough_to_surface(self, caddy_dir, upstream):
        block = proxy_block(read_caddyfile(caddy_dir), upstream)
        seconds = _directive_seconds(block, "response_header_timeout")
        assert seconds is not None and seconds <= 60, (
            f"{upstream} response_header_timeout {seconds}s is too long to "
            "turn a wedged upstream into an observable 5xx"
        )


class TestNonIdempotentRetryIsRestricted:
    def test_the_app_proxy_states_its_retry_restriction(self, caddy_dir):
        content = read_caddyfile(caddy_dir)
        block = proxy_block(content, APP_UPSTREAM)
        assert "retry_match" in block, (
            "POST /api/orders/place rides this block with a 15s retry loop and "
            "no stated restriction; order non-duplication must not rest on an "
            "unpinned upstream default"
        )

    def test_the_restriction_names_only_safe_methods(self, caddy_dir):
        block = proxy_block(read_caddyfile(caddy_dir), APP_UPSTREAM)
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
            retry_window_seconds(proxy_block(content, upstream))
            for upstream in (APP_UPSTREAM, IB_UPSTREAM)
        ]
        assert grace >= max(windows), (
            f"grace_period {grace}s hard-cuts an in-flight {max(windows)}s retry "
            "loop during the very event it exists to absorb"
        )


class TestRideOutMatchesItsNamedClient:
    def test_the_ib_window_is_not_longer_than_getwsticket_waits(self, caddy_dir):
        block = proxy_block(read_caddyfile(caddy_dir), IB_UPSTREAM)
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


# The longest assistant turn the radon-nextjs journal has recorded answering
# upstream: 2026-08-28 18:20:00 UTC, `[assistant] done rounds=2
# outcome=answered toolCalls=3 ... ms=55278`. The edge killed the client's
# request 25 seconds before that answer existed.
OBSERVED_ANSWERED_TURN_SECONDS = 55.278


class TestTheAssistantTurnOutlivesTheGenericGuard:
    """2026-08-29: a pasted-chart turn 504'd at the edge while the route answered.

    The operator pasted a chart and asked how it related to their TLT position.
    The image uploaded, the turn ran, and the browser got
    `POST /api/assistant -> 504` after tens of seconds; the chat rendered the
    generic "Assistant service returned an error."

    `/api/assistant` rode the catch-all `handle`, whose R-219 guard abandons a
    request after 30 s without a response header. The route is NON-STREAMING
    (`web/app/api/assistant/route.ts` runs the whole multi-round
    `runAssistantLoop` and only then writes JSON), so a turn writes NO header
    until it is completely finished — and it declares `maxDuration = 300`,
    ten times the edge's patience. The journal proves the mismatch is real and
    not hypothetical: one recorded turn answered at 55.3 s.

    R-219 is still right for every other route: an upstream that accepts the
    socket and never writes a header must become an observable 5xx quickly, or
    a total front-end hang stays invisible to `@edge_health_status`. So the
    assistant gets its OWN `handle` with its own bound, and the generic guard
    below stays where it is. R-262.
    """

    def _assistant_block(self, caddy_dir):
        content = read_caddyfile(caddy_dir)
        return reverse_proxy_block(handle_block(content, ASSISTANT_MATCHER), APP_UPSTREAM)

    def test_the_assistant_path_has_its_own_handle(self, caddy_dir):
        block = self._assistant_block(caddy_dir)
        assert _directive_seconds(block, "response_header_timeout") is not None, (
            "/api/assistant has no handle of its own, so it rides the "
            "catch-all's 30s header guard and every turn slower than that "
            "reaches the operator as a 504"
        )

    def test_the_assistant_handle_precedes_the_catch_all(self, caddy_dir):
        active = strip_comments(read_caddyfile(caddy_dir))
        assistant = active.find("handle " + ASSISTANT_MATCHER)
        catch_all = re.search(r"handle\s*\{", active)
        assert assistant != -1 and catch_all
        assert assistant < catch_all.start(), (
            "handle blocks are mutually exclusive in written order; a "
            "catch-all declared first swallows /api/assistant and the "
            "dedicated bound never applies"
        )

    def test_the_assistant_bound_outlasts_an_observed_answered_turn(self, caddy_dir):
        seconds = _directive_seconds(self._assistant_block(caddy_dir), "response_header_timeout")
        # Twice the longest turn actually observed answering: a vision turn
        # carrying a pasted chart plus the portfolio tool rounds the operator's
        # question forces is strictly more work than that text turn was.
        assert seconds >= 2 * OBSERVED_ANSWERED_TURN_SECONDS, (
            f"response_header_timeout {seconds}s leaves no room over the "
            f"{OBSERVED_ANSWERED_TURN_SECONDS}s turn the journal already "
            "recorded answering; the 504 cliff has barely moved"
        )

    def test_the_assistant_bound_stays_inside_the_routes_own_budget(self, caddy_dir):
        block = self._assistant_block(caddy_dir)
        seconds = _directive_seconds(block, "response_header_timeout")
        source = ASSISTANT_ROUTE.read_text(encoding="utf-8")
        match = re.search(r"maxDuration\s*=\s*(\d+)", source)
        assert match, "the assistant route no longer states a maxDuration"
        assert seconds <= int(match.group(1)), (
            f"the edge waits {seconds}s for a route that gives itself "
            f"{match.group(1)}s; the extra wait can only ever be spent on a "
            "request the route has already abandoned"
        )
        read_timeout = _directive_seconds(block, "read_timeout")
        assert read_timeout is not None and seconds <= read_timeout, (
            "response_header_timeout outlasts read_timeout, so the connection "
            "is torn down before the header bound it is supposed to honour"
        )

    def test_the_assistant_block_never_replays_the_turn(self, caddy_dir):
        block = self._assistant_block(caddy_dir)
        assert retry_window_seconds(block) == 0, (
            "a retry window on the assistant handle would replay a severed "
            "POST /api/assistant: a second vision turn billed to the operator "
            "and a second set of tool calls, with no idempotency key anywhere"
        )

    def test_the_generic_guard_is_still_in_force(self, caddy_dir):
        """R-219 must not have been widened for everything else."""
        seconds = _directive_seconds(
            proxy_block(read_caddyfile(caddy_dir), APP_UPSTREAM), "response_header_timeout"
        )
        assert seconds is not None and seconds <= 60, (
            f"the catch-all header guard is {seconds}s: raising it globally "
            "undoes R-219, and a wedged upstream stops producing an "
            "observable 5xx for every other route"
        )


# ── Mechanism tests ──────────────────────────────────────────────────────
#
# The assertions above prove the config STATES the right behaviour. These drive
# a real caddy with the shipped proxy block, which is what the existing harness
# in test_caddyfile.py does for the restart-gap case — that one drives only a
# GET against a dead port, so neither the hung-upstream nor the non-idempotent
# case was covered in either direction (R-219, R-220).

import contextlib
import fnmatch
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
CI_WORKFLOW = REPO / ".github" / "workflows" / "ci.yml"

# The stub writes its response header this long AFTER the edge's own
# response_header_timeout. A stimulus, not a bound on correct behaviour: it
# gives the assertion a second event to order the edge's answer against, so an
# edge that has no bound at all is caught FORWARDING the late header rather
# than timing the client out inconclusively.
LATE_HEADER_MARGIN_SECONDS = 8


class _AnswersOnlyAfterTheEdgesBound(http.server.BaseHTTPRequestHandler):
    """Accepts the socket and holds the response header open.

    This is the dominant radon-api failure mode in this repo: R-014's
    data-role lock held by a wedged gateway, R-060's zombie pool client. It is
    exactly the case `lb_try_duration` cannot help with, because the connection
    attempt SUCCEEDS.

    The handler blocks on an Event rather than a fixed sleep so teardown can
    release it: a `time.sleep(120)` handler pins its accepted connection — and
    the server object, and therefore the listening socket — for the rest of the
    pytest process.
    """

    protocol_version = "HTTP/1.1"
    release = threading.Event()
    LATE_BODY = b"late"

    def _hang(self):
        if not type(self).release.wait(300):
            return
        with contextlib.suppress(OSError):
            body = type(self).LATE_BODY
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    do_GET = _hang
    do_POST = _hang

    def log_message(self, *args):
        pass

    def handle_one_request(self):
        with contextlib.suppress(OSError):
            super().handle_one_request()


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


def _require_caddy():
    if shutil.which(CADDY_BIN) is None:
        raise AssertionError(
            f"{CADDY_BIN} is not on PATH. CI installs caddy in the cloud-tests "
            "shard that collects this file (T-205); locally, point "
            "RADON_CADDY_BIN at a binary or set RADON_SKIP_CADDY_E2E=1 to skip "
            "the edge mechanism tests."
        )


def _caddy_env(tmp_path):
    return {
        **os.environ,
        "XDG_DATA_HOME": str(tmp_path / "data"),
        "XDG_CONFIG_HOME": str(tmp_path / "config"),
    }


def _run_caddy(tmp_path, proxy_sock, upstream_port, block, site_prelude=""):
    """Run a real caddy with `block` as the proxy body.

    `site_prelude` injects site-level directives ahead of the handle — the
    shipped site block puts `encode zstd gzip` in front of every proxy, and a
    compressor that buffers is invisible to a test that omits it.
    """
    _require_caddy()
    # caddy binds the listener itself, so the reservation is given up at the
    # last possible moment instead of at draw time.
    proxy_port = release_to_peer(proxy_sock)
    config = tmp_path / "Caddyfile"
    config.write_text(
        "{\n\tadmin off\n\tgrace_period 20s\n}\n\n"
        f"http://127.0.0.1:{proxy_port} {{\n"
        f"{site_prelude}"
        "\thandle {\n"
        f"\t\treverse_proxy 127.0.0.1:{upstream_port} {block}\n"
        "\t}\n}\n"
    )
    return subprocess.Popen(
        [CADDY_BIN, "run", "--config", str(config), "--adapter", "caddyfile"],
        env=_caddy_env(tmp_path),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


class TestCiProvidesTheCaddyBinary:
    """T-205 (a direct T-164 recurrence): a binary-gated guard CI cannot run.

    `TestEdgeMechanism` is the ONLY executable proof of the R-219 bound on a
    hung upstream and the R-220 non-replay of a severed
    `POST /api/orders/place`. R-220 is an order-duplication guard on a path
    with no idempotency key, where the operator's only evidence would be two
    fills. With no caddy binary anywhere in CI, both were covered on the gate
    that ships production by regex over the Caddyfile text alone, and a Caddy
    release that changes `retry_match` semantics shipped green.
    """

    def _cloud_tests_job(self):
        workflow = yaml.safe_load(CI_WORKFLOW.read_text(encoding="utf-8"))
        job = workflow["jobs"]["cloud-tests"]
        return job

    @staticmethod
    def _modules_gated_on_the_caddy_binary() -> list[str]:
        """Every cloud test module whose collection silently shrinks without caddy.

        T-322 (T-164 -> T-205 recurring a third time): the self-check below was
        scoped to THIS file, so when the caddy install moved to the `edge`
        shard, `test_caddyfile.py::TestRestartWindowMechanism` (the executable
        proof that a request arriving during the deploy restart window is held
        and served, not 502'd) began skipping on `al` with nobody watching.
        """
        gate = re.compile(r'shutil\.which\((?:CADDY_BIN|"caddy")\)')
        here = Path(__file__).resolve().parent
        gated = sorted(
            module.name
            for module in here.glob("test_*.py")
            if gate.search(module.read_text(encoding="utf-8"))
        )
        assert Path(__file__).name in gated, "the self-check lost its own gate"
        return gated

    @staticmethod
    def _shard_that_collects(job, name: str) -> str:
        def collects(entry) -> bool:
            included = fnmatch.fnmatch(name, Path(entry["paths"]).name)
            omitted = Path(entry.get("omit", "")).name
            return included and name != omitted

        shards = [
            entry["shard"]
            for entry in job["strategy"]["matrix"]["include"]
            if collects(entry)
        ]
        assert len(shards) == 1, (
            f"{name} is collected by shards {shards}; the caddy install step "
            "cannot be targeted"
        )
        return shards[0]

    @staticmethod
    def _shards_named_in(condition: str, all_shards: list[str]) -> set[str]:
        if not condition:
            return set(all_shards)
        return set(re.findall(r"matrix\.shard\s*==\s*'([^']+)'", condition))

    def test_every_caddy_gated_module_runs_on_a_shard_that_installs_caddy(self):
        job = self._cloud_tests_job()
        all_shards = [str(shard) for shard in job["strategy"]["matrix"]["shard"]]
        providers = [
            step for step in job["steps"]
            if "caddy" in str(step.get("run", "")).lower()
            or "RADON_CADDY_BIN" in str(step.get("env", ""))
        ]
        assert providers, (
            "no step in the cloud-tests job installs caddy or sets "
            "RADON_CADDY_BIN, so TestEdgeMechanism skips on the gate that "
            "ships production and the R-219 / R-220 fixes are proved by regex "
            "over Caddyfile text only"
        )
        provided: set[str] = set()
        for step in providers:
            provided |= self._shards_named_in(str(step.get("if", "")), all_shards)
        unprovided = {
            module: shard
            for module in self._modules_gated_on_the_caddy_binary()
            if (shard := self._shard_that_collects(job, module)) not in provided
        }
        assert not unprovided, (
            f"caddy is installed only on shards {sorted(provided)}, but these "
            f"binary-gated modules are collected elsewhere and silently skip "
            f"their mechanism tests on the gate that ships production: "
            f"{unprovided}"
        )

    def test_cloud_shards_print_skip_reasons(self):
        """`-q` hides skips; the T-322 class skipped for weeks as a +1 count."""
        job = self._cloud_tests_job()
        invocations = [
            line.strip()
            for step in job["steps"]
            for line in str(step.get("run", "")).splitlines()
            if re.search(r"python\s+-m\s+pytest\b", line)
        ]
        assert invocations, "no pytest invocation in the cloud-tests job"
        for invocation in invocations:
            assert re.search(r"\s-r\w*[saA]\w*(?=\s|$)", invocation), (
                f"cloud shard invocation hides skip reasons: {invocation!r}"
            )

    def test_the_only_escape_is_the_declared_env_var(self):
        marks = [
            mark for mark in getattr(TestEdgeMechanism, "pytestmark", [])
            if mark.name == "skipif"
        ]
        assert len(marks) == 1, f"expected one skipif on TestEdgeMechanism, got {marks}"
        condition, reason = marks[0].args[0], marks[0].kwargs.get("reason", "")
        assert "RADON_SKIP_CADDY_E2E" in reason
        # Nobody opted out of this process, so the class must be running.
        # A gate on the binary's mere presence trips here on any host without
        # caddy — which is exactly how T-164 recurred as T-205.
        assert not condition or os.environ.get("RADON_SKIP_CADDY_E2E") == "1", (
            "TestEdgeMechanism is skipped without an explicit opt-out, so the "
            "R-219 and R-220 proofs are decorative again"
        )


@pytest.mark.skipif(
    os.environ.get("RADON_SKIP_CADDY_E2E") == "1",
    reason="RADON_SKIP_CADDY_E2E=1 local-dev escape (T-205). CI installs caddy.",
)
class TestEdgeMechanism:
    def test_the_shipped_caddyfile_adapts(self, caddy_dir, tmp_path):
        """The regex assertions above pass on a Caddyfile Caddy cannot parse.

        `retry_match` and bare `dial_timeout` / `response_header_timeout` /
        `read_timeout` are the JSON and transport spellings, not reverse_proxy
        subdirectives; the adapter rejects them outright. Every text assertion
        in this file was green on a config `caddy validate` refuses, so
        `configure_caddy` and the deploy's Caddyfile gate could never install
        it and R-219 / R-220 / R-258 were never actually in force at the edge.
        `adapt` rather than `validate`: adapting is the syntax check, while
        validating also provisions the log writer against /var/log/caddy.
        T-205.
        """
        _require_caddy()
        result = subprocess.run(
            [
                CADDY_BIN, "adapt",
                "--config", str(caddy_dir / "Caddyfile"),
                "--adapter", "caddyfile",
            ],
            env={**_caddy_env(tmp_path), "DOMAIN": "app.radon.run"},
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert result.returncode == 0, (
            "the shipped Caddyfile does not adapt, so no host can install "
            f"it:\n{result.stderr}"
        )

    def test_a_hung_upstream_becomes_a_5xx_within_a_bound(self, caddy_dir, tmp_path):
        block = proxy_block(read_caddyfile(caddy_dir), APP_UPSTREAM)
        header_timeout = _directive_seconds(block, "response_header_timeout")
        assert header_timeout is not None

        stub = _AnswersOnlyAfterTheEdgesBound
        stub.release = threading.Event()
        late_header = threading.Timer(
            header_timeout + LATE_HEADER_MARGIN_SECONDS, stub.release.set
        )

        with held_port() as upstream_sock, held_port() as proxy_sock:
            server = serve_on(upstream_sock, stub)
            proxy_port = port_of(proxy_sock)
            caddy = _run_caddy(tmp_path, proxy_sock, port_of(upstream_sock), block)
            try:
                assert wait_for_listener(proxy_port, time.monotonic() + 10)
                late_header.start()
                try:
                    with urllib.request.urlopen(
                        f"http://127.0.0.1:{proxy_port}/admin",
                        # Safety net only, and derived: it has to outlast the
                        # stub's late header so a missing bound shows up as a
                        # forwarded 200 rather than an inconclusive timeout.
                        timeout=header_timeout + LATE_HEADER_MARGIN_SECONDS * 2,
                    ) as response:
                        status, body = response.status, response.read()
                except urllib.error.HTTPError as exc:
                    status, body = exc.code, exc.read()
                except (TimeoutError, OSError, urllib.error.URLError) as exc:
                    pytest.fail(
                        "the edge never answered a hung upstream at all "
                        f"({exc!r}); the hang was forwarded to the client"
                    )

                # Ordering, not a stopwatch. The stub DOES answer, one
                # LATE_HEADER_MARGIN_SECONDS after the edge's own stated bound,
                # so the response itself says which came first and no elapsed
                # float is involved: its body means the edge rode out an
                # arbitrarily wedged upstream and emitted no 5xx at all, which
                # is exactly why @edge_health_status could keep answering 200
                # through a total front-end hang (R-219).
                assert body != _AnswersOnlyAfterTheEdgesBound.LATE_BODY, (
                    "the edge forwarded the upstream's late response header "
                    "instead of giving up first: it does not bound a wedged "
                    "upstream, so the hang produces no 5xx anywhere"
                )
                assert 500 <= status < 600, (
                    f"the edge answered {status}; @edge_health_status only maps "
                    "502/503/504, so a total front-end hang stayed invisible"
                )
            finally:
                late_header.cancel()
                stub.release.set()
                stop_serving(server)
                caddy.terminate()
                caddy.wait(timeout=10)

    def test_a_severed_post_is_not_replayed(self, caddy_dir, tmp_path):
        block = proxy_block(read_caddyfile(caddy_dir), APP_UPSTREAM)
        _CountsPosts.seen = []
        # The client deadline is the edge's own retry window plus one dial, both
        # read from the shipped config: a severed POST cannot legitimately take
        # longer than the loop that would replay it.
        dial_timeout = _directive_seconds(block, "dial_timeout")
        assert dial_timeout is not None
        deadline = retry_window_seconds(block) + dial_timeout

        with held_port() as upstream_sock, held_port() as proxy_sock:
            server = serve_on(upstream_sock, _CountsPosts)
            proxy_port = port_of(proxy_sock)
            caddy = _run_caddy(tmp_path, proxy_sock, port_of(upstream_sock), block)
            try:
                assert wait_for_listener(proxy_port, time.monotonic() + 10)
                request = urllib.request.Request(
                    f"http://127.0.0.1:{proxy_port}/api/orders/place",
                    data=b'{"symbol":"SPY"}',
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with pytest.raises(Exception):
                    urllib.request.urlopen(request, timeout=deadline)
                # Caddy answers the client only once it has stopped retrying, so
                # the tally at this point is final.
                assert _CountsPosts.seen == ["/api/orders/place"], (
                    f"the edge replayed a severed order POST: {_CountsPosts.seen}; "
                    "there is no idempotency key on this path, so the operator's "
                    "only evidence would be two fills"
                )
            finally:
                stop_serving(server)
                caddy.terminate()
                caddy.wait(timeout=10)


class TestTheDeployPublishesTheEdgeConfig:
    """cloud/caddy/Caddyfile was the one release artifact no deploy shipped.

    Editing it, merging it and watching CI go green published nothing: the repo
    and /etc/caddy diverged silently until someone ran publish-caddy by hand.
    It cost two incidents on 2026-08-29 that were both already fixed in the
    repo and both still broken in production — the assistant 504
    (`response_header_timeout` on `/api/assistant`) and the headlines
    websocket dialling `localhost:8766` when the hub listens on 127.0.0.1
    only, so Caddy resolved ::1 first and never reached it.
    """

    def test_deploy_publishes_the_caddyfile_after_a_verified_release(self) -> None:
        deploy = (REPO / "cloud/scripts/deploy.sh").read_text(encoding="utf-8")
        assert "publish-caddy" in deploy, (
            "cloud/scripts/deploy.sh never publishes cloud/caddy/Caddyfile, so an "
            "edge fix can merge green and never reach production"
        )
        # Every site that syncs scheduled units has just finished verifying a
        # release; the edge config belongs on exactly those paths.
        sync_sites = deploy.count("sync_scheduled_units || log_warn")
        publish_sites = deploy.count("publish_edge_config || log_warn")
        assert publish_sites == sync_sites, (
            f"{publish_sites} edge-config publishes for {sync_sites} unit syncs; "
            "a verified release that syncs units must also publish the edge config"
        )

    def test_edge_publish_degrades_to_a_warning_rather_than_failing_a_release(
        self,
    ) -> None:
        deploy = (REPO / "cloud/scripts/deploy.sh").read_text(encoding="utf-8")
        assert "edge_config_publish_is_granted" in deploy, (
            "publish-caddy must be grant-checked like sync-scheduled-units so a "
            "host without the sudoers verb warns instead of failing the deploy"
        )


# ── The assistant turn streams (R-262 durable fix) ───────────────────────
#
# Raising `response_header_timeout` only moved the cliff: the route wrote
# nothing until `runAssistantLoop` finished, so any turn slower than the bound
# still 504'd. The route now opens a `text/event-stream` and flushes a `start`
# frame BEFORE the loop is awaited, so the response header exists within
# milliseconds no matter how long the turn takes.

# One `start` frame is a few dozen bytes, well under `encode`'s 512-byte
# minimum_length. A compressor that waits for its buffer to fill, or a proxy
# that batches writes, re-creates the exact bug being fixed here.
STREAM_FIRST_BYTE_BUDGET_SECONDS = 5


class _StreamsStartThenAnswersLate(http.server.BaseHTTPRequestHandler):
    """The streaming route: header + `start` immediately, `done` much later.

    `Connection: close` rather than a Content-Length or hand-rolled chunking:
    the body is EOF-delimited, which is what a ReadableStream response looks
    like to Caddy's transport, and it keeps the stub honest about never
    knowing its own length up front.
    """

    protocol_version = "HTTP/1.1"
    release = threading.Event()
    START_FRAME = b"event: start\ndata: {}\n\n"
    DONE_FRAME = b'event: done\ndata: {"content":"answered"}\n\n'

    def do_POST(self):
        # Drain the turn's JSON body. Closing a socket with unread request
        # bytes still in it makes the kernel send RST rather than FIN, Caddy
        # reads that as a severed upstream, and the client sees a truncated
        # chunked stream instead of the clean end of a finished turn.
        length = int(self.headers.get("Content-Length") or 0)
        if length:
            self.rfile.read(length)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache, no-transform")
        self.send_header("Connection", "close")
        self.end_headers()
        with contextlib.suppress(OSError):
            self.wfile.write(type(self).START_FRAME)
            self.wfile.flush()
        type(self).release.wait(300)
        with contextlib.suppress(OSError):
            self.wfile.write(type(self).DONE_FRAME)
            self.wfile.flush()
        self.close_connection = True

    def log_message(self, *args):
        pass

    def handle_one_request(self):
        with contextlib.suppress(OSError):
            super().handle_one_request()


def _assistant_proxy_block(caddy_dir):
    content = read_caddyfile(caddy_dir)
    return reverse_proxy_block(handle_block(content, ASSISTANT_MATCHER), APP_UPSTREAM)


class TestTheAssistantHandleStatesItStreams:
    def test_the_assistant_handle_flushes_every_write(self, caddy_dir):
        block = _assistant_proxy_block(caddy_dir)
        assert re.search(r"flush_interval\s+-1\b", block), (
            "the assistant handle does not state flush_interval -1, so whether "
            "the SSE frames reach the browser as they are written rests on "
            "Caddy's content-type auto-detection; a stream that sits in the "
            "proxy's buffer is the 504 all over again"
        )

    def test_the_route_writes_its_header_before_the_loop(self):
        source = ASSISTANT_ROUTE.read_text(encoding="utf-8")
        assert "text/event-stream" in source, (
            "the assistant route does not answer with an event stream, so it "
            "still writes nothing until runAssistantLoop resolves and any turn "
            "slower than the edge's header bound reaches the operator as a 504"
        )


@pytest.mark.skipif(
    os.environ.get("RADON_SKIP_CADDY_E2E") == "1",
    reason="RADON_SKIP_CADDY_E2E=1 local-dev escape (T-205). CI installs caddy.",
)
class TestTheAssistantStreamMechanism:
    """The money test: a real caddy in front of a deliberately slow upstream.

    `test_a_hung_upstream_becomes_a_5xx_within_a_bound` above already proves
    the other half — an upstream that writes NO header inside the guard, which
    is what `/api/assistant` was until this change, becomes a 504. These drive
    the same guard with the streaming shape and assert the opposite outcome, so
    the pair is the before and after of the 2026-08-29 incident.
    """

    def test_a_streaming_turn_survives_the_generic_header_guard(
        self, caddy_dir, tmp_path
    ):
        """The turn no longer depends on a bespoke bound to reach the operator.

        Driven through the CATCH-ALL block on purpose: that is the 30 s guard
        `/api/assistant` rode on 2026-08-29, and a turn that answers only after
        it expires has to succeed anyway now that the header is written first.
        """
        block = proxy_block(read_caddyfile(caddy_dir), APP_UPSTREAM)
        guard = _directive_seconds(block, "response_header_timeout")
        assert guard is not None

        stub = _StreamsStartThenAnswersLate
        stub.release = threading.Event()
        # The answer lands AFTER the guard the incident died on.
        answer_at = threading.Timer(guard + LATE_HEADER_MARGIN_SECONDS, stub.release.set)

        with held_port() as upstream_sock, held_port() as proxy_sock:
            server = serve_on(upstream_sock, stub)
            proxy_port = port_of(proxy_sock)
            caddy = _run_caddy(tmp_path, proxy_sock, port_of(upstream_sock), block)
            try:
                assert wait_for_listener(proxy_port, time.monotonic() + 10)
                answer_at.start()
                request = urllib.request.Request(
                    f"http://127.0.0.1:{proxy_port}/api/assistant",
                    data=b'{"messages":[{"role":"user","content":"hi"}]}',
                    headers={
                        "Content-Type": "application/json",
                        "Accept": "text/event-stream",
                    },
                    method="POST",
                )
                started = time.monotonic()
                try:
                    response = urllib.request.urlopen(
                        request, timeout=guard + LATE_HEADER_MARGIN_SECONDS * 3
                    )
                except urllib.error.HTTPError as exc:
                    pytest.fail(
                        f"the edge answered {exc.code} instead of streaming the "
                        "turn: the route wrote no header before the loop ran, "
                        "which is exactly the 2026-08-29 incident"
                    )
                with response:
                    assert response.status == 200
                    header_at = time.monotonic() - started
                    first = response.read(len(stub.START_FRAME))
                    first_byte_at = time.monotonic() - started
                    body = first + response.read()

                assert first == stub.START_FRAME, (
                    f"the first thing off the wire was {first!r}, not the start "
                    "frame; something between the route and the client is "
                    "holding the stream"
                )
                assert first_byte_at < STREAM_FIRST_BYTE_BUDGET_SECONDS, (
                    f"the first byte took {first_byte_at:.1f}s against a "
                    f"{guard}s guard; the stream is being buffered, so a slower "
                    "turn still 504s"
                )
                assert header_at < STREAM_FIRST_BYTE_BUDGET_SECONDS
                assert stub.DONE_FRAME in body, (
                    "the connection did not carry the turn's answer through to "
                    f"the end: {body!r}"
                )
            finally:
                answer_at.cancel()
                stub.release.set()
                stop_serving(server)
                caddy.terminate()
                caddy.wait(timeout=10)

    def test_the_shipped_assistant_handle_does_not_buffer_the_stream(
        self, caddy_dir, tmp_path
    ):
        """`encode zstd gzip` sits in front of this path in the real site block.

        A compressor that waits for `minimum_length` (512 bytes by default)
        before deciding whether to compress would hold a ~24-byte `start` frame
        until the turn finished, reintroducing the bug in the one place nobody
        would look for it. So this drives the SHIPPED assistant proxy block with
        the site's own `encode` directive in front of it.
        """
        block = _assistant_proxy_block(caddy_dir)
        stub = _StreamsStartThenAnswersLate
        stub.release = threading.Event()
        answer_at = threading.Timer(
            STREAM_FIRST_BYTE_BUDGET_SECONDS * 2, stub.release.set
        )

        with held_port() as upstream_sock, held_port() as proxy_sock:
            server = serve_on(upstream_sock, stub)
            proxy_port = port_of(proxy_sock)
            caddy = _run_caddy(
                tmp_path,
                proxy_sock,
                port_of(upstream_sock),
                block,
                site_prelude="\tencode zstd gzip\n",
            )
            try:
                assert wait_for_listener(proxy_port, time.monotonic() + 10)
                answer_at.start()
                request = urllib.request.Request(
                    f"http://127.0.0.1:{proxy_port}/api/assistant",
                    data=b'{"messages":[{"role":"user","content":"hi"}]}',
                    headers={
                        "Content-Type": "application/json",
                        "Accept": "text/event-stream",
                        # What a browser sends. Without it `encode` is a no-op
                        # and the buffering question is never asked.
                        "Accept-Encoding": "gzip",
                    },
                    method="POST",
                )
                started = time.monotonic()
                with urllib.request.urlopen(request, timeout=60) as response:
                    assert response.status == 200
                    first = response.read(len(stub.START_FRAME))
                    first_byte_at = time.monotonic() - started
                assert first == stub.START_FRAME, first
                assert first_byte_at < STREAM_FIRST_BYTE_BUDGET_SECONDS, (
                    f"the shipped assistant handle held the start frame for "
                    f"{first_byte_at:.1f}s; the turn is buffered at the edge"
                )
            finally:
                answer_at.cancel()
                stub.release.set()
                stop_serving(server)
                caddy.terminate()
                caddy.wait(timeout=10)
