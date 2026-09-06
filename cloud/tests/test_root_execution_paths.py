"""Root execution boundary for cloud/ units and the radon caddy sudoers rule.

Security scan 2026-08-11 (F1/F2/F4/F8): every root-owned execution path whose
payload lives in the radon-writable deploy checkout turns radon-level code
execution into root.

  * A `User=root` unit whose interpreter or script sits under /home/radon runs
    whatever the unprivileged account last wrote there.
  * CPython prepends the script's own directory to sys.path, so a module
    dropped BESIDE a root-run script (difflib.py, json.py) is imported by root
    even when the script itself is root-owned. `python3 -I` / `-P` closes that.
  * `radon ALL=(ALL) NOPASSWD: /bin/cp <checkout Caddyfile> /etc/caddy/Caddyfile`
    was both an arbitrary root READ (cp follows a symlinked source, so
    `ln -sf /etc/shadow <source>` published a root-only file to a 0644
    destination) and an unvalidated root WRITE of the edge proxy config, which
    scripts/api/auth.py:is_trusted_local_request depends on. Publishing now
    goes through one fixed root-owned action that stages, validates, installs
    atomically, and reloads under a bound.
"""

from __future__ import annotations

import os
import pathlib
import re
import shlex
import subprocess
import sys

import pytest


CLOUD_ROOT = pathlib.Path(__file__).resolve().parent.parent
SERVICES_DIR = CLOUD_ROOT / "services"
ROOT_HELPER = CLOUD_ROOT / "scripts" / "deploy-root-helper.sh"
CADDY_SUDOERS = CLOUD_ROOT / "config" / "sudoers.d" / "radon-caddy"

RADON_WRITABLE_PREFIX = "/home/radon"

# Units whose root payload still lives in the radon-writable checkout. This is
# an escape hatch for a unit that genuinely cannot be converted yet, NOT a
# parking lot: an entry here suppresses the class-level guard below for that
# unit, so it must stay empty in the steady state.
#   * radon-drift-audit.service now runs the root-owned control-plane copy of
#     the audit and receives the checkout root as an argument.
#   * radon-nextjs-db-watchdog.service now runs as radon and reaches root only
#     through the fixed, argument-validating operator restart sudo rule.
# test_pending_root_payload_exceptions_are_still_needed rejects stale entries.
PENDING_ROOT_OWNED_PAYLOAD: dict[str, str] = {}

INTERPRETER_ISOLATION_FLAGS = ("-I", "-P")


def _unit_texts(services_dir: pathlib.Path = SERVICES_DIR) -> dict[str, str]:
    """Base units with their `*.service.d/*.conf` drop-ins merged in.

    The drop-ins are precisely what flips five `User=radon` units to
    `User=root`, and they reset `ExecStartPre=`/`ExecStart=` and reinstate
    `ExecStart=` from scratch. Reading only `*.service` meant the guard below
    could not see the artifacts it exists to police. R-393.
    """
    texts: dict[str, str] = {}
    for path in sorted(services_dir.iterdir()):
        if path.is_file() and path.suffix == ".service":
            texts[path.name] = path.read_text(encoding="utf-8")
    for path in sorted(services_dir.glob("*.service.d/*.conf")):
        name = path.parent.name[: -len(".d")]
        # `radon-.service.d` is a systemd PREFIX drop-in with no base unit of its
        # own; merging it would invent a phantom `radon-.service`. Only drop-ins
        # that name a real unit are merged.
        if name not in texts:
            continue
        texts[name] = texts[name] + "\n" + path.read_text(encoding="utf-8")
    return texts


def _directive_values(text: str, prefix: str) -> list[str]:
    """Values for every directive starting with `prefix`, with reset semantics.

    An empty assignment (`ExecStart=`) clears the values accumulated for THAT
    exact key, which is how a drop-in replaces a base unit's command rather than
    appending to it. Without this the merged text above would still report the
    base unit's `/home/radon` ExecStart that the drop-in overrode. R-393.
    """
    per_key: dict[str, list[str]] = {}
    order: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith(prefix):
            continue
        key, sep, value = stripped.partition("=")
        if not sep:
            continue
        key = key.strip()
        if not key.startswith(prefix):
            continue
        if key not in per_key:
            per_key[key] = []
            order.append(key)
        cleaned = value.strip().lstrip("-+!:@").strip()
        if not cleaned:
            per_key[key].clear()
            continue
        per_key[key].append(cleaned)
    return [value for key in order for value in per_key[key]]


def _runs_as_root(text: str) -> bool:
    users = [value.strip() for value in _directive_values(text, "User")]
    # A unit that omits User= runs as root. Treating "no User= line" as
    # not-root would let a new unit opt out of every guard below by saying
    # nothing at all.
    return not users or "root" in users


def _root_units() -> dict[str, str]:
    return {name: text for name, text in _unit_texts().items() if _runs_as_root(text)}


def _exec_argv(text: str) -> list[list[str]]:
    argvs = []
    for value in _directive_values(text, "Exec"):
        if not value:
            continue
        argvs.append(shlex.split(value))
    return argvs


def _executed_tokens(argv: list[str]) -> list[str]:
    """The tokens root actually EXECUTES, as opposed to reads.

    An interpreter executes argv[0] and the script operand; arguments after the
    script are data. That distinction is the whole point of the drift audit's
    shape: root runs a root-owned copy of the audit and is HANDED the
    radon-writable checkout to compare against. Anything else is treated as
    executable, so a non-interpreter ExecStart cannot smuggle a payload past
    this.
    """
    if not argv:
        return []
    rest = argv[1:]
    if not pathlib.Path(argv[0]).name.startswith("python"):
        return argv
    operands = [token for token in rest if not token.startswith("-")]
    return [argv[0], *operands[:1]]


def _checkout_paths(argv: list[str]) -> list[str]:
    return [
        token
        for token in _executed_tokens(argv)
        if token.startswith(RADON_WRITABLE_PREFIX + "/")
    ]


def _checkout_payload_offenders(units: dict[str, str]) -> dict[str, list[str]]:
    offenders = {}
    for name, text in units.items():
        if name in PENDING_ROOT_OWNED_PAYLOAD:
            continue
        found = sorted({p for argv in _exec_argv(text) for p in _checkout_paths(argv)})
        if found:
            offenders[name] = found
    return offenders


def test_no_root_unit_executes_code_from_the_radon_writable_checkout():
    offenders = _checkout_payload_offenders(_root_units())
    assert not offenders, (
        "User=root units execute an interpreter or script the radon account can "
        f"rewrite: {offenders}. Install a root-owned copy outside /home/radon "
        "and run it with a root-owned interpreter."
    )


def _radon_writable_env_files(text: str) -> list[str]:
    return [
        value
        for value in _directive_values(text, "EnvironmentFile")
        if value.startswith(RADON_WRITABLE_PREFIX + "/")
    ]


def test_no_root_unit_inherits_a_radon_writable_environment_file():
    """Relocating the payload is not enough if the environment still comes from radon.

    systemd merges every KEY=VALUE line of an EnvironmentFile into the process
    environment with no filtering, and /home/radon/radon-cloud/.env is a
    compatibility path the unprivileged account can replace. One appended
    LD_PRELOAD line makes the next timer tick load radon-authored code into
    root, and PATH= makes root's bare `docker` / `systemctl` calls resolve to
    radon-owned binaries. `-I` does not affect either. A root unit may READ
    specific keys out of that file as data; it must not inherit the file
    wholesale.
    """
    offenders = {
        name: paths
        for name, text in _root_units().items()
        if (paths := _radon_writable_env_files(text))
    }
    assert not offenders, (
        "User=root units inherit an environment file the radon account can "
        f"rewrite: {offenders}. Read the specific keys the unit needs as data "
        "instead, and set PATH explicitly in the unit."
    )


def test_root_unit_pins_its_own_path():
    """Bare `docker` / `systemctl` in a root process must not resolve via an inherited PATH."""
    missing = [
        name
        for name, text in _root_units().items()
        if not any(
            value.startswith("PATH=") for value in _directive_values(text, "Environment")
        )
    ]
    assert not missing, (
        f"User=root units do not pin PATH: {missing}. A root process that execs "
        "bare binary names inherits whatever PATH it was given."
    )


CHECKOUT_INTERPRETER_UNIT = """\
[Service]
User=root
ExecStart=/home/radon/radon/.venv/bin/python /usr/local/lib/radon/probe.py
"""

CHECKOUT_SCRIPT_UNIT = """\
[Service]
User=root
ExecStart=/usr/bin/python3 -I /home/radon/radon/cloud/scripts/probe.py
"""


@pytest.mark.parametrize(
    ("label", "text"),
    (
        ("interpreter", CHECKOUT_INTERPRETER_UNIT),
        ("script", CHECKOUT_SCRIPT_UNIT),
    ),
)
def test_the_checkout_payload_guard_fails_loud_for_a_root_unit(label, text):
    """The guard above only means something if it can still fail.

    Round 1 of this fix exempted every User=root unit in cloud/services, which
    made the class-level assertion pass vacuously. Pin the detector against
    synthetic units so an empty offender set is evidence, not an artifact of the
    exemption list.
    """
    assert _runs_as_root(text)
    offenders = _checkout_payload_offenders({f"synthetic-{label}.service": text})
    assert offenders, (
        "the root-payload detector no longer flags a User=root unit whose "
        f"{label} lives under /home/radon"
    )


CHECKOUT_ARGUMENT_UNIT = """\
[Service]
User=root
ExecStart=/usr/bin/python3 -I /usr/local/lib/radon/probe.py /home/radon/radon/cloud
"""


def test_a_checkout_path_passed_as_data_is_not_a_root_payload():
    """The relaxation that lets the audit be handed its checkout, pinned.

    Root reading a radon-writable tree is the audit's job; root EXECUTING one
    is the finding. Widening this past the script operand would re-open F2 by
    accident.
    """
    assert not _checkout_payload_offenders({"synthetic-argument.service": CHECKOUT_ARGUMENT_UNIT})
    assert _checkout_paths(_exec_argv(CHECKOUT_SCRIPT_UNIT)[0]), (
        "the same shape with the checkout in the SCRIPT position must still flag"
    )


def test_pending_root_payload_exceptions_are_still_needed():
    units = _unit_texts()
    stale = []
    for name in PENDING_ROOT_OWNED_PAYLOAD:
        text = units.get(name, "")
        if not text or not _runs_as_root(text):
            stale.append(name)
            continue
        if not [p for argv in _exec_argv(text) for p in _checkout_paths(argv)]:
            stale.append(name)
    assert not stale, (
        "these units no longer execute checkout code as root -- drop them from "
        f"PENDING_ROOT_OWNED_PAYLOAD so the contract stays enforced: {stale}"
    )


@pytest.mark.parametrize("name", sorted(_root_units()))
def test_root_python_units_do_not_import_from_their_script_directory(name):
    text = _root_units()[name]
    for argv in _exec_argv(text):
        if not argv or not pathlib.Path(argv[0]).name.startswith("python"):
            continue
        options = []
        for token in argv[1:]:
            if not token.startswith("-"):
                break
            options.append(token)
        isolated = any(
            option in INTERPRETER_ISOLATION_FLAGS
            or (not option.startswith("--") and any(f[1] in option[1:] for f in INTERPRETER_ISOLATION_FLAGS))
            for option in options
        )
        assert isolated, (
            f"{name} runs python as root without -I/-P, so a module dropped "
            f"beside {argv[-1]} is imported by root: {' '.join(argv)}"
        )


INSTALLED_DRIFT_AUDIT = "/usr/local/lib/radon/drift_audit.py"
CHECKOUT_CLOUD_ROOT = "/home/radon/radon/cloud"


def test_drift_audit_runs_the_installed_root_owned_copy():
    text = (SERVICES_DIR / "radon-drift-audit.service").read_text(encoding="utf-8")
    argv = _exec_argv(text)[0]
    assert INSTALLED_DRIFT_AUDIT in argv, (
        "the root drift audit must execute the control-plane copy, not the "
        f"radon-writable checkout script: {' '.join(argv)}"
    )
    assert CHECKOUT_CLOUD_ROOT in argv, (
        "the relocated audit no longer derives the checkout from __file__, so "
        "the unit must pass the checkout root it audits against"
    )


def test_drift_audit_is_a_control_plane_bundle_member():
    """A root-owned copy is only root-owned if the root bootstrap installs it."""
    bootstrap = (CLOUD_ROOT / "scripts" / "bootstrap-control-plane.sh").read_text(
        encoding="utf-8"
    )
    helper = ROOT_HELPER.read_text(encoding="utf-8")
    for text, label in ((bootstrap, "bootstrap"), (helper, "root helper")):
        assert "scripts/drift_audit.py" in text, f"{label} omits the audit source"
        assert INSTALLED_DRIFT_AUDIT in text, f"{label} omits the audit target"


def test_nextjs_db_watchdog_does_not_run_as_root():
    text = (SERVICES_DIR / "radon-nextjs-db-watchdog.service").read_text(
        encoding="utf-8"
    )
    assert not _runs_as_root(text), (
        "the probe execs the radon-owned venv interpreter and imports the "
        "checkout, so running it as root hands radon a root shell; it needs "
        "only one privileged action and the operator sudo rule already grants "
        "exactly that"
    )


def test_nextjs_db_watchdog_uses_systemd_owned_private_state_directory():
    """State provisioning must not recursively chown a writable tree."""
    text = (SERVICES_DIR / "radon-nextjs-db-watchdog.service").read_text(
        encoding="utf-8"
    )
    assert "StateDirectory=radon-nextjs-db-watchdog" in text
    assert "StateDirectoryMode=0700" in text
    assert "chown" not in text


def test_nextjs_db_watchdog_restarts_through_the_validating_operator():
    probe = (CLOUD_ROOT / "scripts" / "nextjs_db_watchdog.py").read_text(
        encoding="utf-8"
    )
    assert "/usr/local/bin/radon" in probe and "radon-nextjs.service" in probe, (
        "the restart must go through the argument-validating operator, which "
        "config/sudoers.d/radon-ops already grants for radon-*.service"
    )
    ops_rules = (CLOUD_ROOT / "config" / "sudoers.d" / "radon-ops").read_text(
        encoding="utf-8"
    )
    assert "/usr/local/bin/radon unit restart radon-*.service" in ops_rules


def test_caddy_sudoers_grants_no_root_file_write():
    text = CADDY_SUDOERS.read_text(encoding="utf-8")
    rules = "\n".join(
        line for line in text.splitlines() if line.strip() and not line.strip().startswith("#")
    )
    for command in ("cp", "dd", "tee", "install", "mv", "ln", "sh", "bash"):
        for directory in ("/bin/", "/usr/bin/", "/usr/local/bin/"):
            assert f"{directory}{command}" not in rules, (
                f"radon-caddy grants {directory}{command}: a root file write whose "
                "source the radon account controls (cp also follows a symlinked "
                "source, which reads any root-only file into a 0644 destination)"
            )
    assert "/etc/caddy/Caddyfile" not in rules, (
        "radon-caddy still names the live Caddyfile as a sudo argument; the "
        "destination must only be reachable through the fixed helper action"
    )


def test_caddy_sudoers_publishes_through_the_fixed_root_helper():
    text = CADDY_SUDOERS.read_text(encoding="utf-8")
    rules = [
        line.strip()
        for line in text.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    assert any(
        "/usr/local/sbin/radon-deploy-root publish-caddy" in line for line in rules
    ), "radon-caddy must publish Caddy config through the fixed root-owned action"
    for line in rules:
        assert "ALL=(root)" in line, (
            f"radon-caddy rule widens the run-as scope beyond root: {line}"
        )


def _write_executable(path: pathlib.Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)


def _publish_fixture(
    tmp_path: pathlib.Path, *, caddy_valid: bool = True, caddy_stderr: str = ""
):
    systemctl_log = tmp_path / "systemctl.log"
    fake_systemctl = tmp_path / "systemctl"
    # A real host almost always has queued jobs for radon-* units in flight.
    # Emitting one makes any cancel_radon_jobs call observable in the log.
    _write_executable(
        fake_systemctl,
        f"""#!/bin/bash
printf '%s\\n' "$*" >> {shlex.quote(str(systemctl_log))}
if [[ "${{1:-}}" == "list-jobs" ]]; then
  printf '%s\\n' "4242 radon-api.service start running"
fi
exit 0
""",
    )
    caddy_log = tmp_path / "caddy.log"
    fake_caddy = tmp_path / "caddy"
    _write_executable(
        fake_caddy,
        f"""#!/bin/bash
printf '%s\\n' "$*" >> {shlex.quote(str(caddy_log))}
printf '%s' {shlex.quote(caddy_stderr)} >&2
exit {0 if caddy_valid else 1}
""",
    )
    fake_sync = tmp_path / "sync"
    _write_executable(fake_sync, "#!/bin/bash\nexit 0\n")
    fake_rm = tmp_path / "rm"
    _write_executable(fake_rm, '#!/bin/bash\nexec /bin/rm "$@"\n')

    source = tmp_path / "checkout" / "caddy" / "Caddyfile"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("app.radon.run {\n  respond \"ok\"\n}\n", encoding="utf-8")
    config = tmp_path / "etc" / "caddy" / "Caddyfile"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text("# live known-good\n", encoding="utf-8")

    env = {
        **os.environ,
        "RADON_DEPLOY_HELPER_TEST_MODE": "1",
        "RADON_TEST_SYSTEMCTL": str(fake_systemctl),
        "RADON_TEST_RM": str(fake_rm),
        "RADON_TEST_SYNC": str(fake_sync),
        "RADON_TEST_ACTIVE_STATE_FILE": str(tmp_path / "active-units"),
        "RADON_TEST_REPLICA_PREFIX": str(tmp_path / "replica.db"),
        "RADON_TEST_CADDY_SOURCE": str(source),
        "RADON_TEST_CADDY_CONFIG": str(config),
        "RADON_TEST_CADDY_BIN": str(fake_caddy),
    }
    return env, source, config, systemctl_log, caddy_log


def _run_publish(env) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(ROOT_HELPER), "publish-caddy"],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_publish_caddy_validates_then_installs_and_reloads(tmp_path):
    env, source, config, systemctl_log, caddy_log = _publish_fixture(tmp_path)
    result = _run_publish(env)
    assert result.returncode == 0, result.stdout + result.stderr
    assert config.read_text(encoding="utf-8") == source.read_text(encoding="utf-8")
    assert "validate" in caddy_log.read_text(encoding="utf-8")
    assert "reload caddy" in systemctl_log.read_text(encoding="utf-8")


def test_publish_caddy_refuses_a_symlinked_source(tmp_path):
    env, source, config, systemctl_log, _caddy_log = _publish_fixture(tmp_path)
    secret = tmp_path / "shadow"
    secret.write_text("root:$6$rootpasswordhash\n", encoding="utf-8")
    source.unlink()
    source.symlink_to(secret)
    before = config.read_text(encoding="utf-8")

    result = _run_publish(env)

    assert result.returncode != 0
    assert config.read_text(encoding="utf-8") == before, (
        "a symlinked source published a root-only file to the 0644 live config"
    )
    assert not systemctl_log.exists() or "reload caddy" not in systemctl_log.read_text(
        encoding="utf-8"
    )


def test_publish_caddy_leaves_live_config_untouched_when_validation_fails(tmp_path):
    env, _source, config, systemctl_log, _caddy_log = _publish_fixture(
        tmp_path, caddy_valid=False
    )
    before = config.read_text(encoding="utf-8")

    result = _run_publish(env)

    assert result.returncode != 0
    assert config.read_text(encoding="utf-8") == before
    assert not systemctl_log.exists() or "reload caddy" not in systemctl_log.read_text(
        encoding="utf-8"
    )
    assert not list(config.parent.glob("Caddyfile.candidate.*")), (
        "a rejected candidate was left behind in the live config directory"
    )


def test_rejected_caddy_candidate_does_not_cancel_queued_radon_jobs(tmp_path):
    """A publish failure must not tear down another operation's systemd jobs.

    supervise_root_action cancels every queued radon-* job when its child exits
    non-zero, which is the right recovery for a torn stop-clean/restart-managed
    but catastrophic for an unprivileged caller who merely pushed a Caddyfile
    that failed validation: a deploy's in-flight restarts would be cancelled.
    """
    env, _source, _config, systemctl_log, _caddy_log = _publish_fixture(
        tmp_path, caddy_valid=False
    )

    result = _run_publish(env)

    assert result.returncode != 0
    commands = systemctl_log.read_text(encoding="utf-8") if systemctl_log.exists() else ""
    assert "cancel" not in commands, (
        "a rejected Caddyfile cancelled queued systemd jobs for radon-* units: "
        f"{commands!r}"
    )


def test_caddy_validation_output_is_not_relayed_to_the_unprivileged_caller(tmp_path):
    """caddyfile adapter errors quote the offending token.

    The publish source is radon-writable, so `import /etc/shadow` in it turns a
    relayed validator diagnostic into a root-only file read. The caller gets the
    verdict, never the validator's own output.
    """
    leaked = "root:$6$SECRETHASH:19000:0:99999:7:::"
    env, _source, config, _systemctl_log, _caddy_log = _publish_fixture(
        tmp_path, caddy_valid=False, caddy_stderr=f"parse error near {leaked}\n"
    )
    before = config.read_text(encoding="utf-8")

    result = _run_publish(env)

    assert result.returncode != 0
    assert config.read_text(encoding="utf-8") == before
    assert leaked not in result.stderr + result.stdout, (
        "caddy validate output reached the unprivileged caller, leaking the "
        "content of whatever file the candidate imported"
    )


def test_publish_caddy_restarts_when_reload_fails(tmp_path):
    """TERMing a hung reload leaves Type=notify in `reloading`. Later
    `systemctl reload caddy` waits out the helper timeout and rolls back
    (db69ccb4 through 3866d693, HTTP-only included). Restart unwedes and
    loads the already-installed candidate."""
    env, source, config, systemctl_log, _caddy_log = _publish_fixture(tmp_path)
    fake_systemctl = tmp_path / "systemctl"
    _write_executable(
        fake_systemctl,
        f"""#!/bin/bash
printf '%s\\n' "$*" >> {shlex.quote(str(systemctl_log))}
if [[ "${{1:-}}" == "reload" ]]; then
  exit 1
fi
exit 0
""",
    )

    result = _run_publish(env)

    assert result.returncode == 0, result.stdout + result.stderr
    commands = systemctl_log.read_text(encoding="utf-8")
    assert "reload caddy" in commands
    assert "restart caddy" in commands
    assert config.read_text(encoding="utf-8") == source.read_text(encoding="utf-8")


def test_publish_caddy_action_timeout_outlasts_reload_and_restart():
    """publish-caddy is supervised at ROOT_MUTATION_ACTION_TIMEOUT (180s).

    reload_caddy used that full 180s, so the supervisor killed the helper
    before restart_caddy ran (11a0575d and 868ee0f2 both failed at 180s
    with no restart). The action budget must cover reload + restart.
    """
    text = ROOT_HELPER.read_text(encoding="utf-8")
    reload_s = int(
        re.search(
            r'kill-after=2s\s+(\d+)s\s+"\$SYSTEMCTL"\s+reload caddy', text
        ).group(1)
    )
    restart_s = int(
        re.search(
            r'kill-after=2s\s+(\d+)s\s+"\$SYSTEMCTL"\s+restart caddy', text
        ).group(1)
    )
    start = text.index("root_action_timeout() {")
    body = text[start : text.index("\n}", start) + 2]
    assert "publish-caddy" in body
    # Dedicated publish budget, or the shared mutation timeout if not split.
    dedicated = re.search(
        r'ROOT_PUBLISH_CADDY_ACTION_TIMEOUT=(\d+)', text
    )
    mutation = int(re.search(r'(?m)^  readonly ROOT_MUTATION_ACTION_TIMEOUT=(\d+)', text).group(1))
    action_s = int(dedicated.group(1)) if dedicated else mutation
    assert action_s >= reload_s + restart_s + 20, (
        f"publish-caddy action {action_s}s cannot fit reload {reload_s}s "
        f"+ restart {restart_s}s"
    )


if __name__ == "__main__":  # pragma: no cover - convenience only
    sys.exit(pytest.main([__file__]))
