"""scripts/runner/run_loop.sh: allowlisted PUSHOVER_* load from a Radon dotenv.

Do not edit test_runner_run_loop.py from this suite. Reuse its harness.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

from test_runner_run_loop import rig, PR_URL, _source_tree, _install, _plist  # noqa: F401
from test_runner_run_loop import BASH, REPO


def _home(rig) -> Path:
    return rig.state.parent


def _bot_env(rig) -> Path:
    return _home(rig) / ".radon-runner.env"


def _prompt_text(rig) -> str:
    files = list((rig.state / "logs" / "doc").glob("*.prompt.md"))
    return "".join(p.read_text() for p in files)


def _launchd_text(rig) -> str:
    path = rig.state / "launchd-doc.log"
    return path.read_text() if path.exists() else ""


def _operator_surfaces(rig, proc) -> str:
    return "\n".join(
        [
            rig.log(),
            proc.stdout or "",
            proc.stderr or "",
            _launchd_text(rig),
            _prompt_text(rig),
        ]
    )


def _all_stub_env(rig) -> str:
    return "".join(p.read_text() for p in rig.calls.glob("*.env") if p.is_file())


def _pushover_lines(rig) -> list[str]:
    return [ln for ln in rig.log().splitlines() if "pushover:" in ln]


def test_dotenv_pair_beats_bot_file_and_keeps_quotes_comments_crlf(rig):
    dotenv = rig.calls.parent / "radon.env"
    dotenv.write_bytes(
        b'export PUSHOVER_USER="u-dot"\r\n'
        b"PUSHOVER_TOKEN='t-dot'  # comment\r\n"
    )

    proc = rig.run(RADON_RUNNER_DOTENV=str(dotenv))

    assert proc.returncode == 0, proc.stderr + rig.log()
    note = rig.notification()
    assert note["user"] == "u-dot" and note["token"] == "t-dot"


def test_dotenv_allowlist_never_loads_other_keys(rig):
    dotenv = rig.calls.parent / "radon.env"
    dotenv.write_text(
        "PUSHOVER_USER=u-dot\n"
        "PUSHOVER_TOKEN=t-dot\n"
        "GH_TOKEN=gh-from-dotenv\n"
        "TURSO_AUTH_TOKEN=turso-sentinel\n"
        "IB_PASSWORD=ib-sentinel\n"
    )

    proc = rig.run(RADON_RUNNER_DOTENV=str(dotenv))

    assert proc.returncode == 0, proc.stderr + rig.log()
    blob = "\n".join(
        [
            _all_stub_env(rig),
            rig.calls.joinpath("curl").read_text() if (rig.calls / "curl").exists() else "",
            _operator_surfaces(rig, proc),
        ]
    )
    for sentinel in ("gh-from-dotenv", "turso-sentinel", "ib-sentinel"):
        assert sentinel not in blob, sentinel
    assert "GH_TOKEN=gh-dummy" in (rig.calls / "grok.env").read_text()


def test_dotenv_values_are_literal_and_never_evaluated(rig):
    dotenv = rig.calls.parent / "radon.env"
    dotenv.write_text(
        "PUSHOVER_USER=$(touch $CALLS/pwned)\n"
        "PUSHOVER_TOKEN=`touch $CALLS/pwned2`\n"
    )

    proc = rig.run(RADON_RUNNER_DOTENV=str(dotenv))

    assert proc.returncode == 0, proc.stderr + rig.log()
    assert not (rig.calls / "pwned").exists()
    assert not (rig.calls / "pwned2").exists()
    note = rig.notification()
    assert note["user"] == "$(touch $CALLS/pwned)"
    assert note["token"] == "`touch $CALLS/pwned2`"


def test_values_never_logged_and_one_present_line_names_the_source(rig):
    # Built at runtime: a token-shaped literal next to a token identifier
    # trips gitleaks generic-api-key. These are sentinels, not credentials.
    user = "-".join(("u", "SENTINEL", "aa11bb22"))
    secret = "-".join(("t", "SENTINEL", "cc33dd44"))
    dotenv = rig.calls.parent / "radon.env"
    dotenv.write_text(f"PUSHOVER_USER={user}\nPUSHOVER_TOKEN={secret}\n")

    proc = rig.run(RADON_RUNNER_DOTENV=str(dotenv))

    assert proc.returncode == 0, proc.stderr + rig.log()
    surfaces = _operator_surfaces(rig, proc)
    assert user not in surfaces and secret not in surfaces
    present = [ln for ln in _pushover_lines(rig) if "present" in ln]
    assert len(present) == 1
    assert "source:" in present[0] and str(dotenv) in present[0]


def test_missing_credentials_skip_notify_and_still_run(rig):
    _bot_env(rig).unlink()

    proc = rig.run()

    assert proc.returncode == 0, proc.stderr + rig.log()
    assert rig.called() == ["grok"]
    assert rig.notifications() == []
    missing = [ln for ln in _pushover_lines(rig) if "credentials missing" in ln]
    assert len(missing) == 1
    assert "notifications skipped" in missing[0]


def test_half_pair_does_not_mix_and_logs_incomplete(rig):
    dotenv = rig.calls.parent / "radon.env"
    dotenv.write_text("PUSHOVER_USER=u-half\n")

    proc = rig.run(RADON_RUNNER_DOTENV=str(dotenv))

    assert proc.returncode == 0, proc.stderr + rig.log()
    note = rig.notification()
    assert note["user"] == "u-dummy" and note["token"] == "t-dummy"
    assert "u-half" not in note["user"]
    log = rig.log()
    assert "incomplete" in log and str(dotenv) in log


def test_agent_never_sees_pushover_keys_from_dotenv(rig):
    dotenv = rig.calls.parent / "radon.env"
    dotenv.write_text("PUSHOVER_USER=u-dot\nPUSHOVER_TOKEN=t-dot\n")

    proc = rig.run(RADON_RUNNER_DOTENV=str(dotenv))

    assert proc.returncode == 0, proc.stderr + rig.log()
    env = (rig.calls / "grok.env").read_text()
    assert "u-dot" not in env and "t-dot" not in env
    assert "PUSHOVER_USER" not in env and "PUSHOVER_TOKEN" not in env
    assert "GH_TOKEN=gh-dummy" in env
    note = rig.notification()
    assert note["user"] == "u-dot" and note["token"] == "t-dot"


def test_dotenv_path_file_is_used_when_env_is_unset(rig):
    dotenv = rig.calls.parent / "from-file.env"
    dotenv.write_text("PUSHOVER_USER=u-path\nPUSHOVER_TOKEN=t-path\n")
    (rig.install / "dotenv-path").write_text(f"{dotenv}\n")

    proc = rig.run()

    assert proc.returncode == 0, proc.stderr + rig.log()
    note = rig.notification()
    assert note["user"] == "u-path" and note["token"] == "t-path"


def test_no_plist_carries_pushover():
    loops = sorted(p.stem for p in (REPO / "scripts" / "runner" / "loops").glob("*.env"))
    assert loops
    for loop in loops:
        raw = subprocess.run(
            [BASH, str(REPO / "scripts" / "runner" / "install.sh"), "--print-plist", loop],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        assert "PUSHOVER" not in raw
        assert "PUSHOVER" not in str(_plist(loop))


def test_install_writes_dotenv_path_only_when_set_and_never_touches_the_target(tmp_path):
    prefix = tmp_path / "prefix"
    prefix.mkdir()
    target = tmp_path / "only-pushover.env"
    target.write_text("PUSHOVER_USER=u\nPUSHOVER_TOKEN=t\n")
    target.chmod(0o600)
    before_mode = target.stat().st_mode
    before_bytes = target.read_bytes()
    install = REPO / "scripts" / "runner" / "install.sh"
    src = install.read_text()
    assert "write_dotenv_path() {" in src
    body = src.split("write_dotenv_path() {", 1)[1].split("\n}\n", 1)[0]
    for line in body.splitlines():
        if "chmod" in line or "chown" in line:
            assert "$dest" in line
            assert "$path" not in line
    assert "cp " not in body
    runner = src.split("install_runner() {", 1)[1].split("\n}\n", 1)[0]
    assert "write_dotenv_path" in runner

    relative = subprocess.run(
        [BASH, str(install), "--write-dotenv-path"],
        env={**os.environ, "RADON_RUNNER_PREFIX": str(prefix), "RADON_RUNNER_DOTENV": "relative.env"},
        capture_output=True,
        text=True,
    )
    assert relative.returncode != 0
    assert "absolute" in relative.stderr
    assert not (prefix / "dotenv-path").exists()

    kept = prefix / "dotenv-path"
    kept.write_text("/kept/path\n")
    env = {k: v for k, v in os.environ.items() if k != "RADON_RUNNER_DOTENV"}
    env["RADON_RUNNER_PREFIX"] = str(prefix)
    unset = subprocess.run(
        [BASH, str(install), "--write-dotenv-path"],
        env=env,
        capture_output=True,
        text=True,
    )
    assert unset.returncode == 0, unset.stderr
    assert kept.read_text() == "/kept/path\n"

    written = subprocess.run(
        [BASH, str(install), "--write-dotenv-path"],
        env={**os.environ, "RADON_RUNNER_PREFIX": str(prefix), "RADON_RUNNER_DOTENV": str(target)},
        capture_output=True,
        text=True,
    )
    assert written.returncode == 0, written.stderr
    assert (prefix / "dotenv-path").read_text() == f"{target}\n"
    assert target.stat().st_mode == before_mode
    assert target.read_bytes() == before_bytes


def test_notify_logs_curl_failure_status_without_values(rig):
    fail = rig.calls.parent / "fail-curl"
    fail.write_text("#!/bin/bash\nexit 22\n")
    fail.chmod(0o755)

    proc = rig.run(RADON_RUNNER_CURL=str(fail))

    assert proc.returncode == 0, proc.stderr + rig.log()
    failed = [ln for ln in _pushover_lines(rig) if "send failed" in ln]
    assert failed
    assert "u-dummy" not in rig.log() and "t-dummy" not in rig.log()
    assert "status=" in failed[0]
