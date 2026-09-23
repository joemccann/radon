"""Nightly wrappers must not follow agent-plantable symlinks (CWE-59).

The codex-rung agent can write anywhere inside its clone, including paths the
wrapper later touches with privileged file operations: the LOG_DIR that gets
`chmod 700` + rotation `rm`, and the `web/.env` scrub that clobbers via
`cat >`. A planted symlink at any of those paths would redirect the operation
outside the sandbox. Each wrapper carries a `refuse_symlink` helper and calls
it before those operations; the rotation loop additionally deletes regular
files only.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
BASH = shutil.which("bash") or "/bin/bash"

LOOPS = {
    "reliability": REPO / "scripts" / "reliability_weekend.sh",
    "testing": REPO / "scripts" / "testing_weekend.sh",
    "ci-performance": REPO / "scripts" / "ci_performance_nightly.sh",
    "documentation": REPO / "scripts" / "documentation_nightly.sh",
    "security": REPO / "scripts" / "security_nightly.sh",
    "security-deepsec": REPO / "scripts" / "security_deepsec_nightly.sh",
}


def _run_helper(loop: str, path: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            BASH,
            "-c",
            f'source "{LOOPS[loop]}" --lock-lib-only; refuse_symlink "$1"',
            "bash",
            str(path),
        ],
        capture_output=True,
        text=True,
    )


@pytest.mark.parametrize("loop", sorted(LOOPS))
def test_refuse_symlink_rejects_symlinked_dir(loop, tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "planted"
    link.symlink_to(real)
    proc = _run_helper(loop, link)
    assert proc.returncode != 0, f"{loop}: refuse_symlink followed a symlinked dir"
    assert "symlink" in proc.stderr


@pytest.mark.parametrize("loop", sorted(LOOPS))
def test_refuse_symlink_rejects_symlinked_file(loop, tmp_path):
    real = tmp_path / "real.env"
    real.write_text("A=1\n")
    link = tmp_path / "planted.env"
    link.symlink_to(real)
    proc = _run_helper(loop, link)
    assert proc.returncode != 0, f"{loop}: refuse_symlink followed a symlinked file"
    assert "symlink" in proc.stderr


@pytest.mark.parametrize("loop", sorted(LOOPS))
def test_refuse_symlink_accepts_regular_paths(loop, tmp_path):
    d = tmp_path / "logs"
    d.mkdir()
    f = tmp_path / "run.log"
    f.write_text("ok\n")
    for p in (d, f, tmp_path / "does-not-exist-yet"):
        proc = _run_helper(loop, p)
        assert proc.returncode == 0, f"{loop}: refuse_symlink rejected {p}: {proc.stderr}"


@pytest.mark.parametrize("loop", sorted(LOOPS))
def test_log_dir_verified_before_chmod(loop):
    src = LOOPS[loop].read_text()
    guard = 'refuse_symlink "$LOG_DIR"'
    chmod = 'chmod 700 "$LOG_DIR"'
    assert guard in src, f"{loop}: LOG_DIR is chmodded without a symlink refusal"
    assert src.index(guard) < src.index(chmod), (
        f"{loop}: symlink refusal must run before chmod 700 on LOG_DIR"
    )


@pytest.mark.parametrize("loop", sorted(LOOPS))
def test_logs_parent_verified_before_mkdir(loop):
    """logs/ survives every git clean; a symlinked logs/ ancestor would make
    `mkdir -p "$LOG_DIR"` create the leaf outside the clone, where the leaf
    check then passes."""
    src = LOOPS[loop].read_text()
    guard = 'refuse_symlink "$REPO/logs" || exit 2'
    mkdir = 'mkdir -p "$LOG_DIR"'
    assert guard in src, f"{loop}: logs/ ancestor is not checked for a symlink"
    assert src.index(guard) < src.index(mkdir), (
        f"{loop}: symlink refusal on logs/ must run before mkdir -p LOG_DIR"
    )


@pytest.mark.parametrize("loop", sorted(LOOPS))
def test_log_rotation_deletes_regular_files_only(loop):
    src = LOOPS[loop].read_text()
    assert '[[ -f "$LOG_DIR/$old" && ! -L "$LOG_DIR/$old" ]] || continue' in src, (
        f"{loop}: rotation rm is not constrained to regular non-symlink files"
    )


@pytest.mark.parametrize("loop", sorted(LOOPS))
def test_env_scrub_refuses_symlinked_env(loop):
    src = LOOPS[loop].read_text()
    clobber = "cat web/.env.scrub > web/.env"
    guard = "refuse_symlink web/.env"
    assert clobber in src
    assert guard in src, f"{loop}: web/.env scrub clobbers without a symlink refusal"
    assert src.index(guard) < src.index(clobber), (
        f"{loop}: symlink refusal must run before the web/.env clobber"
    )
    assert "rm -f -- web/.env.scrub" in src, (
        f"{loop}: scratch scrub path must be unlinked before being written through"
    )


PRIVATE_REPORT_LOOPS = {"security": LOOPS["security"], "security-deepsec": LOOPS["security-deepsec"]}


@pytest.mark.parametrize("loop", sorted(PRIVATE_REPORT_LOOPS))
def test_private_report_src_verified_before_read(loop):
    """$PRIVATE_SCRATCH is agent-writable; a planted symlink at the report
    path must not be read and redacted into the pushed archive repo."""
    src = PRIVATE_REPORT_LOOPS[loop].read_text()
    guard = 'refuse_symlink "$src"'
    read = '_redact_secret_classes < "$src"'
    assert guard in src, f"{loop}: report src is read without a symlink refusal"
    assert src.index(guard) < src.index(read), (
        f"{loop}: symlink refusal must run before the report src is read"
    )


@pytest.mark.parametrize("loop", sorted(PRIVATE_REPORT_LOOPS))
def test_private_report_pinned_hosts_verified_before_write(loop):
    """A planted symlink at the pinned-hosts scratch path must not have the
    host-key pin written through it."""
    src = PRIVATE_REPORT_LOOPS[loop].read_text()
    guard = 'refuse_symlink "$pinned_hosts"'
    write = 'printf \'%s\\n\' \'github.com ssh-ed25519'
    assert guard in src, f"{loop}: pinned_hosts is written without a symlink refusal"
    assert src.index(guard) < src.index(write), (
        f"{loop}: symlink refusal must run before the pinned_hosts write"
    )
