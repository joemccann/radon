"""Trusted promote step for the Grok CLI upgrade (DS-2026-09-29-07).

The upgrade unit promotes the binary that the secret-bearing
subscription-tokens unit executes. Its code and interpreter must therefore
come from a root-owned copy, never from the responder clone or its venv,
which the responder (grok over untrusted page text) can rewrite.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

CLOUD = Path(__file__).resolve().parents[1]
REPO = CLOUD.parent
SERVICES = CLOUD / "services"
UPGRADE = SERVICES / "radon-grok-upgrade.service"
INSTALL = CLOUD / "scripts" / "install-grok-upgrade-controller.sh"
SETUP = CLOUD / "scripts" / "setup-grok-page-responder.sh"
DOC = REPO / "docs" / "grok-page-responder.md"
CLONE = "/home/radon/radon-page-responder"
CONTROLLER = "/usr/local/lib/radon/grok-upgrade"
PYTHON = "/usr/bin/python3.13 -E -S"


def _directive(name: str) -> list[str]:
    text = UPGRADE.read_text(encoding="utf-8")
    return [l.split("=", 1)[1] for l in text.splitlines() if l.startswith(f"{name}=")]


def test_upgrade_runs_root_owned_code_on_the_system_interpreter():
    (exec_start,) = _directive("ExecStart")
    assert exec_start.startswith(f"{PYTHON} {CONTROLLER}/scripts/grok_upgrade.py ")
    assert CLONE not in exec_start
    assert ".venv" not in exec_start


def test_upgrade_cannot_reach_the_responder_clone():
    (workdir,) = _directive("WorkingDirectory")
    assert not workdir.startswith(CLONE)
    rw = " ".join(_directive("ReadWritePaths")).split()
    assert not any(p.lstrip("-").startswith(CLONE) for p in rw)
    denied = {p.lstrip("-") for p in " ".join(_directive("InaccessiblePaths")).split()}
    assert CLONE in denied


def test_upgrade_never_writes_its_own_code():
    rw = {p.lstrip("-") for p in " ".join(_directive("ReadWritePaths")).split()}
    assert not any(CONTROLLER.startswith(p.rstrip("/") + "/") or p == CONTROLLER for p in rw)


def _copy_scripts(dest: Path) -> Path:
    shutil.copytree(REPO / "scripts", dest / "scripts",
                    ignore=shutil.ignore_patterns("tests", "__pycache__"))
    return dest / "scripts"


def test_upgrader_import_closure_is_stdlib_only(tmp_path):
    """-E -S drops every site-packages dir: a third-party import fails here."""
    scripts = _copy_scripts(tmp_path)
    probe = (
        "import sys; sys.path.insert(0, sys.argv[1]);"
        "import grok_upgrade;"
        "from db.hrana_http import write_service_health_http;"
        "from watchdog import notify;"
        "import db.service_health_sql"
    )
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin")}
    result = subprocess.run([sys.executable, "-E", "-S", "-c", probe, str(scripts)],
                            capture_output=True, text=True, env=env, cwd=tmp_path)
    assert result.returncode == 0, result.stderr
    result = subprocess.run([sys.executable, "-E", "-S", str(scripts / "grok_upgrade.py"), "--help"],
                            capture_output=True, text=True, env=env, cwd=tmp_path)
    assert result.returncode == 0, result.stderr


def _install(src: Path, target: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(INSTALL), str(src), str(target)],
        capture_output=True, text=True,
        env={**os.environ, "RADON_HELPER_SKIP_CHOWN": "1"},
    )


def test_install_replaces_the_controller_atomically(tmp_path):
    src = tmp_path / "stage" / "scripts"
    src.mkdir(parents=True)
    (src / "grok_upgrade.py").write_text("new\n")
    target = tmp_path / "lib" / "grok-upgrade"
    (target / "scripts").mkdir(parents=True)
    (target / "scripts" / "stale.py").write_text("old\n")

    result = _install(src, target)

    assert result.returncode == 0, result.stderr
    assert (target / "scripts" / "grok_upgrade.py").read_text() == "new\n"
    assert not (target / "scripts" / "stale.py").exists()
    assert not target.is_symlink()
    assert [p.name for p in target.parent.iterdir()] == ["grok-upgrade"]
    mode = (target / "scripts" / "grok_upgrade.py").stat().st_mode
    assert not mode & 0o022


def test_install_refuses_a_linked_target_or_parent(tmp_path):
    src = tmp_path / "stage" / "scripts"
    src.mkdir(parents=True)
    (src / "grok_upgrade.py").write_text("new\n")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()

    linked_target = tmp_path / "a" / "grok-upgrade"
    linked_target.parent.mkdir()
    linked_target.symlink_to(elsewhere)
    assert _install(src, linked_target).returncode != 0

    linked_parent = tmp_path / "b"
    linked_parent.symlink_to(elsewhere)
    assert _install(src, linked_parent / "grok-upgrade").returncode != 0
    assert list(elsewhere.iterdir()) == []


def test_install_refuses_a_source_without_the_upgrader(tmp_path):
    src = tmp_path / "stage" / "scripts"
    src.mkdir(parents=True)
    assert _install(src, tmp_path / "lib" / "grok-upgrade").returncode != 0


def test_setup_installs_the_controller_from_its_root_owned_stage():
    text = SETUP.read_text(encoding="utf-8")
    assert '"$SCRIPT_DIR/install-grok-upgrade-controller.sh"' in text
    assert '"$SCRIPT_DIR/../../scripts"' in text
    # The LKG seed runs the trusted copy too, never the clone's code.
    assert f'{PYTHON} {CONTROLLER}/scripts/grok_upgrade.py' in text
    assert '"$CLONE/scripts/grok_upgrade.py"' not in text


def test_doc_stages_scripts_and_names_the_controller():
    text = DOC.read_text(encoding="utf-8")
    assert 'archive "$SHA" cloud scripts' in text
    assert CONTROLLER in text
    assert "test_grok_upgrade_controller.py" in text


@pytest.mark.parametrize("path", [CONTROLLER, CONTROLLER + "/scripts/grok_upgrade.py"])
def test_responder_cannot_write_the_controller(path):
    text = (SERVICES / "radon-grok-page-responder.service").read_text(encoding="utf-8")
    rw = [l.split("=", 1)[1] for l in text.splitlines() if l.startswith("ReadWritePaths=")]
    assert not any(path.startswith(p.lstrip("-")) for p in " ".join(rw).split())
