"""REL-180 (R-477, R-503..R-508): the loop launchers cannot hang, collide,
leak or go silently quiet. Only the security and DeepSec wrappers remain; the
other nightly loops run through scripts/runner/run_loop.sh.

Every case here either parses the artifact (plists, .gitignore) or RUNS the
wrapper against a staged clone with stub `gh` / `claude` / `git` binaries, so
a rail is proven at the wire rather than by string presence.
"""
from __future__ import annotations

import os
import plistlib
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import weekend_notify  # noqa: E402

# 2026-09-06: these suites stub `claude` and are about wrapper behaviour around the agent, not about which provider runs it. Pin a claude rung so the provider ladder is not what decides the outcome; the ladder itself is covered by test_provider_failover.py.
CLAUDE_RUNG_LADDER = "claude:claude-fable-5[1m]"

REPO = Path(__file__).resolve().parents[2]
BASH = "/bin/bash"
WRAPPERS = {
    "security": "security_nightly.sh",
}
LOOP_MARKERS = {loop: f".radon-{loop}-runner" for loop in WRAPPERS}
PLISTS = {loop: REPO / "config" / f"com.radon.{loop}-daily.plist" for loop in WRAPPERS}
# The DeepSec loop (2026-09-18) shares setup_security_nightly.sh, so it is not
# in WRAPPERS/SETUPS; its launcher still has to obey the fetch-timeout and
# stagger contracts.
PLISTS["security-deepsec"] = REPO / "config" / "com.radon.security-deepsec.plist"


def _plist(loop: str) -> dict:
    return plistlib.loads(PLISTS[loop].read_bytes())


# --- R-477: the pre-lock fetch is bounded -----------------------------------


class TestPlistFetchIsBounded:
    @pytest.mark.parametrize("loop", sorted(PLISTS))
    def test_the_pre_lock_fetch_runs_under_a_timeout(self, loop: str) -> None:
        program = " ".join(_plist(loop)["ProgramArguments"])
        assert "fetch" in program, program
        # `timeout` must govern the fetch: present, and BEFORE the fetch verb.
        assert "timeout" in program and program.index("timeout") < program.index("fetch"), (
            f"{loop}: the plist runs `git fetch` before the wrapper's lock, traps and "
            f"dead-man with no bound: {program!r}"
        )
        assert "ConnectTimeout" in program and "ServerAliveInterval" in program, program

    @staticmethod
    def _run_program(loop: str, tmp_path: Path, *, tools: dict[str, str]) -> tuple[int, str]:
        """Run the plist's shell program with PATH limited to `tools` stubs."""
        clone = tmp_path / "clone"
        (clone / "scripts").mkdir(parents=True)
        log = tmp_path / "calls.log"
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        for name in ("dirname", "cat"):
            (bin_dir / name).symlink_to(shutil.which(name))
        _executable(bin_dir / "git", f'#!/bin/sh\necho "git $*" >> "{log}"\nexit 0\n')
        for name, body in tools.items():
            _executable(bin_dir / name, body.replace("__LOG__", str(log)))
        args = _plist(loop)["ProgramArguments"]
        script = args[2].replace("__WEEKEND_REPO__", str(clone)).replace("__DEEPSEC_REPO__", str(clone))
        wrapper = re.search(r'exec /bin/bash "\$C/scripts/([a-z_]+\.sh)"', script).group(1)
        _executable(clone / "scripts" / wrapper, f'#!/bin/sh\necho "wrapper" >> "{log}"\nexit 0\n')
        proc = subprocess.run(
            [BASH, "-c", script], env={"PATH": str(bin_dir), "HOME": str(tmp_path)},
            capture_output=True, text=True, timeout=30,
        )
        return proc.returncode, log.read_text() if log.exists() else ""

    @pytest.mark.parametrize("loop", sorted(PLISTS))
    def test_the_pre_lock_fetch_falls_back_to_gtimeout(self, loop: str, tmp_path: Path) -> None:
        # Homebrew coreutils ships only the g-prefixed binary on the plist PATH.
        rc, calls = self._run_program(loop, tmp_path, tools={
            "gtimeout": '#!/bin/sh\necho "gtimeout $*" >> "__LOG__"\nshift 3\nexec "$@"\n',
        })
        assert rc == 0, calls
        assert re.search(r"^gtimeout .*git .* fetch", calls, re.M), (
            f"{loop}: with only gtimeout installed the pre-lock fetch ran unbounded: {calls!r}"
        )

    @pytest.mark.parametrize("loop", sorted(PLISTS))
    def test_the_pre_lock_fetch_fails_closed_without_a_timeout(self, loop: str, tmp_path: Path) -> None:
        rc, calls = self._run_program(loop, tmp_path, tools={})
        assert rc != 0, f"{loop}: ran with no timeout binary: {calls!r}"
        assert "fetch" not in calls and "wrapper" not in calls, calls


# --- R-503: the fires are staggered -----------------------------------------


class TestStaggeredFires:
    def test_calendar_slots_are_pairwise_distinct(self) -> None:
        slots = {}
        for loop in PLISTS:
            cal = _plist(loop)["StartCalendarInterval"]
            slots[loop] = (int(cal["Hour"]), int(cal["Minute"]))
        assert len(set(slots.values())) == len(slots), slots
        assert all(hour == 0 for hour, _ in slots.values()), slots
        assert all(minute % 10 == 0 for _, minute in slots.values()), slots


# --- staged clone + stub binaries -------------------------------------------


def _executable(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)


def _clone(tmp_path: Path, name: str, *, markers: list[str]) -> Path:
    repo = tmp_path / name
    (repo / "scripts").mkdir(parents=True)
    for marker in markers:
        (repo / marker).write_text("", encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    return repo


def _stub_bin(tmp_path: Path, *, claude_body: str = "#!/bin/sh\nexit 0\n") -> tuple[Path, Path, Path]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    gh_log = tmp_path / "gh.log"
    py_log = tmp_path / "py.log"
    _executable(
        bin_dir / "gh",
        "#!/bin/sh\n"
        f'printf "%s\\n" "$*" >> "{gh_log}"\n'
        'if [ "$1 $2" = "issue list" ]; then echo 4242; fi\n'
        "exit 0\n",
    )
    # python3: the notifier is stubbed (and logged).
    _executable(
        bin_dir / "python3",
        "#!/bin/sh\n"
        f'printf "%s\\n" "$*" >> "{py_log}"\n'
        "exit 0\n",
    )
    _executable(bin_dir / "claude", claude_body)
    _executable(bin_dir / "git", "#!/bin/sh\nexit 0\n")
    _executable(
        bin_dir / "curl",
        "#!/bin/bash\n"
        f'printf "%s\\n" "$*" >> "{py_log}"\n'
        "i=1\n"
        'while [ "$i" -le "$#" ]; do\n'
        '  eval "arg=\\${$i}"\n'
        '  if [ "$arg" = "--config" ] || [ "$arg" = "-K" ]; then\n'
        "    i=$((i + 1))\n"
        '    eval "cfg=\\${$i}"\n'
        f'    if [ "$cfg" = "-" ]; then cat >> "{py_log}"\n'
        f'    elif [ -f "$cfg" ]; then cat "$cfg" >> "{py_log}"; fi\n'
        "  fi\n"
        "  i=$((i + 1))\n"
        "done\n"
        "exit 0\n",
    )
    return bin_dir, gh_log, py_log


_HOST_PUSHOVER_KEYS = ("PUSHOVER_USER", "PUSHOVER_TOKEN")


def _run(loop: str, repo: Path, bin_dir: Path, home: Path, *, timeout: int = 120) -> subprocess.CompletedProcess:
    src = REPO / "scripts" / WRAPPERS[loop]
    dest = repo / "scripts" / WRAPPERS[loop]
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest)
    dest.chmod(dest.stat().st_mode | 0o100)
    curl_stub = bin_dir / "curl"
    dest.write_text(
        dest.read_text(encoding="utf-8").replace("/usr/bin/curl", str(curl_stub)),
        encoding="utf-8",
    )
    env = {k: v for k, v in os.environ.items() if k not in _HOST_PUSHOVER_KEYS}
    env["PATH"] = f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '/usr/bin:/bin')}"
    env["RADON_WEEKEND_REPO"] = str(repo)
    env["RADON_WEEKEND_PROVIDER_LADDER"] = CLAUDE_RUNG_LADDER
    env["HOME"] = str(home)
    for key in _HOST_PUSHOVER_KEYS:
        env.pop(key, None)
    return subprocess.run(
        [BASH, str(dest), "audit"],
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


class TestRunDoesNotFireHostPushover:
    def test_host_pushover_env_is_dropped_and_curl_is_the_stub(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PUSHOVER_USER", "host-user")
        monkeypatch.setenv("PUSHOVER_TOKEN", "host-token")
        home = tmp_path / "home"
        home.mkdir()
        repo = _clone(
            tmp_path,
            "own-clone",
            markers=[".radon-weekend-runner", LOOP_MARKERS["security"]],
        )
        (tmp_path / ".env").write_text(
            "PUSHOVER_USER=test-user\nPUSHOVER_TOKEN=test-token\n",
            encoding="utf-8",
        )
        started = tmp_path / "claude-started"
        bin_dir, _gh_log, py_log = _stub_bin(
            tmp_path,
            claude_body=f"#!/bin/sh\ntouch {started}\nexit 0\n",
        )
        result = _run("security", repo, bin_dir, home)
        assert started.exists(), (result.returncode, result.stderr[-600:])
        pages = py_log.read_text(encoding="utf-8") if py_log.exists() else ""
        assert "host-token" not in pages, pages
        assert "host-user" not in pages, pages
        assert "pushover.net" in pages, pages
        assert "test-token" in pages, pages
        argv_line = pages.splitlines()[0]
        assert argv_line.split()[0] == "-q", argv_line
        src = Path(__file__).read_text(encoding="utf-8")
        run = src[src.index("def _run(") : src.index("class TestLoopMarkerGuard")]
        assert 'replace("/usr/bin/curl"' in run
        assert "[BASH, str(dest)" in run
        assert "**os.environ" not in run


# --- R-504: every loop needs its OWN marker ---------------------------------


class TestLoopMarkerGuard:
    @pytest.mark.parametrize("loop", sorted(WRAPPERS))
    def test_the_shared_marker_alone_is_refused(self, loop: str, tmp_path: Path) -> None:
        home = tmp_path / "home"
        home.mkdir()
        repo = _clone(tmp_path, "sibling-clone", markers=[".radon-weekend-runner"])
        started = tmp_path / "claude-started"
        bin_dir, gh_log, _ = _stub_bin(tmp_path, claude_body=f"#!/bin/sh\ntouch {started}\nexit 0\n")
        result = _run(loop, repo, bin_dir, home)
        assert result.returncode == 2, (result.returncode, result.stderr[-400:])
        assert "REFUSING" in result.stderr and LOOP_MARKERS[loop] in result.stderr, result.stderr[-400:]
        assert not started.exists(), "the agent ran inside a sibling loop's clone"
        assert "REFUSED" in (gh_log.read_text() if gh_log.exists() else ""), "the refusal never reached the dead-man"

    @pytest.mark.parametrize("loop", sorted(WRAPPERS))
    def test_the_loop_marker_admits(self, loop: str, tmp_path: Path) -> None:
        home = tmp_path / "home"
        home.mkdir()
        repo = _clone(tmp_path, "own-clone", markers=[".radon-weekend-runner", LOOP_MARKERS[loop]])
        started = tmp_path / "claude-started"
        bin_dir, _, _ = _stub_bin(tmp_path, claude_body=f"#!/bin/sh\ntouch {started}\nexit 0\n")
        result = _run(loop, repo, bin_dir, home)
        assert started.exists(), (result.returncode, result.stderr[-600:])

    @pytest.mark.parametrize("loop", sorted(WRAPPERS))
    def test_setup_stamps_the_loop_marker(self, loop: str) -> None:
        setup = SETUPS[loop]
        text = "\n".join(
            line for line in (REPO / "scripts" / setup).read_text(encoding="utf-8").splitlines()
            if not line.lstrip().startswith("#")
        )
        assert LOOP_MARKERS[loop] in text, f"{setup} never stamps {LOOP_MARKERS[loop]}"


# --- the setup scripts clone the origin of THEIR OWN checkout ---------------

SETUPS = {
    "security": "setup_security_nightly.sh",
}
SCRIPT_ORIGIN = "git@example.invalid:radon/script-checkout.git"
CALLER_ORIGIN = "git@example.invalid:someone/unrelated.git"


def _git_repo(path: Path, origin: str) -> Path:
    path.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    subprocess.run(["git", "-C", str(path), "remote", "add", "origin", origin], check=True)
    return path


def _setup_stubs(tmp_path: Path, git_log: Path) -> Path:
    """Real git answers `config` / `rev-parse`; anything that would touch the
    network or a clone is logged and succeeds. Everything else the setup
    scripts probe is a no-op stub so the run aborts only after the clone."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    real_git = shutil.which("git")
    (bin_dir / "git").write_text(
        "#!/bin/sh\n"
        f'printf "%s\\n" "$*" >> "{git_log}"\n'
        'sub="$1"; [ "$1" = "-C" ] && sub="$3"\n'
        'case "$sub" in clone|fetch|checkout|reset|ls-remote) exit 0 ;; esac\n'
        f'exec "{real_git}" "$@"\n'
    )
    for name in ("gh", "claude", "ssh", "bun", "node", "caddy", "plutil", "launchctl"):
        (bin_dir / name).write_text("#!/bin/sh\nexit 0\n")
    for exe in bin_dir.iterdir():
        exe.chmod(0o755)
    return bin_dir


def _run_setup(loop: str, script_repo: Path, cwd: Path, tmp_path: Path, extra_env=None):
    git_log = tmp_path / "git.log"
    bin_dir = _setup_stubs(tmp_path, git_log)
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    env = {
        "PATH": f"{bin_dir}:/usr/bin:/bin",
        "HOME": str(home),
        "RADON_WEEKEND_ROOT": str(tmp_path / "weekend"),
    }
    env.update(extra_env or {})
    result = subprocess.run(
        [BASH, str(script_repo / "scripts" / SETUPS[loop])],
        cwd=cwd, env=env, capture_output=True, text=True, timeout=60,
    )
    log = git_log.read_text() if git_log.exists() else ""
    clones = [line for line in log.splitlines() if line.startswith("clone ")]
    return result, clones


class TestSetupClonesItsOwnOrigin:
    @pytest.mark.parametrize("loop", sorted(SETUPS))
    def test_the_clone_origin_is_the_script_checkout_not_the_cwd(self, loop: str, tmp_path: Path) -> None:
        script_repo = _git_repo(tmp_path / "script-checkout", SCRIPT_ORIGIN)
        (script_repo / "scripts").mkdir()
        for name in (SETUPS[loop], WRAPPERS[loop]):
            shutil.copy2(REPO / "scripts" / name, script_repo / "scripts" / name)
        caller = _git_repo(tmp_path / "unrelated-cwd", CALLER_ORIGIN)
        result, clones = _run_setup(loop, script_repo, caller, tmp_path)
        assert clones, (result.returncode, result.stdout[-400:], result.stderr[-400:])
        assert all(SCRIPT_ORIGIN in line for line in clones), clones
        assert not any(CALLER_ORIGIN in line for line in clones), (
            f"{SETUPS[loop]} cloned the caller's cwd origin: {clones}"
        )

    @pytest.mark.parametrize("loop", sorted(SETUPS))
    def test_a_script_outside_a_radon_checkout_refuses(self, loop: str, tmp_path: Path) -> None:
        stray = tmp_path / "stray"
        (stray / "scripts").mkdir(parents=True)
        shutil.copy2(REPO / "scripts" / SETUPS[loop], stray / "scripts" / SETUPS[loop])
        caller = _git_repo(tmp_path / "unrelated-cwd", CALLER_ORIGIN)
        result, clones = _run_setup(loop, stray, caller, tmp_path)
        assert result.returncode == 2, (result.returncode, result.stdout[-400:], result.stderr[-400:])
        assert "REFUSING" in result.stderr and "Radon checkout" in result.stderr, result.stderr[-400:]
        assert not clones, clones


# --- R-506: the security clone refuses credentials --------------------------


class TestSecurityCloneIsCredentialFree:
    @pytest.mark.parametrize("credential", [".env", "web/.env", ".env.ib-mode"])
    def test_a_credential_file_in_the_security_clone_is_refused(self, credential: str, tmp_path: Path) -> None:
        home = tmp_path / "home"
        home.mkdir()
        repo = _clone(tmp_path, "security-clone", markers=[".radon-weekend-runner", ".radon-security-runner"])
        target = repo / credential
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("UW_TOKEN=x\n")
        started = tmp_path / "claude-started"
        bin_dir, gh_log, _ = _stub_bin(tmp_path, claude_body=f"#!/bin/sh\ntouch {started}\nexit 0\n")
        result = _run("security", repo, bin_dir, home)
        assert result.returncode == 2, (result.returncode, result.stderr[-400:])
        assert "REFUSING" in result.stderr and "credential" in result.stderr.lower(), result.stderr[-400:]
        assert not started.exists()
        assert "REFUSED" in (gh_log.read_text() if gh_log.exists() else "")


# --- R-507: runner artifacts are gitignored ---------------------------------


class TestRunnerArtifactsAreIgnored:
    @pytest.mark.parametrize(
        "path",
        [".weekend-runner.lock/pid", ".radon-security-runner", ".radon-security-deepsec-runner"],
    )
    def test_ignored(self, path: str) -> None:
        result = subprocess.run(["git", "check-ignore", "-q", path], cwd=REPO, check=False)
        assert result.returncode == 0, f"{path} is not gitignored"


# --- R-508: the notifier says why it did not page ---------------------------


class TestNotifierSaysWhyItSkipped:
    ARGV = ["--loop", "security", "--phase", "audit", "--status", "OK"]

    def test_missing_credentials_are_named_on_stderr(self, monkeypatch, capsys) -> None:
        monkeypatch.delenv("PUSHOVER_USER", raising=False)
        monkeypatch.delenv("PUSHOVER_TOKEN", raising=False)
        assert weekend_notify.main([*self.ARGV, "--env-file", "/nonexistent/.env"]) == 0
        err = capsys.readouterr().err
        assert "pushover skipped" in err, err
        assert "/nonexistent/.env" in err
