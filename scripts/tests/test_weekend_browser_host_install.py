"""The host Playwright browser install (#576/#578) never ran on the mini.

Two darwin-only defects, both found by running the real setup scripts on the
runner (2026-09-20):

1. The `@playwright/test` pin is read with `sed` `\\^\\?`, a GNU BRE optional
   quantifier. BSD `sed` treats `\\?` as a literal `?`, so the expression never
   matches `"@playwright/test": "^1.58.2"`, `PW_SPEC` is empty and setup prints
   `MISSING  @playwright/test pin` without ever installing the browser host.

2. The smoke connects with `require("playwright")` under `NODE_PATH`. Node
   resolves `node_modules` from the CWD first, so the loop's own repo copy wins
   over the browser host's pinned one. A version skew (1.59.1 client against
   the 1.58.2 run-server) fails the handshake with
   `428 Precondition Required`, reported as `MISSING  playwright run-server`.

Both surface as the same operator-facing line, which blames Seatbelt for a
host that launches Chromium fine.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from test_weekend_runner_env_provisioning import SETUPS, _run, _stage

# Only these two loops install a host browser.
BROWSER_LOOPS = ("reliability", "testing")

REPO = Path(__file__).resolve().parents[2]
NODE = shutil.which("node")


@pytest.mark.parametrize("name", BROWSER_LOOPS)
class TestBrowserHostInstall:
    def test_setup_extracts_the_caret_pinned_playwright_version(
        self, name, tmp_path
    ):
        src, clone, env = _stage(tmp_path, name)

        proc = _run(name, env, tmp_path)
        out = proc.stdout + proc.stderr

        assert "MISSING  @playwright/test pin" not in out, (
            f"{name}: the caret-pinned @playwright/test version was not read "
            "from web/package.json, so the host browser is never installed and "
            "every sandboxed browser verification stays blocked.\n" + out
        )
        assert "ok  browser host (playwright 1.58.2)" in out, out

    def test_smoke_connects_with_the_browser_hosts_own_playwright(
        self, name, tmp_path
    ):
        """A stale `playwright` in the CWD must not be what the smoke loads."""
        if NODE is None:
            pytest.skip("node is required to exercise module resolution")

        src, clone, env = _stage(tmp_path, name)
        # Real node only: npm/npx stay stubbed, so nothing is downloaded.
        node_only = tmp_path / "node-only"
        node_only.mkdir()
        (node_only / "node").symlink_to(NODE)
        env["PATH"] = f"{node_only}:{env['PATH']}"

        decoy = tmp_path / "node_modules" / "playwright"
        decoy.mkdir(parents=True)
        (decoy / "package.json").write_text(
            '{"name":"playwright","main":"index.js"}\n', encoding="utf-8"
        )
        (decoy / "index.js").write_text(
            'throw new Error("connect: 428 Precondition Required");\n',
            encoding="utf-8",
        )

        proc = _run(name, env, tmp_path)
        out = proc.stdout + proc.stderr

        assert "ok  playwright run-server (host)" in out, (
            f"{name}: the smoke resolved `playwright` from the CWD instead of "
            "the browser host's pinned copy. On the mini that is a 1.59.1 "
            "client against a 1.58.2 run-server: 428 Precondition Required.\n"
            + out
        )


@pytest.mark.parametrize("name", BROWSER_LOOPS)
def test_setup_script_uses_no_gnu_only_sed_quantifier(name):
    """`\\?` and `\\+` are GNU BRE extensions; the runner is BSD sed."""
    body = SETUPS[name][0].read_text(encoding="utf-8")
    for line_no, line in enumerate(body.splitlines(), 1):
        if "sed" not in line:
            continue
        assert "\\?" not in line and "\\+" not in line, (
            f"{SETUPS[name][0].name}:{line_no} uses a GNU-only sed quantifier; "
            "it silently matches nothing on the darwin runner:\n" + line
        )
