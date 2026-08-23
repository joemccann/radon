"""P1 contract: `radon-deploy-root refresh-control-plane`.

Unit-only control-plane refresh during deploy. No process start/stop, no
Gateway lifecycle, no sudoers wildcards, privileged diffs fail closed.
"""

from __future__ import annotations

import hashlib
import os
import re
import shlex
import shutil
import subprocess
from pathlib import Path

import pytest


CLOUD_ROOT = Path(__file__).resolve().parents[1]
ROOT_HELPER = CLOUD_ROOT / "scripts" / "deploy-root-helper.sh"
DEPLOY = CLOUD_ROOT / "scripts" / "deploy.sh"
SUDOERS = CLOUD_ROOT / "config" / "sudoers.d" / "radon-deploy"

GATEWAY_UNIT = "services/radon-ib-gateway.service"
API_UNIT = "services/radon-api.service"
HELPER_SOURCE = "scripts/deploy-root-helper.sh"
SUDOERS_SOURCE = "config/sudoers.d/radon-deploy"
POLKIT_SOURCE = "config/polkit/50-radon-services.rules"
APP_TRANSITION = "home/radon/.radon-deploy-transition.json"
GATEWAY_TRANSITION = "var/lib/radon/ib-gateway-transition.json"


def function_body(script: str, name: str) -> str:
    match = re.search(
        rf"^{name}\(\)\s*\{{\s*\n(.+?)\n\}}\s*$",
        script,
        re.MULTILINE | re.DOTALL,
    )
    assert match, f"{name}() missing"
    return match.group(1)


def _bash_string_array(script: str, name: str) -> list[str]:
    match = re.search(
        rf"^readonly -a {re.escape(name)}=\((.*?)\)",
        script,
        re.MULTILINE | re.DOTALL,
    )
    assert match, f"{name} missing"
    return match.group(1).split()


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sha256_path(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _write_executable(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)


def sudoers_helper_verbs(text: str) -> list[str]:
    verbs: list[str] = []
    token = "/usr/local/sbin/radon-deploy-root "
    for part in text.replace("\n", " ").split(","):
        part = part.split("#", 1)[0].strip()
        if token not in part:
            continue
        verb = part.split(token, 1)[1].strip()
        if verb:
            verbs.append(verb)
    return verbs


def _timeout_arm(helper: str, action: str) -> str:
    timeout = function_body(helper, "root_action_timeout")
    for patterns, body in re.findall(r"([^\n]*?)\)\s*\n(.*?)(?:;;)", timeout, re.DOTALL):
        names = [item.strip() for item in patterns.split("|")]
        if action in names:
            return body
    raise AssertionError(f"{action} missing from root_action_timeout")


HELPER_TEXT = ROOT_HELPER.read_text(encoding="utf-8")
CONTROL_PLANE_SOURCES = _bash_string_array(HELPER_TEXT, "CONTROL_PLANE_SOURCES")
CONTROL_PLANE_TARGETS = _bash_string_array(HELPER_TEXT, "CONTROL_PLANE_TARGETS")
CONTROL_PLANE_MODES = _bash_string_array(HELPER_TEXT, "CONTROL_PLANE_MODES")


def _diff_marker(source_rel: str) -> str:
    if source_rel.endswith(".rules"):
        return "\n// radon-refresh-test\n"
    return "\n# radon-refresh-test\n"


class Sandbox:
    def __init__(self, tmp_path: Path) -> None:
        self.tmp = tmp_path
        self.cloud = tmp_path / "cloud"
        self.rootfs = tmp_path / "rootfs"
        self.unit_dir = self.rootfs / "etc" / "systemd" / "system"
        self.manifest = self.rootfs / "var" / "lib" / "radon" / "control-plane-manifest.sha256"
        self.ready = self.rootfs / "var" / "lib" / "radon" / "control-plane-ready"
        self.systemctl_log = tmp_path / "systemctl.log"
        self.visudo_log = tmp_path / "visudo.log"
        fake_systemctl = tmp_path / "systemctl"
        _write_executable(
            fake_systemctl,
            f"#!/bin/bash\nprintf '%s\\n' \"$*\" >> {shlex.quote(str(self.systemctl_log))}\nexit 0\n",
        )
        fake_visudo = tmp_path / "visudo"
        _write_executable(
            fake_visudo,
            f"""#!/bin/bash
printf '%s\\n' "$*" >> {shlex.quote(str(self.visudo_log))}
if [[ "${{RADON_TEST_VISUDO_FAIL:-0}}" == "1" ]]; then
  exit 1
fi
exit 0
""",
        )
        fake_sync = tmp_path / "sync"
        _write_executable(fake_sync, "#!/bin/bash\nexit 0\n")
        fake_rm = tmp_path / "rm"
        _write_executable(fake_rm, '#!/bin/bash\nexec /bin/rm "$@"\n')
        sha256sum = tmp_path / "sha256sum"
        _write_executable(
            sha256sum,
            '#!/bin/bash\n'
            'if command -v sha256sum >/dev/null 2>&1; then exec sha256sum "$@"; fi\n'
            'exec shasum -a 256 "$@"\n',
        )
        for source_rel, target, mode in zip(
            CONTROL_PLANE_SOURCES, CONTROL_PLANE_TARGETS, CONTROL_PLANE_MODES, strict=True
        ):
            source = self.cloud / source_rel
            source.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(CLOUD_ROOT / source_rel, source)
            installed = self.rootfs / target.lstrip("/")
            installed.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, installed)
            installed.chmod(int(mode, 8))
        self.write_manifest_and_ready()
        self.env = {
            **os.environ,
            "RADON_DEPLOY_HELPER_TEST_MODE": "1",
            "RADON_TEST_SYSTEMCTL": str(fake_systemctl),
            "RADON_TEST_RM": str(fake_rm),
            "RADON_TEST_SYNC": str(fake_sync),
            "RADON_TEST_ACTIVE_STATE_FILE": str(tmp_path / "active-units"),
            "RADON_TEST_REPLICA_PREFIX": str(tmp_path / "replica.db"),
            "RADON_TEST_UNIT_SOURCE_DIR": str(self.cloud / "services"),
            "RADON_TEST_CLOUD_ROOT": str(self.cloud),
            "RADON_TEST_CONTROL_PLANE_ROOT": str(self.rootfs),
            "RADON_TEST_SYSTEMD_UNIT_DIR": str(self.unit_dir),
            "RADON_TEST_SHA256SUM": str(sha256sum),
            "RADON_TEST_VISUDO": str(fake_visudo),
        }

    def write_manifest_and_ready(self) -> None:
        lines = []
        for source_rel, target in zip(
            CONTROL_PLANE_SOURCES, CONTROL_PLANE_TARGETS, strict=True
        ):
            digest = _sha256_path(self.cloud / source_rel)
            lines.append(f"{digest}  {source_rel} -> {target}")
        self.manifest.parent.mkdir(parents=True, exist_ok=True)
        self.manifest.write_text("\n".join(lines) + "\n", encoding="utf-8")
        ready_hash = _sha256_path(self.manifest)
        self.ready.write_text(
            f"{ready_hash}  /var/lib/radon/control-plane-manifest.sha256\n",
            encoding="utf-8",
        )

    def installed_path(self, source_rel: str) -> Path:
        index = CONTROL_PLANE_SOURCES.index(source_rel)
        return self.rootfs / CONTROL_PLANE_TARGETS[index].lstrip("/")

    def mutate_source(self, source_rel: str) -> None:
        path = self.cloud / source_rel
        path.write_text(path.read_text(encoding="utf-8") + _diff_marker(source_rel), encoding="utf-8")

    def snapshot_installed(self) -> dict[Path, bytes]:
        return {
            self.installed_path(source_rel): self.installed_path(source_rel).read_bytes()
            for source_rel in CONTROL_PLANE_SOURCES
        }

    def write_transition(self, relative: str, body: str = '{"phase":"promoting"}\n') -> Path:
        path = self.rootfs / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
        return path

    def systemctl_calls(self) -> list[str]:
        if not self.systemctl_log.exists():
            return []
        return self.systemctl_log.read_text(encoding="utf-8").splitlines()

    def assert_no_gateway_lifecycle(self) -> None:
        for line in self.systemctl_calls():
            tokens = line.split()
            if any("radon-ib-gateway" in token for token in tokens):
                assert not any(verb in tokens for verb in ("start", "stop", "restart")), line

    def run(self, action: str = "refresh-control-plane") -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", str(ROOT_HELPER), action],
            env=self.env,
            capture_output=True,
            text=True,
            timeout=60,
        )


# --- A. sudoers --------------------------------------------------------------


def test_sudoers_grants_exact_refresh_verb_and_not_privileged() -> None:
    sudoers = SUDOERS.read_text(encoding="utf-8")
    verbs = sudoers_helper_verbs(sudoers)
    assert "/usr/local/sbin/radon-deploy-root refresh-control-plane" in sudoers
    assert "refresh-control-plane" in verbs
    assert "refresh-control-plane-privileged" not in verbs
    assert "refresh-control-plane-privileged" not in sudoers
    assert "radon-*" not in sudoers
    assert "/usr/bin/systemctl" not in sudoers
    for verb in verbs:
        assert "*" not in verb
        assert "?" not in verb


def test_privileged_refresh_action_is_not_in_sudoers() -> None:
    sudoers = SUDOERS.read_text(encoding="utf-8")
    helper = ROOT_HELPER.read_text(encoding="utf-8")
    verbs = sudoers_helper_verbs(sudoers)
    assert "refresh-control-plane-privileged" not in verbs
    assert "refresh-control-plane-privileged" not in sudoers
    if re.search(r"refresh-control-plane-privileged\)", helper):
        assert "refresh-control-plane-privileged" not in verbs


# --- B. helper usage / timeout / job-cancel ----------------------------------


def test_helper_usage_and_case_support_refresh_control_plane() -> None:
    helper = ROOT_HELPER.read_text(encoding="utf-8")
    usage = re.search(r"usage: radon-deploy-root \{([^}]+)\}", helper)
    assert usage, "helper usage string missing"
    actions = usage.group(1).split("|")
    assert "refresh-control-plane" in actions
    assert re.search(r"^\s*refresh-control-plane\)", helper, re.MULTILINE)


def test_refresh_timeout_is_mutation_class_and_does_not_queue_radon_jobs() -> None:
    helper = ROOT_HELPER.read_text(encoding="utf-8")
    arm = _timeout_arm(helper, "refresh-control-plane")
    assert "ROOT_MUTATION_ACTION_TIMEOUT" in arm
    queues = function_body(helper, "action_queues_radon_jobs")
    assert "refresh-control-plane" not in queues


# --- C. sandbox --------------------------------------------------------------


def test_unit_only_refresh_copies_diff_reloads_once_and_skips_gateway(tmp_path: Path) -> None:
    box = Sandbox(tmp_path)
    gateway = box.installed_path(GATEWAY_UNIT)
    gateway_before = gateway.read_bytes()
    gateway_stat = gateway.stat()
    box.mutate_source(API_UNIT)
    expected_api = (box.cloud / API_UNIT).read_bytes()

    result = box.run()

    assert result.returncode == 0, result.stdout + result.stderr
    assert box.installed_path(API_UNIT).read_bytes() == expected_api
    assert gateway.read_bytes() == gateway_before
    assert gateway.stat().st_ino == gateway_stat.st_ino
    assert gateway.stat().st_mtime_ns == gateway_stat.st_mtime_ns
    calls = box.systemctl_calls()
    assert calls.count("daemon-reload") == 1
    assert not any(
        verb in " ".join(calls).split()
        for verb in ("start", "stop", "restart", "enable", "disable")
    )
    box.assert_no_gateway_lifecycle()
    api_hash = _sha256_bytes(expected_api)
    assert api_hash in box.manifest.read_text(encoding="utf-8")
    assert f"{api_hash}  {API_UNIT} -> " in box.manifest.read_text(encoding="utf-8")
    ready_hash = _sha256_path(box.manifest)
    assert box.ready.read_text(encoding="utf-8") == (
        f"{ready_hash}  /var/lib/radon/control-plane-manifest.sha256\n"
    )


def test_privileged_sudoers_refresh_runs_visudo_then_installs(tmp_path: Path) -> None:
    box = Sandbox(tmp_path)
    box.mutate_source(SUDOERS_SOURCE)
    expected = (box.cloud / SUDOERS_SOURCE).read_bytes()

    result = box.run("refresh-control-plane-privileged")

    assert result.returncode == 0, result.stdout + result.stderr
    assert box.installed_path(SUDOERS_SOURCE).read_bytes() == expected
    visudo_calls = box.visudo_log.read_text(encoding="utf-8").splitlines()
    assert visudo_calls, "privileged sudoers refresh must run visudo"
    assert any("-cf" in line or "-c" in line for line in visudo_calls)
    box.assert_no_gateway_lifecycle()


def test_privileged_sudoers_refresh_refuses_when_visudo_fails(tmp_path: Path) -> None:
    box = Sandbox(tmp_path)
    snapshot = box.snapshot_installed()
    box.mutate_source(SUDOERS_SOURCE)
    box.env["RADON_TEST_VISUDO_FAIL"] = "1"

    result = box.run("refresh-control-plane-privileged")

    assert result.returncode != 0, result.stdout + result.stderr
    assert {path: path.read_bytes() for path in snapshot} == snapshot
    assert box.systemctl_calls() == []


@pytest.mark.parametrize(
    "source_rel",
    (SUDOERS_SOURCE, HELPER_SOURCE, POLKIT_SOURCE),
    ids=("sudoers", "helper", "polkit"),
)
def test_privileged_source_diff_exits_78_and_installs_nothing(
    tmp_path: Path, source_rel: str
) -> None:
    box = Sandbox(tmp_path)
    snapshot = box.snapshot_installed()
    manifest_before = box.manifest.read_bytes()
    ready_before = box.ready.read_bytes()
    box.mutate_source(source_rel)

    result = box.run()

    assert result.returncode == 78, result.stdout + result.stderr
    assert {path: path.read_bytes() for path in snapshot} == snapshot
    assert box.manifest.read_bytes() == manifest_before
    assert box.ready.read_bytes() == ready_before
    assert box.systemctl_calls() == []
    box.assert_no_gateway_lifecycle()


def test_pending_gateway_transition_exits_75_and_installs_nothing(tmp_path: Path) -> None:
    box = Sandbox(tmp_path)
    snapshot = box.snapshot_installed()
    box.write_transition(GATEWAY_TRANSITION)
    box.mutate_source(API_UNIT)

    result = box.run()

    assert result.returncode == 75, result.stdout + result.stderr
    assert {path: path.read_bytes() for path in snapshot} == snapshot
    assert box.systemctl_calls() == []
    box.assert_no_gateway_lifecycle()


def test_pending_app_transition_does_not_block_unit_refresh(tmp_path: Path) -> None:
    box = Sandbox(tmp_path)
    box.write_transition(APP_TRANSITION)
    box.mutate_source(API_UNIT)
    expected_api = (box.cloud / API_UNIT).read_bytes()

    result = box.run()

    assert result.returncode == 0, result.stdout + result.stderr
    assert box.installed_path(API_UNIT).read_bytes() == expected_api
    assert box.systemctl_calls().count("daemon-reload") == 1
    box.assert_no_gateway_lifecycle()


def test_already_matching_is_noop(tmp_path: Path) -> None:
    box = Sandbox(tmp_path)
    snapshot = box.snapshot_installed()
    manifest_before = box.manifest.read_bytes()
    ready_before = box.ready.read_bytes()

    result = box.run()

    assert result.returncode == 0, result.stdout + result.stderr
    combined = (result.stdout + result.stderr).lower()
    assert any(word in combined for word in ("current", "unchanged", "no-op", "noop"))
    assert box.systemctl_calls() == []
    assert {path: path.read_bytes() for path in snapshot} == snapshot
    assert box.manifest.read_bytes() == manifest_before
    assert box.ready.read_bytes() == ready_before
    box.assert_no_gateway_lifecycle()


def test_refresh_never_starts_stops_or_restarts_gateway(tmp_path: Path) -> None:
    box = Sandbox(tmp_path)
    box.mutate_source(API_UNIT)
    result = box.run()
    assert result.returncode == 0, result.stdout + result.stderr
    box.assert_no_gateway_lifecycle()
    joined = "\n".join(box.systemctl_calls())
    assert "start radon-ib-gateway" not in joined
    assert "stop radon-ib-gateway" not in joined
    assert "restart radon-ib-gateway" not in joined


# --- D. deploy.sh ------------------------------------------------------------


def test_main_still_preflights_control_plane_before_build() -> None:
    deploy = DEPLOY.read_text(encoding="utf-8")
    main = function_body(deploy, "main")
    assert main.index("preflight_control_plane") < main.index("build_staged_release")


def test_preflight_skips_services_hash_mismatch_and_fails_closed_on_scripts_config() -> None:
    preflight = function_body(DEPLOY.read_text(encoding="utf-8"), "preflight_control_plane")
    assert "services/" in preflight


def test_restart_services_invokes_refresh_after_install_units() -> None:
    deploy = DEPLOY.read_text(encoding="utf-8")
    restart = function_body(deploy, "restart_services")
    assert "refresh_control_plane" in restart
    assert restart.index("install_release_units") < restart.index("refresh_control_plane")
    refresh = function_body(deploy, "refresh_control_plane")
    assert "refresh-control-plane" in refresh
    assert "refresh-control-plane-privileged" not in refresh
    assert "sudo -n -l --" in refresh
    assert "bootstrap-control-plane.sh" in refresh
    assert "return 0" in refresh


def _preflight_runner(tmp_path: Path, mutate_rel: str | None) -> Path:
    runner = tmp_path / "runner" / "cloud"
    rels = (
        "scripts/deploy.sh",
        HELPER_SOURCE,
        "scripts/ib-gateway-control.sh",
        SUDOERS_SOURCE,
        API_UNIT,
    )
    for rel in rels:
        dest = runner / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(CLOUD_ROOT / rel, dest)
    if mutate_rel is not None:
        path = runner / mutate_rel
        path.write_text(path.read_text(encoding="utf-8") + _diff_marker(mutate_rel), encoding="utf-8")
    return runner


def _run_preflight(tmp_path: Path, mutate_rel: str | None) -> subprocess.CompletedProcess[str]:
    runner = _preflight_runner(tmp_path, mutate_rel)
    originals = {
        HELPER_SOURCE: (CLOUD_ROOT / HELPER_SOURCE).read_bytes(),
        "scripts/ib-gateway-control.sh": (
            CLOUD_ROOT / "scripts" / "ib-gateway-control.sh"
        ).read_bytes(),
        SUDOERS_SOURCE: (CLOUD_ROOT / SUDOERS_SOURCE).read_bytes(),
        API_UNIT: (CLOUD_ROOT / API_UNIT).read_bytes(),
    }
    root_helper = tmp_path / "radon-deploy-root"
    gateway_helper = tmp_path / "radon-ib-gateway-control"
    sudoers_target = tmp_path / "radon-deploy"
    api_target = tmp_path / "radon-api.service"
    root_helper.write_bytes(originals[HELPER_SOURCE])
    gateway_helper.write_bytes(originals["scripts/ib-gateway-control.sh"])
    sudoers_target.write_bytes(originals[SUDOERS_SOURCE])
    api_target.write_bytes(originals[API_UNIT])
    root_helper.chmod(0o755)
    gateway_helper.chmod(0o755)
    manifest = tmp_path / "control-plane-manifest.sha256"
    manifest.write_text(
        f"{_sha256_bytes(originals[HELPER_SOURCE])}  {HELPER_SOURCE} -> {root_helper}\n"
        f"{_sha256_bytes(originals['scripts/ib-gateway-control.sh'])}  "
        f"scripts/ib-gateway-control.sh -> {gateway_helper}\n"
        f"{_sha256_bytes(originals[SUDOERS_SOURCE])}  {SUDOERS_SOURCE} -> {sudoers_target}\n"
        f"{_sha256_bytes(originals[API_UNIT])}  {API_UNIT} -> {api_target}\n",
        encoding="utf-8",
    )
    ready = tmp_path / "control-plane-ready"
    ready.write_text(
        f"{_sha256_path(manifest)}  {manifest}\n",
        encoding="utf-8",
    )
    sha256sum = shutil.which("sha256sum")
    assert sha256sum is not None
    shell = f"""
set -euo pipefail
source {runner / "scripts" / "deploy.sh"}
sudo() {{
  [[ "$*" == "-n {root_helper} verify-control-plane" \
    || "$*" == "-n {root_helper} verify-restored" ]]
}}
preflight_control_plane
"""
    return subprocess.run(
        ["bash", "-c", shell],
        env={
            **os.environ,
            "RADON_DEPLOY_ROOT_HELPER": str(root_helper),
            "RADON_GATEWAY_CONTROL_HELPER": str(gateway_helper),
            "RADON_CONTROL_PLANE_MANIFEST": str(manifest),
            "RADON_CONTROL_PLANE_READY": str(ready),
            "RADON_SHA256SUM": sha256sum,
        },
        capture_output=True,
        text=True,
    )


def test_preflight_logs_and_continues_when_services_unit_hash_mismatches(
    tmp_path: Path,
) -> None:
    result = _run_preflight(tmp_path, API_UNIT)
    combined = result.stdout + result.stderr
    assert result.returncode == 0, combined
    assert API_UNIT in combined


@pytest.mark.parametrize(
    "mutate_rel",
    (HELPER_SOURCE, SUDOERS_SOURCE),
    ids=("scripts", "config"),
)
def test_preflight_still_fails_for_scripts_and_config_mismatches(
    tmp_path: Path, mutate_rel: str
) -> None:
    result = _run_preflight(tmp_path, mutate_rel)
    combined = result.stdout + result.stderr
    assert result.returncode != 0
    assert mutate_rel in combined


def test_ungranted_refresh_warns_and_does_not_fail_restart(tmp_path: Path) -> None:
    calls = tmp_path / "calls"
    shell = f"""
set -euo pipefail
source {DEPLOY}
prepare_release_transition() {{ return 0; }}
activate_staged_release() {{ printf 'activate\\n' >> {calls}; }}
wait_for_gateway_ready() {{ return 0; }}
sudo() {{
  printf '%s\\n' "$*" >> {calls}
  [[ "$*" == *install-units ]] && return 0
  [[ "$*" == *refresh-control-plane* ]] && return 1
  return 0
}}
restart_services aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb
"""
    result = subprocess.run(
        ["bash", "-c", shell],
        env={
            **os.environ,
            "RADON_DEPLOY_ROOT_HELPER": "/fixed/root-helper",
            "RADON_DIR": str(tmp_path / "live"),
            "RADON_CLOUD_DIR": str(CLOUD_ROOT),
        },
        capture_output=True,
        text=True,
    )
    combined = result.stdout + result.stderr
    assert result.returncode == 0, combined
    assert "bootstrap-control-plane.sh" in combined or "not granted" in combined.lower()
    lines = calls.read_text(encoding="utf-8").splitlines()
    assert "/fixed/root-helper restart-managed" in lines
    assert not any("rollback" in line.lower() for line in lines)
