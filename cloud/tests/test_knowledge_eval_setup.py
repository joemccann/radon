"""Knowledge golden-eval units copy on setup but stay disabled.

Do not enable until a human reviews the draft golden set. A failed oneshot pages via
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


def test_enable_gate_is_the_draft_review_not_a_missing_baseline():
    """The checked-in baseline is already a live snapshot. The skip stays."""
    setup = SETUP.read_text(encoding="utf-8")
    comment = setup.split("radon-knowledge-eval.service", 1)[1].split(
        "radon-ib-gateway-remote.service", 1
    )[0]
    assert "draft golden set" in comment
    assert "placeholder-baseline" not in comment
    assert "until a live baseline" not in comment

    allow = (CLOUD / "config" / "drift-allowlist.conf").read_text(encoding="utf-8")
    for unit in UNITS:
        line = next(row for row in allow.splitlines() if row.startswith(f"not-installed:{unit} "))
        assert "golden_set.json draft" in line
        assert "baseline write" not in line

    repo = CLOUD.parent
    embeddings = (repo / "docs" / "knowledge-embeddings.md").read_text(encoding="utf-8")
    assert "until a live baseline replaces" not in embeddings
    assert "After the first live VPS write" not in embeddings
    operations = (repo / "docs" / "operations.md").read_text(encoding="utf-8")
    assert "write-baseline` run before" not in operations
