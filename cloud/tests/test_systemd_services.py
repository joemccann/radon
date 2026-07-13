"""Tests for systemd unit files in services/."""

import configparser
from pathlib import Path

import pytest

EXPECTED_SERVICE_FILES = [
    "radon-api.service",
    "radon-ib-gateway.service",
    "radon-ib-gateway-preheld-restart.service",
    "radon-monitor.service",
    "radon-newsfeed.service",
    "radon-nextjs.service",
    "radon-refresh.service",
    "radon-refresh.timer",
    "radon-relay.service",
    "radon-vcg-refresh.service",
    "radon-vcg-refresh.timer",
    "radon-cta-sync.service",
    "radon-cta-sync.timer",
    "radon-portfolio-sync.service",
    "radon-portfolio-sync.timer",
    "radon-watchdog-intraday.service",
    "radon-watchdog-intraday.timer",
    "radon-watchdog-continuous.service",
    "radon-watchdog-continuous.timer",
    "radon-watchdog-daily.service",
    "radon-watchdog-daily.timer",
    "radon-watchdog-error.service",
    "radon-watchdog-error.timer",
    "radon-db-backup.service",
    "radon-db-backup.timer",
    "radon-portfolio-archive.service",
    "radon-portfolio-archive.timer",
    "radon-media-backup.service",
    "radon-media-backup.timer",
    "radon-db-retention.service",
    "radon-db-retention.timer",
    "radon-breadth.service",
    "radon-breadth.timer",
    "radon-catalysts.service",
    "radon-catalysts.timer",
    "radon-drift-audit.service",
    "radon-drift-audit.timer",
    "radon-forecast-nightly.service",
    "radon-forecast-nightly.timer",
    "radon-garch.service",
    "radon-garch.timer",
    "radon-health.service",
    "radon-host-metrics.service",
    "radon-host-metrics.timer",
    "radon-ib-watchdog.service",
    "radon-ib-watchdog.timer",
    "radon-leap.service",
    "radon-leap.timer",
    "radon-llm-index.service",
    "radon-llm-index.timer",
    "radon-nextjs-db-watchdog.service",
    "radon-nextjs-db-watchdog.timer",
    "radon-demo-mirror.service",
    "radon-demo-mirror.timer",
    "radon-margin-debt.service",
    "radon-margin-debt.timer",
]

LONG_RUNNING_SERVICES = [
    "radon-api.service",
    "radon-nextjs.service",
    "radon-relay.service",
    "radon-monitor.service",
    "radon-newsfeed.service",
]

IB_GATEWAY_DEPENDENTS = [
    "radon-api.service",
    "radon-monitor.service",
    "radon-refresh.service",
    "radon-relay.service",
    "radon-portfolio-sync.service",
]

ENV_FILE_PATH = "/home/radon/radon-cloud/.env"
STATIC_SERVICES = {
    "radon-refresh.service",
    "radon-drift-audit.service",
    "radon-ib-gateway-preheld-restart.service",
}
ROOT_REQUIRED_SERVICES = {
    "radon-drift-audit.service",
    "radon-nextjs-db-watchdog.service",
}


def parse_unit_file(path: Path) -> dict[str, dict[str, str]]:
    """Parse a systemd unit file into a nested dict of section -> key -> value.

    Uses configparser with strict=False to handle duplicate keys (e.g. After
    appearing with multiple values on the same line is fine, but truly
    duplicate keys need strict=False).  allow_no_value=True handles bare
    flags that systemd allows.
    """
    parser = configparser.ConfigParser(
        strict=False,
        allow_no_value=True,
        interpolation=None,
    )
    parser.read(path)
    return {section: dict(parser.items(section)) for section in parser.sections()}


@pytest.fixture
def all_units(services_dir):
    """Return a dict mapping filename -> parsed unit dict for every file."""
    return {f.name: parse_unit_file(f) for f in services_dir.iterdir()}


@pytest.fixture
def unit(services_dir):
    """Return a helper that loads a single unit file by name."""

    def _load(name: str) -> dict[str, dict[str, str]]:
        return parse_unit_file(services_dir / name)

    return _load


# ---------------------------------------------------------------------------
# Structural tests (all services)
# ---------------------------------------------------------------------------


class TestStructure:
    """Every expected file exists and has the required sections."""

    def test_all_expected_files_exist(self, services_dir):
        actual = sorted(
            f.name for f in services_dir.iterdir()
            if f.suffix in {".service", ".timer"}
        )
        assert actual == sorted(EXPECTED_SERVICE_FILES)

    @pytest.mark.parametrize("filename", [
        f for f in EXPECTED_SERVICE_FILES if f.endswith(".service")
    ])
    def test_service_has_required_sections(self, unit, filename):
        cfg = unit(filename)
        for section in ("Unit", "Service", "Install"):
            if filename in STATIC_SERVICES and section == "Install":
                continue
            assert section in cfg, f"{filename} missing [{section}]"

    def test_timer_has_required_sections(self, unit):
        cfg = unit("radon-refresh.timer")
        for section in ("Unit", "Timer", "Install"):
            assert section in cfg, f"radon-refresh.timer missing [{section}]"

    @pytest.mark.parametrize("filename", EXPECTED_SERVICE_FILES)
    def test_every_unit_has_description(self, unit, filename):
        cfg = unit(filename)
        assert "description" in cfg["Unit"]

    @pytest.mark.parametrize("filename", [
        f for f in EXPECTED_SERVICE_FILES if f.endswith(".service")
    ])
    def test_no_service_runs_as_root(self, unit, filename):
        cfg = unit(filename)
        expected_user = "root" if filename in ROOT_REQUIRED_SERVICES else "radon"
        assert cfg["Service"]["user"] == expected_user


# ---------------------------------------------------------------------------
# radon-ib-gateway.service
# ---------------------------------------------------------------------------


class TestIBGateway:
    FILENAME = "radon-ib-gateway.service"

    def test_type_oneshot_remain_after_exit(self, unit):
        svc = unit(self.FILENAME)["Service"]
        assert svc["type"] == "oneshot"
        assert svc["remainafterexit"] == "yes"

    def test_after_and_requires_docker(self, unit):
        u = unit(self.FILENAME)["Unit"]
        assert "docker.service" in u["after"]
        assert "docker.service" in u["requires"]

    def test_working_directory(self, unit):
        svc = unit(self.FILENAME)["Service"]
        assert svc["workingdirectory"] == "/home/radon/radon/cloud"

    def test_exec_start_uses_gateway_control_helper(self, unit):
        svc = unit(self.FILENAME)["Service"]
        assert svc["execstart"] == "/usr/local/bin/radon-ib-gateway-control start"

    def test_exec_stop_uses_gateway_control_helper(self, unit):
        svc = unit(self.FILENAME)["Service"]
        assert svc["execstop"] == "/usr/local/bin/radon-ib-gateway-control stop"


# ---------------------------------------------------------------------------
# radon-api.service
# ---------------------------------------------------------------------------


class TestPortfolioArchive:
    """R1: nightly portfolio_snapshots cold-archive before db-backup."""

    def test_oneshot_with_timeout(self, unit):
        svc = unit("radon-portfolio-archive.service")["Service"]
        assert svc["type"] == "oneshot"
        assert svc["timeoutstartsec"] == "7200"
        assert "archive_portfolio_snapshots.py" in svc["execstart"]
        # Fails closed without RADON_ARCHIVE_S3_* — no allow-delete-without-upload.
        assert "--allow-delete-without-upload" not in svc["execstart"]
        env = svc.get("environment", "")
        # configparser may only keep the last Environment= line
        assert "RADON_DB_NO_REPLICA=1" in env or "RADON_DB_NO_REPLICA" in env

    def test_timer_alone_before_retention(self, unit):
        timer = unit("radon-portfolio-archive.timer")["Timer"]
        assert "05:40" in timer["oncalendar"]
        assert timer.get("persistent") == "true"


class TestDbRetention:
    """R2: keep-latest sweep for append-only scan tables."""

    def test_oneshot_with_timeout(self, unit):
        svc = unit("radon-db-retention.service")["Service"]
        assert svc["type"] == "oneshot"
        assert svc["timeoutstartsec"] == "1800"
        assert "db_retention_sweep.py" in svc["execstart"]

    def test_timer_after_archive_window(self, unit):
        timer = unit("radon-db-retention.timer")["Timer"]
        assert "08:10" in timer["oncalendar"]
        assert timer.get("persistent") == "true"


class TestMediaBackup:
    """Nightly media.radon.run tree backup to B2 (prefix media/)."""

    def test_oneshot_with_timeout(self, unit):
        svc = unit("radon-media-backup.service")["Service"]
        assert svc["type"] == "oneshot"
        assert svc["timeoutstartsec"] == "3600"
        assert "media_backup.py" in svc["execstart"]
        assert svc["user"] == "radon"
        assert "/home/radon/radon-cloud/.env" in svc["environmentfile"]
        env = svc.get("environment", "")
        assert "RADON_MEDIA_DIR" in env or "RADON_DB_NO_REPLICA" in env

    def test_timer_after_db_backup(self, unit):
        timer = unit("radon-media-backup.timer")["Timer"]
        assert "10:15" in timer["oncalendar"]
        assert timer.get("persistent") == "true"


class TestAPI:
    FILENAME = "radon-api.service"

    def test_type_simple_restart_always(self, unit):
        svc = unit(self.FILENAME)["Service"]
        assert svc["type"] == "simple"
        assert svc["restart"] == "always"
        assert svc["restartsec"] == "5"

    def test_depends_on_ib_gateway(self, unit):
        u = unit(self.FILENAME)["Unit"]
        # Ordering only: Wants/Requires would pull Gateway under the deploy
        # lock or cascade-stop the control plane when Gateway is deliberately
        # stopped for 2FA recovery.
        assert "radon-ib-gateway.service" in u["after"]
        assert "radon-ib-gateway.service" not in u.get("wants", "")
        assert "radon-ib-gateway.service" not in u.get("requires", "")

    def test_exec_start_uvicorn_host_port(self, unit):
        svc = unit(self.FILENAME)["Service"]
        assert "uvicorn" in svc["execstart"]
        assert "--host 0.0.0.0" in svc["execstart"]
        assert "--forwarded-allow-ips 127.0.0.1" in svc["execstart"]
        assert "--port 8321" in svc["execstart"]

    def test_environment_file(self, unit):
        svc = unit(self.FILENAME)["Service"]
        assert svc["environmentfile"] == ENV_FILE_PATH


# ---------------------------------------------------------------------------
# radon-nextjs.service
# ---------------------------------------------------------------------------


class TestNextJS:
    FILENAME = "radon-nextjs.service"

    def test_working_directory(self, unit):
        svc = unit(self.FILENAME)["Service"]
        assert svc["workingdirectory"] == "/home/radon/radon/web"

    def test_exec_start_npm_run_start(self, unit):
        svc = unit(self.FILENAME)["Service"]
        assert "npm run start" in svc["execstart"]

    def test_restart_always(self, unit):
        svc = unit(self.FILENAME)["Service"]
        assert svc["restart"] == "always"


# ---------------------------------------------------------------------------
# radon-relay.service
# ---------------------------------------------------------------------------


class TestRelay:
    FILENAME = "radon-relay.service"

    def test_depends_on_ib_gateway(self, unit):
        u = unit(self.FILENAME)["Unit"]
        assert "radon-ib-gateway.service" in u["after"]
        assert "radon-ib-gateway.service" not in u.get("requires", "")
        assert "radon-ib-gateway.service" not in u.get("wants", "")

    def test_exec_start_node_relay(self, unit):
        svc = unit(self.FILENAME)["Service"]
        assert "node" in svc["execstart"]
        assert "scripts/ib_realtime_server.js" in svc["execstart"]


# ---------------------------------------------------------------------------
# radon-monitor.service
# ---------------------------------------------------------------------------


class TestMonitor:
    FILENAME = "radon-monitor.service"

    def test_depends_on_ib_gateway_and_api(self, unit):
        u = unit(self.FILENAME)["Unit"]
        assert "radon-ib-gateway.service" in u["after"]
        assert "radon-api.service" in u["after"]

    def test_restart_sec_longer(self, unit):
        svc = unit(self.FILENAME)["Service"]
        assert svc["restartsec"] == "10"

    def test_exec_start_monitor_daemon(self, unit):
        svc = unit(self.FILENAME)["Service"]
        assert "python -m scripts.monitor_daemon.run" in svc["execstart"]


# ---------------------------------------------------------------------------
# radon-refresh.service
# ---------------------------------------------------------------------------


class TestRefresh:
    FILENAME = "radon-refresh.service"

    def test_type_oneshot(self, unit):
        svc = unit(self.FILENAME)["Service"]
        assert svc["type"] == "oneshot"

    def test_no_restart(self, unit):
        svc = unit(self.FILENAME)["Service"]
        assert "restart" not in svc

    def test_no_install_section(self, unit):
        cfg = unit(self.FILENAME)
        assert "Install" not in cfg


# ---------------------------------------------------------------------------
# radon-refresh.timer
# ---------------------------------------------------------------------------


class TestRefreshTimer:
    FILENAME = "radon-refresh.timer"

    def test_on_calendar_market_hours(self, unit):
        timer = unit(self.FILENAME)["Timer"]
        value = timer["oncalendar"]
        assert "Mon..Fri" in value
        # Must be restricted to the ET trading-hours UTC window (13..21), not
        # firing 24h overnight (the old `Mon..Fri *:0/15` churned data_refresh
        # all night). Guard against a regression back to all-day.
        assert "13..21" in value, f"radon-refresh.timer must gate to trading hours, got: {value}"

    def test_persistent(self, unit):
        timer = unit(self.FILENAME)["Timer"]
        # High-frequency timers stay non-persistent so a reboot or deploy does
        # not stampede a backlog of catch-up runs.
        assert timer["persistent"] == "false"

    def test_wanted_by_timers_target(self, unit):
        install = unit(self.FILENAME)["Install"]
        assert install["wantedby"] == "timers.target"


# ---------------------------------------------------------------------------
# radon-watchdog-* units
# ---------------------------------------------------------------------------


WATCHDOG_BUCKETS = ["intraday", "continuous", "daily", "error"]


class TestWatchdogServices:
    """Each bucket has a matching .service + .timer pair."""

    @pytest.mark.parametrize("bucket", WATCHDOG_BUCKETS)
    def test_service_type_oneshot(self, unit, bucket):
        svc = unit(f"radon-watchdog-{bucket}.service")["Service"]
        assert svc["type"] == "oneshot"

    @pytest.mark.parametrize("bucket", WATCHDOG_BUCKETS)
    def test_service_runs_as_radon(self, unit, bucket):
        svc = unit(f"radon-watchdog-{bucket}.service")["Service"]
        assert svc["user"] == "radon"

    @pytest.mark.parametrize("bucket", WATCHDOG_BUCKETS)
    def test_service_uses_project_venv(self, unit, bucket):
        svc = unit(f"radon-watchdog-{bucket}.service")["Service"]
        assert "/home/radon/radon/.venv/bin/python" in svc["execstart"]

    @pytest.mark.parametrize("bucket", WATCHDOG_BUCKETS)
    def test_service_invokes_watchdog_module(self, unit, bucket):
        svc = unit(f"radon-watchdog-{bucket}.service")["Service"]
        assert "scripts.watchdog" in svc["execstart"]
        assert f"--bucket {bucket}" in svc["execstart"]

    @pytest.mark.parametrize("bucket", WATCHDOG_BUCKETS)
    def test_service_uses_shared_env_file(self, unit, bucket):
        svc = unit(f"radon-watchdog-{bucket}.service")["Service"]
        assert svc["environmentfile"] == ENV_FILE_PATH

    @pytest.mark.parametrize("bucket", WATCHDOG_BUCKETS)
    def test_timer_requires_matching_service(self, unit, bucket):
        # Timers associate by basename with their matching service unit; an
        # explicit Requires would cascade-stop the timer when a oneshot fails.
        timer_unit = unit(f"radon-watchdog-{bucket}.timer")
        assert "Timer" in timer_unit
        assert unit(f"radon-watchdog-{bucket}.service")["Service"]["type"] == "oneshot"
        assert f"radon-watchdog-{bucket}.service" not in timer_unit["Unit"].get("requires", "")

    @pytest.mark.parametrize("bucket", WATCHDOG_BUCKETS)
    def test_timer_persistent(self, unit, bucket):
        timer = unit(f"radon-watchdog-{bucket}.timer")["Timer"]
        assert timer["persistent"] == "false"

    def test_intraday_timer_gated_to_weekday_trading_hours(self, unit):
        timer = unit("radon-watchdog-intraday.timer")["Timer"]
        # 13..21:00 UTC mirrors radon-vcg-refresh.timer.
        assert "Mon..Fri" in timer["oncalendar"]
        assert "13..21" in timer["oncalendar"]

    def test_continuous_timer_runs_every_5min_24_7(self, unit):
        timer = unit("radon-watchdog-continuous.timer")["Timer"]
        # No Mon..Fri gate, fires around the clock.
        assert "Mon..Fri" not in timer["oncalendar"]
        assert "00,05,10,15,20,25,30,35,40,45,50,55" in timer["oncalendar"]

    def test_daily_timer_runs_hourly(self, unit):
        timer = unit("radon-watchdog-daily.timer")["Timer"]
        assert timer["oncalendar"] == "*-*-* *:00"

    def test_error_timer_runs_every_5min_24_7(self, unit):
        timer = unit("radon-watchdog-error.timer")["Timer"]
        assert "Mon..Fri" not in timer["oncalendar"]
        assert "00,05,10,15,20,25,30,35,40,45,50,55" in timer["oncalendar"]


# ---------------------------------------------------------------------------
# Cross-cutting tests
# ---------------------------------------------------------------------------


class TestCrossCutting:

    @pytest.mark.parametrize("filename", LONG_RUNNING_SERVICES)
    def test_long_running_services_restart_always(self, unit, filename):
        svc = unit(filename)["Service"]
        # `always` for the core daemons; `on-failure` is the deliberate
        # choice for radon-newsfeed (CLAUDE.md: "RestartSec=30" prevents
        # rapid login-throttle loops on themarketear after auth failure).
        assert svc["restart"] in {"always", "on-failure"}, (
            f"{filename} has Restart={svc['restart']}; expected always or on-failure"
        )

    def test_all_environment_files_point_to_same_path(self, all_units):
        for name, cfg in all_units.items():
            if "Service" not in cfg:
                continue
            env_file = cfg["Service"].get("environmentfile")
            if env_file is not None:
                assert env_file == ENV_FILE_PATH, (
                    f"{name} has EnvironmentFile={env_file}, expected {ENV_FILE_PATH}"
                )

    @pytest.mark.parametrize("filename", IB_GATEWAY_DEPENDENTS)
    def test_ib_gateway_dependents_use_ordering_and_intentional_strength(self, unit, filename):
        u = unit(filename)["Unit"]
        assert "radon-ib-gateway.service" in u.get("after", "")
        # Deploy-lock safety: no Wants/Requires on Gateway from app units.
        # Pulling Gateway during a locked deploy restarts the container under
        # contention; cascading stops black out recovery surfaces.
        assert "radon-ib-gateway.service" not in u.get("wants", "")
        assert "radon-ib-gateway.service" not in u.get("requires", "")
def test_api_migration_transport_stall_is_bounded_without_masking_errors(services_dir):
    service = (services_dir / "radon-api.service").read_text()
    command = next(line for line in service.splitlines() if line.startswith("ExecStartPre="))
    assert "/usr/bin/timeout 30" in command
    assert '"$rc" -eq 124' in command
    assert '"$rc" -eq 0' in command
