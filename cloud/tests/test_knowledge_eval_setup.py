"""Knowledge golden-eval units copy on setup but stay disabled.

Do not enable until a live baseline is written. A failed oneshot pages via
the unit watchdog; install-units must not `enable --now` this timer.
"""
from pathlib import Path
import os
import re
import subprocess

CLOUD = Path(__file__).resolve().parents[1]
SETUP = CLOUD / "scripts" / "setup-vps.sh"
OWNER = CLOUD / "CLAUDE.md"
UNITS = (
    "radon-knowledge-eval.service",
    "radon-knowledge-eval.timer",
)


def test_setup_vps_inventories_knowledge_eval_and_does_not_enable_it(tmp_path):
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


def test_owner_doc_records_knowledge_eval_stay_off():
    text = OWNER.read_text(encoding="utf-8")
    assert "radon-knowledge-eval.{service,timer}" in text
    assert "enable_services` skips both" in text
    assert "test_knowledge_eval_setup.py" in text
