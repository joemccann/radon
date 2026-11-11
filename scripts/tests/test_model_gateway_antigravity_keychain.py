"""Antigravity on the Mac gateway: macOS agy keeps its grant in a keychain.

Linux agy writes ~/.gemini/antigravity-cli/antigravity-oauth-token, which the
VPS path detects and copies into a throwaway HOME. macOS agy stores the grant
with /usr/bin/security instead, which follows $HOME. The Mac gateway therefore
gives agy a dedicated home (RADON_ANTIGRAVITY_KEYCHAIN_HOME) holding only an
empty-password, no-timeout keychain; the engine detects the grant there,
unlocks it before each call, and never shows agy the gateway's other grants.
"""
from __future__ import annotations

import json
import subprocess as sp
from pathlib import Path

import pytest

from model_gateway import engine

SECURITY = "/usr/bin/security"


def keychain_home(tmp_path: Path) -> tuple[Path, Path]:
    home = tmp_path / "agy-home"
    keychain = home / "Library" / "Keychains" / "radon-agy.keychain-db"
    keychain.parent.mkdir(parents=True)
    keychain.write_bytes(b"kc")
    return home, keychain


def executable(tmp_path: Path) -> Path:
    cli = tmp_path / "agy"
    cli.write_text("#!/bin/sh\n")
    cli.chmod(0o755)
    return cli


def env_for(home: Path, cli: Path) -> dict:
    return {"RADON_ANTIGRAVITY_KEYCHAIN_HOME": str(home), "ANTIGRAVITY_CLI": str(cli), "HOME": "/gateway/home"}


@pytest.mark.parametrize("found,expected", [(0, True), (44, False)])
def test_grant_is_detected_in_the_dedicated_keychain(tmp_path, monkeypatch, found, expected):
    home, keychain = keychain_home(tmp_path)
    cli = executable(tmp_path)
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs["env"]))
        return sp.CompletedProcess(argv, found if argv[1] == "find-generic-password" else 0, "", "")

    monkeypatch.setattr(engine.subprocess, "run", run)
    assert engine._antigravity_cli(env_for(home, cli)) == (str(cli) if expected else "")
    assert [argv for argv, _ in calls] == [
        [SECURITY, "unlock-keychain", "-p", "", str(keychain)],
        [SECURITY, "find-generic-password", "-s", "antigravity", str(keychain)],
    ]
    assert all(env == {"HOME": str(home), "PATH": "/usr/bin:/bin"} for _, env in calls)


def test_no_keychain_file_means_no_grant_and_no_probe(tmp_path, monkeypatch):
    home = tmp_path / "agy-home"
    home.mkdir()
    monkeypatch.setattr(engine.subprocess, "run", lambda *a, **k: pytest.fail("probed a missing keychain"))
    assert engine._antigravity_cli(env_for(home, executable(tmp_path))) == ""


def test_a_failing_keychain_probe_reads_as_no_grant(tmp_path, monkeypatch):
    home, _ = keychain_home(tmp_path)

    def run(argv, **kwargs):
        raise sp.TimeoutExpired(argv, 10)

    monkeypatch.setattr(engine.subprocess, "run", run)
    assert engine._antigravity_cli(env_for(home, executable(tmp_path))) == ""


def test_relative_keychain_home_is_ignored(tmp_path, monkeypatch):
    monkeypatch.setattr(engine.subprocess, "run", lambda *a, **k: pytest.fail("probed a relative keychain"))
    env = env_for(Path("agy-home"), executable(tmp_path)) | {"HOME": str(tmp_path)}
    assert engine._antigravity_cli(env) == ""  # no token file under HOME either


@pytest.mark.parametrize("failure", [False, True])
def test_isolated_call_runs_agy_in_its_keychain_home_only(tmp_path, monkeypatch, failure):
    home, keychain = keychain_home(tmp_path)
    monkeypatch.setenv("RADON_ANTIGRAVITY_ISOLATED", "1")
    monkeypatch.setenv("RADON_ANTIGRAVITY_KEYCHAIN_HOME", str(home))
    monkeypatch.setenv("HOME", str(tmp_path / "gateway-home"))
    monkeypatch.setenv("TURSO_AUTH_TOKEN", "must-not-leak")
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        if argv[0] == SECURITY:
            return sp.CompletedProcess(argv, 0, "", "")
        env = kwargs["env"]
        private = Path(kwargs["cwd"])
        assert env["HOME"] == str(home)
        assert "TURSO_AUTH_TOKEN" not in env
        for key in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME", "XDG_RUNTIME_DIR", "TMPDIR"):
            assert env[key].startswith(str(private)) and Path(env[key]).is_dir()
        assert "--sandbox" in argv and "--disable-slash-commands" in argv
        assert argv[argv.index("--mode") + 1] == "plan"
        if failure:
            raise sp.TimeoutExpired(argv, 1)
        return sp.CompletedProcess(argv, 0, stdout=json.dumps({"response": '{"ok": true}'}), stderr="")

    monkeypatch.setattr(engine.subprocess, "run", run)
    if failure:
        with pytest.raises(RuntimeError, match="network:TimeoutExpired"):
            engine._antigravity_complete("agy", {}, "", "", "evaluate", [])
    else:
        assert engine._antigravity_complete("agy", {}, "", "", "evaluate", [])[0] == 200
    assert calls[0][0] == [SECURITY, "unlock-keychain", "-p", "", str(keychain)]
    assert calls[0][1]["env"] == {"HOME": str(home), "PATH": "/usr/bin:/bin"}
    assert calls[1][0][0] == "agy"
    assert not Path(calls[1][1]["cwd"]).exists(), "private scratch must be removed"
    assert not (home / engine._ANTIGRAVITY_TOKEN_RELPATH).exists(), "no Linux token seed is written"
