"""GAP A: the nightly runner clones are never given `web/.env`.

`web/.env` is gitignored, so the dedicated clones at
`~/radon-weekend/radon{,-testing}` (created by `git clone` and hard-reset to
`origin/main` every night) structurally cannot contain it. Both wrappers
already exclude it from their per-round `git clean`
(`reliability_weekend.sh` `reground_for_continuation`/`ground_truth`,
`testing_weekend.sh` `ground_truth`), i.e. surviving the reset has always been
the intent; only the provisioning step was never written.

ONLY `web/.env` is provisioned. The root `.env` is deliberately left out:
python-dotenv already walks up from the clone to `$WEEKEND_ROOT/.env` for the
same TURSO/IB keys, so a copy is redundant and would duplicate IB_FLEX_TOKEN.
`test_setup_does_not_provision_the_root_env` guards that.

Consequences the loops actually hit: no `NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY`
means a dev server the browser-verification step starts renders no
Clerk-wrapped page, and no `TURSO_DB_URL` in `os.environ` means the runner's
pytest pins the OPPOSITE of production wherever a collected module's
import-time `load_dotenv` would have set it.

These tests drive the real setup scripts against a staged fake source checkout
and a staged fake clone, with the whole toolchain stubbed on PATH. No real
credential is ever read or written.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
BASH = shutil.which("bash") or "/bin/bash"

SETUPS = {
    "reliability": (REPO / "scripts" / "setup_reliability_weekend.sh", "radon"),
    "testing": (REPO / "scripts" / "setup_testing_weekend.sh", "radon-testing"),
    "ci-performance": (
        REPO / "scripts" / "setup_ci_performance.sh",
        "radon-ci-performance",
    ),
}
PLISTS = {
    "reliability": "com.radon.reliability-daily.plist",
    "testing": "com.radon.testing-daily.plist",
    "ci-performance": "com.radon.ci-performance-daily.plist",
}

# Dummy values only. Never stage a real credential into a fixture.
DUMMY = {
    ".env": "TURSO_DB_URL=libsql://dummy.invalid\nTURSO_AUTH_TOKEN=dummy\n",
    ".env.ib-mode": "IB_GATEWAY_MODE=local\n",
    "web/.env": "NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY=pk_test_dummy\n",
}


def _stub_bin(tmp_path: Path) -> Path:
    """Every external the setup scripts shell out to, neutered."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    scripts = {
        # `git config --get remote.origin.url` must answer; everything else
        # (ls-remote / fetch / checkout / reset) just succeeds.
        "git": (
            "#!/bin/sh\n"
            'case "$*" in *"config --get remote.origin.url"*)'
            ' echo git@example.invalid:stub/radon.git;; esac\n'
            "exit 0\n"
        ),
        "gh": "#!/bin/sh\nexit 0\n",
        "claude": "#!/bin/sh\nexit 0\n",
        "node": "#!/bin/sh\nexit 0\n",
        "bun": "#!/bin/sh\nexit 0\n",
        "caddy": "#!/bin/sh\nexit 0\n",
        # Only the `bash 4+` toolchain check shells out to `bash`; this
        # keeps [1/4] green on a macOS runner so the run reaches [2/4].
        "bash": "#!/bin/sh\nexit 0\n",
        "ssh": "#!/bin/sh\nexit 0\n",
        "python3.13": "#!/bin/sh\nexit 0\n",
        "launchctl": "#!/bin/sh\nexit 0\n",
        # -lint succeeds; -extract feeds the closing printf a number.
        "plutil": (
            "#!/bin/sh\n"
            'if [ "$1" = "-lint" ]; then exit 0; fi\n'
            "echo 0\n"
        ),
    }
    for name, body in scripts.items():
        path = bin_dir / name
        path.write_text(body, encoding="utf-8")
        path.chmod(0o755)
    return bin_dir


def _stage(tmp_path: Path, name: str) -> tuple[Path, Path, dict]:
    """A fake source checkout, a fake already-provisioned clone, and env."""
    _, clone_name = SETUPS[name]

    src = tmp_path / "src"
    (src / "web").mkdir(parents=True)
    for rel, body in DUMMY.items():
        (src / rel).write_text(body, encoding="utf-8")

    root = tmp_path / "weekend"
    clone = root / clone_name
    (clone / ".git").mkdir(parents=True)
    (clone / "web").mkdir()
    (clone / "config").mkdir()
    (clone / "requirements.txt").write_text("", encoding="utf-8")
    shutil.copy(REPO / "config" / PLISTS[name], clone / "config" / PLISTS[name])

    # The venv the toolchain check and the pip lines address.
    venv_bin = root / "venv" / "bin"
    venv_bin.mkdir(parents=True)
    for tool in ("python", "pip"):
        exe = venv_bin / tool
        exe.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        exe.chmod(0o755)

    (root / ".env").write_text("PUSHOVER_USER=dummy\nPUSHOVER_TOKEN=dummy\n", encoding="utf-8")

    home = tmp_path / "home"
    home.mkdir()
    env = {
        "PATH": f"{_stub_bin(tmp_path)}:/usr/bin:/bin:/usr/sbin:/sbin",
        "HOME": str(home),
        "RADON_WEEKEND_ROOT": str(root),
        "RADON_WEEKEND_SRC_REPO": str(src),
    }
    return src, clone, env


def _run(name: str, env: dict, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [BASH, str(SETUPS[name][0])],
        env=env,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=120,
    )


@pytest.mark.parametrize("name", sorted(SETUPS))
class TestRunnerEnvProvisioning:
    def test_setup_copies_the_env_files_into_the_clone(self, name, tmp_path):
        src, clone, env = _stage(tmp_path, name)

        proc = _run(name, env, tmp_path)

        rel = "web/.env"
        dst = clone / rel
        assert dst.is_file(), (
            f"{name}: {rel} was never provisioned into the runner clone, so the "
            "Next dev server cannot boot and the nightly agent cannot do the "
            "browser verification CLAUDE.md requires.\n"
            f"{proc.stdout}\n{proc.stderr}"
        )
        assert dst.read_text(encoding="utf-8") == DUMMY[rel]
        assert oct(dst.stat().st_mode & 0o777) == "0o600", (
            f"{name}: {rel} holds secrets and must land 0600"
        )

    def test_setup_does_not_provision_the_root_env(self, name, tmp_path):
        """The root .env must stay OUT of the runner clone.

        python-dotenv already walks up from the clone to $WEEKEND_ROOT/.env for
        the same TURSO/IB keys, so a second copy is redundant, and it would put
        IB_FLEX_TOKEN in one more place. web/.env is read by Next, never pytest.
        """
        src, clone, env = _stage(tmp_path, name)

        proc = _run(name, env, tmp_path)

        for rel in (".env", ".env.ib-mode"):
            assert not (clone / rel).exists(), (
                f"{name}: {rel} was copied into the runner clone. Only web/.env "
                "is provisioned; see the comment above provision_env_file.\n"
                f"{proc.stdout}\n{proc.stderr}"
            )

    def test_setup_reports_a_missing_source_env_file(self, name, tmp_path):
        src, clone, env = _stage(tmp_path, name)
        (src / "web" / ".env").unlink()

        proc = _run(name, env, tmp_path)
        out = proc.stdout + proc.stderr

        assert "MISSING" in out and "web/.env" in out, (
            f"{name}: the toolchain block said nothing about the absent "
            f"web/.env:\n{out}"
        )
        # Usable on a fresh machine: warn, do not hard-fail.
        assert proc.returncode == 0, out
        assert not (clone / "web" / ".env").exists()
        # It must say what it did rather than skip in silence.
        assert "web/.env" in out.split("[2/4]", 1)[-1]

    def test_setup_does_not_silently_clobber_a_newer_clone_copy(self, name, tmp_path):
        src, clone, env = _stage(tmp_path, name)
        newer = "NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY=pk_test_rotated\n"
        (clone / "web" / ".env").write_text(newer, encoding="utf-8")
        # `-nt` compares whole seconds, so make the gap unambiguous.
        stamp = time.time() + 120
        os.utime(clone / "web" / ".env", (stamp, stamp))

        proc = _run(name, env, tmp_path)
        out = proc.stdout + proc.stderr

        assert (clone / "web" / ".env").read_text(encoding="utf-8") == newer, (
            f"{name}: an older source copy overwrote a newer clone copy:\n{out}"
        )
        assert "web/.env" in out.split("[2/4]", 1)[-1], out
