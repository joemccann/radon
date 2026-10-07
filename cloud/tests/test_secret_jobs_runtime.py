"""DS-2026-10-05-05 follow-up: hardening on top of the secret-store key units.

`test_ds_secret_store_key_units.py` owns the base contract (which units load
the key, their drop-ins, narrow binds, staging mode, the broker). This file
pins what sits on top of it: container privilege flags on both engines, no
host executable or checkout bind into a key-holding container (radon-api
included), fail-closed credential delivery, an unverifiable group boundary,
ancestor-pinned private directories, and stop/cleanup ordering.
"""

from __future__ import annotations

import os
import shlex
import stat
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_app_runtime import TEST_SHA, _run, _run_line, _subscription_home, _write_executable  # noqa: E402

STORE_UNITS = (
    "radon-ai-cycle.service",
    "radon-ai-cycle-backfill.service",
    "radon-aa-frontier-refresh.service",
    "radon-subscription-vault.service",
)
KEY_UNITS = ("radon-api.service", *STORE_UNITS)
KEY = "radon-secret-store-key"


def _no_container_started(result) -> None:
    log = result.docker_log.read_text() if result.docker_log.exists() else ""
    assert not any(line.startswith("run ") for line in log.splitlines()), log


@pytest.mark.parametrize("unit", STORE_UNITS)
@pytest.mark.parametrize("engine", ("docker", "podman"))
def test_store_unit_runs_unprivileged_pinned_image(tmp_path, unit, engine):
    home = _subscription_home(tmp_path)
    (home / ".grok" / "bin").mkdir()
    result = _run(tmp_path, ["run", unit], extra_env={
        "RADON_SUBSCRIPTION_HOME": str(home),
        "RADON_TEST_ENGINE": engine,
        "NOTIFY_SOCKET": "",
    })
    assert result.returncode == 0, result.stderr
    run = _run_line(result)
    assert f"ghcr.io/joemccann/radon-python:{TEST_SHA}" in run
    assert "--user 1000:1000" in run and "--group-add 1001" in run
    assert "--cap-drop ALL" in run and "no-new-privileges" in run
    assert "--privileged" not in run and "docker.sock" not in run
    if engine == "podman":
        assert "--cgroups=split" in run
    else:
        assert "--cgroup-parent=system.slice" in run


@pytest.mark.parametrize("unit", KEY_UNITS)
def test_key_holding_container_gets_no_host_executable_or_checkout_bind(tmp_path, unit):
    home = _subscription_home(tmp_path)
    (home / ".grok" / "bin").mkdir()
    (home / ".local" / "bin" / "python").write_text("host executable must never run")
    result = _run(tmp_path, ["run", unit], extra_env={
        "RADON_SUBSCRIPTION_HOME": str(home), "NOTIFY_SOCKET": "",
    })
    assert result.returncode == 0, result.stderr
    run = _run_line(result)
    # Even a read-only bind executes code the host radon account can replace.
    for forbidden in ("/.local/bin:", "/.grok/bin:", "/scripts:", "/.venv:"):
        assert forbidden not in run, run
    args = shlex.split(run)
    for index, arg in enumerate(args):
        if arg == "-v":
            destination = args[index + 1].split(":")[1]
            assert destination not in ("/home/radon", "/home/radon/radon"), run


def test_non_key_llm_consumers_keep_the_host_cli_bind(tmp_path):
    home = _subscription_home(tmp_path)
    result = _run(tmp_path, ["run", "radon-research.service"], extra_env={
        "RADON_SUBSCRIPTION_HOME": str(home), "NOTIFY_SOCKET": "",
    })
    assert result.returncode == 0, result.stderr
    assert f"{home}/.local/bin:/home/radon/.local/bin:ro" in _run_line(result)


@pytest.mark.parametrize("unit", STORE_UNITS)
@pytest.mark.parametrize("failure", ("group_missing", "key_missing", "key_symlink", "key_short"))
def test_store_unit_refuses_invalid_credential_delivery_before_reaping(tmp_path, unit, failure):
    env = {"NOTIFY_SOCKET": ""}
    if failure == "group_missing":
        env["RADON_TEST_SECRET_GROUP_MISSING"] = "1"
    else:
        directory = tmp_path / "invalid-credentials"
        directory.mkdir()
        key = directory / KEY
        if failure == "key_symlink":
            target = tmp_path / "target-key"
            target.write_bytes(os.urandom(32))
            key.symlink_to(target)
        elif failure == "key_short":
            key.write_bytes(os.urandom(31))
        env["CREDENTIALS_DIRECTORY"] = str(directory)
    result = _run(tmp_path, ["run", unit], extra_env=env)
    assert result.returncode == 78, result.stderr
    _no_container_started(result)
    log = result.docker_log.read_text() if result.docker_log.exists() else ""
    assert f"rm -f {unit}" not in log
    assert not (Path(result.proxy_dir) / "credentials" / unit).exists()


@pytest.mark.parametrize("unit", KEY_UNITS)
@pytest.mark.parametrize("answer", ("fail", "empty"))
def test_key_unit_refuses_when_group_membership_is_unverifiable(tmp_path, unit, answer):
    fake_id = tmp_path / "id-unverifiable"
    body = "exit 1" if answer == "fail" else "exit 0"
    _write_executable(
        fake_id,
        "#!/bin/bash\n"
        f'if [[ "$1" == "-nG" ]]; then {body}; fi\n'
        "echo 1000\n",
    )
    result = _run(tmp_path, ["run", unit], extra_env={"RADON_TEST_ID": str(fake_id), "NOTIFY_SOCKET": ""})
    assert result.returncode == 78, result.stderr
    _no_container_started(result)
    assert not (Path(result.proxy_dir) / "credentials" / unit).exists()


def test_private_dir_refuses_a_symlinked_ancestor(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real)
    result = _run(tmp_path, ["run", "radon-ai-cycle.service"], extra_env={
        "RADON_TEST_AI_CYCLE_DIR": str(link / "ai-cycle"), "NOTIFY_SOCKET": "",
    })
    assert result.returncode == 78, result.stderr
    assert not (real / "ai-cycle").exists()
    _no_container_started(result)


@pytest.mark.parametrize("unit", STORE_UNITS)
def test_store_unit_cleans_key_on_env_render_failure(tmp_path, unit):
    result = _run(tmp_path, ["run", unit], extra_env={
        "RADON_TEST_ENV_FILE": str(tmp_path / "missing-env"), "NOTIFY_SOCKET": "",
    })
    assert result.returncode == 71, result.stderr
    _no_container_started(result)
    assert not (Path(result.proxy_dir) / "credentials" / unit).exists()


@pytest.mark.parametrize("unit", STORE_UNITS)
def test_store_unit_stop_removes_only_its_credential(tmp_path, unit):
    result = _run(tmp_path, ["run", unit], extra_env={"NOTIFY_SOCKET": ""})
    assert result.returncode == 0, result.stderr
    proxy_dir = Path(result.proxy_dir)
    other_key = proxy_dir / "credentials" / "radon-api.service" / KEY
    other_key.parent.mkdir()
    other_key.write_bytes(b"another unit")
    database = tmp_path / "data" / "secret_store" / "secrets.db"
    database.write_bytes(b"persistent")
    stopped = _run(tmp_path, ["stop", unit], extra_env={"RADON_TEST_NOTIFY_PROXY_DIR": str(proxy_dir)})
    assert stopped.returncode == 0, stopped.stderr
    assert not (proxy_dir / "credentials" / unit).exists()
    assert other_key.read_bytes() == b"another unit"
    assert database.read_bytes() == b"persistent"


@pytest.mark.parametrize("unit", STORE_UNITS)
def test_store_unit_surviving_container_preserves_staged_key(tmp_path, unit):
    proxy_dir = tmp_path / "proxy"
    key = proxy_dir / "credentials" / unit / KEY
    key.parent.mkdir(parents=True)
    key.write_bytes(b"existing container key")
    result = _run(tmp_path, ["stop", unit], extra_env={"RADON_TEST_NOTIFY_PROXY_DIR": str(proxy_dir)}, docker_body='''#!/bin/bash
printf '%s\\n' "$*" >> {log}
if [[ "$1" == rm ]]; then exit 1; fi
exit 0
''')
    assert result.returncode == 75, result.stderr
    assert key.read_bytes() == b"existing container key"
    assert stat.S_ISREG(key.stat().st_mode)
