"""IBC TrustedTwsApiClientIPs reference (Ops Plane step 2C).

cloud/ibc-overrides/trusted-ips.txt trusted 100.0.0.0/8: every tailnet node
plus a slice of public address space. The only API clients the Gateway
should ever see are loopback (the in-container socat relay and broker-local
tools) and the app host over radon-private (10.0.0.2).

The file is a reference, not a deployed artifact: nothing in deploy,
setup or compose reads it, and the gnzsnz image fronts the Gateway with an
in-container socat relay, so every API client already reaches the Gateway
as 127.0.0.1. The last test pins that fact: if someone wires the file into
a deploy path, the rollout story in docs/operations.md must change.
"""

from __future__ import annotations

import ipaddress
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TRUSTED = ROOT / "ibc-overrides" / "trusted-ips.txt"


def _entries() -> list[str]:
    lines = [
        line.strip()
        for line in TRUSTED.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    assert len(lines) == 1, lines
    key, _, value = lines[0].partition("=")
    assert key == "TrustedTwsApiClientIPs"
    return [v.strip() for v in value.split(",") if v.strip()]


def test_only_loopback_and_the_app_private_address():
    assert _entries() == ["127.0.0.1", "10.0.0.2"]


def test_every_entry_is_a_single_host_outside_the_tailnet():
    tailnet = ipaddress.ip_network("100.64.0.0/10")
    for entry in _entries():
        assert "/" not in entry, entry
        address = ipaddress.ip_address(entry)
        assert address not in tailnet, entry
        assert address.is_loopback or address.is_private, entry


def test_no_deploy_path_reads_the_file():
    sources = [ROOT / "docker-compose.yml"]
    sources += sorted((ROOT / "scripts").glob("*.sh"))
    sources += sorted((ROOT / "scripts").glob("*.py"))
    sources += sorted((ROOT / "services").glob("*"))
    for path in sources:
        if path.is_file():
            text = path.read_text(encoding="utf-8", errors="replace")
            assert "ibc-overrides" not in text, path
            assert "TrustedTwsApiClientIPs" not in text, path
