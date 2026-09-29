"""Sandbox contract for the grok responder and upgrader units.

Both units run a third-party agent CLI. Their own env file is read by
systemd before the namespace is built, so hiding it from the process
costs nothing and keeps its credentials off disk for the agent.
"""

from __future__ import annotations

from pathlib import Path

import pytest

SERVICES = Path(__file__).resolve().parents[1] / "services"
RESPONDER = "radon-grok-page-responder.service"
UPGRADE = "radon-grok-upgrade.service"
ENV_FILE = "/home/radon/radon-page-responder.env"


def _paths(unit: str, directive: str) -> set[str]:
    text = (SERVICES / unit).read_text(encoding="utf-8")
    out: set[str] = set()
    for line in text.splitlines():
        if line.startswith(f"{directive}="):
            out.update(p.lstrip("-") for p in line.split("=", 1)[1].split())
    return out


@pytest.mark.parametrize("unit", [RESPONDER, UPGRADE])
def test_env_file_is_inaccessible_to_the_process(unit):
    assert f"EnvironmentFile={ENV_FILE}" in (SERVICES / unit).read_text()
    assert ENV_FILE in _paths(unit, "InaccessiblePaths")
