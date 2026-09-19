"""Installer refusal and deployment-boundary contracts; never installs locally."""

import os
import subprocess
from pathlib import Path

import pytest

CLOUD = Path(__file__).resolve().parents[1]
INSTALLER = CLOUD / "gpu" / "install-runtime.sh"


def test_non_root_install_refused_without_host_mutations():
    if os.geteuid() == 0:
        pytest.skip("CI normally executes as an unprivileged user")
    result = subprocess.run(["bash", str(INSTALLER)], text=True, capture_output=True, check=False)
    assert result.returncode == 64
    assert "Run as root without arguments" in result.stderr


def test_untrusted_source_refused_by_real_preflight(tmp_path: Path):
    # Execute only the first Python heredoc, which performs read-only validation.
    # It must reject this writable source before considering live destinations.
    script = INSTALLER.read_text()
    validator = script.split("python3 - \"$source_dir\" <<'PY'\n", 1)[1].split("\nPY\n", 1)[0]
    source = tmp_path / "untrusted"
    source.mkdir(mode=0o777)
    source.chmod(0o777)
    for name in ("runtime.py", "pins.json", "slm.json.example", "radon-slm.service"):
        (source / name).write_text("placeholder")
    result = subprocess.run(
        ["python3", "-", str(source)],
        input=validator,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode != 0
    assert "Refusing untrusted release source" in result.stderr


def test_install_does_not_activate_runtime_and_secrets_remain_private():
    script = INSTALLER.read_text()
    systemctl_lines = [line.strip() for line in script.splitlines() if line.strip().startswith("systemctl ")]
    assert systemctl_lines == ["systemctl daemon-reload"]
    assert "os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600" in script
    assert "secrets.token_urlsafe(48)" in script
    assert 'handle.write("VLLM_API_KEY="' in script
    assert 'print("VLLM_API_KEY' not in script
    assert 'echo "$VLLM_API_KEY' not in script


def test_untrusted_marker_refused_by_real_marker_validation(tmp_path: Path):
    marker = tmp_path / "gpu-host"
    marker.write_text("radon-slm\n")
    marker.chmod(0o666)
    script = INSTALLER.read_text()
    validation = script.split('marker = pathlib.Path("/etc/radon/gpu-host")\n', 1)[1]
    validation = validation.split("\nfor path in map(pathlib.Path,", 1)[0]
    result = subprocess.run(
        ["python3", "-", str(marker)],
        input="import pathlib, sys\nmarker = pathlib.Path(sys.argv[1])\n" + validation,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode != 0
    assert "Refusing untrusted GPU host marker" in result.stderr


def test_gpu_runtime_not_in_app_deployment_inventory():
    assert not (CLOUD / "services" / "radon-slm.service").exists()
    for name in ("installed-units.sha256", "auto-sync-units.txt"):
        assert "radon-slm" not in (CLOUD / "config" / name).read_text()
