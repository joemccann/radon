"""Account-scoped responder installation and compatibility contracts."""
from pathlib import Path
import importlib.util
import json
import os
import stat
import subprocess
from types import SimpleNamespace

import pytest

CLOUD = Path(__file__).resolve().parents[1]
HOME = "/var/lib/radon-responder"
USER = "radon-responder"
RESPONDER = "radon-grok-responder.service"
UPGRADE = "radon-grok-responder-upgrade.service"


def _directives(name):
    directory = "services/responder-domain" if name in {RESPONDER, UPGRADE} else "services"
    text = (CLOUD / directory / name).read_text()
    out = {}
    for line in text.splitlines():
        if line and not line.startswith(("#", ";", "[")) and "=" in line:
            key, value = line.split("=", 1)
            out.setdefault(key, []).append(value)
    return out


def test_isolated_responder_has_own_identity_home_and_readiness_gate():
    unit = _directives(RESPONDER)
    assert unit["User"] == [USER]
    assert unit["Group"] == [USER]
    assert unit["ProtectProc"] == ["invisible"]
    assert unit["ProtectHome"] == ["yes"]
    assert unit["ProtectSystem"] == ["strict"]
    assert unit["ConditionPathExists"] == [HOME + "/ready"]
    assert "HOME=" + HOME in unit["Environment"]
    assert "GROK_BIN=" + HOME + "/.local/bin/grok" in unit["Environment"]
    assert unit["WorkingDirectory"] == [HOME + "/repo"]
    assert unit["ExecStart"] == [
        "/usr/bin/python3.13 -E -S /usr/local/lib/radon/grok-responder/scripts/grok_page_responder.py"
    ]
    assert "RADON_GROK_REPO=" + HOME + "/repo" in unit["Environment"]
    assert "/usr/local/lib/radon/grok-responder" in " ".join(unit["ReadOnlyPaths"])
    assert HOME + "/repo" in " ".join(unit["ReadWritePaths"]).split()
    assert not any(
        path == "/usr/local/lib/radon/grok-responder" or path.startswith("/usr/local/lib/radon/grok-responder/")
        for path in " ".join(unit["ReadWritePaths"]).split()
    )
    assert ".venv" not in unit["ExecStart"][0]
    assert unit["EnvironmentFile"] == ["/etc/radon/grok-responder/env"]
    assert all("/home/radon" not in value for key in ("ExecStart", "ReadWritePaths") for value in unit.get(key, []))


def test_separate_upgrader_retains_controller_ownership_and_shared_runtime():
    unit = _directives(UPGRADE)
    assert unit["User"] == [USER]
    assert unit["ProtectProc"] == ["invisible"]
    assert unit["ConditionPathExists"] == [HOME + "/ready"]
    command = unit["ExecStart"][0]
    assert command.startswith("/usr/bin/python3.13 -E -S /usr/local/lib/radon/grok-responder-upgrade/scripts/grok_upgrade.py ")
    assert "--lock " + HOME + "/runtime/grok-runtime.lock" in command
    assert "--lkg " + HOME + "/runtime/grok_lkg.json" in command
    assert "--scratch " + HOME + "/upgrade" in command
    assert HOME + "/repo" in " ".join(unit["InaccessiblePaths"])
    assert "--live-bin /home/radon/" in (CLOUD / "services/radon-grok-upgrade.service").read_text()
    responder = _directives(RESPONDER)
    assert "RADON_GROK_RUNTIME_LOCK=" + HOME + "/runtime/grok-runtime.lock" in responder["Environment"]
    assert "RADON_GROK_LKG_PATH=" + HOME + "/runtime/grok_lkg.json" in responder["Environment"]


def test_new_timers_are_opt_in_and_do_not_replace_canonical_keeper_upgrade():
    for name in ("radon-grok-responder.timer", "radon-grok-responder-upgrade.timer"):
        assert (CLOUD / "services/responder-domain" / name).is_file()
        assert name not in (CLOUD / "config/auto-sync-units.txt").read_text().splitlines()
    canonical = _directives("radon-grok-upgrade.service")
    assert canonical["User"] == ["radon"]


def test_migration_prepares_independent_auth_and_controlled_handover():
    setup = (CLOUD / "scripts/setup-grok-responder-account.sh").read_text() + (CLOUD / "scripts/grok_responder_account.py").read_text()
    assert 'ACCOUNT=radon-responder' in setup
    assert 'ACCOUNT_HOME=/var/lib/radon-responder' in setup
    assert 'useradd' in setup and '--system' in setup
    assert 'grok login --device-auth' in setup
    assert 'finalize' in setup and 'rollback' in setup
    assert 'root-owned tree' in setup
    assert 'radon-grok-page-responder.timer' in setup
    assert 'radon-grok-responder.timer' in setup
    assert 'safe.directory' in setup
    assert 'chmod' in setup
    assert 'install-grok-responder-code.sh' in setup
    assert 'verb=refresh' in setup
    assert 'cp /home/radon/.grok' not in setup


def test_action_grants_match_both_accounts_during_migration():
    policy = (CLOUD / "config/polkit/50-radon-services.rules").read_text()
    assert 'subject.user != "radon-responder"' in policy
    assert 'subject.user != "radon"' in policy


def test_pickup_source_has_explicit_account_migration_selection():
    pickup = (CLOUD.parent / "scripts/grok_fix_pickup.py").read_text()
    assert 'RADON_GROK_FIX_SOURCE' in pickup
    doc = (CLOUD.parent / "docs/grok-page-responder.md").read_text()
    assert HOME + "/repo" in doc
    assert 'RADON_GROK_FIX_SOURCE=' in doc
    assert 'rollback' in doc.lower()


@pytest.fixture
def maintenance():
    spec = importlib.util.spec_from_file_location("grok_responder_account", CLOUD / "scripts/grok_responder_account.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_new_environment_is_scoped_and_retains_operator_controls(maintenance):
    production = "TURSO_DB_URL=libsql://synthetic.example.test\nTURSO_AUTH_TOKEN=unrelated-canonical\nGROK_RESPONDER_TURSO_AUTH_TOKEN=synthetic-scoped\nPUSHOVER_USER=synthetic-user\nPUSHOVER_TOKEN=synthetic-page\nGH_TOKEN=unrelated\nOPENAI_API_KEY=unrelated\n"
    result = maintenance.derive_environment(production, "GROK_PAGE_RESPONDER=1\nGROK_PAGE_AUTOSHIP=1\nGROK_PAGE_AUTOPUSH=1\nGROK_PAGE_MAX_ACTIONS_PER_DAY=3\n")
    values = maintenance.parse_env(result)
    assert values["TURSO_AUTH_TOKEN"] == "synthetic-scoped"
    assert "unrelated" not in result
    assert values["GROK_PAGE_AUTOPUSH"] == "0"
    assert values["GROK_PAGE_RESPONDER"] == values["GROK_PAGE_AUTOSHIP"] == "1"
    assert values["GROK_PAGE_MAX_ACTIONS_PER_DAY"] == "3"
    with pytest.raises(maintenance.MaintenanceError):
        maintenance.derive_environment(production.replace("GROK_RESPONDER_TURSO_AUTH_TOKEN=synthetic-scoped\n", ""), "")


def test_native_probe_environment_carries_no_service_credentials(maintenance, monkeypatch):
    monkeypatch.setenv("TURSO_AUTH_TOKEN", "synthetic-parent")
    monkeypatch.setenv("PUSHOVER_TOKEN", "synthetic-parent")
    monkeypatch.setenv("PYTHONPATH", "/synthetic-parent")
    environment = maintenance.native_env()
    assert environment["HOME"] == HOME
    assert not {"TURSO_AUTH_TOKEN", "PUSHOVER_TOKEN", "PYTHONPATH"} & environment.keys()


@pytest.mark.parametrize("failure", [None, "target-enable", "source-disable", "race-active"])
def test_forward_handover_preserves_a_working_timer_on_each_failure(maintenance, tmp_path, monkeypatch, failure):
    calls = []
    marker = tmp_path / "ready"
    active_checks = 0
    monkeypatch.setattr(maintenance, "atomic_root_file", lambda path, content, mode: path.write_bytes(content))

    def runner(args, **kwargs):
        nonlocal active_checks
        calls.append(args)
        if "--property=ActiveState" in args:
            active_checks += 1
            return SimpleNamespace(returncode=0, stdout="active" if failure == "race-active" and active_checks == 2 else "inactive")
        if failure == "target-enable" and args[1:3] == ["enable", "--now"] and args[-1] == maintenance.NEW_TIMERS[1]:
            raise maintenance.MaintenanceError("synthetic enable failure")
        if failure == "source-disable" and args[1:3] == ["disable", "--now"] and args[-1] == maintenance.OLD_TIMER:
            raise maintenance.MaintenanceError("synthetic disable failure")
        return SimpleNamespace(returncode=0)

    if failure:
        with pytest.raises(maintenance.MaintenanceError):
            maintenance.handover(forward=True, runner=runner, marker=marker)
        assert not marker.exists()
        assert ["/usr/bin/systemctl", "enable", "--now", maintenance.OLD_TIMER] in calls
    else:
        maintenance.handover(forward=True, runner=runner, marker=marker)
        assert marker.read_bytes() == b"ready\n"
        assert calls.index(["/usr/bin/systemctl", "disable", "--now", maintenance.OLD_TIMER]) < calls.index(["/usr/bin/systemctl", "enable", "--now", maintenance.NEW_TIMERS[0]])


def test_handover_never_stops_an_active_workload(maintenance, tmp_path):
    calls = []

    def active(args, **kwargs):
        calls.append(args)
        return SimpleNamespace(returncode=0, stdout="active")

    with pytest.raises(maintenance.MaintenanceError):
        maintenance.handover(forward=True, runner=active, marker=tmp_path / "ready")
    assert len(calls) == 1
    assert "--property=ActiveState" in calls[0]


def test_failed_rollback_restores_candidate_domain(maintenance, tmp_path, monkeypatch):
    marker = tmp_path / "ready"
    marker.write_text("ready\n")
    calls = []
    monkeypatch.setattr(maintenance, "atomic_root_file", lambda path, content, mode: path.write_bytes(content))

    def runner(args, **kwargs):
        calls.append(args)
        if "--property=ActiveState" in args:
            return SimpleNamespace(returncode=0, stdout="inactive")
        if args[1:3] == ["enable", "--now"] and args[-1] == maintenance.OLD_TIMER:
            raise maintenance.MaintenanceError("synthetic restore failure")
        return SimpleNamespace(returncode=0)

    with pytest.raises(maintenance.MaintenanceError):
        maintenance.handover(forward=False, runner=runner, marker=marker)
    assert marker.read_text() == "ready\n"
    for timer in maintenance.NEW_TIMERS:
        assert ["/usr/bin/systemctl", "enable", "--now", timer] in calls


def test_private_file_publication_rejects_links_and_preserves_previous(maintenance, tmp_path):
    path = tmp_path / "private"
    maintenance.atomic_root_file(path, b"synthetic-before", owner_uid=os.getuid())
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    other = tmp_path / "other"
    path.rename(other)
    path.symlink_to(other)
    with pytest.raises(maintenance.MaintenanceError):
        maintenance.atomic_root_file(path, b"synthetic-after", owner_uid=os.getuid())
    assert other.read_bytes() == b"synthetic-before"


def test_optional_telemetry_retirement_is_exact_and_uses_fake_transport(maintenance, monkeypatch):
    monkeypatch.setattr(maintenance, "read_root_file", lambda _: "TURSO_DB_URL=libsql://synthetic.example.test\nTURSO_AUTH_TOKEN=synthetic-token\n")
    seen = []

    class Response:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self): return b'{"results":[{"type":"ok"},{"type":"ok"}]}'

    def post(req, **kwargs):
        seen.append((json.loads(req.data), kwargs))
        return Response()

    maintenance.retire_optional_health(post=post)
    statement = seen[0][0]["requests"][0]["stmt"]
    assert statement == {"sql": "DELETE FROM service_health WHERE service = ?", "args": [{"type": "text", "value": "grok-responder-upgrade"}]}
    assert seen[0][1] == {"timeout": 10}


def test_nested_units_are_validated_by_operator_preparation():
    source = (CLOUD / "scripts/grok_responder_account.py").read_text()
    assert '"/usr/bin/systemd-analyze", "verify"' in source
    assert 'installed-units.sha256' in source
    drift = (CLOUD / "scripts/drift_audit.py").read_text()
    assert 'OPTIONAL_RESPONDER_INVENTORY' in drift
    assert 'repo_units.update(_optional_responder_units(drifts))' in drift


def test_optional_inventory_starts_only_after_root_opt_in(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("optional_drift_audit", CLOUD / "scripts/drift_audit.py")
    audit = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(audit)
    directory = tmp_path / "optional"
    directory.mkdir(mode=0o700)
    inventory = directory / "installed-units.sha256"
    monkeypatch.setattr(audit, "OPTIONAL_RESPONDER_INVENTORY", inventory)
    real_fstat = os.fstat

    def root_metadata(fd):
        meta = real_fstat(fd)
        return SimpleNamespace(st_uid=0, st_mode=meta.st_mode)

    isolated_os = SimpleNamespace(**vars(os))
    isolated_os.fstat = root_metadata
    monkeypatch.setattr(audit, "os", isolated_os)
    drifts = []
    assert audit._optional_responder_units(drifts) == {}
    assert drifts == []
    inventory.write_text("\n".join("a" * 64 + "  " + name for name in sorted(audit.OPTIONAL_RESPONDER_UNITS)) + "\n")
    inventory.chmod(0o600)
    selected = audit._optional_responder_units(drifts)
    assert set(selected) == audit.OPTIONAL_RESPONDER_UNITS
    assert all("responder-domain" in str(path) for path in selected.values())
    directory.chmod(0o770)
    assert audit._optional_responder_units(drifts) == {}
    assert drifts[-1]["id"] == "optional-unit-inventory"


@pytest.mark.parametrize("state", ["active", "activating", "deactivating", "reloading"])
@pytest.mark.parametrize("forward", [True, False])
@pytest.mark.parametrize("second_check", [False, True])
def test_handover_refuses_busy_states_before_and_after_timer_disable(maintenance, tmp_path, monkeypatch, state, forward, second_check):
    monkeypatch.setattr(maintenance, "atomic_root_file", lambda path, content, mode: path.write_bytes(content))
    calls = []
    disabled = False
    marker = tmp_path / "ready"
    if not forward:
        marker.write_text("ready\n")

    def runner(args, **kwargs):
        nonlocal disabled
        calls.append(args)
        if "--property=ActiveState" in args:
            selected = state if disabled or not second_check else "inactive"
            return SimpleNamespace(returncode=0, stdout=selected)
        if args[1:3] == ["disable", "--now"]:
            disabled = True
        return SimpleNamespace(returncode=0)

    with pytest.raises(maintenance.MaintenanceError):
        maintenance.handover(forward=forward, runner=runner, marker=marker)
    targets = maintenance.NEW_TIMERS if forward else (maintenance.OLD_TIMER,)
    assert not any(args[1:3] == ["enable", "--now"] and args[-1] in targets for args in calls)
    if not second_check:
        assert len(calls) == 1
    assert marker.exists() == (not forward)


@pytest.mark.parametrize("result", [(1, "inactive"), (0, ""), (0, "unknown"), (0, "inactive\nactive")])
@pytest.mark.parametrize("second_check", [False, True])
def test_handover_fails_closed_on_state_lookup_errors(maintenance, tmp_path, result, second_check):
    calls = []
    disabled = False
    marker = tmp_path / "ready"

    def runner(args, **kwargs):
        nonlocal disabled
        calls.append(args)
        if "--property=ActiveState" in args:
            status, state = result if disabled or not second_check else (0, "inactive")
            return SimpleNamespace(returncode=status, stdout=state)
        if args[1:3] == ["disable", "--now"]:
            disabled = True
        return SimpleNamespace(returncode=0)

    with pytest.raises(maintenance.MaintenanceError):
        maintenance.handover(forward=True, runner=runner, marker=marker)
    assert not marker.exists()
    assert not any(args[1:3] == ["enable", "--now"] and args[-1] in maintenance.NEW_TIMERS for args in calls)


@pytest.mark.parametrize("layout", ["native", "canonical", "canonical-link", "installer-native", "installer-empty", "dangling", "nonexecutable", "directory"])
def test_preparation_normalizes_native_installer_layout_without_overwriting_slots(maintenance, tmp_path, monkeypatch, layout):
    monkeypatch.setattr(maintenance, "ACCOUNT_HOME", tmp_path)
    canonical = tmp_path / ".local/bin/grok"
    native = tmp_path / ".grok/bin/grok"
    canonical.parent.mkdir(parents=True)
    native.parent.mkdir(parents=True)
    installs = []

    def executable(path):
        path.write_text("#!/bin/sh\nexit 0\n")
        path.chmod(0o700)

    if layout in {"native", "canonical-link"}:
        executable(native)
    if layout == "canonical":
        executable(canonical)
    elif layout == "canonical-link":
        canonical.symlink_to(native)
    elif layout == "dangling":
        canonical.symlink_to(tmp_path / "missing")
    elif layout == "nonexecutable":
        canonical.write_text("preserve this slot")
    elif layout == "directory":
        canonical.mkdir()

    def account(args, *, check=True, **kwargs):
        if args[:2] == ["/bin/bash", "-c"]:
            installs.append(args)
            if layout != "installer-empty":
                executable(native)
            return SimpleNamespace(returncode=0)
        result = subprocess.run([str(arg) for arg in args], capture_output=True, text=True, env=maintenance.native_env())
        if check and result.returncode:
            raise maintenance.MaintenanceError("synthetic layout rejected")
        return result

    monkeypatch.setattr(maintenance, "as_account", account)
    if layout in {"installer-empty", "dangling", "nonexecutable", "directory"}:
        with pytest.raises(maintenance.MaintenanceError):
            maintenance.ensure_native_cli()
        if layout == "dangling":
            assert canonical.is_symlink() and os.readlink(canonical) == str(tmp_path / "missing")
        elif layout == "nonexecutable":
            assert canonical.read_text() == "preserve this slot"
        elif layout == "directory":
            assert canonical.is_dir()
    else:
        maintenance.ensure_native_cli()
        assert canonical.is_file() and os.access(canonical, os.X_OK)
        assert bool(installs) == (layout == "installer-native")
        if layout != "canonical":
            assert canonical.is_symlink() and canonical.resolve() == native


@pytest.mark.parametrize("lookup_error", [OSError("synthetic lookup failure"), subprocess.TimeoutExpired("synthetic", 10)])
@pytest.mark.parametrize("second_check", [False, True])
def test_state_lookup_transport_errors_leave_no_candidate_timer_enabled(maintenance, tmp_path, lookup_error, second_check):
    calls = []
    disabled = False

    def runner(args, **kwargs):
        nonlocal disabled
        calls.append(args)
        if "--property=ActiveState" in args:
            if disabled or not second_check:
                raise lookup_error
            return SimpleNamespace(returncode=0, stdout="inactive")
        if args[1:3] == ["disable", "--now"]:
            disabled = True
        return SimpleNamespace(returncode=0)

    with pytest.raises(type(lookup_error)):
        maintenance.handover(forward=True, runner=runner, marker=tmp_path / "ready")
    assert not any(args[1:3] == ["enable", "--now"] and args[-1] in maintenance.NEW_TIMERS for args in calls)


def test_prepare_registers_gitdir_safety_for_pickup_without_trusting_other_repositories(maintenance, tmp_path, monkeypatch):
    """Exercise real Git ownership discovery for the server-side gitdir."""
    account_home = tmp_path / "responder"
    repo = account_home / "repo"
    unrelated = tmp_path / "unrelated"
    pickup_home = tmp_path / "pickup"
    pickup_home.mkdir()
    environment = {
        "PATH": os.defpath,
        "HOME": str(pickup_home),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": str(pickup_home / ".gitconfig"),
        "GIT_TERMINAL_PROMPT": "0",
    }

    def git(*args, different_owner=False):
        selected = dict(environment)
        if different_owner:
            # Git's own test switch exercises its real ownership check without
            # requiring root or modifying any real account/repository ownership.
            selected["GIT_TEST_ASSUME_DIFFERENT_OWNER"] = "1"
        return subprocess.run(
            ["git", "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false", *map(str, args)],
            cwd=tmp_path, env=selected, capture_output=True, text=True, timeout=15,
        )

    for candidate in (repo, unrelated):
        assert git("init", candidate).returncode == 0
        result = git("-C", candidate, "-c", "user.name=synthetic", "-c", "user.email=synthetic@example.test",
                     "commit", "--allow-empty", "-m", "synthetic pickup fixture")
        assert result.returncode == 0, result.stderr
    expected_head = git("-C", repo, "rev-parse", "HEAD").stdout.strip()
    assert len(expected_head) == 40
    assert git("config", "--global", "--add", "safe.directory", "/synthetic/prior").returncode == 0

    # New Git upload-pack permits any owner; older servers enforce this same
    # discovery gate. Check it directly, then retain the real transport below.
    # Fail rather than skip if this Git does not actually enforce ownership.
    for candidate in (repo, unrelated):
        refused = git("-C", candidate / ".git", "rev-parse", "HEAD", different_owner=True)
        assert refused.returncode != 0
        assert "dubious ownership" in refused.stderr
        assert str(candidate / ".git") in refused.stderr

    class DependencyBoundary(Exception):
        pass

    account = SimpleNamespace(pw_uid=32000, pw_gid=32000, pw_dir=str(account_home), pw_shell="/usr/sbin/nologin")
    pickup = SimpleNamespace(pw_uid=32001)
    monkeypatch.setattr(maintenance, "ACCOUNT_HOME", account_home)
    monkeypatch.setattr(maintenance, "READY", account_home / "ready")
    monkeypatch.setattr(maintenance, "pwd", SimpleNamespace(getpwnam=lambda name: account if name == USER else pickup))
    monkeypatch.setattr(maintenance, "grp", SimpleNamespace(
        getgrnam=lambda _: SimpleNamespace(gr_gid=32002),
        getgrgid=lambda _: SimpleNamespace(gr_name=USER),
    ))
    isolated_os = SimpleNamespace(**vars(os))
    isolated_os.getgrouplist = lambda *_: [32000, 32002]
    monkeypatch.setattr(maintenance, "os", isolated_os)
    # Only irrelevant host provisioning is replaced. Production prepare itself
    # must issue the Git registrations, which execute against the isolated HOME.
    monkeypatch.setattr(maintenance, "trusted_tree", lambda _: None)
    monkeypatch.setattr(maintenance, "root_directory", lambda *_: None)
    monkeypatch.setattr(maintenance, "owned_directory", lambda *_: None)
    monkeypatch.setattr(maintenance, "run", lambda *_args, **_kwargs: SimpleNamespace(returncode=0))
    registrations = []

    def as_account(args, *, account=USER, **kwargs):
        if args[:5] == ["/usr/bin/git", "config", "--global", "--add", "safe.directory"]:
            assert account == "radon"
            registrations.append(str(args[5]))
            result = git(*args[1:])
            assert result.returncode == 0, result.stderr
            return result
        if args[:3] == ["/usr/bin/python3.13", "-m", "venv"]:
            raise DependencyBoundary
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(maintenance, "as_account", as_account)
    with pytest.raises(DependencyBoundary):
        maintenance.prepare(tmp_path / "trusted-stage")

    resolved = git("-C", repo / ".git", "rev-parse", "HEAD", different_owner=True)
    assert resolved.returncode == 0, resolved.stderr
    assert resolved.stdout.strip() == expected_head
    advertised = git("ls-remote", repo / ".git", "HEAD", different_owner=True)
    assert advertised.returncode == 0, advertised.stderr
    assert advertised.stdout.split() == [expected_head, "HEAD"]
    status = git("-C", repo, "status", "--porcelain", different_owner=True)
    assert status.returncode == 0, status.stderr
    assert status.stdout == ""
    refused = git("-C", unrelated / ".git", "rev-parse", "HEAD", different_owner=True)
    assert refused.returncode != 0 and "dubious ownership" in refused.stderr
    assert str(unrelated / ".git") in refused.stderr
    values = git("config", "--global", "--get-all", "safe.directory").stdout.splitlines()
    assert values == ["/synthetic/prior", str(repo), str(repo / ".git")]
    assert registrations == [str(repo), str(repo / ".git")]
