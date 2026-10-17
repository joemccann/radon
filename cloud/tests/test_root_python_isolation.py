"""Privileged control-plane Python must ignore caller import paths."""
from __future__ import annotations

import os
from pathlib import Path
import re
import subprocess
import sys

import pytest

CLOUD = Path(__file__).resolve().parents[1]
SCRIPTS = (
    ("deploy-root-helper.sh", "SESSION_PYTHON"),
    ("bootstrap-control-plane.sh", "PYTHON_BIN"),
    ("operator-radon.sh", "OPERATOR_PYTHON"),
    ("ib-gateway-control.sh", "LOCK_PYTHON"),
    ("radon-app-runtime.sh", "PYTHON"),
)


@pytest.mark.parametrize(("script", "variable"), SCRIPTS)
def test_privileged_inline_python_uses_isolated_mode(script, variable):
    text = (CLOUD / "scripts" / script).read_text()
    calls = re.findall(rf'"\${variable}"\s+(-[^\n]+)', text)
    assert calls
    assert all(call.startswith("-I ") for call in calls), (script, calls)


def test_root_python_validator_ignores_caller_module_paths(tmp_path):
    text = (CLOUD / "scripts" / "deploy-root-helper.sh").read_text()
    invocation = re.search(r'"\$SESSION_PYTHON"[^\n]+ast\.parse[^\n]+', text)
    assert invocation
    marker = tmp_path / "caller-module-imported"
    (tmp_path / "ast.py").write_text(
        f"from pathlib import Path\nPath({str(marker)!r}).touch()\n"
        "raise RuntimeError('caller module must not load')\n"
    )
    candidate = tmp_path / "candidate.py"
    candidate.write_text("pass\n")
    result = subprocess.run(
        ["bash", "-c", invocation.group(0).removesuffix("; then")], cwd=tmp_path,
        env={**os.environ, "SESSION_PYTHON": sys.executable,
             "candidate": str(candidate), "PYTHONPATH": str(tmp_path)},
        text=True, capture_output=True,
    )
    assert result.returncode == 0, result.stderr
    assert not marker.exists()


@pytest.mark.parametrize(("script", "variable"), SCRIPTS)
def test_admission_accepts_the_actual_isolated_helpers(script, variable):
    text = (CLOUD / "scripts" / "deploy-root-helper.sh").read_text()
    guard = re.search(r"^python_inline_is_isolated\(\) \{.*?^\}", text, re.M | re.S)
    assert guard
    result = subprocess.run(
        ["bash", "-c", guard.group(0) + '\npython_inline_is_isolated "$1"',
         "guard", str(CLOUD / "scripts" / script)],
        env={**os.environ, "SESSION_PYTHON": sys.executable},
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
