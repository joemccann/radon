"""Tests for docker-compose.yml configuration."""

import yaml
import pytest


@pytest.fixture
def compose_path(root):
    return root / "docker-compose.yml"


@pytest.fixture
def compose(compose_path):
    return yaml.safe_load(compose_path.read_text())


@pytest.fixture
def ib_service(compose):
    return compose["services"]["ib-gateway"]


class TestFileValidity:
    def test_file_exists(self, compose_path):
        assert compose_path.exists()

    def test_valid_yaml(self, compose):
        assert isinstance(compose, dict)


class TestServiceDefinition:
    def test_single_service_named_ib_gateway(self, compose):
        assert list(compose["services"].keys()) == ["ib-gateway"]

    def test_image_is_gnzsnz_ib_gateway(self, ib_service):
        assert ib_service["image"].startswith("ghcr.io/gnzsnz/ib-gateway")

    def test_image_is_pinned_by_digest(self, ib_service):
        assert "@sha256:" in ib_service["image"]

    def test_image_matches_production_verified_digest(self, ib_service):
        assert ib_service["image"] == (
            "ghcr.io/gnzsnz/ib-gateway@sha256:"
            "1bbdeb1bb467ee6546810733b3709c5146ad1cdc6cd2a486b6f75dcf35a67c75"
        )

    def test_restart_owned_by_lock_aware_watchdog(self, ib_service):
        assert ib_service["restart"] == "no"

    def test_env_file_supports_an_external_production_path(self, ib_service):
        assert ib_service["env_file"] == ["${RADON_COMPOSE_ENV_FILE:-.env}"]


class TestPortBindings:
    @pytest.fixture
    def ports(self, ib_service):
        return ib_service["ports"]

    def test_port_4001_bound_to_loopback_and_tailscale(self, ports):
        # Host 4001 -> container 4003 (IB API), published on loopback (for the
        # VPS-local FastAPI) and the Tailscale interface IP (for the laptop),
        # never on the public wildcard.
        matching = [p for p in ports if "4001:4003" in p]
        assert len(matching) == 2
        assert any(p.startswith("127.0.0.1:") for p in matching)
        assert any(p.startswith("100.") for p in matching)
        assert all(not p.startswith("0.0.0.0") for p in matching)

    def test_paper_port_4002_maps_to_container_4004_on_localhost(self, ports):
        matching = [p for p in ports if "4002:4004" in p]
        assert len(matching) == 1
        assert matching[0].startswith("127.0.0.1:")

    def test_no_host_api_port_maps_to_gateway_loopback_ports(self, ports):
        assert all(not p.endswith(":4001") for p in ports)
        assert all(not p.endswith(":4002") for p in ports)

    def test_port_5900_bound_to_localhost(self, ports):
        matching = [p for p in ports if "5900:5900" in p]
        assert len(matching) == 1
        assert matching[0].startswith("127.0.0.1:")

    def test_no_ports_bound_to_all_interfaces(self, ports):
        for port in ports:
            assert "0.0.0.0" not in port, f"Port {port} is bound to 0.0.0.0"
            parts = port.split(":")
            assert len(parts) == 3, (
                f"Port {port} missing explicit bind address (defaults to 0.0.0.0)"
            )


class TestHealthcheck:
    @pytest.fixture
    def healthcheck(self, ib_service):
        return ib_service["healthcheck"]

    def test_has_test_command(self, healthcheck):
        assert "test" in healthcheck
        assert len(healthcheck["test"]) > 0

    def test_command_selects_internal_api_port_from_trading_mode(self, healthcheck):
        assert healthcheck["test"][0] == "CMD-SHELL"
        command = healthcheck["test"][1]
        assert "$${TRADING_MODE:-paper}" in command
        assert "live) port=4001" in command
        assert "paper) port=4002" in command
        assert "*) exit 1" in command

    def test_has_interval(self, healthcheck):
        assert "interval" in healthcheck

    def test_has_timeout(self, healthcheck):
        assert "timeout" in healthcheck

    def test_has_retries(self, healthcheck):
        assert "retries" in healthcheck
        assert isinstance(healthcheck["retries"], int)

    def test_has_start_period(self, healthcheck):
        assert "start_period" in healthcheck


class TestImagePinned:
    def test_image_is_not_latest(self, ib_service):
        assert not ib_service["image"].endswith(":latest"), \
            "Image must be pinned to a specific tag, not :latest"


class TestEnvironment:
    def test_has_existing_session_action(self, compose_path):
        content = compose_path.read_text()
        assert "EXISTING_SESSION_DETECTED_ACTION" in content

    def test_has_vnc_server_password(self, compose_path):
        content = compose_path.read_text()
        assert "VNC_SERVER_PASSWORD" in content

    def test_ibc_cannot_self_schedule_an_unleased_restart(self, ib_service):
        environment = ib_service["environment"]
        assert "AUTO_RESTART_TIME=" in environment
        assert "TWS_COLD_RESTART=" in environment


class TestSecurity:
    def test_no_new_privileges(self, ib_service):
        assert "no-new-privileges:true" in ib_service["security_opt"]

    def test_privileged_is_false(self, ib_service):
        assert ib_service["privileged"] is False


class TestVolumes:
    def test_named_volume_ib_config_exists(self, compose):
        assert "ib-config" in compose["volumes"]


class TestLogging:
    @pytest.fixture
    def logging_config(self, ib_service):
        return ib_service["logging"]

    def test_driver_is_json_file(self, logging_config):
        assert logging_config["driver"] == "json-file"

    def test_has_max_size(self, logging_config):
        assert "max-size" in logging_config["options"]

    def test_has_max_file(self, logging_config):
        assert "max-file" in logging_config["options"]


class TestUlimits:
    def test_ulimits_configured(self, ib_service):
        assert "ulimits" in ib_service
        assert len(ib_service["ulimits"]) > 0
