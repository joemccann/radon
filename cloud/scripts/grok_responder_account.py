#!/usr/bin/env python3
"""Operator-owned preparation and transactional responder account handover."""
from __future__ import annotations

import fcntl
import grp
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import secrets
import shutil
import stat
import subprocess
import sys
from urllib import request

ACCOUNT = "radon-responder"
ACCOUNT_HOME = Path("/var/lib/radon-responder")
PICKUP_GROUP = "radon-responder-pickup"
PRIVATE_DIR = Path("/etc/radon/grok-responder")
PRODUCTION_ENV = Path("/etc/radon/env")
LEGACY_ENV = Path("/home/radon/radon-page-responder.env")
LEGACY_REPO = Path("/home/radon/radon-page-responder")
CONTROLLER = Path("/usr/local/lib/radon/grok-responder-upgrade")
CODE_ROOT = Path("/usr/local/lib/radon/grok-responder")
CODE_INSTALLER = "cloud/scripts/install-grok-responder-code.sh"
INSTALLED_HELPER = Path("/usr/local/lib/radon/grok_responder_account.py")
SYSTEMD = Path("/etc/systemd/system")
READY = ACCOUNT_HOME / "ready"
INVENTORY = PRIVATE_DIR / "installed-units.sha256"
OPTIONAL_UNITS = (
    "radon-grok-responder.service", "radon-grok-responder.timer",
    "radon-grok-responder-upgrade.service", "radon-grok-responder-upgrade.timer",
)
NEW_TIMERS = ("radon-grok-responder.timer",)
RETIRED_TIMER = "radon-grok-responder-upgrade.timer"
MODEL_GROUP = "radon-model-connect"
MODEL_ROOT = Path("/opt/radon-model-clients/current")
MODEL_SOCKET = "/run/radon-model-gateway/gateway.sock"
OLD_TIMER = "radon-grok-page-responder.timer"
OPERATOR_FLAGS = ("GROK_PAGE_RESPONDER", "GROK_PAGE_AUTOSHIP", "GROK_PAGE_MAX_ACTIONS_PER_DAY")


class MaintenanceError(RuntimeError):
    pass


def run(argv, *, check=True, timeout=900, **kwargs):
    result = subprocess.run([str(x) for x in argv], capture_output=True, text=True, timeout=timeout, **kwargs)
    if check and result.returncode:
        # Child output can contain native authentication diagnostics. Never echo it.
        raise MaintenanceError("operation failed: " + Path(str(argv[0])).name)
    return result


def trusted_tree(path: Path, *, owner_uid=0):
    path = Path(path)
    if not path.is_absolute():
        raise MaintenanceError("root-owned tree requires an absolute path")
    for component in (path, *path.parents):
        meta = component.lstat()
        if stat.S_ISLNK(meta.st_mode) or meta.st_uid not in {0, owner_uid}:
            raise MaintenanceError("source must be a root-owned tree")
        if (meta.st_mode & 0o002 or (meta.st_mode & 0o020 and meta.st_gid != 0)) and not (stat.S_ISDIR(meta.st_mode) and meta.st_mode & stat.S_ISVTX):
            raise MaintenanceError("root-owned tree is writable by another account")


def native_env():
    return {"HOME": str(ACCOUNT_HOME), "USER": ACCOUNT, "LOGNAME": ACCOUNT,
            "PATH": str(MODEL_ROOT / "bin") + ":/usr/local/bin:/usr/bin:/bin",
            "RADON_MODEL_GATEWAY_SOCKET": MODEL_SOCKET,
            "LANG": "C.UTF-8", "GROK_PAGE_NO_DOTENV": "1"}


def as_account(argv, *, account=ACCOUNT, **kwargs):
    environment = native_env() if account == ACCOUNT else {
        "HOME": "/home/radon", "USER": "radon", "LOGNAME": "radon",
        "PATH": "/usr/local/bin:/usr/bin:/bin", "LANG": "C.UTF-8"}
    assignments = [f"{key}={value}" for key, value in environment.items()]
    return run(["/usr/bin/sudo", "-u", account, "/usr/bin/env", "-i", *assignments, *argv], **kwargs)


def parse_env(text):
    values = {}
    for line in text.splitlines():
        key, sep, value = line.partition("=")
        if sep:
            values[key.strip()] = value.strip()
    return values


def unquote(value):
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


def derive_environment(production_text, existing_text):
    production, existing = parse_env(production_text), parse_env(existing_text)
    mapping = (("TURSO_DB_URL", "TURSO_DB_URL"),
               ("TURSO_AUTH_TOKEN", "GROK_RESPONDER_TURSO_AUTH_TOKEN"),
               ("PUSHOVER_USER", "PUSHOVER_USER"), ("PUSHOVER_TOKEN", "PUSHOVER_TOKEN"))
    if any(not unquote(production.get(source, "")).strip() for _, source in mapping):
        raise MaintenanceError("required responder environment inputs are missing")
    lines = [f"{target}={production[source]}" for target, source in mapping]
    for name in OPERATOR_FLAGS:
        value = unquote(existing.get(name, ""))
        if re.fullmatch(r"[0-9]{1,4}", value):
            lines.append(f"{name}={value}")
    lines += ["GROK_PAGE_AUTOPUSH=0", "GROK_PAGE_NO_DOTENV=1", "GROK_PAGE_SYNC_REMOTE=1",
              "GROK_BIN=" + str(MODEL_ROOT / "bin/grok"), "RADON_MODEL_GATEWAY_SOCKET=" + MODEL_SOCKET]
    return "\n".join(lines) + "\n"


def read_root_file(path: Path):
    trusted_tree(path.parent)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        meta = os.fstat(fd)
        if not stat.S_ISREG(meta.st_mode) or meta.st_uid != 0 or meta.st_mode & 0o022:
            raise MaintenanceError("private input ownership or mode is invalid")
        with os.fdopen(fd, "r") as stream:
            fd = -1
            result = stream.read(1_048_577)
        if len(result) > 1_048_576:
            raise MaintenanceError("private input exceeds the supported size")
        return result
    finally:
        if fd >= 0:
            os.close(fd)


def root_directory(path: Path, mode=0o700):
    if not path.exists() and not path.is_symlink():
        trusted_tree(path.parent)
        path.mkdir(mode=mode)
    trusted_tree(path)
    path.chmod(mode)


def atomic_root_file(path: Path, content: bytes, mode=0o600, *, owner_uid=0, owner_gid=None):
    trusted_tree(path.parent, owner_uid=owner_uid)
    directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    temporary = "." + path.name + "." + secrets.token_hex(12)
    try:
        try:
            current = os.stat(path.name, dir_fd=directory, follow_symlinks=False)
        except FileNotFoundError:
            current = None
        if current and (not stat.S_ISREG(current.st_mode) or current.st_uid != owner_uid):
            raise MaintenanceError("private destination is not a canonical root file")
        fd = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600, dir_fd=directory)
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
            stream.flush()
            if owner_gid is not None:
                os.fchown(stream.fileno(), owner_uid, owner_gid)
            os.fchmod(stream.fileno(), mode)
            os.fsync(stream.fileno())
        os.rename(temporary, path.name, src_dir_fd=directory, dst_dir_fd=directory)
        os.fsync(directory)
    finally:
        try:
            os.unlink(temporary, dir_fd=directory)
        except FileNotFoundError:
            pass
        os.close(directory)


def owned_directory(path, uid, gid, mode):
    relative = path.relative_to(ACCOUNT_HOME)
    trusted_tree(ACCOUNT_HOME)
    fd = os.open(ACCOUNT_HOME, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in relative.parts:
            try:
                os.mkdir(part, mode, dir_fd=fd)
            except FileExistsError:
                pass
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
            if os.fstat(fd).st_uid not in {0, uid}:
                raise MaintenanceError("account directory ownership is invalid")
        os.fchown(fd, uid, gid)
        os.fchmod(fd, mode)
    finally:
        os.close(fd)


def ensure_native_cli():
    """Verify the root-owned CI receipt as the responder, never install a CLI."""
    proof = ("import sys;sys.path.insert(0,sys.argv[1]);"
             "from grok_runtime import managed_manifest;managed_manifest()")
    as_account(["/usr/bin/python3.13", "-I", "-S", "-c", proof, CODE_ROOT / "scripts"])


def derive_model_environment(original, uid, gid):
    if any(type(value) is not int or value <= 0 or value == 1000 for value in (uid, gid)):
        raise MaintenanceError("invalid dedicated model identity")
    values = parse_env(original)
    existing_gid = unquote(values.get("RADON_MODEL_TUNNEL_GID", ""))
    if existing_gid and existing_gid != str(gid):
        raise MaintenanceError("model admission group differs from existing transport")
    peers = unquote(values.get("RADON_MODEL_CLIENT_UIDS", ""))
    if peers and not re.fullmatch(r"[1-9][0-9]*(,[1-9][0-9]*){0,15}", peers):
        raise MaintenanceError("invalid existing model peer list")
    allowed = {int(value) for value in peers.split(",") if value}
    allowed.add(uid)
    if 1000 in allowed or len(allowed) > 16:
        raise MaintenanceError("model peer admission exceeds its scope")
    updates = {"RADON_MODEL_TUNNEL_GID": str(gid), "RADON_MODEL_CLIENT_UIDS": ",".join(map(str, sorted(allowed)))}
    lines = [line for line in original.splitlines() if line.partition("=")[0] not in updates]
    return "\n".join(lines + [name + "=" + value for name, value in updates.items()]) + "\n"


def admit_model_client(account):
    try:
        group = grp.getgrnam(MODEL_GROUP)
    except KeyError:
        run(["/usr/sbin/groupadd", "--system", MODEL_GROUP])
        group = grp.getgrnam(MODEL_GROUP)
    if account.pw_gid in {0, 1000}:
        raise MaintenanceError("responder primary group has application authority")
    pickup = grp.getgrnam(PICKUP_GROUP)
    allowed_groups = {account.pw_gid, pickup.gr_gid, group.gr_gid}
    if set(os.getgrouplist(ACCOUNT, account.pw_gid)) - allowed_groups:
        raise MaintenanceError("responder account has unrelated supplemental groups")
    original = read_root_file(PRODUCTION_ENV)
    updated = derive_model_environment(original, account.pw_uid, group.gr_gid)
    run(["/usr/sbin/usermod", "-aG", MODEL_GROUP, ACCOUNT])
    metadata = PRODUCTION_ENV.stat()
    if original != updated:
        atomic_root_file(PRODUCTION_ENV, updated.encode(), stat.S_IMODE(metadata.st_mode), owner_gid=metadata.st_gid)
        run(["/usr/bin/systemctl", "try-restart", "radon-model-gateway.service"])


def install_responder_code(stage: Path):
    """Publish the executed tree. The account clone stays a writable worktree."""
    installer = stage / CODE_INSTALLER
    scripts = stage / "scripts"
    trusted_tree(installer)
    trusted_tree(scripts / "grok_page_responder.py")
    run(["/bin/bash", installer, scripts, CODE_ROOT])


def publish_optional_units(stage: Path):
    source = stage / "cloud/services/responder-domain"
    trusted_tree(source)
    run(["/usr/bin/systemd-analyze", "verify", *[source / name for name in OPTIONAL_UNITS]])
    inventory = []
    for name in OPTIONAL_UNITS:
        trusted_tree(source / name)
        content = (source / name).read_bytes()
        atomic_root_file(SYSTEMD / name, content, 0o644)
        inventory.append(hashlib.sha256(content).hexdigest() + "  " + name)
    atomic_root_file(INVENTORY, ("\n".join(inventory) + "\n").encode())
    policy_source = stage / "cloud/config/polkit/50-radon-services.rules"
    trusted_tree(policy_source)
    atomic_root_file(PRIVATE_DIR / "policy.sha256", (hashlib.sha256(policy_source.read_bytes()).hexdigest() + "\n").encode())
    root_directory(INSTALLED_HELPER.parent, 0o755)
    atomic_root_file(INSTALLED_HELPER, Path(__file__).read_bytes(), 0o644)
    run(["/usr/bin/systemctl", "daemon-reload"])
    run(["/usr/bin/systemctl", "disable", "--now", RETIRED_TIMER])


def _responder_idle():
    for workload in ("radon-grok-responder.service", "radon-grok-responder-upgrade.service"):
        # Pass `run` at call time. The default argument is bound at import.
        if _unit_active(workload, runner=run):
            raise MaintenanceError("responder workload is active; retry after it finishes")


def refresh(stage: Path):
    """Upgrade an already-finalized domain without rebuilding its clone."""
    trusted_tree(stage)
    trusted_tree(stage / "cloud/scripts")
    trusted_tree(stage / "scripts")
    if READY.is_symlink() or not READY.is_file():
        raise MaintenanceError("refresh requires the prepared responder marker")
    _responder_idle()
    install_responder_code(stage)
    account = pwd.getpwnam(ACCOUNT)
    admit_model_client(account)
    ensure_native_cli()
    production = read_root_file(PRODUCTION_ENV)
    existing = read_root_file(PRIVATE_DIR / "env")
    atomic_root_file(PRIVATE_DIR / "env", derive_environment(production, existing).encode())
    _responder_idle()
    publish_optional_units(stage)


def prepare(stage: Path):
    trusted_tree(stage)
    trusted_tree(stage / "cloud/scripts")
    trusted_tree(stage / "scripts")
    if READY.exists():
        raise MaintenanceError("rollback the active domain before preparing a replacement")
    root_directory(ACCOUNT_HOME, 0o755)
    try:
        account = pwd.getpwnam(ACCOUNT)
    except KeyError:
        run(["/usr/sbin/useradd", "--system", "--user-group", "--no-create-home", "--home-dir", ACCOUNT_HOME,
             "--shell", "/usr/sbin/nologin", ACCOUNT])
        account = pwd.getpwnam(ACCOUNT)
    if (account.pw_dir != str(ACCOUNT_HOME) or account.pw_uid in {0, pwd.getpwnam("radon").pw_uid}
            or grp.getgrgid(account.pw_gid).gr_name != ACCOUNT or account.pw_shell != "/usr/sbin/nologin"):
        raise MaintenanceError("existing responder account layout differs")
    try:
        pickup_group = grp.getgrnam(PICKUP_GROUP)
    except KeyError:
        run(["/usr/sbin/groupadd", "--system", PICKUP_GROUP])
        pickup_group = grp.getgrnam(PICKUP_GROUP)
    for member in (ACCOUNT, "radon"):
        run(["/usr/sbin/usermod", "-aG", PICKUP_GROUP, member])
    allowed_groups = {account.pw_gid, pickup_group.gr_gid}
    try:
        model_gid = grp.getgrnam(MODEL_GROUP).gr_gid
        if model_gid in {0, 1000}:
            raise MaintenanceError("model group aliases an application authority")
        allowed_groups.add(model_gid)
    except KeyError:
        pass
    if set(os.getgrouplist(ACCOUNT, account.pw_gid)) - allowed_groups:
        raise MaintenanceError("responder account has unrelated supplemental groups")
    for child in (".grok", ".local", "runtime", "upgrade", "cache"):
        owned_directory(ACCOUNT_HOME / child, account.pw_uid, account.pw_gid, 0o700)
    for child in (".grok/bin", ".grok/hooks", ".grok/downloads", ".local/bin"):
        owned_directory(ACCOUNT_HOME / child, account.pw_uid, account.pw_gid, 0o700)
    repo = ACCOUNT_HOME / "repo"
    owned_directory(repo, account.pw_uid, pickup_group.gr_gid, 0o2750)
    if not (repo / ".git").exists():
        as_account(["/usr/bin/git", "clone", "--", "https://github.com/joemccann/radon.git", repo])
    for key, value in (("user.name", "radon-grok-responder"), ("user.email", "ops@radon.run"), ("core.sharedRepository", "0640")):
        as_account(["/usr/bin/git", "-C", repo, "config", key, value])
    as_account(["/usr/bin/touch", repo / ".radon-page-responder"])
    as_account(["/usr/bin/chmod", "-R", "g-w,g+rX,o-rwx", repo])
    for path in (repo, repo / ".git"):
        as_account(["/usr/bin/git", "config", "--global", "--add", "safe.directory", path], account="radon")
    if not (repo / ".venv/bin/python").exists():
        as_account(["/usr/bin/python3.13", "-m", "venv", repo / ".venv"])
    as_account([repo / ".venv/bin/pip", "install", "-q", "-r", repo / "requirements.txt"])
    bun = shutil.which("bun", path="/usr/local/bin:/usr/bin:/bin")
    if not bun:
        raise MaintenanceError("root-installed bun prerequisite is missing")
    trusted_tree(Path(bun).resolve())
    for directory in (repo, repo / "web"):
        as_account([bun, "install", "--frozen-lockfile"], cwd=directory)
    as_account(["/usr/bin/touch", ACCOUNT_HOME / "runtime/grok-runtime.lock"])
    as_account(["/usr/bin/chmod", "0600", ACCOUNT_HOME / "runtime/grok-runtime.lock"])
    root_directory(PRIVATE_DIR)
    production = read_root_file(PRODUCTION_ENV)
    existing = read_root_file(PRIVATE_DIR / "env") if (PRIVATE_DIR / "env").exists() else as_account(
        ["/bin/cat", LEGACY_ENV], account="radon", check=False).stdout
    atomic_root_file(PRIVATE_DIR / "env", derive_environment(production, existing).encode())
    run(["/bin/bash", stage / "cloud/scripts/install-grok-upgrade-controller.sh", stage / "scripts", CONTROLLER])
    install_responder_code(stage)
    admit_model_client(account)
    ensure_native_cli()
    publish_optional_units(stage)


def verify_installed_units():
    expected_policy = read_root_file(PRIVATE_DIR / "policy.sha256").strip()
    actual_policy = read_root_file(Path("/etc/polkit-1/rules.d/50-radon-services.rules"))
    if hashlib.sha256(actual_policy.encode()).hexdigest() != expected_policy:
        raise MaintenanceError("deploy the tested action policy before finalization")
    expected = dict(line.split("  ", 1)[::-1] for line in read_root_file(INVENTORY).splitlines())
    if set(expected) != set(OPTIONAL_UNITS):
        raise MaintenanceError("optional unit inventory is incomplete")
    for name, digest in expected.items():
        content = read_root_file(SYSTEMD / name).encode()
        if hashlib.sha256(content).hexdigest() != digest:
            raise MaintenanceError("optional unit verification differs")


def _unit_active(name, runner=run):
    result = runner(["/usr/bin/systemctl", "show", "--property=ActiveState", "--value", name], check=False)
    state = result.stdout.strip()
    if result.returncode or state not in {"inactive", "failed", "active", "activating", "deactivating", "reloading"}:
        raise MaintenanceError("responder workload state is unavailable; retry after inspection")
    return state not in {"inactive", "failed"}


def handover(*, forward: bool, runner=run, marker=READY):
    """Switch timer ownership without killing an in-flight responder run."""
    source_timers = (OLD_TIMER,) if forward else NEW_TIMERS
    target_timers = NEW_TIMERS if forward else (OLD_TIMER,)
    workloads = ("radon-grok-page-responder.service",) if forward else (
        "radon-grok-responder.service", "radon-grok-responder-upgrade.service")
    for workload in workloads:
        if _unit_active(workload, runner):
            raise MaintenanceError("responder workload is active; retry after it finishes")
    try:
        for timer in source_timers:
            runner(["/usr/bin/systemctl", "disable", "--now", timer])
        for workload in workloads:
            if _unit_active(workload, runner):
                raise MaintenanceError("responder workload started during handover")
        if forward:
            atomic_root_file(marker, b"ready\n", 0o644)
        else:
            marker.unlink(missing_ok=True)
        for timer in target_timers:
            runner(["/usr/bin/systemctl", "enable", "--now", timer])
    except BaseException:
        for timer in target_timers:
            runner(["/usr/bin/systemctl", "disable", "--now", timer], check=False)
        if forward:
            marker.unlink(missing_ok=True)
        else:
            atomic_root_file(marker, b"ready\n", 0o644)
        restoration_failed = False
        for timer in source_timers:
            try:
                restored = runner(["/usr/bin/systemctl", "enable", "--now", timer], check=False)
                restoration_failed = restoration_failed or restored.returncode != 0
            except (MaintenanceError, OSError, subprocess.SubprocessError):
                restoration_failed = True
        if restoration_failed:
            raise MaintenanceError("prior timer restoration needs operator review") from None
        raise


def verify_authentication():
    ensure_native_cli()
    proof = ("import sys;sys.path.insert(0,sys.argv[1]);"
             "from model_gateway.client import request;"
             "status=request('status',{},timeout_seconds=20);"
             "sys.exit(status.get('status')!='ok' or 'grok' not in status.get('providers',[]))")
    as_account(["/usr/bin/python3.13", "-I", "-S", "-c", proof, CODE_ROOT / "scripts"], timeout=30)


def verify_pickup_and_pending_work():
    repo = ACCOUNT_HOME / "repo"
    as_account(["/usr/bin/git", "-c", "safe.directory=" + str(repo), "ls-remote", repo, "HEAD"], account="radon")
    if not LEGACY_REPO.exists():
        return
    pending = as_account(["/usr/bin/git", "-C", LEGACY_REPO, "for-each-ref", "--format=%(refname)", "refs/heads/fix"], account="radon", check=False)
    if pending.returncode:
        raise MaintenanceError("legacy branch inventory is unavailable")
    for ref in pending.stdout.splitlines():
        if not re.fullmatch(r"refs/heads/fix/[A-Za-z0-9][A-Za-z0-9._-]{0,99}", ref):
            raise MaintenanceError("legacy branch inventory requires operator review")
        merged = as_account(["/usr/bin/git", "-C", LEGACY_REPO, "merge-base", "--is-ancestor", ref, "origin/main"], account="radon", check=False)
        if merged.returncode:
            raise MaintenanceError("pick up unmerged legacy branches before finalization")


def retire_optional_health(post=None):
    """Rollback retires only this optional domain's historical health row."""
    values = parse_env(read_root_file(PRODUCTION_ENV))
    url = unquote(values.get("TURSO_DB_URL", ""))
    token = unquote(values.get("TURSO_AUTH_TOKEN", ""))
    if url.startswith("libsql://"):
        url = "https://" + url[len("libsql://"):]
    if not url.startswith("https://") or not token:
        raise MaintenanceError("optional telemetry retirement needs canonical database inputs")
    body = json.dumps({"requests": [{"type": "execute", "stmt": {"sql": "DELETE FROM service_health WHERE service = ?", "args": [{"type": "text", "value": "grok-responder-upgrade"}]}}, {"type": "close"}]}).encode()
    req = request.Request(url.rstrip("/") + "/v2/pipeline", data=body, headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"}, method="POST")
    with (post or request.urlopen)(req, timeout=10) as response:
        payload = json.loads(response.read())
    results = payload.get("results") if isinstance(payload, dict) else None
    if not isinstance(results, list) or len(results) != 2 or any(result.get("type") != "ok" for result in results):
        raise MaintenanceError("legacy restored; optional telemetry retirement needs retry")


def main(argv):
    if os.geteuid() != 0 or not argv or argv[0] not in {"prepare", "refresh", "finalize", "rollback"}:
        return 64
    os.environ.clear()
    os.environ.update({"PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C.UTF-8"})
    try:
        # Root's fixed lock is never inherited by the native CLI children.
        lock = os.open("/run/lock/radon-grok-responder-account.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        with os.fdopen(lock, "a+") as stream:
            lock_meta = os.fstat(stream.fileno())
            if lock_meta.st_uid != 0 or not stat.S_ISREG(lock_meta.st_mode) or lock_meta.st_mode & 0o077:
                raise MaintenanceError("operator lock metadata differs")
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if argv[0] == "prepare" and len(argv) == 2:
                prepare(Path(argv[1]))
            elif argv[0] == "refresh" and len(argv) == 2:
                refresh(Path(argv[1]))
            elif argv == ["finalize", "--pickup-configured"]:
                verify_installed_units()
                environment = parse_env(read_root_file(PRIVATE_DIR / "env"))
                if environment.get("GROK_PAGE_RESPONDER") != "1":
                    raise MaintenanceError("responder must be explicitly enabled in its private environment")
                verify_authentication()
                verify_pickup_and_pending_work()
                handover(forward=True)
            elif argv == ["rollback"]:
                verify_installed_units()
                handover(forward=False)
                retire_optional_health()
            else:
                return 64
    except (OSError, ValueError, MaintenanceError, subprocess.SubprocessError) as exc:
        detail = str(exc) if isinstance(exc, MaintenanceError) else type(exc).__name__
        print("responder account maintenance stopped: " + detail, file=sys.stderr)
        return 78
    print("responder account maintenance completed: " + argv[0])
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
