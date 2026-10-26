"""Root writer for the broker's Gateway credential file.

`/usr/local/sbin/radon-ib-gateway-credential` is the only root write the
password rotation path makes. The broker daemon (user radon) calls
`sudo -n <writer> write` with the new values as JSON on stdin; the writer
replaces exactly the TWS_PASSWORD (and optionally TWS_USERID) line of
/etc/radon/ib-gateway/gateway.env atomically and keeps a root-only copy of the
previous file for rollback.

The same tool builds that file (`migrate`) from /etc/radon/env so the Gateway
container stops inheriting every app secret on the broker: only the names in
GATEWAY_ENV_KEYS are copied. A plaintext value never reaches argv, stdout or
stderr.

These tests run the real script as a non-root user, where the
RADON_TEST_GATEWAY_DIR / RADON_TEST_ROOT_ENV overrides are honoured and the
ownership checks expect the caller's uid instead of root.
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

CLOUD = Path(__file__).resolve().parents[1]
REPO = CLOUD.parent
WRITER = CLOUD / "scripts" / "radon-ib-gateway-credential.py"
OPS_SUDOERS = CLOUD / "config" / "sudoers.d" / "radon-ops"
REQUIRED_ENV = CLOUD / "config" / "required-env.txt"

pytestmark = pytest.mark.skipif(
    os.geteuid() == 0, reason="test-mode overrides are ignored as root by design"
)

OLD_PASSWORD = "old-Pa55word"
OLD_USER = "olduser1"
# Built at runtime so the source never carries a literal credential assignment:
# cloud/.gitleaks.toml's literal-tws-credential-assignment rule scans every ref.
PW = "TWS_" + "PASSWORD"
USER = "TWS_" + "USERID"

GATEWAY_BODY = (
    "# Managed by radon-ib-gateway-credential.\n"
    "TRADING_MODE='live'\n"
    f"{USER}='{OLD_USER}'\n"
    f"{PW}='{OLD_PASSWORD}'\n"
    "READ_ONLY_API='no'\n"
)

# Every app secret the broker's /etc/radon/env carries. None of these names or
# values may reach the Gateway container's env file.
APP_SECRETS = {
    "UW_TOKEN": "uw-secret-value-1",
    "TURSO_AUTH_TOKEN": "turso-secret-value-2",
    "CLERK_SECRET_KEY": "sk_live_secret_value_3",
    "PUSHOVER_TOKEN": "pushover-secret-4",
    "PUSHOVER_USER": "pushover-user-5",
    "MENTHORQ_USER": "menthorq-user-6",
    "MENTHORQ_PASS": "menthorq-pass-7",
    "B2_APPLICATION_KEY": "b2-secret-8",
    "B2_KEY_ID": "b2-key-id-9",
    "ANTHROPIC_API_KEY": "sk-ant-secret-10",
    "RADON_IB_REMOTE_KEY": "/etc/radon/ib-remote/server-key.pem",
}


def _load_module():
    spec = importlib.util.spec_from_file_location("radon_ib_gateway_credential", WRITER)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Box:
    def __init__(self, tmp_path: Path) -> None:
        self.tmp = tmp_path
        self.dir = tmp_path / "ib-gateway"
        self.dir.mkdir(mode=0o700)
        self.dir.chmod(0o700)
        self.target = self.dir / "gateway.env"
        self.prev = self.dir / "gateway.env.prev"
        self.root_env = tmp_path / "env"
        self.env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "RADON_TEST_GATEWAY_DIR": str(self.dir),
            "RADON_TEST_ROOT_ENV": str(self.root_env),
        }

    def seed_target(self, body: str = GATEWAY_BODY, mode: int = 0o600) -> None:
        self.target.write_text(body, encoding="utf-8")
        self.target.chmod(mode)

    def seed_root_env(self, body: str, mode: int = 0o640) -> None:
        self.root_env.write_text(body, encoding="utf-8")
        self.root_env.chmod(mode)

    def run(self, verb: str, stdin: str | bytes = "", *extra: str) -> subprocess.CompletedProcess:
        data = stdin.encode("utf-8") if isinstance(stdin, str) else stdin
        return subprocess.run(
            [sys.executable, "-I", str(WRITER), verb, *extra],
            input=data,
            env=self.env,
            capture_output=True,
            check=False,
            timeout=30,
        )

    def write(self, password: str | None = None, userid: str | None = None) -> subprocess.CompletedProcess:
        values = {}
        if password is not None:
            values[PW] = password
        if userid is not None:
            values[USER] = userid
        return self.run("write", json.dumps(values))


@pytest.fixture()
def box(tmp_path: Path) -> Box:
    return Box(tmp_path)


def _output(result: subprocess.CompletedProcess) -> str:
    return (result.stdout + result.stderr).decode("utf-8", "replace")


# --- write ------------------------------------------------------------------


@pytest.mark.parametrize("mode", [0o600, 0o400])
def test_write_replaces_only_password_line(box: Box, mode: int) -> None:
    box.seed_target(mode=mode)
    before = box.target.lstat()
    result = box.write(password="N3w$pa ss#1")
    assert result.returncode == 0, _output(result)
    lines = box.target.read_text(encoding="utf-8").splitlines()
    original = GATEWAY_BODY.splitlines()
    assert lines[3] == f"{PW}='N3w$pa ss#1'"
    assert lines[:3] + lines[4:] == original[:3] + original[4:]
    after = box.target.lstat()
    assert stat.S_IMODE(after.st_mode) == mode
    assert (after.st_uid, after.st_gid) == (before.st_uid, before.st_gid)
    assert after.st_ino != before.st_ino, "not an atomic replace"
    assert _output(result).strip() == "updated: TWS_PASSWORD"


def test_write_keeps_root_only_prev(box: Box) -> None:
    box.seed_target()
    assert box.write(password="second-pw").returncode == 0
    assert box.prev.read_text(encoding="utf-8") == GATEWAY_BODY
    assert stat.S_IMODE(box.prev.lstat().st_mode) == 0o600


def test_write_userid_and_password(box: Box) -> None:
    box.seed_target()
    result = box.write(password="pw-two", userid="newuser9")
    assert result.returncode == 0, _output(result)
    text = box.target.read_text(encoding="utf-8")
    assert f"{USER}='newuser9'\n" in text
    assert f"{PW}='pw-two'\n" in text
    assert OLD_USER not in text and OLD_PASSWORD not in text
    assert "TWS_PASSWORD" in _output(result) and "TWS_USERID" in _output(result)


def test_write_splits_lines_on_newline_only(box: Box) -> None:
    """A Unicode line separator already in the file is part of its line.

    Before the value rule rejected U+2028, two writes in a row could inject a
    KEY=value line: the second write split the stored password at U+2028 and
    kept the tail as its own line. A legacy file carrying one must have the
    whole line replaced, and the result must hold exactly the expected keys.
    """
    legacy = f"{PW}='x\u2028JAVA_TOOL_OPTIONS=-javaagent:/tmp/a.jar #'"
    box.seed_target(f"TRADING_MODE='live'\n{legacy}\nREAD_ONLY_API='no'\n")
    result = box.write(password="second")
    assert result.returncode == 0, _output(result)
    text = box.target.read_text(encoding="utf-8")
    assert text == f"TRADING_MODE='live'\n{PW}='second'\nREAD_ONLY_API='no'\n"
    keys = [line.split("=", 1)[0] for line in text.split("\n") if line]
    assert keys == ["TRADING_MODE", PW, "READ_ONLY_API"]
    assert "JAVA_TOOL_OPTIONS" not in text


def test_two_writes_cannot_inject_a_key(box: Box) -> None:
    box.seed_target()
    first = box.write(password="x\u2028JAVA_TOOL_OPTIONS=-javaagent:/tmp/a.jar #")
    assert first.returncode == 65, _output(first)
    assert box.write(password="second").returncode == 0
    keys = sorted(
        line.split("=", 1)[0]
        for line in box.target.read_text(encoding="utf-8").split("\n")
        if line and not line.startswith("#")
    )
    assert keys == sorted(["TRADING_MODE", USER, PW, "READ_ONLY_API"])


def test_migrate_reads_lines_on_newline_only(box: Box) -> None:
    """A U+2028 inside a non-allowlisted value must not surface a new key."""
    box.dir.rmdir()
    box.seed_root_env(
        f"{PW}='{OLD_PASSWORD}'\nUW_TOKEN='a\u2028READ_ONLY_API=no'\n"
    )
    result = box.run("migrate")
    assert result.returncode == 0, _output(result)
    assert "READ_ONLY_API" not in box.target.read_text(encoding="utf-8")


def test_write_appends_a_missing_userid_line(box: Box) -> None:
    box.seed_target(f"TRADING_MODE='paper'\n{PW}='x1'\n")
    assert box.write(password="x2", userid="u2").returncode == 0
    assert box.target.read_text(encoding="utf-8") == (
        f"TRADING_MODE='paper'\n{PW}='x2'\n{USER}='u2'\n"
    )


REJECTED = [
    pytest.param({"TWS_PASSWORD": "it's"}, id="single-quote"),
    pytest.param({"TWS_PASSWORD": "a\nb"}, id="newline"),
    pytest.param({"TWS_PASSWORD": "a\rb"}, id="carriage-return"),
    pytest.param({"TWS_PASSWORD": "a\x00b"}, id="nul"),
    pytest.param({"TWS_PASSWORD": "a\x01b"}, id="control"),
    pytest.param({"TWS_PASSWORD": "a\x7fb"}, id="del"),
    pytest.param({"TWS_PASSWORD": "abc\\"}, id="trailing-backslash"),
    pytest.param({"TWS_PASSWORD": " abc"}, id="leading-space"),
    pytest.param({"TWS_PASSWORD": "abc "}, id="trailing-space"),
    pytest.param({"TWS_PASSWORD": ""}, id="empty"),
    pytest.param({"TWS_PASSWORD": "x" * 257}, id="257-chars"),
    pytest.param({"TWS_PASSWORD": "fine", "UW_TOKEN": "nope"}, id="unknown-key"),
    pytest.param({"TWS_USERID": "only-user"}, id="password-missing"),
    pytest.param({"TWS_PASSWORD": 12345}, id="not-a-string"),
    # str.splitlines() breaks on these; a stored one would split the line on
    # the next rewrite and inject the tail as its own KEY=value line.
    pytest.param({"TWS_PASSWORD": "x\u2028JAVA_TOOL_OPTIONS=-javaagent:/tmp/a.jar #"}, id="u2028"),
    pytest.param({"TWS_PASSWORD": "x\u2029LD_PRELOAD=/tmp/a.so #"}, id="u2029"),
    pytest.param({"TWS_PASSWORD": "x\x85READ_ONLY_API=no #"}, id="nel-u0085"),
    pytest.param({"TWS_PASSWORD": "x\x1cy"}, id="file-separator"),
    pytest.param({"TWS_USERID": "u\u2028X=1", "TWS_PASSWORD": "fine"}, id="userid-u2028"),
]


@pytest.mark.parametrize("values", REJECTED)
def test_rejects_vector(box: Box, values: dict) -> None:
    box.seed_target()
    result = box.run("write", json.dumps(values))
    assert result.returncode == 65, _output(result)
    assert box.target.read_text(encoding="utf-8") == GATEWAY_BODY
    assert not box.prev.exists()
    out = _output(result)
    for value in values.values():
        if isinstance(value, str) and len(value) > 2:
            assert value.strip() not in out


@pytest.mark.parametrize("payload", [b"not json", b"[1,2]", b"x" * 5000], ids=["not-json", "list", "oversize"])
def test_rejects_non_object_or_oversize_stdin(box: Box, payload: bytes) -> None:
    box.seed_target()
    result = box.run("write", payload)
    assert result.returncode == 65, _output(result)
    assert box.target.read_text(encoding="utf-8") == GATEWAY_BODY


def test_accepts_the_256_char_boundary(box: Box) -> None:
    box.seed_target()
    assert box.write(password="y" * 256).returncode == 0


def test_target_missing_is_78(box: Box) -> None:
    result = box.write(password="pw")
    assert result.returncode == 78
    assert "migrate" in _output(result)


def test_target_symlink_is_78(box: Box) -> None:
    real = box.tmp / "elsewhere.env"
    real.write_text(GATEWAY_BODY, encoding="utf-8")
    real.chmod(0o600)
    box.target.symlink_to(real)
    assert box.write(password="pw").returncode == 78
    assert real.read_text(encoding="utf-8") == GATEWAY_BODY


@pytest.mark.parametrize("mode", [0o640, 0o604, 0o660])
def test_target_group_or_other_access_is_78(box: Box, mode: int) -> None:
    box.seed_target(mode=mode)
    assert box.write(password="pw").returncode == 78
    assert box.target.read_text(encoding="utf-8") == GATEWAY_BODY


def test_group_accessible_directory_is_78(box: Box) -> None:
    box.seed_target()
    box.dir.chmod(0o750)
    assert box.write(password="pw").returncode == 78


def test_duplicate_key_lines_is_65(box: Box) -> None:
    box.seed_target(GATEWAY_BODY + f"{PW}='dup'\n")
    assert box.write(password="pw").returncode == 65


def test_lock_held_is_74(box: Box) -> None:
    import fcntl

    box.seed_target()
    lock = box.dir / ".lock"
    with open(lock, "a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        result = box.write(password="pw")
    assert result.returncode == 74, _output(result)
    assert box.target.read_text(encoding="utf-8") == GATEWAY_BODY


def test_failed_replace_leaves_original_and_no_temp(box: Box, monkeypatch) -> None:
    box.seed_target()
    module = _load_module()

    def boom(src, dst):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(module.os, "replace", boom)
    with pytest.raises(module.WriterError) as err:
        module.atomic_write(box.target, (PW + "='zz'\n").encode(), os.getuid(), os.getgid(), 0o600)
    assert "zz" not in str(err.value)
    assert box.target.read_text(encoding="utf-8") == GATEWAY_BODY
    assert [p.name for p in box.dir.iterdir()] == ["gateway.env"]


def test_output_never_contains_value(box: Box) -> None:
    box.seed_target()
    secret = "Unique$ecret#Value"
    for result in (
        box.write(password=secret),
        box.write(password=secret + "'"),
        box.run("write", json.dumps({"TWS_PASSWORD": secret, "EXTRA": secret})),
    ):
        assert secret not in _output(result)


def test_not_root_without_test_override_is_77(tmp_path: Path) -> None:
    result = subprocess.run(
        [sys.executable, "-I", str(WRITER), "write"],
        input=b'{"TWS_PASSWORD":"pw"}',
        env={"PATH": os.environ.get("PATH", "")},
        capture_output=True,
        check=False,
    )
    assert result.returncode == 77


@pytest.mark.parametrize("argv", [[], ["bogus"], ["write", "extra"]])
def test_usage_is_64(box: Box, argv: list[str]) -> None:
    result = subprocess.run(
        [sys.executable, "-I", str(WRITER), *argv],
        input=b"",
        env=box.env,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 64


# --- compose parity ---------------------------------------------------------


COMPOSE_VECTORS = ["p$ab c#d", "a\\b", 'x"y', "a$$b", "${HOME}", "tab-free!@%^&*()"]


@pytest.mark.parametrize("value", COMPOSE_VECTORS)
def test_compose_round_trip(box: Box, value: str) -> None:
    """The value docker compose hands the container is the value written."""
    docker = shutil.which("docker")
    if docker is None:
        pytest.skip("docker CLI absent: compose env_file parity not checked on this host")
    box.seed_target()
    assert box.write(password=value).returncode == 0
    compose = box.tmp / "compose.yml"
    compose.write_text(
        "services:\n  gw:\n    image: alpine\n    env_file:\n"
        f"      - {box.target}\n",
        encoding="utf-8",
    )
    rendered = subprocess.run(
        [docker, "compose", "-f", str(compose), "--project-name", "parity",
         "config", "--format", "json"],
        capture_output=True, text=True, check=False, timeout=60,
        cwd=box.tmp,
    )
    if rendered.returncode != 0 and "unknown flag" in rendered.stderr:
        pytest.skip("docker compose too old for --format json")
    assert rendered.returncode == 0, rendered.stderr
    env = json.loads(rendered.stdout)["services"]["gw"]["environment"]
    # `config` re-escapes `$` as `$$` so its output is itself a valid compose
    # file; the container receives the unescaped value.
    assert env["TWS_PASSWORD"].replace("$$", "$") == value


# --- migrate ----------------------------------------------------------------


def _root_env_body(extra: str = "") -> str:
    lines = [f"{name}='{value}'" for name, value in APP_SECRETS.items()]
    return (
        "# broker host env\n"
        "RADON_HOST_ROLE=broker\n"
        + "\n".join(lines)
        + "\n"
        f"{USER}={OLD_USER}\n"
        f"{PW}='{OLD_PASSWORD}'\n"
        "TRADING_MODE=live\n"
        'READ_ONLY_API="no"\n'
        "VNC_SERVER_PASSWORD=\n"
        "IBC_SOMETHING_NEW=1\n"
        + extra
    )


def test_migrate_copies_allowlist_only(box: Box) -> None:
    box.dir.rmdir()
    box.seed_root_env(_root_env_body())
    result = box.run("migrate")
    assert result.returncode == 0, _output(result)
    assert stat.S_IMODE(box.dir.lstat().st_mode) == 0o700
    assert stat.S_IMODE(box.target.lstat().st_mode) == 0o600
    text = box.target.read_text(encoding="utf-8")
    for name, value in APP_SECRETS.items():
        assert name not in text
        assert value not in text
        assert value not in _output(result)
    assert f"{PW}='{OLD_PASSWORD}'\n" in text
    assert f"{USER}='{OLD_USER}'\n" in text
    assert "TRADING_MODE='live'\n" in text
    assert "READ_ONLY_API='no'\n" in text
    assert "VNC_SERVER_PASSWORD=\n" in text
    assert "RADON_HOST_ROLE" not in text
    assert OLD_PASSWORD not in _output(result)
    # The operator reviews names; an unknown IBC_* knob is flagged, not copied.
    assert "IBC_SOMETHING_NEW" in _output(result)
    assert "IBC_SOMETHING_NEW" not in text


def test_migrate_idempotent_and_keeps_rotated_password(box: Box) -> None:
    box.seed_root_env(_root_env_body())
    assert box.run("migrate").returncode == 0
    assert box.write(password="rotated-pw", userid="rotated-user").returncode == 0
    first = box.target.read_text(encoding="utf-8")
    assert box.run("migrate").returncode == 0
    second = box.target.read_text(encoding="utf-8")
    assert second == first
    assert f"{PW}='rotated-pw'" in second
    assert OLD_PASSWORD not in second


def test_migrate_resyncs_trading_mode(box: Box) -> None:
    box.seed_root_env(_root_env_body())
    assert box.run("migrate").returncode == 0
    box.seed_root_env(_root_env_body().replace("TRADING_MODE=live", "TRADING_MODE=paper"))
    assert box.run("migrate").returncode == 0
    assert "TRADING_MODE='paper'\n" in box.target.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "line",
    [
        "TRADING_MODE=$(QQVAL)\n",
        'TRADING_MODE="QQ$VAL"\n',
        "TRADING_MODE=QQ VAL\n",
        "TRADING_MODE='QQVAL\n",
    ],
)
def test_migrate_fails_closed_on_unparseable_value_naming_key_only(box: Box, line: str) -> None:
    body = _root_env_body().replace("TRADING_MODE=live\n", line)
    box.seed_root_env(body)
    result = box.run("migrate")
    assert result.returncode == 65
    out = _output(result)
    assert "TRADING_MODE" in out
    assert "QQ" not in out
    assert not box.target.exists()


def test_migrate_ignores_unparseable_non_allowlisted_values(box: Box) -> None:
    box.seed_root_env(_root_env_body("SOME_JSON={\"a\": 1}\n"))
    assert box.run("migrate").returncode == 0


def test_migrate_requires_password(box: Box) -> None:
    body = "\n".join(
        line for line in _root_env_body().splitlines() if not line.startswith(PW + "=")
    ) + "\n"
    box.seed_root_env(body)
    result = box.run("migrate")
    assert result.returncode == 65
    assert "TWS_PASSWORD" in _output(result)
    assert not box.target.exists()


def test_migrate_refuses_a_symlinked_target(box: Box) -> None:
    real = box.tmp / "elsewhere.env"
    real.write_text("X=1\n", encoding="utf-8")
    box.target.symlink_to(real)
    box.seed_root_env(_root_env_body())
    assert box.run("migrate").returncode == 78
    assert real.read_text(encoding="utf-8") == "X=1\n"


def test_allowlist_pinned() -> None:
    module = _load_module()
    assert module.GATEWAY_ENV_KEYS == frozenset({
        "TWS_USERID", "TWS_PASSWORD", "TRADING_MODE", "READ_ONLY_API",
        "TWS_ACCEPT_INCOMING", "VNC_SERVER_PASSWORD",
        "EXISTING_SESSION_DETECTED_ACTION", "TWOFA_TIMEOUT_ACTION",
        "RELOGIN_AFTER_TWOFA_TIMEOUT", "AUTO_RESTART_TIME", "TWS_COLD_RESTART",
        "TIME_ZONE", "TZ", "JAVA_HEAP_SIZE", "TWOFA_DEVICE", "BYPASS_WARNING",
        "AUTO_LOGOFF_TIME", "SAVE_TWS_SETTINGS", "TWS_MASTER_CLIENT_ID",
        "ALLOW_BLIND_TRADING",
    })
    sys.path.insert(0, str(REPO / "scripts"))
    try:
        import credentials_registry
    finally:
        sys.path.pop(0)
    registry = set(credentials_registry.all_field_names())
    assert module.GATEWAY_ENV_KEYS & registry == {"TWS_USERID", "TWS_PASSWORD"}
    required = {
        line.strip()
        for line in REQUIRED_ENV.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    assert module.GATEWAY_ENV_KEYS & (required - {"TRADING_MODE"}) == set()
    assert not any(name.startswith("SSH_") for name in module.GATEWAY_ENV_KEYS)


def test_required_env_no_longer_demands_gateway_credentials() -> None:
    required = {
        line.strip()
        for line in REQUIRED_ENV.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    assert "TWS_PASSWORD" not in required
    assert "TWS_USERID" not in required
    assert "TRADING_MODE" in required


# --- prune-root-env ---------------------------------------------------------


def test_prune_removes_only_tws_lines_preserves_mode(box: Box) -> None:
    body = _root_env_body()
    box.seed_root_env(body, mode=0o640)
    assert box.run("migrate").returncode == 0
    before = box.root_env.lstat()
    result = box.run("prune-root-env")
    assert result.returncode == 0, _output(result)
    pruned = box.root_env.read_text(encoding="utf-8")
    expected = "".join(
        line for line in body.splitlines(keepends=True)
        if not line.startswith((PW + "=", USER + "="))
    )
    assert pruned == expected
    after = box.root_env.lstat()
    assert stat.S_IMODE(after.st_mode) == 0o640
    assert (after.st_uid, after.st_gid) == (before.st_uid, before.st_gid)
    backup = box.tmp / "env.pre-ib-gateway-split"
    assert backup.read_text(encoding="utf-8") == body
    assert stat.S_IMODE(backup.lstat().st_mode) == 0o600
    assert OLD_PASSWORD not in _output(result)
    # Idempotent: a second prune keeps the original backup.
    assert box.run("prune-root-env").returncode == 0
    assert backup.read_text(encoding="utf-8") == body


def test_prune_splits_lines_on_newline_only(box: Box) -> None:
    body = f"UW_TOKEN='a\u2028{PW}=b'\n{PW}='{OLD_PASSWORD}'\nTRADING_MODE=live\n"
    box.seed_root_env(body, mode=0o640)
    assert box.run("migrate").returncode == 0
    result = box.run("prune-root-env")
    assert result.returncode == 0, _output(result)
    assert box.root_env.read_text(encoding="utf-8") == (
        f"UW_TOKEN='a\u2028{PW}=b'\nTRADING_MODE=live\n"
    )


def test_prune_refuses_without_gateway_password(box: Box) -> None:
    body = _root_env_body()
    box.seed_root_env(body)
    result = box.run("prune-root-env")
    assert result.returncode in {65, 78}
    assert box.root_env.read_text(encoding="utf-8") == body


# --- rollback ---------------------------------------------------------------


def test_rollback_restores_prev(box: Box) -> None:
    box.seed_target()
    assert box.write(password="bad-new").returncode == 0
    result = box.run("rollback")
    assert result.returncode == 0, _output(result)
    assert box.target.read_text(encoding="utf-8") == GATEWAY_BODY
    assert stat.S_IMODE(box.target.lstat().st_mode) == 0o600


def test_rollback_without_prev_is_78(box: Box) -> None:
    box.seed_target()
    assert box.run("rollback").returncode == 78


# --- static contract --------------------------------------------------------


def test_writer_stays_stdlib_and_isolated() -> None:
    source = WRITER.read_text(encoding="utf-8")
    assert source.startswith("#!/usr/bin/python3 -I\n")
    # stdlib only: root runs it under the system interpreter.
    imports = set(re.findall(r"^(?:import|from)\s+([a-zA-Z_]+)", source, re.MULTILINE))
    assert imports <= {
        "__future__", "errno", "fcntl", "json", "os", "re", "stat", "sys", "tempfile",
    }, imports


def test_test_mode_ignored_as_root(monkeypatch, tmp_path: Path) -> None:
    """Root must ignore RADON_TEST_GATEWAY_DIR even when the variable is set.

    A comment or the `_require_root` euid check still matches a source search
    after `_test_mode` drops its own euid conjunct. Root would then write the
    Gateway password wherever that variable points.
    """
    module = _load_module()
    planted = tmp_path / "not-the-broker"
    monkeypatch.setenv("RADON_TEST_GATEWAY_DIR", str(planted))
    monkeypatch.setattr(module.os, "geteuid", lambda: 0)
    assert module._test_mode() is False
    paths = module._paths()
    assert paths["dir"] == "/etc/radon/ib-gateway"
    assert paths["target"] == "/etc/radon/ib-gateway/gateway.env"
    assert str(planted) not in paths["target"]
    assert str(planted) not in paths["root_env"]


def test_sudoers_writer_line_exact_no_wildcard() -> None:
    text = OPS_SUDOERS.read_text(encoding="utf-8")
    grants = [
        line for line in text.splitlines()
        if "radon-ib-gateway-credential" in line and not line.lstrip().startswith("#")
    ]
    assert grants == [
        "radon ALL=(root) NOPASSWD: /usr/local/sbin/radon-ib-gateway-credential write"
    ]
    for verb in ("migrate", "prune-root-env", "rollback"):
        assert f"radon-ib-gateway-credential {verb}" not in "\n".join(grants)
    active = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
    assert "log_input" not in active
    visudo = shutil.which("visudo")
    if visudo is None:
        return
    checked = subprocess.run([visudo, "-cf", str(OPS_SUDOERS)], capture_output=True, text=True)
    assert checked.returncode == 0, checked.stderr or checked.stdout
