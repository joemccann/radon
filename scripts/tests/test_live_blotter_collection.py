"""The default gate must not execute the standalone live Gateway smoke tests."""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MODULE = 'scripts/trade_blotter/test_integration.py'


def test_default_gate_does_not_collect_live_gateway_smoke_tests():
    result = subprocess.run(
        [sys.executable, '-m', 'pytest', '--collect-only', '-q', MODULE],
        cwd=ROOT, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 5, result.stdout + result.stderr
    assert MODULE + '::' not in result.stdout
    assert '4 deselected' in result.stdout


def test_explicit_integration_opt_in_keeps_all_live_smoke_cases():
    result = subprocess.run(
        [sys.executable, '-m', 'pytest', '--collect-only', '-q', '-m', 'integration', MODULE],
        cwd=ROOT, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.count(MODULE + '::') == 4
