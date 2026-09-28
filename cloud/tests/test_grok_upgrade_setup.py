"""Grok upgrade units copy on setup and are enabled by default."""
from pathlib import Path
import os
import re
import subprocess

CLOUD = Path(__file__).resolve().parents[1]
SETUP = CLOUD / "scripts" / "setup-vps.sh"
OWNER = CLOUD / "CLAUDE.md"
UNITS = (
    "radon-grok-upgrade.service",
    "radon-grok-upgrade.timer",
)


def test_setup_vps_inventories_and_enables_grok_upgrade(tmp_path):
    setup = SETUP.read_text(encoding="utf-8")
    inventory = re.search(r"readonly SERVICE_FILES=\(.*?\n\)", setup, re.S).group(0)
    for unit in UNITS:
        assert unit in inventory
    body = re.search(r"^enable_services\(\) \{\n(.*?)^\}", setup, re.M | re.S).group(0)
    for unit in UNITS:
        assert f'[[ "$svc" == "{unit}" ]] && continue' not in body
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
        assert unit in calls


def test_owner_doc_records_grok_upgrade_enabled():
    text = OWNER.read_text(encoding="utf-8")
    assert "radon-grok-upgrade.{service,timer}" in text
    assert "enable_services` enables both" in text
    assert "test_grok_upgrade_setup.py" in text


def test_upgrade_timer_is_daily_0740_utc():
    timer = (CLOUD / "services" / "radon-grok-upgrade.timer").read_text()
    assert "OnCalendar=*-*-* 07:40:00 UTC" in timer
    assert "Persistent=true" in timer
    service = (CLOUD / "services" / "radon-grok-upgrade.service").read_text()
    assert "grok_upgrade.py" in service
    assert "/var/lib/radon/grok_lkg.json" in service
    assert "/var/lib/radon/grok-runtime.lock" in service
