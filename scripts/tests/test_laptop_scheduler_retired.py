"""The laptop is attach-only: every local backend launcher and scheduler is retired.

The Hetzner VPS owns FastAPI, the relay, the collectors and the broker
Gateway. Nothing in the tree may reintroduce a laptop-side launcher, mode
overlay, IBC install or launchd collector.
"""
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

RETIRED_PATHS = (
    "scripts/local.sh",
    "scripts/ib",
    "scripts/_set_radon_mode.sh",
    "scripts/docker_ib_gateway.sh",
    "scripts/setup_ibc.sh",
    "scripts/ibc_remote_control.sh",
    "scripts/setup_cri_service.sh",
    "scripts/run_cri_scan.sh",
    "scripts/setup_cta_sync_service.sh",
    "scripts/setup_data_refresh_service.sh",
    "scripts/run_data_refresh.sh",
    "scripts/setup_monitor_daemon.sh",
    "scripts/setup_exit_order_service.sh",
    "scripts/run_margin_debt_refresh.sh",
    "docker/ib-gateway",
    "docs/ib-gateway-docker.md",
    "docs/ibc-remote-access.md",
    "config/com.radon.cri-scan.plist",
    "config/com.radon.cta-sync.plist",
    "config/com.radon.data-refresh.plist",
    "config/com.radon.vcg-refresh.plist",
    "config/com.radon.catalysts.plist",
    "config/com.radon.informed-flow.plist",
    "config/com.radon.event-odds.plist",
    "config/com.radon.monitor-daemon.plist",
    "config/com.radon.ibc-gateway.plist",
    "config/com.radon.margin-debt-refresh.plist",
    "config/com.radon.exit-order-service.plist",
)

SURVIVING_LAUNCHD_AGENTS = {
    "com.radon.codemap-nightly",
    "com.radon.external-probe-dispatch",
    "com.radon.fx-stable-sync",
    "com.radon.grok-fix-pickup",
    "com.radon.grok-page-responder",
    "com.radon.incident-responder",
}

PRODUCTION_EXEC_START_WRAPPERS = (
    "scripts/run_cta_sync.sh",
    "scripts/run_vcg_refresh.sh",
    "scripts/run_catalysts.sh",
    "scripts/run_informed_flow.sh",
    "scripts/run_event_odds.sh",
)


@pytest.mark.parametrize("path", RETIRED_PATHS)
def test_laptop_launcher_or_scheduler_is_retired(path):
    assert not (ROOT / path).exists(), path


def test_only_vps_watching_launchd_agents_remain():
    names = {p.stem for p in (ROOT / "config").glob("*.plist")}
    assert names == SURVIVING_LAUNCHD_AGENTS


@pytest.mark.parametrize("path", PRODUCTION_EXEC_START_WRAPPERS)
def test_production_exec_start_wrapper_exists(path):
    assert (ROOT / path).is_file(), path
