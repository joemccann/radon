"""SLM tagger units install on setup but stay disabled until Joe enables them.

Rung stays off; setup copies units only.
"""
from pathlib import Path
import os
import re
import subprocess

SETUP = Path(__file__).resolve().parents[1] / "scripts" / "setup-vps.sh"
UNITS = (
    "radon-slm-tagger.service",
    "radon-slm-tagger-monitor.service",
    "radon-slm-tagger-monitor.timer",
)


def test_setup_vps_inventories_slm_units_and_does_not_enable_them(tmp_path):
    setup = SETUP.read_text(encoding="utf-8")
    inventory = re.search(r"readonly SERVICE_FILES=\(.*?\n\)", setup, re.S).group(0)
    for unit in UNITS:
        assert unit in inventory
    body = re.search(r"^enable_services\(\) \{\n(.*?)^\}", setup, re.M | re.S).group(0)
    log = tmp_path / "calls"
    stub = (
        "SERVICE_FILES=("
        + " ".join(UNITS)
        + " radon-api.service)\n"
        "log_info() { :; }\nlog_success() { :; }\n"
        'systemctl() { printf \'%s\\n\' "$*" >> "$CALLS"; }\n'
    )
    result = subprocess.run(
        ["bash", "-c", stub + body + "\nenable_services\n"],
        env={**os.environ, "CLOUD_DIR": str(tmp_path), "CALLS": str(log)},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    calls = log.read_text()
    assert "radon-api.service" in calls
    for unit in UNITS:
        assert unit not in calls


def test_tagger_sidecar_loads_no_production_env():
    # llama-server needs only the model path and thread count, both inline
    # Environment= values; handing it /etc/radon/env would expose the full
    # production credential set through a third-party binary's process.
    unit = SETUP.parents[1] / "services" / "radon-slm-tagger.service"
    text = unit.read_text(encoding="utf-8")
    assert "EnvironmentFile=" not in text
    assert "Environment=RADON_SLM_TAGGER_THREADS=" in text
