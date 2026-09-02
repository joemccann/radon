"""REL-180 (R-477, R-503..R-508): the five loop launchers cannot hang, collide,
leak or go silently quiet.

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

REPO = Path(__file__).resolve().parents[2]
BASH = "/bin/bash"
WRAPPERS = {
    "reliability": "reliability_weekend.sh",
    "testing": "testing_weekend.sh",
    "ci-performance": "ci_performance_nightly.sh",
    "documentation": "documentation_nightly.sh",
    "security": "security_nightly.sh",
}
CANONICAL = {
    "reliability": "radon",
    "testing": "radon-testing",
    "ci-performance": "radon-ci-performance",
    "documentation": "radon-documentation",
    "security": "radon-security",
}
LOOP_MARKERS = {loop: f".radon-{loop}-runner" for loop in WRAPPERS}
PLISTS = {loop: REPO / "config" / f"com.radon.{loop}-daily.plist" for loop in WRAPPERS}
TAIL_POSTERS = ["reliability", "testing", "ci-performance", "documentation"]


def _plist(loop: str) -> dict:
    return plistlib.loads(PLISTS[loop].read_bytes())


# --- R-477: the pre-lock fetch is bounded -----------------------------------


class TestPlistFetchIsBounded:
    @pytest.mark.parametrize("loop", sorted(WRAPPERS))
    def test_the_pre_lock_fetch_runs_under_a_timeout(self, loop: str) -> None:
        program = " ".join(_plist(loop)["ProgramArguments"])
        assert "fetch" in program, program
        # `timeout` must govern the fetch: present, and BEFORE the fetch verb.
        assert "timeout" in program and program.index("timeout") < program.index("fetch"), (
            f"{loop}: the plist runs `git fetch` before the wrapper's lock, traps and "
            f"dead-man with no bound: {program!r}"
        )
        assert "ConnectTimeout" in program and "ServerAliveInterval" in program, program


# --- R-503: the five fires are staggered ------------------------------------


class TestStaggeredFires:
    def test_five_calendar_slots_are_pairwise_distinct(self) -> None:
        slots = {}
        for loop in WRAPPERS:
            cal = _plist(loop)["StartCalendarInterval"]
            slots[loop] = (int(cal["Hour"]), int(cal["Minute"]))
        assert len(set(slots.values())) == len(slots), slots
        assert all(hour == 0 for hour, _ in slots.values()), slots
        # The reliability loop keeps the documented 00:00; the others follow.
        assert slots["reliability"] == (0, 0)
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
    # python3: the notifier is stubbed (and logged); the redactor runs for real.
    _executable(
        bin_dir / "python3",
        "#!/bin/sh\n"
        'case "$*" in\n'
        f'  *weekend_redact.py*) exec "{sys.executable}" "$@" ;;\n'
        "esac\n"
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
        f'    if [ -f "$cfg" ]; then cat "$cfg" >> "{py_log}"; fi\n'
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
            markers=[".radon-weekend-runner", LOOP_MARKERS["testing"]],
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
        result = _run("testing", repo, bin_dir, home)
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
    @pytest.mark.parametrize("loop", ["reliability", "testing", "ci-performance", "documentation"])
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

    @pytest.mark.parametrize("loop", ["reliability", "testing", "ci-performance", "documentation"])
    def test_the_loop_marker_admits(self, loop: str, tmp_path: Path) -> None:
        home = tmp_path / "home"
        home.mkdir()
        repo = _clone(tmp_path, "own-clone", markers=[".radon-weekend-runner", LOOP_MARKERS[loop]])
        started = tmp_path / "claude-started"
        bin_dir, _, _ = _stub_bin(tmp_path, claude_body=f"#!/bin/sh\ntouch {started}\nexit 0\n")
        result = _run(loop, repo, bin_dir, home)
        assert started.exists(), (result.returncode, result.stderr[-600:])

    @pytest.mark.parametrize("loop", ["reliability", "testing", "ci-performance", "documentation"])
    def test_the_canonical_clone_self_stamps_its_marker(self, loop: str, tmp_path: Path) -> None:
        """Installed clones predate the loop marker: the canonical path is
        admitted once and stamps itself, so a merge cannot silence a loop
        until the operator re-runs setup. A stray path never matches."""
        home = tmp_path / "home"
        (home / "radon-weekend").mkdir(parents=True)
        repo = _clone(home / "radon-weekend", CANONICAL[loop], markers=[".radon-weekend-runner"])
        started = tmp_path / "claude-started"
        bin_dir, _, _ = _stub_bin(tmp_path, claude_body=f"#!/bin/sh\ntouch {started}\nexit 0\n")
        result = _run(loop, repo, bin_dir, home)
        assert started.exists(), (result.returncode, result.stderr[-600:])
        assert (repo / LOOP_MARKERS[loop]).exists()

    @pytest.mark.parametrize("loop", sorted(WRAPPERS))
    def test_setup_stamps_the_loop_marker(self, loop: str) -> None:
        setup = {
            "reliability": "setup_reliability_weekend.sh",
            "testing": "setup_testing_weekend.sh",
            "ci-performance": "setup_ci_performance.sh",
            "documentation": "setup_documentation_nightly.sh",
            "security": "setup_security_nightly.sh",
        }[loop]
        text = "\n".join(
            line for line in (REPO / "scripts" / setup).read_text(encoding="utf-8").splitlines()
            if not line.lstrip().startswith("#")
        )
        assert LOOP_MARKERS[loop] in text, f"{setup} never stamps {LOOP_MARKERS[loop]}"


# --- R-505: the posted tail is redacted -------------------------------------


class TestRunLogTailIsRedacted:
    @pytest.mark.parametrize("loop", TAIL_POSTERS)
    def test_a_secret_in_the_agent_tail_never_reaches_the_issue(self, loop: str, tmp_path: Path) -> None:
        home = tmp_path / "home"
        home.mkdir()
        repo = _clone(tmp_path, "own-clone", markers=[".radon-weekend-runner", LOOP_MARKERS[loop]])
        (repo / "web").mkdir()
        (repo / "web" / ".env").write_text("UW_TOKEN=abc123secretvalue\nANTHROPIC_API_KEY=sk-ant-zzz999888\n")
        # The wrapper's report() runs the redactor from the CLONE's scripts/.
        (repo / "scripts" / "weekend_redact.py").write_bytes((REPO / "scripts" / "weekend_redact.py").read_bytes())
        bin_dir, gh_log, _ = _stub_bin(
            tmp_path,
            claude_body="#!/bin/sh\necho 'token dump UW_TOKEN=abc123secretvalue and Bearer sk-ant-zzz999888'\nexit 0\n",
        )
        result = _run(loop, repo, bin_dir, home)
        calls = gh_log.read_text(encoding="utf-8") if gh_log.exists() else ""
        assert "issue comment" in calls, (result.returncode, result.stderr[-400:])
        assert "abc123secretvalue" not in calls, "the raw agent tail reached the public issue"
        assert "sk-ant-zzz999888" not in calls
        assert "**Issue discovered**" not in calls
        assert "**audit**" in calls or "**OK**" in calls


class TestRedactor:
    def test_scrubs_env_values_key_patterns_and_bearers(self, tmp_path: Path) -> None:
        sys.path.insert(0, str(REPO / "scripts"))
        import weekend_redact

        # Built at runtime: a full `UW_TOKEN='<high-entropy>'` assignment
        # spelled in the source trips gitleaks' generic-api-key rule over the
        # full history, and this file is public. Same treatment as the TWS
        # fixtures in cloud/tests/test_gitleaks_policy.py.
        fake_token = "tok-" + "1234567890"
        (tmp_path / "web").mkdir()
        (tmp_path / "web" / ".env").write_text(
            "UW_" + "TOKEN='" + fake_token + "'\nSHORT=ab\n"
        )
        values = weekend_redact.env_values(tmp_path)
        assert fake_token in values and "ab" not in values
        out = weekend_redact.redact(
            f"x {fake_token} y CLERK_SECRET_KEY=sk_live_abcdef "
            "Authorization: Bearer eyJhbGci.zz TURSO_AUTH_TOKEN: qqq",
            values,
        )
        assert fake_token not in out and "sk_live_abcdef" not in out
        assert "eyJhbGci" not in out and "qqq" not in out
        assert out.count("[REDACTED]") >= 4


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
        [".weekend-runner.lock/pid", ".radon-security-runner", ".radon-documentation-runner", ".radon-reliability-runner"],
    )
    def test_ignored(self, path: str) -> None:
        result = subprocess.run(["git", "check-ignore", "-q", path], cwd=REPO, check=False)
        assert result.returncode == 0, f"{path} is not gitignored"


# --- R-508: the notifier says why it did not page ---------------------------


class TestNotifierSaysWhyItSkipped:
    ARGV = ["--loop", "reliability", "--phase", "audit", "--status", "OK"]

    def test_missing_credentials_are_named_on_stderr(self, monkeypatch, capsys) -> None:
        monkeypatch.delenv("PUSHOVER_USER", raising=False)
        monkeypatch.delenv("PUSHOVER_TOKEN", raising=False)
        assert weekend_notify.main([*self.ARGV, "--env-file", "/nonexistent/.env"]) == 0
        err = capsys.readouterr().err
        assert "pushover skipped" in err, err
        assert "/nonexistent/.env" in err
