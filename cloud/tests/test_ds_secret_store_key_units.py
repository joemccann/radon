"""DS-2026-10-05-05: the secret-store master key never reaches a radon-uid host process.

Four `User=radon` units loaded `radon-secret-store-key` with
`LoadCredentialEncrypted=` and ran the radon-writable checkout venv, so the
decrypted key sat in a process the `radon` account controls -- the boundary
R-619 defends for radon-api. Now:

* every unit that loads the key runs its store work through the root-owned
  `radon-app-runtime` (image code, key staged `root:radon-secrets 0040`);
* radon-subscription-tokens keeps its third-party CLI runs as `radon` with no
  key at all, and reaches the four CLI credential slots through the
  radon-subscription-vault broker, which is the process that holds the key.
"""

from __future__ import annotations

import re
import stat
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_app_runtime import _run, _run_line  # noqa: E402

CLOUD = Path(__file__).resolve().parents[1]
SERVICES = CLOUD / "services"
RUNTIME = CLOUD / "scripts" / "radon-app-runtime.sh"
BOOTSTRAP = CLOUD / "scripts" / "bootstrap-control-plane.sh"
HELPER = CLOUD / "scripts" / "deploy-root-helper.sh"
SETUP = CLOUD / "scripts" / "setup-vps.sh"
MANIFEST = CLOUD / "config" / "installed-units.sha256"

KEY = "radon-secret-store-key"
STORE_JOBS = (
    "radon-ai-cycle.service",
    "radon-ai-cycle-backfill.service",
    "radon-aa-frontier-refresh.service",
)
VAULT = "radon-subscription-vault.service"
TOKENS = "radon-subscription-tokens.service"
VAULT_SOCKET = "/run/radon-subscription-vault/vault.sock"


def _drop_in(unit: str) -> Path:
    return SERVICES / f"{unit}.d" / "runtime-container.conf"


def _runtime_list(name: str) -> list[str]:
    match = re.search(rf'^readonly {name}="([^"]*)"', RUNTIME.read_text(encoding="utf-8"), re.M)
    assert match, f"{name} not found in radon-app-runtime.sh"
    return match.group(1).split()


def _readonly_array(path: Path, name: str) -> list[str]:
    match = re.search(rf"readonly(?: -a)? {name}=\((.*?)\)", path.read_text(encoding="utf-8"), re.S)
    assert match, f"{name} not found in {path.name}"
    return match.group(1).split()


# --- the contract the finding asks for ------------------------------------


def _key_loading_units() -> list[str]:
    return sorted(
        path.name
        for path in SERVICES.glob("*.service")
        if f"LoadCredentialEncrypted={KEY}:" in path.read_text(encoding="utf-8")
    )


def test_the_key_loading_set_is_exactly_the_runtime_wrapped_units() -> None:
    assert _key_loading_units() == sorted(("radon-api.service", VAULT, *STORE_JOBS))
    assert sorted(_runtime_list("SECRET_STORE_UNITS")) == _key_loading_units()


@pytest.mark.parametrize("unit", _key_loading_units())
def test_no_unit_hands_the_key_to_a_radon_uid_process(unit: str) -> None:
    """A unit that loads the key must be overridden to the root runtime."""
    dropin = _drop_in(unit)
    assert dropin.is_file(), f"{unit} loads the key without a runtime-container drop-in"
    text = dropin.read_text(encoding="utf-8")
    assert re.search(r"^User=root$", text, re.M), unit
    assert re.search(r"^ExecStartPre=$", text, re.M), unit
    assert re.search(r"^ExecStart=$", text, re.M), unit
    execs = re.findall(r"^ExecStart=(\S.*)$", text, re.M)
    assert execs == ["/usr/local/sbin/radon-app-runtime run %n"], unit
    assert unit in _runtime_list("APP_UNITS")


def test_subscription_tokens_holds_no_key_and_uses_the_broker() -> None:
    text = (SERVICES / TOKENS).read_text(encoding="utf-8")
    assert "LoadCredentialEncrypted" not in text
    assert "RADON_SECRET_STORE_PATH" not in text
    assert "secret_store.py" not in text
    assert f"Environment=RADON_SUBSCRIPTION_VAULT_SOCKET={VAULT_SOCKET}" in text.splitlines()
    assert re.search(rf"^Wants=.*\b{re.escape(VAULT)}\b", text, re.M)
    assert re.search(rf"^After=.*\b{re.escape(VAULT)}\b", text, re.M)
    assert not _drop_in(TOKENS).exists(), "the CLI runs stay radon; only the broker is wrapped"


# --- drop-ins and control-plane membership ----------------------------------


@pytest.mark.parametrize("unit", STORE_JOBS)
def test_store_job_dropins_keep_the_oneshot_contract(unit: str) -> None:
    text = _drop_in(unit).read_text(encoding="utf-8")
    assert re.search(r"^Type=oneshot$", text, re.M)
    assert "Delegate=yes" in text and "KillMode=mixed" in text
    assert "ExecStopPost=/usr/local/sbin/radon-app-runtime stop %n" in text
    assert "Environment=RADON_RUNTIME=container" in text
    assert "Restart=" not in text


def test_vault_dropin_is_a_supervised_long_running_container() -> None:
    text = _drop_in(VAULT).read_text(encoding="utf-8")
    assert re.search(r"^Type=simple$", text, re.M)
    assert "Delegate=yes" in text and "KillMode=mixed" in text
    base = (SERVICES / VAULT).read_text(encoding="utf-8")
    assert "Restart=always" in base
    assert "-m scripts.subscription_vault --serve" in base


@pytest.mark.parametrize("unit", (*STORE_JOBS, VAULT))
def test_new_dropins_are_root_installed_control_plane_artifacts(unit: str) -> None:
    rel = f"services/{unit}.d/runtime-container.conf"
    target = f"/etc/systemd/system/{unit}.d/runtime-container.conf"
    assert rel in _readonly_array(BOOTSTRAP, "SOURCES")
    assert target in _readonly_array(BOOTSTRAP, "LOGICAL_TARGETS")
    assert rel in _readonly_array(HELPER, "CONTROL_PLANE_SOURCES")
    assert target in _readonly_array(HELPER, "CONTROL_PLANE_TARGETS")


def test_vault_base_unit_is_control_plane_and_provisioned() -> None:
    assert f"services/{VAULT}" in _readonly_array(BOOTSTRAP, "SOURCES")
    assert f"/etc/systemd/system/{VAULT}" in _readonly_array(BOOTSTRAP, "LOGICAL_TARGETS")
    assert f"services/{VAULT}" in _readonly_array(HELPER, "CONTROL_PLANE_SOURCES")
    assert VAULT in _readonly_array(SETUP, "SERVICE_FILES")


def test_manifest_pins_the_new_and_changed_unit_files() -> None:
    import hashlib

    entries = dict(
        reversed(line.split("  ", 1))
        for line in MANIFEST.read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#")
    )
    for name in (VAULT, TOKENS, *(f"{u}.d/runtime-container.conf" for u in (*STORE_JOBS, VAULT))):
        digest = hashlib.sha256((SERVICES / name).read_bytes()).hexdigest()
        assert entries.get(name) == digest, name


# --- the runtime hands each unit only what it needs --------------------------


@pytest.mark.parametrize("unit", (*STORE_JOBS, VAULT))
def test_store_units_get_the_key_through_the_secrets_group(tmp_path: Path, unit: str) -> None:
    result = _run(tmp_path, ["run", unit])
    assert result.returncode == 0, result.stderr
    line = _run_line(result)
    host_dir = Path(result.proxy_dir) / "credentials" / unit  # type: ignore[attr-defined]
    container_dir = f"/run/credentials/{unit}"
    assert "--group-add 1001" in line
    assert f"--env CREDENTIALS_DIRECTORY={container_dir}" in line
    assert f"type=bind,src={host_dir},dst={container_dir},readonly" in line
    assert "RADON_SECRET_STORE_PATH=/home/radon/radon/data/secret_store/secrets.db" in line
    assert stat.S_IMODE(host_dir.stat().st_mode) == 0o050
    staged = host_dir / KEY
    host_dir.chmod(0o700)
    assert stat.S_IMODE(staged.stat().st_mode) == 0o040
    assert "ghcr.io/joemccann/radon-python:" in line


@pytest.mark.parametrize("unit", (*STORE_JOBS, VAULT))
def test_store_units_get_narrow_binds_only(tmp_path: Path, unit: str) -> None:
    result = _run(tmp_path, ["run", unit])
    assert result.returncode == 0, result.stderr
    line = _run_line(result)
    data = tmp_path / "data"
    assert f"-v {data}/secret_store:/home/radon/radon/data/secret_store " in line
    assert f"-v {data}:/home/radon/radon/data " not in line
    assert "/var/lib/radon/media" not in line
    assert "ib-lease" not in line
    assert "/run/radon-control" not in line
    # No radon-writable executable reaches a container that holds the key.
    assert ".local/bin" not in line
    assert ".grok" not in line and ".codex" not in line and ".claude" not in line
    assert ".gemini" not in line


@pytest.mark.parametrize("unit", STORE_JOBS)
def test_store_jobs_bind_the_ai_cycle_state_dir_private(tmp_path: Path, unit: str) -> None:
    result = _run(tmp_path, ["run", unit])
    assert result.returncode == 0, result.stderr
    state = tmp_path / "state" / "ai-cycle"
    assert f"-v {state}:/home/radon/.radon/ai-cycle " in _run_line(result)
    assert state.is_dir() and not state.is_symlink()
    assert stat.S_IMODE(state.stat().st_mode) == 0o700


def test_store_jobs_refuse_a_symlinked_ai_cycle_dir(tmp_path: Path) -> None:
    target = tmp_path / "elsewhere"
    target.mkdir()
    (tmp_path / "state").mkdir(exist_ok=True)
    (tmp_path / "state" / "ai-cycle").symlink_to(target)
    result = _run(tmp_path, ["run", "radon-ai-cycle.service"])
    assert result.returncode == 78, result.stderr
    assert not (Path(result.proxy_dir) / "credentials" / "radon-ai-cycle.service").exists()  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    ("unit", "command"),
    (
        ("radon-ai-cycle.service", "python scripts/secret_store.py && exec python -m scripts.ai_cycle.collect --record"),
        (
            "radon-ai-cycle-backfill.service",
            "python scripts/secret_store.py && exec python -m scripts.ai_cycle.collect --record --backfill"
            " --start 2006-12-31 --sources vercel,gpu-rental,sec,eia,noaa,openrouter"
            " --checkpoint /home/radon/.radon/ai-cycle/backfill-checkpoint.json --max-requests 400",
        ),
        ("radon-aa-frontier-refresh.service", "python scripts/secret_store.py && exec python -m scripts.aa_frontier_refresh"),
        (VAULT, "python scripts/secret_store.py && exec python -m scripts.subscription_vault --serve"),
    ),
)
def test_store_units_run_the_same_command_as_their_base_unit(tmp_path: Path, unit: str, command: str) -> None:
    result = _run(tmp_path, ["run", unit])
    assert result.returncode == 0, result.stderr
    assert command in _run_line(result)
    base = (SERVICES / unit).read_text(encoding="utf-8")
    module_args = command.split("exec python ", 1)[1]
    assert re.search(rf"^ExecStart=\S+/python {re.escape(module_args)}$", base, re.M), unit


def test_vault_binds_its_socket_dir_and_nothing_else_store_jobs_get(tmp_path: Path) -> None:
    result = _run(tmp_path, ["run", VAULT])
    assert result.returncode == 0, result.stderr
    line = _run_line(result)
    sock_dir = tmp_path / "state" / "subscription-vault"
    assert f"-v {sock_dir}:/run/radon-subscription-vault " in line
    assert f"--env RADON_SUBSCRIPTION_VAULT_SOCKET={VAULT_SOCKET}" in line
    assert "/home/radon/.radon/ai-cycle" not in line
    assert stat.S_IMODE(sock_dir.stat().st_mode) == 0o700


@pytest.mark.parametrize("unit", (*STORE_JOBS, VAULT))
def test_stop_removes_the_staged_key(tmp_path: Path, unit: str) -> None:
    result = _run(tmp_path, ["run", unit])
    assert result.returncode == 0, result.stderr
    proxy_dir = Path(result.proxy_dir)  # type: ignore[attr-defined]
    assert (proxy_dir / "credentials" / unit).exists()
    stopped = _run(tmp_path, ["stop", unit], extra_env={"RADON_TEST_NOTIFY_PROXY_DIR": str(proxy_dir)})
    assert stopped.returncode == 0, stopped.stderr
    assert not (proxy_dir / "credentials" / unit).exists()


@pytest.mark.parametrize("unit", (*STORE_JOBS, VAULT))
def test_store_units_refuse_when_radon_joined_the_secrets_group(tmp_path: Path, unit: str) -> None:
    result = _run(tmp_path, ["run", unit], extra_env={"RADON_TEST_RADON_GROUPS": "radon radon-secrets"})
    assert result.returncode == 78, result.stderr
    log = result.docker_log.read_text(encoding="utf-8")  # type: ignore[attr-defined]
    assert not [l for l in log.splitlines() if l.startswith("run ")]
