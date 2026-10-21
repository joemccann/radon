"""UW collectors participate in managed releases with narrow secret grants."""
from __future__ import annotations
import hashlib
import re
import sys
from pathlib import Path
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_app_runtime import _run, _run_line

CLOUD = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("unit", ["radon-uw-stream.service", "radon-uw-research.service"])
def test_uw_units_use_root_image_runtime_and_secret_group(tmp_path, unit):
    result = _run(tmp_path, ["run", unit])
    assert result.returncode == 0, result.stderr
    command = _run_line(result)
    assert "--group-add 1001" in command
    assert "RADON_SECRET_STORE_PATH=/home/radon/radon/data/secret_store/secrets.db" in command
    assert "python scripts/secret_store.py && exec python scripts/uw_" in command
    assert ".grok" not in command and ".codex" not in command
    assert "/run/radon-control" not in command


def test_stream_has_no_media_or_broker_lease_mount(tmp_path):
    result = _run(tmp_path, ["run", "radon-uw-stream.service"])
    assert result.returncode == 0, result.stderr
    command = _run_line(result)
    assert f"-v {tmp_path}/data/uw_stream:/home/radon/radon/data/uw_stream" in command
    assert f"-v {tmp_path}/data:/home/radon/radon/data " not in command
    assert "/var/lib/radon/media" not in command and "ib-lease" not in command


def test_uw_units_and_dropins_are_pinned_and_installed():
    names = ["radon-uw-stream.service", "radon-uw-research.service", "radon-uw-research.timer", "radon-uw-stream.service.d/runtime-container.conf", "radon-uw-research.service.d/runtime-container.conf"]
    manifest = (CLOUD / "config/installed-units.sha256").read_text()
    bootstrap = (CLOUD / "scripts/bootstrap-control-plane.sh").read_text()
    helper = (CLOUD / "scripts/deploy-root-helper.sh").read_text()
    for name in names:
        digest = hashlib.sha256((CLOUD / "services" / name).read_bytes()).hexdigest()
        assert digest + "  " + name in manifest
        assert "services/" + name in bootstrap and "services/" + name in helper
    timer = (CLOUD / "services/radon-uw-research.timer").read_text()
    assert "OnCalendar=*-*-* *:07:00" in timer
    assert "Persistent=true" in timer
