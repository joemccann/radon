"""GAP A: the nightly runner clones are never given `web/.env`.

`web/.env` is gitignored, so the dedicated clones at
`~/radon-weekend/radon{,-testing}` (created by `git clone` and hard-reset to
`origin/main` every night) structurally cannot contain it. Both wrappers
already exclude it from their per-round `git clean`
(`reliability_weekend.sh` `reground_for_continuation`/`ground_truth`,
`testing_weekend.sh` `ground_truth`), i.e. surviving the reset has always been
the intent; only the provisioning step was never written.

ONLY `web/.env` is provisioned. The root `.env` is deliberately left out: a
copy would duplicate IB_FLEX_TOKEN. `test_setup_does_not_provision_the_root_env`
guards that.

`web/.env` IS read by pytest, not only by Next: 50 `scripts/**/*.py` producers
(`cash_flow_sync.py`, `fetch_*.py`, `scanner.py`, `api/server.py`, ...) call
`load_dotenv(web/.env)` at import, so provisioning it puts the clone's TURSO
credentials into `os.environ` for every collected module. That flipped 22
CI-green tests red with `FlexTokenLocked` (T-317). `scripts/conftest.py`
`_strip_turso_credentials` (hoisted for the api subtree, T-368) removes the
keys per test;
`TestPytestReadsTheProvisionedWebEnv` pins both halves of that contract.

Consequence the loops actually hit without it: no
`NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY` means a dev server the browser-verification
step starts renders no Clerk-wrapped page.

These tests drive the real setup scripts against a staged fake source checkout
and a staged fake clone, with the whole toolchain stubbed on PATH. No real
credential is ever read or written.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
BASH = shutil.which("bash") or "/bin/bash"

SETUPS = {
    "reliability": (REPO / "scripts" / "setup_reliability_weekend.sh", "radon"),
    "testing": (REPO / "scripts" / "setup_testing_weekend.sh", "radon-testing"),
    "ci-performance": (
        REPO / "scripts" / "setup_ci_performance.sh",
        "radon-ci-performance",
    ),
    "documentation": (
        REPO / "scripts" / "setup_documentation_nightly.sh",
        "radon-documentation",
    ),
}
PLISTS = {
    "reliability": "com.radon.reliability-daily.plist",
    "testing": "com.radon.testing-daily.plist",
    "ci-performance": "com.radon.ci-performance-daily.plist",
    "documentation": "com.radon.documentation-daily.plist",
}

# Dummy values only. Never stage a real credential into a fixture.
WRAPPERS = {
    "reliability": "reliability_weekend.sh",
    "testing": "testing_weekend.sh",
    "ci-performance": "ci_performance_nightly.sh",
    "documentation": "documentation_nightly.sh",
}
DUMMY = {
    ".env": "TURSO_DB_URL=libsql://dummy.invalid\nTURSO_AUTH_TOKEN=dummy\n",
    ".env.ib-mode": "IB_GATEWAY_MODE=local\n",
    "web/.env": "NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY=pk_test_dummy\n",
}


def _stub_bin(tmp_path: Path) -> Path:
    """Every external the setup scripts shell out to, neutered."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    scripts = {
        # `git config --get remote.origin.url` must answer; everything else
        # (ls-remote / fetch / checkout / reset) just succeeds.
        "git": (
            "#!/bin/sh\n"
            'case "$*" in *"config --get remote.origin.url"*)'
            ' echo git@example.invalid:stub/radon.git;; esac\n'
            "exit 0\n"
        ),
        "gh": "#!/bin/sh\nexit 0\n",
        "claude": "#!/bin/sh\nexit 0\n",
        "node": "#!/bin/sh\nexit 0\n",
        "bun": "#!/bin/sh\nexit 0\n",
        "caddy": "#!/bin/sh\nexit 0\n",
        # Only the `bash 4+` toolchain check shells out to `bash`; this
        # keeps [1/4] green on a macOS runner so the run reaches [2/4].
        "bash": "#!/bin/sh\nexit 0\n",
        "ssh": "#!/bin/sh\nexit 0\n",
        "python3.13": "#!/bin/sh\nexit 0\n",
        "launchctl": "#!/bin/sh\nexit 0\n",
        # -lint succeeds; -extract feeds the closing printf a number.
        "plutil": (
            "#!/bin/sh\n"
            'if [ "$1" = "-lint" ]; then exit 0; fi\n'
            "echo 0\n"
        ),
        "timeout": (
            "#!/bin/sh\n"
            'while [ $# -gt 0 ]; do\n'
            '  case "$1" in\n'
            '    -k|--kill-after) shift 2 ;;\n'
            '    --foreground|--preserve-status) shift ;;\n'
            '    [0-9]*) shift; break ;;\n'
            '    *) shift; break ;;\n'
            '  esac\n'
            'done\n'
            'exec "$@"\n'
        ),
        "npm": (
            "#!/bin/sh\n"
            'prefix=""\n'
            'pkg=""\n'
            'while [ $# -gt 0 ]; do\n'
            '  case "$1" in\n'
            '    --prefix) prefix="$2"; shift 2 ;;\n'
            '    @playwright/test@*) pkg="$1"; shift ;;\n'
            '    *) shift ;;\n'
            '  esac\n'
            'done\n'
            'if [ -n "$prefix" ]; then\n'
            '  mkdir -p "$prefix/node_modules/@playwright/test" "$prefix/node_modules/.bin" "$prefix/node_modules/playwright"\n'
            '  ver="${pkg##*@}"\n'
            '  [ -n "$ver" ] || ver="1.58.2"\n'
            '  printf \'{"version":"%s"}\\n\' "$ver" > "$prefix/node_modules/@playwright/test/package.json"\n'
            '  printf \'%s\\n\' "#!/bin/sh" "echo Listening on ws://127.0.0.1:4711/tok" "sleep 5" > "$prefix/node_modules/.bin/playwright"\n'
            '  chmod +x "$prefix/node_modules/.bin/playwright"\n'
            '  printf \'%s\\n\' "module.exports={chromium:{connect:async()=>({newPage:async()=>({setContent:async()=>{}}),close:async()=>{}})}};" > "$prefix/node_modules/playwright/index.js"\n'
            'fi\n'
            "exit 0\n"
        ),
        "npx": "#!/bin/sh\nexit 0\n",
    }
    for name, body in scripts.items():
        path = bin_dir / name
        path.write_text(body, encoding="utf-8")
        path.chmod(0o755)
    return bin_dir


def _stage(tmp_path: Path, name: str) -> tuple[Path, Path, dict]:
    """A fake source checkout, a fake already-provisioned clone, and env."""
    _, clone_name = SETUPS[name]

    src = tmp_path / "src"
    (src / "web").mkdir(parents=True)
    for rel, body in DUMMY.items():
        (src / rel).write_text(body, encoding="utf-8")
    # The setup script refuses a source checkout that does not carry its own
    # loop wrapper (it reads the clone origin from there, never the cwd).
    (src / "scripts").mkdir()
    shutil.copy2(REPO / "scripts" / WRAPPERS[name], src / "scripts" / WRAPPERS[name])
    (src / "web" / "package.json").write_text(
        '{"devDependencies":{"@playwright/test":"^1.58.2"}}\n',
        encoding="utf-8",
    )

    root = tmp_path / "weekend"
    clone = root / clone_name
    (clone / ".git").mkdir(parents=True)
    (clone / "web").mkdir()
    (clone / "config").mkdir()
    (clone / "requirements.txt").write_text("", encoding="utf-8")
    shutil.copy(REPO / "config" / PLISTS[name], clone / "config" / PLISTS[name])

    # The venv the toolchain check and the pip lines address. Per-loop.
    venv_name = {
        "reliability": "venv-reliability",
        "testing": "venv-testing",
        "ci-performance": "venv-ci-performance",
        "documentation": "venv-documentation",
    }[name]
    venv_bin = root / venv_name / "bin"
    venv_bin.mkdir(parents=True)
    for tool in ("python", "pip"):
        exe = venv_bin / tool
        exe.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        exe.chmod(0o755)

    (root / ".env").write_text("PUSHOVER_USER=dummy\nPUSHOVER_TOKEN=dummy\n", encoding="utf-8")

    home = tmp_path / "home"
    home.mkdir()
    env = {
        "PATH": f"{_stub_bin(tmp_path)}:/usr/bin:/bin:/usr/sbin:/sbin",
        "HOME": str(home),
        "RADON_WEEKEND_ROOT": str(root),
        "RADON_WEEKEND_SRC_REPO": str(src),
    }
    return src, clone, env


def _run(name: str, env: dict, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [BASH, str(SETUPS[name][0])],
        env=env,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=120,
    )


@pytest.mark.parametrize("name", sorted(SETUPS))
class TestRunnerEnvProvisioning:
    def test_setup_copies_the_env_files_into_the_clone(self, name, tmp_path):
        src, clone, env = _stage(tmp_path, name)

        proc = _run(name, env, tmp_path)

        rel = "web/.env"
        dst = clone / rel
        assert dst.is_file(), (
            f"{name}: {rel} was never provisioned into the runner clone, so the "
            "Next dev server cannot boot and the nightly agent cannot do the "
            "browser verification CLAUDE.md requires.\n"
            f"{proc.stdout}\n{proc.stderr}"
        )
        assert dst.read_text(encoding="utf-8") == DUMMY[rel]
        assert oct(dst.stat().st_mode & 0o777) == "0o600", (
            f"{name}: {rel} holds secrets and must land 0600"
        )

    def test_setup_does_not_provision_the_root_env(self, name, tmp_path):
        """The root .env must stay OUT of the runner clone.

        A copy would put IB_FLEX_TOKEN in one more place. This says nothing
        about pytest: web/.env alone already reaches it through the producers'
        import-time `load_dotenv(web/.env)` (see the module docstring, T-317).
        """
        src, clone, env = _stage(tmp_path, name)

        proc = _run(name, env, tmp_path)

        for rel in (".env", ".env.ib-mode"):
            assert not (clone / rel).exists(), (
                f"{name}: {rel} was copied into the runner clone. Only web/.env "
                "is provisioned; see the comment above provision_env_file.\n"
                f"{proc.stdout}\n{proc.stderr}"
            )

    def test_setup_reports_a_missing_source_env_file(self, name, tmp_path):
        src, clone, env = _stage(tmp_path, name)
        (src / "web" / ".env").unlink()

        proc = _run(name, env, tmp_path)
        out = proc.stdout + proc.stderr

        assert "MISSING" in out and "web/.env" in out, (
            f"{name}: the toolchain block said nothing about the absent "
            f"web/.env:\n{out}"
        )
        # Usable on a fresh machine: warn, do not hard-fail.
        assert proc.returncode == 0, out
        assert not (clone / "web" / ".env").exists()
        # It must say what it did rather than skip in silence.
        assert "web/.env" in out.split("[2/4]", 1)[-1]

    def test_setup_does_not_silently_clobber_a_newer_clone_copy(self, name, tmp_path):
        src, clone, env = _stage(tmp_path, name)
        newer = "NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY=pk_test_rotated\n"
        (clone / "web" / ".env").write_text(newer, encoding="utf-8")
        # `-nt` compares whole seconds, so make the gap unambiguous.
        stamp = time.time() + 120
        os.utime(clone / "web" / ".env", (stamp, stamp))

        proc = _run(name, env, tmp_path)
        out = proc.stdout + proc.stderr

        assert (clone / "web" / ".env").read_text(encoding="utf-8") == newer, (
            f"{name}: an older source copy overwrote a newer clone copy:\n{out}"
        )
        assert "web/.env" in out.split("[2/4]", 1)[-1], out


class TestSetupLockHygiene:
    def test_case12_dead_shared_parent_is_swept(self, tmp_path):
        src, clone, env = _stage(tmp_path, "testing")
        root = Path(env["RADON_WEEKEND_ROOT"])
        shared = root / ".weekend-runner.lock"
        shared.write_text("999999\n", encoding="utf-8")
        proc = _run("testing", env, tmp_path)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert not shared.exists()
        preserved = list((root / ".stale-locks").glob("shared.weekend-runner.lock.999999.*"))
        assert preserved, proc.stdout + proc.stderr
        assert "ok  no shared-parent lock" in proc.stdout

    def test_case12_live_shared_parent_exits_1(self, tmp_path):
        src, clone, env = _stage(tmp_path, "testing")
        root = Path(env["RADON_WEEKEND_ROOT"])
        shared = root / ".weekend-runner.lock"
        shared.write_text(f"{os.getpid()}\n", encoding="utf-8")
        proc = _run("testing", env, tmp_path)
        assert proc.returncode == 1, proc.stdout + proc.stderr
        assert shared.exists()
        assert str(os.getpid()) in proc.stdout + proc.stderr

    def test_case12_dead_clone_lock_is_reclaimed(self, tmp_path):
        src, clone, env = _stage(tmp_path, "testing")
        lock = clone / ".weekend-runner.lock"
        lock.mkdir()
        (lock / "pid").write_text("999999\n", encoding="utf-8")
        proc = _run("testing", env, tmp_path)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert "moved stale lock" in proc.stdout
        assert "999999" in proc.stdout


class TestSetupBrowserHostSymlinkRefusal:
    @pytest.mark.parametrize("name", ("testing", "reliability"))
    def test_symlinked_browser_host_dir_is_refused(self, name, tmp_path):
        src, clone, env = _stage(tmp_path, name)
        home = Path(env["HOME"])
        outside = tmp_path / "outside-browser-host"
        outside.mkdir()
        planted = home / ".radon" / "agent-cli"
        planted.mkdir(parents=True)
        (planted / "browser-host").symlink_to(outside)
        proc = _run(name, env, tmp_path)
        out = proc.stdout + proc.stderr
        assert proc.returncode != 0, out
        assert "symlink" in out
        assert not (outside / "node_modules").exists()
        assert list(outside.iterdir()) == []

    @pytest.mark.parametrize("name", ("testing", "reliability"))
    def test_symlinked_agent_cli_parent_is_refused(self, name, tmp_path):
        src, clone, env = _stage(tmp_path, name)
        home = Path(env["HOME"])
        outside = tmp_path / "outside-agent-cli"
        outside.mkdir()
        radon = home / ".radon"
        radon.mkdir()
        (radon / "agent-cli").symlink_to(outside)
        proc = _run(name, env, tmp_path)
        out = proc.stdout + proc.stderr
        assert proc.returncode != 0, out
        assert "symlink" in out
        assert not (outside / "browser-host").exists()


def _web_env_loaders() -> list[Path]:
    """Every scripts/**/*.py producer whose import-time load_dotenv reads web/.env."""
    loaders = []
    for path in (REPO / "scripts").rglob("*.py"):
        if "tests" in path.parts or "node_modules" in path.parts:
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            if "load_dotenv(" in line and "web" in line and ".env" in line:
                loaders.append(path)
                break
    return sorted(loaders)


class TestPytestReadsTheProvisionedWebEnv:
    """The provisioned web/.env reaches pytest, and the gate must not care.

    T-317: `14065b74` provisioned web/.env into the runner clone on the premise
    that only Next reads it. 50 producers `load_dotenv(web/.env)` at import,
    so the clone's TURSO credentials landed in `os.environ`, `flex_embargo`
    saw a configured-but-unreadable durable store, and 22 CI-green tests red
    with `FlexTokenLocked`. The conftest fixture is the host-independence.
    """

    def test_producers_load_web_env_at_import(self):
        loaders = {p.relative_to(REPO).as_posix() for p in _web_env_loaders()}
        assert "scripts/cash_flow_sync.py" in loaders, sorted(loaders)
        assert len(loaders) >= 22, (
            f"{len(loaders)} producers load web/.env at import; the T-317 "
            f"finding counted 22, and a drop below that means a loader moved "
            f"or the scan broke: {sorted(loaders)}"
        )

    def test_the_gate_strips_the_clones_turso_credentials(self):
        """Holds on a provisioned clone ONLY because of
        `scripts/conftest.py::_strip_turso_credentials` (hoisted, T-368)."""
        import cash_flow_sync  # noqa: F401  a loader, imported inside the test on purpose

        assert "TURSO_DB_URL" not in os.environ
        assert "TURSO_AUTH_TOKEN" not in os.environ


# Dummy "production" and "scoped" values. Never a real credential.
PROD_TURSO = "prod-turso-rw-dummy-7f3a"
PROD_UW = "prod-uw-dummy-91c2"
SCOPED_TURSO = "scoped-turso-ro-dummy-4b1e"
SCOPED_UW = "scoped-uw-dummy-d05a"
PROD_WEB_ENV = (
    "NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY=pk_test_dummy\n"
    f"TURSO_AUTH_TOKEN={PROD_TURSO}\n"
    "TURSO_DB_URL=libsql://dummy.invalid\n"
    f"export UW_TOKEN='{PROD_UW}'\n"
)


def _env_keys(path: Path) -> dict:
    out = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.removeprefix("export ")
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip().strip("'\"")
    return out


class TestTestingCloneScopedCredentials:
    """The testing clone never keeps the operator's production Turso/UW keys.

    setup_testing_weekend.sh copies web/.env into the clone, then replaces
    TURSO_AUTH_TOKEN and UW_TOKEN with the operator-minted least-privilege
    values from $WEEKEND_ROOT/.env.testing-scoped, or strips them when that
    file or a key is absent. No value is ever echoed.
    """

    def _stage_prod(self, tmp_path: Path):
        src, clone, env = _stage(tmp_path, "testing")
        (src / "web" / ".env").write_text(PROD_WEB_ENV, encoding="utf-8")
        scoped = Path(env["RADON_WEEKEND_ROOT"]) / ".env.testing-scoped"
        return src, clone, env, scoped

    @staticmethod
    def _assert_no_value_echoed(proc: subprocess.CompletedProcess) -> None:
        out = proc.stdout + proc.stderr
        for value in (PROD_TURSO, PROD_UW, SCOPED_TURSO, SCOPED_UW):
            assert value not in out, "a credential value was echoed"

    def test_scoped_values_replace_the_production_keys(self, tmp_path):
        src, clone, env, scoped = self._stage_prod(tmp_path)
        scoped.write_text(
            f"TURSO_AUTH_TOKEN={SCOPED_TURSO}\nUW_TOKEN=\"{SCOPED_UW}\"\n",
            encoding="utf-8",
        )
        scoped.chmod(0o600)

        proc = _run("testing", env, tmp_path)

        assert proc.returncode == 0, proc.stdout + proc.stderr
        dst = clone / "web" / ".env"
        keys = _env_keys(dst)
        assert keys["TURSO_AUTH_TOKEN"] == SCOPED_TURSO
        assert keys["UW_TOKEN"] == SCOPED_UW
        # Everything else in the operator copy is untouched.
        assert keys["NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY"] == "pk_test_dummy"
        assert keys["TURSO_DB_URL"] == "libsql://dummy.invalid"
        text = dst.read_text(encoding="utf-8")
        assert PROD_TURSO not in text and PROD_UW not in text
        assert text.count("TURSO_AUTH_TOKEN=") == 1
        assert text.count("UW_TOKEN=") == 1
        assert oct(dst.stat().st_mode & 0o777) == "0o600"
        # The source checkout is never rewritten.
        assert (src / "web" / ".env").read_text(encoding="utf-8") == PROD_WEB_ENV
        self._assert_no_value_echoed(proc)

    def test_absent_scoped_file_strips_both_keys(self, tmp_path):
        src, clone, env, scoped = self._stage_prod(tmp_path)

        proc = _run("testing", env, tmp_path)
        out = proc.stdout + proc.stderr

        assert proc.returncode == 0, out
        dst = clone / "web" / ".env"
        keys = _env_keys(dst)
        assert "TURSO_AUTH_TOKEN" not in keys
        assert "UW_TOKEN" not in keys
        assert keys["NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY"] == "pk_test_dummy"
        assert oct(dst.stat().st_mode & 0o777) == "0o600"
        missing = [ln for ln in out.splitlines() if "MISSING" in ln and str(scoped) in ln]
        assert missing, out
        self._assert_no_value_echoed(proc)

    def test_empty_scoped_key_is_stripped_not_kept(self, tmp_path):
        src, clone, env, scoped = self._stage_prod(tmp_path)
        scoped.write_text(f"TURSO_AUTH_TOKEN={SCOPED_TURSO}\nUW_TOKEN=\n", encoding="utf-8")

        proc = _run("testing", env, tmp_path)
        out = proc.stdout + proc.stderr

        keys = _env_keys(clone / "web" / ".env")
        assert keys["TURSO_AUTH_TOKEN"] == SCOPED_TURSO
        assert "UW_TOKEN" not in keys
        assert any("MISSING" in ln and "UW_TOKEN" in ln for ln in out.splitlines()), out
        self._assert_no_value_echoed(proc)

    def test_a_kept_newer_clone_copy_is_still_scoped(self, tmp_path):
        """The 'clone copy is newer' early return must not keep prod keys."""
        src, clone, env, scoped = self._stage_prod(tmp_path)
        dst = clone / "web" / ".env"
        dst.write_text(PROD_WEB_ENV, encoding="utf-8")
        stamp = time.time() + 120
        os.utime(dst, (stamp, stamp))

        proc = _run("testing", env, tmp_path)

        text = dst.read_text(encoding="utf-8")
        assert PROD_TURSO not in text and PROD_UW not in text, proc.stdout
        self._assert_no_value_echoed(proc)

    def _planted_link(self, tmp_path: Path, clone: Path, rel: str) -> Path:
        """A clone-planted link to an operator file outside the sandbox."""
        victim_dir = tmp_path / "operator"
        victim_dir.mkdir()
        victim = victim_dir / ".env"
        victim.write_text(PROD_WEB_ENV, encoding="utf-8")
        # Newer than the source so the "kept" branch would follow it.
        stamp = time.time() + 120
        os.utime(victim, (stamp, stamp))
        if rel == "web":
            shutil.rmtree(clone / "web")
            (clone / "web").symlink_to(victim_dir)
        else:
            (clone / "web" / ".env").symlink_to(victim)
        return victim

    @pytest.mark.parametrize("rel", ["web/.env", "web"])
    def test_a_symlinked_clone_env_is_refused_not_written_through(self, tmp_path, rel):
        src, clone, env, scoped = self._stage_prod(tmp_path)
        victim = self._planted_link(tmp_path, clone, rel)
        before = victim.stat()

        proc = _run("testing", env, tmp_path)

        assert proc.returncode != 0, proc.stdout + proc.stderr
        assert "REFUSING" in proc.stdout + proc.stderr
        assert victim.read_text(encoding="utf-8") == PROD_WEB_ENV
        after = victim.stat()
        assert (after.st_mode, after.st_mtime) == (before.st_mode, before.st_mtime)
        self._assert_no_value_echoed(proc)

    def test_scoping_replaces_the_file_instead_of_writing_into_it(self):
        text = (REPO / "scripts" / "setup_testing_weekend.sh").read_text(encoding="utf-8")
        body = text.split("scope_clone_credentials() {", 1)[1].split("\n}\n", 1)[0]
        assert 'cat "$tmp" > "$dst"' not in body
        assert 'mv -f -- "$tmp" "$dst"' in body

    def test_wrapper_never_recopies_web_env(self):
        """Only setup provisions web/.env; no phase may restore the prod copy."""
        text = (REPO / "scripts" / "testing_weekend.sh").read_text(encoding="utf-8")
        for line in text.splitlines():
            code = line.split("#", 1)[0]
            if "web/.env" in code:
                assert not any(
                    tok in code.split() for tok in ("cp", "install", "rsync", "ln", "ditto")
                ), f"testing_weekend.sh re-copies web/.env: {line.strip()}"
            assert "SRC_REPO" not in code, line
