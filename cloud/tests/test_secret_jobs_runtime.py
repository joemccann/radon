"""DS-2026-10-05-05: key-bearing jobs execute only the trusted image.

Exercise the real wrapper with the existing fake engine fixture. Host radon
must never gain the key or supply executable code to a key-bearing container.
"""

from __future__ import annotations

import os
import shlex
import stat
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_app_runtime import CLOUD, TEST_SHA, _run, _run_line, _subscription_home  # noqa: E402

JOBS = (
    "radon-subscription-tokens.service",
    "radon-ai-cycle.service",
    "radon-ai-cycle-backfill.service",
    "radon-aa-frontier-refresh.service",
)
COMMANDS = {
    JOBS[0]: "python -m scripts.subscription_tokens --once",
    JOBS[1]: "python -m scripts.ai_cycle.collect --record",
    JOBS[2]: "python -m scripts.ai_cycle.collect --record --backfill --start 2006-12-31 --sources vercel,gpu-rental,sec,eia,noaa,openrouter --checkpoint /home/radon/.radon/ai-cycle/backfill-checkpoint.json --max-requests 400",
    JOBS[3]: "python -m scripts.aa_frontier_refresh",
}


def _job_run(tmp_path, args, extra_env=None, **kwargs):
    home = tmp_path / "radon-home"
    home.mkdir(exist_ok=True)
    env = {"RADON_SUBSCRIPTION_HOME": str(home), "NOTIFY_SOCKET": ""}
    env.update(extra_env or {})
    return _run(tmp_path, args, extra_env=env, **kwargs)


def _no_container_started(result) -> None:
    log = result.docker_log.read_text() if result.docker_log.exists() else ""
    assert not any(line.startswith("run ") for line in log.splitlines()), log


@pytest.mark.parametrize("unit", JOBS)
@pytest.mark.parametrize("engine", ("docker", "podman"))
def test_secret_job_executes_pinned_python_without_host_executables(tmp_path, unit, engine):
    home = _subscription_home(tmp_path)
    (home / ".grok" / "bin").mkdir()
    (home / ".local" / "bin" / "python").write_text("host executable must never run")
    result = _job_run(tmp_path, ["run", unit], extra_env={
        "RADON_SUBSCRIPTION_HOME": str(home),
        "RADON_TEST_ENGINE": engine,
        "NOTIFY_SOCKET": "",
    })
    assert result.returncode == 0, result.stderr
    run = _run_line(result)
    assert f"ghcr.io/joemccann/radon-python:{TEST_SHA}" in run
    assert COMMANDS[unit] in run
    assert "python scripts/secret_store.py" in run
    assert "--user 1000:1000" in run and "--group-add 1001" in run
    assert "--cap-drop ALL" in run and "no-new-privileges" in run
    assert "--privileged" not in run and "docker.sock" not in run
    if engine == "podman":
        assert "--cgroups=split" in run
    else:
        assert "--cgroup-parent=system.slice" in run
    # Even a read-only bind executes code an attacker can replace on the host.
    for forbidden in ("/.local/bin:", "/.grok/bin:", "/scripts:", "/.venv:"):
        assert forbidden not in run, run
    args = shlex.split(run)
    for index, arg in enumerate(args):
        if arg == "-v":
            destination = args[index + 1].split(":")[1]
            assert destination not in ("/home/radon", "/home/radon/radon"), run
    assert f"{tmp_path / 'data' / 'secret_store'}:/home/radon/radon/data/secret_store" in run
    assert f"{tmp_path / 'data'}:/home/radon/radon/data " not in run
    assert ":/var/lib/radon/media" not in run and ":/var/lib/radon/ib-lease" not in run
    assert "--env PATH=/usr/local/bin:/usr/bin:/bin" in run
    if unit == JOBS[0]:
        assert "RADON_SUBSCRIPTION_ISOLATED=1" in run
        assert f"{tmp_path / 'state' / 'subscription-tokens'}:/var/lib/radon/subscription-tokens" in run
        for directory in (".grok", ".codex", ".claude", ".gemini/antigravity-cli", ".gemini/config"):
            assert f"{home}/{directory}:/home/radon/{directory}:rw" in run
    else:
        assert f"{home}/.radon/ai-cycle:/home/radon/.radon/ai-cycle" in run
        assert "/.grok:" not in run and "/.codex:" not in run


@pytest.mark.parametrize("unit", JOBS)
def test_secret_job_stages_root_group_only_key(tmp_path, unit):
    result = _job_run(tmp_path, ["run", unit], extra_env={"NOTIFY_SOCKET": ""})
    assert result.returncode == 0, result.stderr
    host_dir = Path(result.proxy_dir) / "credentials" / unit
    container_dir = f"/run/credentials/{unit}"
    run = _run_line(result)
    assert f"CREDENTIALS_DIRECTORY={container_dir}" in run
    assert f"type=bind,src={host_dir},dst={container_dir},readonly" in run
    assert "RADON_SECRET_STORE_PATH=/home/radon/radon/data/secret_store/secrets.db" in run
    assert stat.S_IMODE(host_dir.stat().st_mode) == 0o050
    host_dir.chmod(0o700)  # The fixture stubs root ownership.
    key = host_dir / "radon-secret-store-key"
    assert stat.S_IMODE(key.stat().st_mode) == 0o040
    key.chmod(0o400)
    assert key.read_bytes() == (tmp_path / "credentials" / key.name).read_bytes()
    assert f"root:1001 {host_dir} {key}" in (tmp_path / "chown.log").read_text()


@pytest.mark.parametrize("unit", JOBS)
@pytest.mark.parametrize("failure", ("group_missing", "host_group_member", "key_missing", "key_symlink", "key_short"))
def test_secret_job_refuses_invalid_credential_delivery_before_reaping(tmp_path, unit, failure):
    env = {"NOTIFY_SOCKET": ""}
    if failure == "group_missing":
        env["RADON_TEST_SECRET_GROUP_MISSING"] = "1"
    elif failure == "host_group_member":
        env["RADON_TEST_RADON_GROUPS"] = "radon docker radon-secrets"
    else:
        directory = tmp_path / "invalid-credentials"
        directory.mkdir()
        key = directory / "radon-secret-store-key"
        if failure == "key_symlink":
            target = tmp_path / "target-key"
            target.write_bytes(os.urandom(32))
            key.symlink_to(target)
        elif failure == "key_short":
            key.write_bytes(os.urandom(31))
        env["CREDENTIALS_DIRECTORY"] = str(directory)
    result = _job_run(tmp_path, ["run", unit], extra_env=env)
    assert result.returncode == 78, result.stderr
    _no_container_started(result)
    log = result.docker_log.read_text() if result.docker_log.exists() else ""
    assert f"rm -f {unit}" not in log
    assert not (Path(result.proxy_dir) / "credentials" / unit).exists()


@pytest.mark.parametrize("unit", JOBS)
def test_secret_job_cleans_key_on_env_render_failure(tmp_path, unit):
    result = _job_run(tmp_path, ["run", unit], extra_env={
        "RADON_TEST_ENV_FILE": str(tmp_path / "missing-env"), "NOTIFY_SOCKET": "",
    })
    assert result.returncode == 71, result.stderr
    _no_container_started(result)
    assert not (Path(result.proxy_dir) / "credentials" / unit).exists()


@pytest.mark.parametrize("unit", JOBS)
def test_secret_job_stop_removes_only_its_credential(tmp_path, unit):
    result = _job_run(tmp_path, ["run", unit], extra_env={"NOTIFY_SOCKET": ""})
    assert result.returncode == 0, result.stderr
    proxy_dir = Path(result.proxy_dir)
    other_key = proxy_dir / "credentials" / "radon-api.service" / "radon-secret-store-key"
    other_key.parent.mkdir()
    other_key.write_bytes(b"another unit")
    database = tmp_path / "data" / "secret_store" / "secrets.db"
    database.write_bytes(b"persistent")
    stopped = _job_run(tmp_path, ["stop", unit], extra_env={"RADON_TEST_NOTIFY_PROXY_DIR": str(proxy_dir)})
    assert stopped.returncode == 0, stopped.stderr
    assert not (proxy_dir / "credentials" / unit).exists()
    assert other_key.read_bytes() == b"another unit"
    assert database.read_bytes() == b"persistent"


@pytest.mark.parametrize("unit", JOBS)
def test_secret_job_surviving_container_preserves_staged_key(tmp_path, unit):
    proxy_dir = tmp_path / "proxy"
    key = proxy_dir / "credentials" / unit / "radon-secret-store-key"
    key.parent.mkdir(parents=True)
    key.write_bytes(b"existing container key")
    result = _job_run(tmp_path, ["stop", unit], extra_env={"RADON_TEST_NOTIFY_PROXY_DIR": str(proxy_dir)}, docker_body='''#!/bin/bash
printf '%s\\n' "$*" >> {log}
if [[ "$1" == rm ]]; then exit 1; fi
exit 0
''')
    assert result.returncode == 75, result.stderr
    assert key.read_bytes() == b"existing container key"


@pytest.mark.parametrize("unit", JOBS)
def test_secret_job_systemd_replaces_all_host_execution(unit):
    base = (CLOUD / "services" / unit).read_text()
    lines = base.splitlines()
    assert "LoadCredentialEncrypted=radon-secret-store-key:" in base
    assert "User=root" in lines and "WorkingDirectory=/" in lines
    assert not any(line.startswith("ExecStartPre=") for line in lines)
    assert "ExecStart=/usr/local/sbin/radon-app-runtime run %n" in lines
    assert "Type=oneshot" in lines
    assert "ExecStopPost=/usr/local/sbin/radon-app-runtime stop %n" in lines
    assert "Delegate=yes" in lines and "KillMode=mixed" in lines
    assert not any(line.startswith("Exec") and "/home/radon/" in line for line in lines)
    path = next(line for line in lines if line.startswith("Environment=PATH="))
    assert "/home/radon" not in path
    for installer in ("bootstrap-control-plane.sh", "deploy-root-helper.sh"):
        assert f"services/{unit}" in (CLOUD / "scripts" / installer).read_text()


def test_secret_job_control_plane_metadata_is_complete_and_paired():
    import re
    tables = {}
    for script, names in (
        ("bootstrap-control-plane.sh", ("SOURCES", "LOGICAL_TARGETS", "MODES", "KINDS")),
        ("deploy-root-helper.sh", ("CONTROL_PLANE_SOURCES", "CONTROL_PLANE_TARGETS", "CONTROL_PLANE_MODES")),
    ):
        text = (CLOUD / "scripts" / script).read_text()
        arrays = []
        for name in names:
            match = re.search(rf"readonly -a {name}=\((.*?)\)", text, re.S)
            assert match, (script, name)
            arrays.append(shlex.split(match.group(1)))
        assert len({len(array) for array in arrays}) == 1, script
        sources, targets, modes = arrays[:3]
        tables[script] = sources
        for job in JOBS:
            for unit in (job, job.replace(".service", ".timer")):
                index = sources.index("services/" + unit)
                assert targets[index] == "/etc/systemd/system/" + unit
                assert int(modes[index], 8) == 0o644
                if len(arrays) == 4:
                    assert arrays[3][index] == "systemd"
    assert tables["bootstrap-control-plane.sh"] == tables["deploy-root-helper.sh"]
