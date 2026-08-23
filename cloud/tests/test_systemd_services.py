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
    "radon-bpi.service",
    "radon-bpi.timer",
    "radon-breadth.service",
    "radon-breadth.timer",
    "radon-catalysts.service",
    "radon-catalysts.timer",
    "radon-drift-audit.service",
    "radon-drift-audit.timer",
    "radon-equibles-13f.service",
    "radon-equibles-13f.timer",
    "radon-equibles-ats.service",
    "radon-equibles-ats.timer",
    "radon-equibles-cot.service",
    "radon-equibles-cot.timer",
    "radon-equibles-filings.service",
    "radon-equibles-filings.timer",
    "radon-equibles-short-crowding.service",
    "radon-equibles-short-crowding.timer",
    "radon-forecast-nightly.service",
    "radon-forecast-nightly.timer",
    "radon-garch.service",
    "radon-garch.timer",
    "radon-health.service",
    "radon-host-metrics.service",
    "radon-host-metrics.timer",
    "radon-ib-watchdog.service",
    "radon-ib-watchdog.timer",
    "radon-incident-watchdog.service",
    "radon-incident-watchdog.timer",
    "radon-grok-page-responder.service",
    "radon-grok-page-responder.timer",
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
    "radon-oi-changes.service",
    "radon-oi-changes.timer",
    "radon-knowledge.service",
    "radon-knowledge.timer",
    "radon-yield-curve.service",
    "radon-yield-curve.timer",
    "radon-straddle.service",
    "radon-straddle.timer",
    "radon-cor.service",
    "radon-cor.timer",
    "radon-vixcor.service",
    "radon-vixcor.timer",
    "radon-skew.service",
    "radon-skew.timer",
    "radon-skew2d.service",
    "radon-skew2d.timer",
    "radon-signals-refresh.service",
    "radon-signals-refresh.timer",
    "radon-flow-refresh.service",
    "radon-flow-refresh.timer",
    "radon-vol-cone.service",
    "radon-vol-cone.timer",
    "radon-vol-cone-intraday.service",
    "radon-vol-cone-intraday.timer",
    "radon-perf-twr.service",
    "radon-perf-twr.timer",
    "radon-credit-spread.service",
    "radon-credit-spread.timer",
    "radon-ivrank.service",
    "radon-ivrank.timer",
    "radon-iei-hyg.service",
    "radon-iei-hyg.timer",
    "radon-trin.service",
    "radon-trin.timer",
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

ENV_FILE_PATH = "/etc/radon/env"
STRIPPED_ENV_SERVICES = {
    "radon-grok-page-responder.service",
}
STRIPPED_ENV_FILE_PATH = "/home/radon/radon-page-responder.env"
STATIC_SERVICES = {
    "radon-refresh.service",
    "radon-drift-audit.service",
    "radon-ib-gateway-preheld-restart.service",
}
# The drift audit must read 0440 root sudoers and run `docker inspect`, so it
# stays root -- but it executes a root-owned control-plane copy of the audit,
# never the radon-writable checkout (cloud/tests/test_root_execution_paths.py).
ROOT_REQUIRED_SERVICES = {
    "radon-drift-audit.service",
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

    def test_catalysts_refreshes_three_times_per_trading_day(self, services_dir):
        text = (services_dir / "radon-catalysts.timer").read_text()
        for wall_time in ("06:30:00", "10:00:00", "16:00:00"):
            assert f"OnCalendar=Mon..Fri *-*-* {wall_time} America/New_York" in text

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


class TestRecoveryBoundaries:
    """Every installed workload needs a finite recovery path.

    A timer-owned oneshot that never exits blocks all future timer slots. A
    long-running process that exits without a restart policy becomes a silent
    outage. This fleet-wide contract pressure-tests the systemd failure mode
    without invoking broker, provider, or production credentials.
    """

    @pytest.mark.parametrize("filename", [
        name for name in EXPECTED_SERVICE_FILES if name.endswith(".service")
    ])
    def test_every_service_has_a_finite_recovery_boundary(self, unit, filename):
        service = unit(filename)["Service"]
        service_type = service.get("type", "simple")
        if service_type == "oneshot":
            assert (
                service.get("timeoutstartsec") or service.get("runtimemaxsec")
            ), f"{filename} needs a finite execution cap"
        else:
            assert service.get("restart") in {"always", "on-failure", "on-abnormal"}, (
                f"{filename} needs a restart policy"
            )


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
        assert int(svc["timeoutstartsec"]) >= 8000
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
        assert int(svc["timeoutstartsec"]) >= 10000
        assert "db_retention_sweep.py" in svc["execstart"]

    def test_timer_after_archive_window(self, unit):
        timer = unit("radon-db-retention.timer")["Timer"]
        assert "08:10" in timer["oncalendar"]
        assert timer.get("persistent") == "true"


class TestOiChanges:
    """P3: market-wide OI-changes oneshot 3x per RTH day."""

    def test_oneshot_with_timeout_and_env(self, unit, services_dir):
        svc = unit("radon-oi-changes.service")["Service"]
        assert svc["type"] == "oneshot"
        assert svc["user"] == "radon"
        assert svc["timeoutstartsec"] == "180"
        assert svc["environmentfile"] == ENV_FILE_PATH
        assert svc["workingdirectory"] == "/home/radon/radon"
        assert "run_oi_changes_refresh.sh" in svc["execstart"]
        raw = (services_dir / "radon-oi-changes.service").read_text()
        assert "Environment=RADON_DB_NO_REPLICA=1" in raw

    def test_timer_three_rth_slots(self, unit, services_dir):
        timer = unit("radon-oi-changes.timer")["Timer"]
        assert timer.get("persistent") == "true"
        assert "Mon..Fri" in timer.get("oncalendar", "")
        raw = (services_dir / "radon-oi-changes.timer").read_text()
        assert "14:00:00 UTC" in raw
        assert "17:00:00 UTC" in raw
        assert "20:00:00 UTC" in raw


class TestSignalsRefresh:
    """The dashboard's "Top candidates" scans need an RTH cadence of their own.

    Both scans shipped with a FastAPI endpoint and no caller, so the panel
    served whatever snapshot a human had last triggered by hand.
    """

    def test_oneshot_with_timeout(self, unit):
        svc = unit("radon-signals-refresh.service")["Service"]
        assert svc["type"] == "oneshot"
        assert svc["environmentfile"] == ENV_FILE_PATH
        assert svc["workingdirectory"] == "/home/radon/radon"
        assert "run_signals_refresh.sh" in svc["execstart"]
        assert "RADON_SIGNALS_SCAN_TIMEOUT=490" in svc.get("environment", "")
        # Sequential POSTs must outlive FastAPI's theta 420s + strength 480s
        # children (curl -m 490 each). A 450s unit cap SIGTERMs a live
        # first scan and pages; the hourly timer gap is 3600s.
        budget = int(svc["timeoutstartsec"])
        assert budget >= 980
        assert budget <= 1800

    def test_timer_covers_et_trading_hours_only(self, unit, services_dir):
        timer = unit("radon-signals-refresh.timer")["Timer"]
        raw = (services_dir / "radon-signals-refresh.timer").read_text()
        oncalendar = timer.get("oncalendar", "")
        assert "Mon..Fri" in oncalendar
        assert "09..16:00:00 America/New_York" in raw
        assert ":00,15,30,45" not in raw
        assert "America/New_York" in raw
        assert timer.get("persistent") == "false"


class TestFlowRefresh:
    """Laptop data-refresh is unloaded. These tabs need a VPS hourly producer."""

    def test_oneshot_with_timeout(self, unit, services_dir):
        svc = unit("radon-flow-refresh.service")["Service"]
        raw = (services_dir / "radon-flow-refresh.service").read_text()
        assert svc["type"] == "oneshot"
        assert svc["environmentfile"] == ENV_FILE_PATH
        assert "run_flow_refresh.sh" in svc["execstart"]
        assert int(svc["timeoutstartsec"]) <= 600
        assert "RADON_UW_CALLER=flow-refresh" in raw

    def test_timer_is_hourly_et(self, unit, services_dir):
        raw = (services_dir / "radon-flow-refresh.timer").read_text()
        assert "Mon..Fri" in raw
        assert "09..16:00:00 America/New_York" in raw
        assert ":00,15,30,45" not in raw
        timer = unit("radon-flow-refresh.timer")["Timer"]
        assert timer.get("persistent") == "false"


class TestSecurityRemediationSchedules:
    def test_api_migration_timeout_requires_verified_current_schema(self, services_dir):
        raw = (services_dir / "radon-api.service").read_text()
        pre = next(line for line in raw.splitlines() if line.startswith("ExecStartPre="))
        assert "timeout 30" in pre
        assert '"$rc" -eq 124' not in pre

    def test_cta_timer_is_explicit_utc(self, services_dir):
        raw = (services_dir / "radon-cta-sync.timer").read_text()
        calendars = [line for line in raw.splitlines() if line.startswith("OnCalendar=")]
        assert calendars and all(line.endswith(" UTC") for line in calendars)

    def test_leap_timer_runs_at_ten_et_across_dst(self, services_dir):
        raw = (services_dir / "radon-leap.timer").read_text()
        assert "10:00:00 America/New_York" in raw

    def test_signals_timer_has_explicit_timezone(self, services_dir):
        raw = (services_dir / "radon-signals-refresh.timer").read_text()
        assert "America/New_York" in raw

    def test_database_maintenance_units_share_serialization_lock(self, services_dir):
        names = (
            "radon-portfolio-archive.service",
            "radon-db-retention.service",
            "radon-db-backup.service",
        )
        execs = [parse_unit_file(services_dir / name)["Service"]["execstart"] for name in names]
        assert all("flock" in value for value in execs)
        lock_paths = [value.split("flock", 1)[1].split()[1] for value in execs]
        assert len(set(lock_paths)) == 1

    def test_db_maintenance_flock_loser_defers_instead_of_failing(self, unit):
        """R-067: the /run/lock/radon-db-maintenance.lock loser must defer to
        its next timer slot, not enter failed.

        Every peer's TimeoutStartSec exceeds the 7500s flock wait, so a
        long-holding peer used to time the waiter's flock out -> exit 1 ->
        Result=failed -> P1 page for pure lock contention. flock -E 75
        (EX_TEMPFAIL) + SuccessExitStatus=75 turns the lock-timeout into a
        clean deferral; the job's own service_health window (48h) still
        surfaces persistent deferral.
        """
        for name in (
            "radon-db-backup.service",
            "radon-db-retention.service",
            "radon-portfolio-archive.service",
        ):
            svc = unit(name)["Service"]
            assert "-E 75" in svc["execstart"], (
                f"{name}: flock needs -E 75 so a lock timeout is "
                "distinguishable from the job's own failure"
            )
            assert svc.get("successexitstatus") == "75", (
                f"{name}: SuccessExitStatus=75 must mark the lock-timeout "
                "deferral as success (unit ends inactive, not failed)"
            )

    def test_db_maintenance_lock_wait_leaves_full_work_budget(self, unit):
        """R-067: acquiring the lock late must not eat the work budget.

        TimeoutStartSec has to cover the full flock wait PLUS the job's own
        work ceiling, otherwise a waiter that wins the lock near the end of
        its wait is killed mid-work.
        """
        work_budgets = {
            "radon-db-backup.service": 12000,
            "radon-db-retention.service": 10000,
            "radon-portfolio-archive.service": 8000,
        }
        for name, work in work_budgets.items():
            svc = unit(name)["Service"]
            wait = int(svc["execstart"].split("-w", 1)[1].split()[0])
            assert int(svc["timeoutstartsec"]) >= wait + work, (
                f"{name}: TimeoutStartSec must be >= flock wait ({wait}s) "
                f"+ work budget ({work}s)"
            )

    def test_one_minute_skew_timer_does_not_trip_start_limit(self, unit):
        assert int(unit("radon-skew.service")["Unit"]["startlimitburst"]) >= 10

    def test_private_services_use_restrictive_umask(self, unit):
        for name in ("radon-db-backup.service", "radon-monitor.service", "radon-newsfeed.service"):
            assert unit(name)["Service"]["umask"] == "0077"

    def test_health_service_has_resource_ceilings(self, unit):
        service = unit("radon-health.service")["Service"]
        assert int(service["tasksmax"]) <= 64
        assert service["memorymax"]

    def test_nextjs_db_watchdog_has_no_recursive_root_chown(self, services_dir):
        raw = (services_dir / "radon-nextjs-db-watchdog.service").read_text()
        assert "chown -R" not in raw
        assert "StateDirectory=" in raw

    def test_cta_timeout_covers_retry_envelope(self, unit):
        assert int(unit("radon-cta-sync.service")["Service"]["timeoutstartsec"]) >= 1800

    def test_leap_fallback_uses_venv_and_has_time_budget(self, unit):
        service = unit("radon-leap.service")["Service"]
        assert "/home/radon/radon/.venv/bin/python" in service.get("environment", "")
        assert int(service["timeoutstartsec"]) >= 660


class TestSkew:
    """SKEW publishes provisional intraday snapshots during RTH."""

    def test_timer_runs_every_five_minutes_across_both_et_dst_windows(self, unit, services_dir):
        timer = unit("radon-skew.timer")["Timer"]
        raw = (services_dir / "radon-skew.timer").read_text()
        rth = next(
            line.split("=", 1)[1].strip()
            for line in raw.splitlines()
            if line.strip().startswith("OnCalendar=") and "13..21" in line
        )
        assert "Mon..Fri *-*-* 13..21:00,05,10,15,20,25,30,35,40,45,50,55" in rth
        assert "UTC" in rth
        assert ":*:00" not in rth
        assert not any(
            part.count(":") == 2 and part.split(":")[1] == "*"
            for part in rth.split()
        )
        assert "*-*-* 21:45:00 UTC" in raw
        assert timer.get("persistent") == "false"



class TestSkew2d:
    """R-066: the derived skew2d job must survive a parked parent.

    Requires=radon-skew.service makes a parent parked by its own
    StartLimitBurst brake fail every 21:50 UTC skew2d fire with a
    dependency error. Wants= keeps the start-attempt ordering without
    hard-failing the child when the parent cannot be started.
    """

    def test_parent_dependency_is_wants_not_requires(self, unit):
        u = unit("radon-skew2d.service")["Unit"]
        assert "radon-skew.service" in u.get("wants", ""), (
            "radon-skew2d.service must Wants= its parent so a parked "
            "radon-skew cannot dependency-fail the derived job"
        )
        assert "radon-skew.service" not in u.get("requires", "")
        assert "radon-skew.service" in u["after"]


class TestMediaBackup:
    """Nightly media.radon.run tree backup to B2 (prefix media/)."""

    def test_oneshot_with_timeout(self, unit):
        svc = unit("radon-media-backup.service")["Service"]
        assert svc["type"] == "oneshot"
        assert svc["timeoutstartsec"] == "3600"
        assert "media_backup.py" in svc["execstart"]
        assert svc["user"] == "radon"
        assert "/etc/radon/env" in svc["environmentfile"]
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
        assert "radon-ib-gateway.service" in u["partof"]
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
        assert "radon-ib-gateway.service" in u["partof"]

    def test_restart_sec_longer(self, unit):
        svc = unit(self.FILENAME)["Service"]
        assert svc["restartsec"] == "10"

    def test_exec_start_monitor_daemon(self, unit):
        svc = unit(self.FILENAME)["Service"]
        assert "python -m scripts.monitor_daemon.run" in svc["execstart"]


# ---------------------------------------------------------------------------
# radon-refresh.service
# ---------------------------------------------------------------------------


class TestTimerOneshotExecutionBounds:
    """Timer-owned scans need a terminal systemd boundary.

    The breadth and VCG endpoints each cap their scan child at 120 seconds;
    their wrapper has a 130-second HTTP deadline plus a direct fallback, so a
    240-second service ceiling leaves normal-path room while releasing the
    five-minute timer before its next slot. ``data_refresh`` runs three
    sequential 120-second children, so 480 seconds preserves that documented
    budget and still leaves seven minutes for the next 15-minute cadence.
    """

    EXECUTION_CAPS = {
        "radon-breadth.service": (240, 300),
        "radon-vcg-refresh.service": (240, 300),
        "radon-refresh.service": (480, 900),
    }

    @pytest.mark.parametrize(("filename", "cap_seconds", "period_seconds"), [
        (filename, cap_seconds, period_seconds)
        for filename, (cap_seconds, period_seconds) in EXECUTION_CAPS.items()
    ])
    def test_timer_oneshot_has_finite_execution_cap(
        self, unit, filename, cap_seconds, period_seconds
    ):
        svc = unit(filename)["Service"]
        assert svc["type"] == "oneshot"
        assert svc["timeoutstartsec"] == str(cap_seconds)
        assert cap_seconds < period_seconds, (
            f"{filename} timeout must release before its next timer slot"
        )


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
            if env_file is None:
                continue
            if name in STRIPPED_ENV_SERVICES:
                assert env_file == STRIPPED_ENV_FILE_PATH, (
                    f"{name} must not load production secrets; "
                    f"got EnvironmentFile={env_file}"
                )
                continue
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
    assert '"$rc" -eq 124' not in command
    assert "migrate.py" in command


class TestBpiScanBudget:
    """Weekday evenings the entire ~2,600-member universe is one session
    stale, so radon-bpi's "incremental" run is a full-universe refetch
    (~35-45 min with Yahoo courtesy sleeps). TimeoutStartSec=1200 killed
    the 2026-07-27 run mid-SPX (Result=timeout, watchdog paged); the
    budget must cover a full sweep with headroom."""

    def test_service_start_budget_covers_full_universe_sweep(self, unit):
        svc = unit("radon-bpi.service")["Service"]
        assert int(svc["timeoutstartsec"]) >= 3600

    def test_timer_has_evening_and_catchup_passes(self, unit, services_dir):
        raw = (services_dir / "radon-bpi.timer").read_text()
        assert "Mon..Fri *-*-* 21:30:00 UTC" in raw
        assert "Mon..Fri *-*-* 23:30:00 UTC" in raw
        assert "Tue..Sat *-*-* 11:00:00 UTC" in raw

    def test_start_budget_ends_before_the_2330_catchup_fire(self, unit):
        """R-071: a still-activating oneshot swallows its own timer fires.

        The 23:30 UTC catch-up exists precisely to recover a lagging 21:30
        run, but TimeoutStartSec=9000 (150 min) let a slow/tarpitted 21:30
        run still be activating at 23:30, so systemd dropped the catch-up
        fire on the floor. The budget must end before the 7200s inter-fire
        gap, worst-compressed by RandomizedDelaySec=120 on the first fire
        (7200 - 120 = 7080s), while still covering the worst measured
        tarpitted sweep (105 min = 6300s from the 2026-07-27 follow-up).
        """
        svc = unit("radon-bpi.service")["Service"]
        budget = int(svc["timeoutstartsec"])
        assert budget <= 7200 - 120, (
            "radon-bpi TimeoutStartSec must end before the 23:30 UTC "
            "catch-up fire (21:30 fire delayed up to RandomizedDelaySec=120)"
        )
        assert budget >= 6300, (
            "budget must still cover the worst measured tarpitted sweep"
        )


class TestLeapGarchScanBudget:
    """Index-universe LEAP/GARCH scans need an hour-scale FastAPI budget
    (3600s) plus systemd headroom so TimeoutStartSec does not kill the
    oneshot mid-sweep."""

    @pytest.mark.parametrize("unit_name", ["radon-leap.service", "radon-garch.service"])
    def test_service_start_budget_covers_index_universe_scan(self, unit, unit_name):
        svc = unit(unit_name)["Service"]
        assert int(svc["timeoutstartsec"]) >= 3900


class TestGrokPageResponder:
    """Dedicated clone + stripped env. Never the live checkout."""

    def test_oneshot_budget_covers_grok(self, unit):
        svc = unit("radon-grok-page-responder.service")["Service"]
        assert svc["type"] == "oneshot"
        assert int(svc["timeoutstartsec"]) >= 3900
        assert svc["workingdirectory"] == "/home/radon/radon-page-responder"
        assert svc["environmentfile"] == STRIPPED_ENV_FILE_PATH
        assert "radon-cloud/.env" not in svc["environmentfile"]
        assert "grok_page_responder.py" in svc["execstart"]
        assert "/home/radon/radon/.venv" not in svc["execstart"]

    def test_cannot_reach_docker_or_deploy_root(self, unit):
        svc = unit("radon-grok-page-responder.service")["Service"]
        hidden = svc.get("inaccessiblepaths", "")
        assert "docker.sock" in hidden
        assert "radon-deploy-root" in hidden

    def test_timer_fires_after_idle(self, unit):
        timer = unit("radon-grok-page-responder.timer")["Timer"]
        assert timer["onunitinactivesec"] == "30"
        assert timer["persistent"] == "false"


class TestIncidentWatchdog:
    """Endpoint/body/deploy prober writing data/incidents artifacts.

    Alert/restart policy stays with scripts/watchdog and the tier-3 external
    probe — this unit only collects evidence, so an open P1 (exit 2) must not
    park it as failed on every 5-minute cycle."""

    def test_oneshot_with_timeout_and_env(self, unit):
        svc = unit("radon-incident-watchdog.service")["Service"]
        assert svc["type"] == "oneshot"
        assert int(svc["timeoutstartsec"]) <= 240
        assert svc["environmentfile"] == ENV_FILE_PATH
        assert svc["workingdirectory"] == "/home/radon/radon"
        assert "scripts.incident_watchdog --once" in svc["execstart"]

    def test_open_p1_exit_code_is_success(self, unit):
        svc = unit("radon-incident-watchdog.service")["Service"]
        assert svc["successexitstatus"] == "2"

    def test_timer_every_five_minutes(self, unit):
        timer = unit("radon-incident-watchdog.timer")["Timer"]
        assert timer["oncalendar"].endswith("*:00,05,10,15,20,25,30,35,40,45,50,55")
        assert timer["persistent"] == "false"


class TestPerMinuteStartLimits:
    """A per-minute oneshot must not sit on its own start-limit boundary.

    radon-skew.timer historically fired every minute through RTH while
    radon-skew.service carried StartLimitBurst=5 per 300s. Five starts per
    300 seconds IS one per minute, so the unit ran exactly at the ceiling and
    any jitter (RandomizedDelaySec, a run crossing a second boundary) tripped
    start-limit-hit. systemd then PARKS the unit -- it does not auto-recover,
    it needs a manual `systemctl reset-failed`, and the watchdog pages.

    The script itself was healthy throughout (70 successful runs in 40
    minutes), which is what makes this shape dangerous: nothing is broken, so
    nothing points at the unit file.
    """

    @staticmethod
    def _fires_every_minute(oncalendar: str) -> bool:
        # "*-*-* *:*:00" or "Mon..Fri *-*-* 13..21:*:00" - a wildcard MINUTE
        # field is what makes it per-minute.
        return any(
            part.count(":") == 2 and part.split(":")[1] == "*"
            for part in oncalendar.split()
        )

    def test_every_per_minute_timer_has_headroom(self, services_dir, all_units):
        offenders = []
        for path in sorted(services_dir.iterdir()):
            name = path.name
            if not name.endswith(".timer"):
                continue

            # Read the raw lines: a timer may carry SEVERAL OnCalendar entries
            # and configparser keeps only the last, which is how the per-minute
            # schedule on radon-skew.timer hid behind a trailing 21:45 entry.
            schedules = [
                line.split("=", 1)[1].strip()
                for line in path.read_text().splitlines()
                if line.strip().startswith("OnCalendar=")
            ]
            if not any(self._fires_every_minute(s) for s in schedules):
                continue

            service = all_units.get(name[: -len(".timer")] + ".service")
            if service is None:
                continue

            burst = int(service.get("Unit", {}).get("startlimitburst", "5"))
            interval = int(service.get("Unit", {}).get("startlimitintervalsec", "0"))
            if interval == 0:
                continue

            # starts the timer will actually attempt inside one interval
            attempts = interval // 60
            if burst <= attempts:
                offenders.append(
                    f"{name}: burst={burst} but the timer attempts ~{attempts} "
                    f"starts per {interval}s window"
                )

        assert offenders == [], "; ".join(offenders)

    def test_skew_specifically_has_the_raised_burst(self, unit):
        svc = unit("radon-skew.service")["Unit"]
        assert int(svc["startlimitburst"]) >= 10
