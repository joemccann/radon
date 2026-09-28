"""Grok pin-bump units copy on setup but stay disabled.

Do not enable until Joe reviews the pin file and smoke path.
"""
from pathlib import Path
import os
import re
import subprocess

CLOUD = Path(__file__).resolve().parents[1]
SETUP = CLOUD / "scripts" / "setup-vps.sh"
OWNER = CLOUD / "CLAUDE.md"
UNITS = (
    "radon-grok-pin-bump.service",
    "radon-grok-pin-bump.timer",
)


def test_setup_vps_inventories_grok_pin_bump_and_does_not_enable_it(tmp_path):
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


def test_owner_doc_records_grok_pin_bump_stay_off():
    text = OWNER.read_text(encoding="utf-8")
    assert "radon-grok-pin-bump.{service,timer}" in text
    assert "enable_services` skips both" in text
    assert "test_grok_pin_bump_setup.py" in text
