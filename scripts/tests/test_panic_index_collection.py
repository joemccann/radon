"""T-500: live Cboe anchors require explicit integration selection."""
import os
from pathlib import Path
import subprocess
import sys


def test_default_collection_excludes_live_cboe_but_explicit_integration_selects_it():
    root = Path(__file__).resolve().parents[2]
    target = "scripts/tests/test_panic_index.py"
    env = dict(os.environ)
    env["PYTEST_ADDOPTS"] = ""
    def collect(*args):
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "--collect-only", "-q", target, *args],
            cwd=root, env=env, capture_output=True, text=True, timeout=60,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        return [line for line in result.stdout.splitlines() if line.startswith(target + "::")]
    default = collect()
    live = target + "::test_live_files_reproduce_c8_anchors"
    assert default and live not in default
    explicit = collect("-m", "integration")
    assert explicit == [live]
