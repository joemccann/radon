"""Route tests for the operator credentials surface (/credentials).

The store is a real SecretStore on tmp paths (cheap local sqlite — no Turso),
the validator is stubbed on the route module. Wire discipline: an invalid
verdict must store NOTHING and return 422; plaintext must never appear in any
response body.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

SCRIPTS_DIR = Path(__file__).resolve().parent.parent.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from credential_validators import ValidationResult  # noqa: E402
from secret_store import SecretStore  # noqa: E402


def _route_module_instances():
    from scripts.api.routes import credentials as credentials_module

    modules = [credentials_module]
    doppelganger = sys.modules.get("api.routes.credentials")
    if doppelganger is not None and doppelganger is not credentials_module:
        modules.append(doppelganger)
    return modules


@pytest.fixture(autouse=True)
def localhost_bypass(monkeypatch):
    from scripts.api import auth, server

    monkeypatch.setattr(auth, "is_trusted_local_request", lambda request: True)
    monkeypatch.setattr(server, "is_trusted_local_request", lambda request: True)
    for module in _route_module_instances():
        monkeypatch.setattr(
            module, "is_trusted_local_request", lambda request: True, raising=False
        )


@pytest.fixture(autouse=True)
def tmp_store(tmp_path, monkeypatch):
    monkeypatch.setenv("RADON_SECRET_STORE_PATH", str(tmp_path / "secrets.db"))
    monkeypatch.setenv(
        "RADON_SECRET_STORE_KEY_FILE", str(tmp_path / "secret_store.key")
    )
    monkeypatch.delenv("CREDENTIALS_DIRECTORY", raising=False)
    return tmp_path


@pytest.fixture(autouse=True)
def reset_validator_throttle(monkeypatch):
    """Cooldown + concurrency state is module-level; every test starts cold."""
    for module in _route_module_instances():
        monkeypatch.setattr(module, "_validator_last_run", {}, raising=False)
        monkeypatch.setattr(module, "_validator_slots", None, raising=False)


@pytest.fixture()
def valid_verdict(monkeypatch):
    calls = []

    def _validate(service_id, values):
        calls.append((service_id, dict(values)))
        return ValidationResult("valid", "")

    for module in _route_module_instances():
        monkeypatch.setattr(module.credential_validators, "validate", _validate)
    return calls


@pytest.fixture()
def invalid_verdict(monkeypatch):
    def _validate(service_id, values):
        return ValidationResult("invalid", "the vendor said no")

    for module in _route_module_instances():
        monkeypatch.setattr(module.credential_validators, "validate", _validate)


@pytest.fixture
def client():
    # No `with` (no lifespan): these tests exercise the route handlers only,
    # and running the lifespan would leak RADON_API_TEST_MODE process-wide
    # (the documented test-ordering pollution class in this suite).
    from scripts.api.server import app

    return TestClient(app)


def _store() -> SecretStore:
    return SecretStore()


class TestList:
    def test_lists_every_registry_service(self, client):
        response = client.get("/credentials")
        assert response.status_code == 200
        payload = response.json()
        ids = {service["id"] for service in payload["services"]}
        assert {"cerebras", "unusual_whales", "turso", "menthorq"} <= ids
        assert not {"anthropic", "xai"} & ids  # subscriptions only, 2026-10-26
        assert payload["groups"]
        assert payload["generated_at"]

    def test_configured_field_reports_hint_never_value(self, client):
        _store().set_secret("UW_TOKEN", "uw-supersecret-value", actor="test")
        response = client.get("/credentials")
        body = response.text
        assert "uw-supersecret-value" not in body
        uw = next(
            service
            for service in response.json()["services"]
            if service["id"] == "unusual_whales"
        )
        field = next(field for field in uw["fields"] if field["name"] == "UW_TOKEN")
        assert field["configured"] is True
        assert field["hint"].endswith("alue")
        assert "value" not in field

    def test_env_fallback_flagged(self, client, monkeypatch):
        monkeypatch.setenv("UW_TOKEN", "from-dotenv")
        response = client.get("/credentials")
        uw = next(
            service
            for service in response.json()["services"]
            if service["id"] == "unusual_whales"
        )
        field = next(field for field in uw["fields"] if field["name"] == "UW_TOKEN")
        assert field["configured"] is False
        assert field["env_fallback"] is True
        assert field["exported_only"] is False

    def test_exported_only_after_delete(self, client, valid_verdict, monkeypatch):
        monkeypatch.delenv("UW_TOKEN", raising=False)
        client.put(
            "/credentials/unusual_whales",
            json={"values": {"UW_TOKEN": "uw-live-token"}, "updated_by": "op-1"},
        )
        client.delete("/credentials/unusual_whales/UW_TOKEN")
        import os

        assert os.environ.get("UW_TOKEN") == "uw-live-token"
        response = client.get("/credentials")
        uw = next(
            service
            for service in response.json()["services"]
            if service["id"] == "unusual_whales"
        )
        field = next(field for field in uw["fields"] if field["name"] == "UW_TOKEN")
        assert field["configured"] is False
        assert field["exported_only"] is True
        assert field["env_fallback"] is False


class TestPut:
    def test_valid_saves_and_applies_env(self, client, valid_verdict, monkeypatch):
        monkeypatch.delenv("UW_TOKEN", raising=False)
        response = client.put(
            "/credentials/unusual_whales",
            json={"values": {"UW_TOKEN": "uw-brand-new-token"}, "updated_by": "op-1"},
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["validation"]["status"] == "valid"
        assert "uw-brand-new-token" not in response.text
        assert _store().get_secret("UW_TOKEN") == "uw-brand-new-token"
        import os

        assert os.environ.get("UW_TOKEN") == "uw-brand-new-token"
        (call,) = valid_verdict
        assert call[0] == "unusual_whales"
        assert call[1]["UW_TOKEN"] == "uw-brand-new-token"

    def test_invalid_stores_nothing_and_422s(self, client, invalid_verdict, monkeypatch):
        monkeypatch.delenv("UW_TOKEN", raising=False)
        response = client.put(
            "/credentials/unusual_whales",
            json={"values": {"UW_TOKEN": "uw-bad-token"}},
        )
        assert response.status_code == 422
        detail = response.json()["detail"]
        assert detail["code"] == "CREDENTIAL_REJECTED"
        assert detail["message"] == "the vendor said no"
        assert _store().get_secret("UW_TOKEN") is None
        import os

        assert os.environ.get("UW_TOKEN") is None

    def test_partial_submit_merges_stored_values(self, client, valid_verdict):
        _store().set_secret("MENTHORQ_USER", "u@x.com", actor="test")
        response = client.put(
            "/credentials/menthorq",
            json={"values": {"MENTHORQ_PASS": "new-pass-123"}},
        )
        assert response.status_code == 200
        (call,) = valid_verdict
        assert call[1] == {"MENTHORQ_USER": "u@x.com", "MENTHORQ_PASS": "new-pass-123"}

    def test_unknown_service_404(self, client, valid_verdict):
        response = client.put(
            "/credentials/nope", json={"values": {"X_KEY": "v"}}
        )
        assert response.status_code == 404

    def test_field_not_in_service_400(self, client, valid_verdict):
        response = client.put(
            "/credentials/unusual_whales",
            json={"values": {"CEREBRAS_API_KEY": "csk-x"}},
        )
        assert response.status_code == 400

    def test_empty_or_non_string_values_400(self, client, valid_verdict):
        for bad in ({}, {"UW_TOKEN": ""}, {"UW_TOKEN": 42}):
            response = client.put(
                "/credentials/unusual_whales", json={"values": bad}
            )
            assert response.status_code == 400, bad

    def test_error_verdict_saves_anyway(self, client, monkeypatch):
        """A vendor outage must not lock the operator out of saving."""
        for module in _route_module_instances():
            monkeypatch.setattr(
                module.credential_validators,
                "validate",
                lambda service_id, values: ValidationResult("error", "vendor 503"),
            )
        response = client.put(
            "/credentials/unusual_whales",
            json={"values": {"UW_TOKEN": "uw-token-during-outage"}},
        )
        assert response.status_code == 200
        assert response.json()["validation"]["status"] == "error"
        assert _store().get_secret("UW_TOKEN") == "uw-token-during-outage"


class TestValidateDryRun:
    def test_validate_does_not_write(self, client, valid_verdict):
        response = client.post(
            "/credentials/unusual_whales/validate",
            json={"values": {"UW_TOKEN": "uw-dry-run-token"}},
        )
        assert response.status_code == 200
        assert response.json()["validation"]["status"] == "valid"
        assert _store().get_secret("UW_TOKEN") is None

    def test_validate_merges_stored(self, client, valid_verdict):
        _store().set_secret("UW_TOKEN", "uw-already-stored", actor="test")
        response = client.post(
            "/credentials/unusual_whales/validate", json={}
        )
        assert response.status_code == 200
        (call,) = valid_verdict
        assert call[1]["UW_TOKEN"] == "uw-already-stored"


class TestValidateEgressPin:
    """Full flow with the REAL validator: a /validate body carrying ONLY a
    destination URL merges the stored/env token, so the probe destination is
    an egress decision — a non-Turso URL must refuse before any wire call."""

    def test_url_only_body_with_env_token_refuses_without_egress(
        self, client, monkeypatch
    ):
        monkeypatch.setenv("TURSO_AUTH_TOKEN", "SYNTHETIC-STORED-TOKEN-NOT-REAL")
        calls = []

        def _wire(*args, **kwargs):
            calls.append((args, kwargs))
            raise AssertionError("credential egress attempted")

        seen = set()
        for module in _route_module_instances():
            cv_mod = module.credential_validators
            if id(cv_mod) in seen:
                continue
            seen.add(id(cv_mod))
            monkeypatch.setattr(cv_mod.requests, "post", _wire)
            monkeypatch.setattr(cv_mod.requests, "get", _wire)

        resp = client.post(
            "/credentials/turso/validate",
            json={"values": {"TURSO_DB_URL": "https://collector.example.net"}},
        )
        assert resp.status_code == 200
        assert resp.json()["validation"]["status"] == "invalid"
        assert calls == []


class TestValidatorThrottle:
    """Validators hold a thread for up to ~90s. Bound them at the route."""

    def test_second_validation_inside_cooldown_429s_without_vendor_call(
        self, client, valid_verdict
    ):
        first = client.post("/credentials/unusual_whales/validate", json={})
        assert first.status_code == 200
        second = client.post("/credentials/unusual_whales/validate", json={})
        assert second.status_code == 429
        assert int(second.headers["Retry-After"]) >= 1
        assert second.json()["detail"]["code"] == "VALIDATION_COOLDOWN"
        assert len(valid_verdict) == 1

    def test_put_shares_the_cooldown_and_stores_nothing(
        self, client, valid_verdict, monkeypatch
    ):
        monkeypatch.delenv("UW_TOKEN", raising=False)
        assert client.post("/credentials/unusual_whales/validate", json={}).status_code == 200
        response = client.put(
            "/credentials/unusual_whales",
            json={"values": {"UW_TOKEN": "uw-throttled-token"}},
        )
        assert response.status_code == 429
        assert len(valid_verdict) == 1
        assert _store().get_secret("UW_TOKEN") is None

    def test_cooldown_is_per_service_and_expires(self, client, valid_verdict):
        assert client.post("/credentials/unusual_whales/validate", json={}).status_code == 200
        assert client.post("/credentials/cerebras/validate", json={}).status_code == 200
        for module in _route_module_instances():
            for service_id in module._validator_last_run:
                module._validator_last_run[service_id] -= module.VALIDATOR_COOLDOWN_S
        assert client.post("/credentials/unusual_whales/validate", json={}).status_code == 200
        assert len(valid_verdict) == 3

    def test_in_flight_validators_are_bounded(self, monkeypatch):
        import asyncio
        import threading

        from scripts.api.routes import credentials as credentials_module

        release = threading.Event()
        started = []

        def _slow_validate(service_id, values):
            started.append(service_id)
            release.wait(timeout=5)
            return ValidationResult("valid", "")

        for module in _route_module_instances():
            monkeypatch.setattr(module.credential_validators, "validate", _slow_validate)

        async def _drive():
            tasks = [
                asyncio.create_task(credentials_module._run_validator(service_id, {}))
                for service_id in ("unusual_whales", "nvidia", "cerebras")
            ]
            await asyncio.sleep(0.3)
            in_flight = list(started)
            release.set()
            verdicts = await asyncio.gather(*tasks)
            return in_flight, verdicts

        in_flight, verdicts = asyncio.run(_drive())
        assert len(in_flight) == credentials_module.VALIDATOR_CONCURRENCY == 2
        assert len(started) == 3
        assert all(verdict.status == "valid" for verdict in verdicts)


class TestDelete:
    def test_delete_removes_field(self, client):
        _store().set_secret("UW_TOKEN", "uw-to-delete", actor="test")
        response = client.delete("/credentials/unusual_whales/UW_TOKEN")
        assert response.status_code == 200
        assert _store().get_secret("UW_TOKEN") is None

    def test_delete_field_not_in_service_400(self, client):
        response = client.delete("/credentials/unusual_whales/CEREBRAS_API_KEY")
        assert response.status_code == 400


# ---------------------------------------------------------------------------
# IB Gateway password rotation. Saving the ib_gateway service sends the new
# password to the broker (one mTLS POST), which writes the Gateway env file and
# recreates the Gateway once. The app store holds the new password if and only
# if the broker says it wrote it. No vendor validator runs: a login probe IS a
# Gateway login and fires an IBKR Mobile push.
# ---------------------------------------------------------------------------

IB_PASSWORD = "N3w$pa ss#9"
OLD_ENV_PASSWORD = "old-env-password-sentinel"


@pytest.fixture()
def broker(monkeypatch):
    """Async recorder standing in for services.rotate_gateway_password."""

    class Broker:
        def __init__(self) -> None:
            self.calls: list[tuple[dict, str]] = []
            self.reply: tuple[int, dict] = (
                200,
                {"ok": True, "written": True, "restarted": True, "detail": "Gateway restarted"},
            )

        async def __call__(self, values, actor):
            self.calls.append((dict(values), actor))
            return self.reply

    recorder = Broker()
    for module in _route_module_instances():
        monkeypatch.setattr(module.gateway_services, "rotate_gateway_password", recorder)
    monkeypatch.setenv("TWS_PASSWORD", OLD_ENV_PASSWORD)
    monkeypatch.setenv("TWS_USERID", "env-user")
    return recorder


@pytest.fixture()
def no_validator(monkeypatch):
    def _validate(service_id, values):
        raise AssertionError("ib_gateway must never run a live login probe")

    for module in _route_module_instances():
        monkeypatch.setattr(module.credential_validators, "validate", _validate)


def _put_ib(client, values):
    return client.put("/credentials/ib_gateway", json={"values": values})


class TestIbGatewayRotation:
    def test_ib_applied_stores_and_calls_broker_once(self, client, broker, no_validator):
        import os

        response = _put_ib(client, {"TWS_PASSWORD": IB_PASSWORD})
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["rotation"]["status"] == "applied"
        assert body["validation"]["status"] == "unchecked"
        assert broker.calls == [({"TWS_PASSWORD": IB_PASSWORD}, "trusted-local")]
        assert _store().get_secret("TWS_PASSWORD") == IB_PASSWORD
        assert os.environ["TWS_PASSWORD"] == IB_PASSWORD
        assert IB_PASSWORD not in response.text
        field = next(f for f in body["service"]["fields"] if f["name"] == "TWS_PASSWORD")
        assert field["configured"] is True

    def test_ib_userid_and_password_forwarded(self, client, broker, no_validator):
        response = _put_ib(client, {"TWS_PASSWORD": IB_PASSWORD, "TWS_USERID": "newuser9"})
        assert response.status_code == 200, response.text
        assert broker.calls[0][0] == {"TWS_PASSWORD": IB_PASSWORD, "TWS_USERID": "newuser9"}
        assert _store().get_secret("TWS_USERID") == "newuser9"

    def test_ib_deferred_stores(self, client, broker, no_validator):
        broker.reply = (202, {
            "ok": True, "written": True, "restarted": False, "code": "RESTART_DEFERRED",
            "detail": "IBKR operator hold active",
        })
        response = _put_ib(client, {"TWS_PASSWORD": IB_PASSWORD})
        assert response.status_code == 200, response.text
        assert response.json()["rotation"] == {
            "status": "deferred", "detail": "IBKR operator hold active",
        }
        assert _store().get_secret("TWS_PASSWORD") == IB_PASSWORD
        assert IB_PASSWORD not in response.text

    def test_ib_restart_failed_stores_200(self, client, broker, no_validator):
        broker.reply = (502, {
            "ok": False, "written": True, "restarted": False, "code": "RESTART_FAILED",
            "returncode": 1, "detail": "compose up failed",
        })
        response = _put_ib(client, {"TWS_PASSWORD": IB_PASSWORD})
        assert response.status_code == 200, response.text
        assert response.json()["rotation"]["status"] == "restart_failed"
        assert _store().get_secret("TWS_PASSWORD") == IB_PASSWORD

    @pytest.mark.parametrize(
        ("reply", "status", "code"),
        [
            pytest.param((409, {"ok": False, "code": "LOGIN_BLOCKED", "reason": "cooldown",
                                "detail": "cooldown: stop ran 3s ago"}),
                         409, "GATEWAY_LOGIN_BLOCKED", id="409"),
            pytest.param((423, {"ok": False, "code": "OPERATOR_HOLD", "detail": "held"}),
                         423, "GATEWAY_OPERATOR_HOLD", id="423"),
            pytest.param((-1, {"ok": False, "detail": "timed out"}),
                         504, "GATEWAY_ROTATION_UNKNOWN", id="504-transport"),
            pytest.param((502, {"ok": False, "written": None, "code": "WRITE_UNKNOWN",
                                "detail": "writer timed out"}),
                         504, "GATEWAY_ROTATION_UNKNOWN", id="504-written-null"),
            pytest.param((404, {"ok": False, "error": "not found"}),
                         503, "GATEWAY_ROTATION_UNAVAILABLE", id="503-old-daemon"),
            pytest.param((503, {"ok": False, "code": "GATEWAY_ROTATION_UNAVAILABLE",
                                "detail": "not configured"}),
                         503, "GATEWAY_ROTATION_UNAVAILABLE", id="503-local"),
            pytest.param((502, {"ok": False, "written": False, "code": "WRITE_FAILED",
                                "detail": "target untrusted"}),
                         502, "GATEWAY_WRITE_FAILED", id="502-write-failed"),
            pytest.param((400, {"ok": False, "code": "INVALID_VALUE", "detail": "password: rule"}),
                         422, "GATEWAY_CREDENTIAL_INVALID", id="400"),
            # Daemon refusals before any write (_refuse: peer / cert-CN
            # allowlist, method, body size) carry no `written`.
            pytest.param((401, {"ok": False, "error": "unauthorized"}),
                         502, "GATEWAY_WRITE_FAILED", id="401-refused"),
            pytest.param((403, {"ok": False, "error": "client certificate not allowlisted"}),
                         502, "GATEWAY_WRITE_FAILED", id="403-cert-cn"),
            pytest.param((405, {"ok": False, "error": "method not allowed"}),
                         502, "GATEWAY_WRITE_FAILED", id="405-refused"),
            pytest.param((413, {"ok": False, "error": "body exceeds 4096 bytes"}),
                         502, "GATEWAY_WRITE_FAILED", id="413-refused"),
            # Connection refused / TLS handshake: the request never left.
            pytest.param((-1, {"ok": False, "sent": False, "detail": "Connection refused"}),
                         503, "GATEWAY_BROKER_UNREACHABLE", id="503-not-sent"),
        ],
    )
    def test_ib_refusals_leave_store_and_env_untouched(
        self, client, broker, no_validator, reply, status, code
    ):
        import os

        broker.reply = reply
        response = _put_ib(client, {"TWS_PASSWORD": IB_PASSWORD})
        assert response.status_code == status, response.text
        assert response.json()["detail"]["code"] == code
        assert _store().get_secret("TWS_PASSWORD") is None
        assert os.environ["TWS_PASSWORD"] == OLD_ENV_PASSWORD
        assert len(broker.calls) == 1
        assert IB_PASSWORD not in response.text

    def test_ib_unknown_outcome_tells_the_operator_not_to_resubmit(self, client, broker, no_validator):
        broker.reply = (-1, {"ok": False, "detail": "timed out"})
        response = _put_ib(client, {"TWS_PASSWORD": IB_PASSWORD})
        assert "Do not resubmit" in response.json()["detail"]["message"]

    @pytest.mark.parametrize(
        "reply",
        [
            pytest.param((401, {"ok": False, "error": "unauthorized"}), id="401"),
            pytest.param((403, {"ok": False, "error": "source not allowlisted"}), id="403"),
            pytest.param((405, {"ok": False, "error": "method not allowed"}), id="405"),
            pytest.param((413, {"ok": False, "error": "body too large"}), id="413"),
            pytest.param((-1, {"ok": False, "sent": False, "detail": "refused"}), id="not-sent"),
        ],
    )
    def test_ib_pre_write_refusal_says_nothing_was_saved(self, client, broker, no_validator, reply):
        broker.reply = reply
        message = _put_ib(client, {"TWS_PASSWORD": IB_PASSWORD}).json()["detail"]["message"]
        assert "Nothing was saved" in message
        assert "Do not resubmit" not in message

    @pytest.mark.parametrize(
        "reply",
        [
            pytest.param((-1, {"ok": False, "detail": "timed out"}), id="sent-unknown"),
            pytest.param((-1, {"ok": False, "sent": None, "detail": "reset"}), id="sent-none"),
            pytest.param((500, {"ok": False, "error": "boom"}), id="500-no-written"),
            pytest.param((200, {"ok": True}), id="200-no-written"),
        ],
    )
    def test_ib_after_send_without_written_stays_unknown(self, client, broker, no_validator, reply):
        broker.reply = reply
        response = _put_ib(client, {"TWS_PASSWORD": IB_PASSWORD})
        assert response.status_code == 504, response.text
        assert response.json()["detail"]["code"] == "GATEWAY_ROTATION_UNKNOWN"

    @pytest.mark.parametrize(
        "value",
        ["it's", "abc\\", " lead", "trail ", "a\nb", "a\x00b", "a\x01b", "x" * 257, "",
         "a\u2028X=1", "a\u2029X=1", "a\x85X=1"],
        ids=["quote", "backslash", "lead", "trail", "newline", "nul", "control", "too-long", "empty",
             "u2028", "u2029", "nel-u0085"],
    )
    def test_ib_invalid_vectors_422_broker_not_called(self, client, broker, no_validator, value):
        response = _put_ib(client, {"TWS_PASSWORD": value})
        assert response.status_code == 422, response.text
        assert response.json()["detail"]["code"] == "GATEWAY_CREDENTIAL_INVALID"
        assert broker.calls == []
        assert _store().get_secret("TWS_PASSWORD") is None
        if value.strip():
            assert value.strip() not in response.text

    def test_ib_userid_only_422(self, client, broker, no_validator):
        response = _put_ib(client, {"TWS_USERID": "newuser9"})
        assert response.status_code == 422
        assert broker.calls == []

    def test_ib_unknown_field_400(self, client, broker, no_validator):
        response = _put_ib(client, {"TWS_PASSWORD": IB_PASSWORD, "UW_TOKEN": "x"})
        assert response.status_code in {400, 422}
        assert broker.calls == []

    def test_ib_broker_detail_echoing_password_is_redacted(self, client, broker, no_validator):
        broker.reply = (502, {
            "ok": False, "written": True, "restarted": False, "code": "RESTART_FAILED",
            "detail": f"helper said {IB_PASSWORD}",
        })
        response = _put_ib(client, {"TWS_PASSWORD": IB_PASSWORD})
        assert response.status_code == 200
        assert IB_PASSWORD not in response.text
        assert "[redacted]" in response.json()["rotation"]["detail"]

        broker.reply = (409, {"ok": False, "code": "LOGIN_BLOCKED", "detail": f"x {IB_PASSWORD}"})
        response = _put_ib(client, {"TWS_PASSWORD": IB_PASSWORD})
        assert IB_PASSWORD not in response.text

    def test_ib_store_unopenable_503_broker_not_called(self, client, broker, no_validator, monkeypatch):
        from secret_store import SecretStoreError

        def _broken():
            raise SecretStoreError("secret_store.key is missing")

        for module in _route_module_instances():
            monkeypatch.setattr(module, "_store", _broken)
        response = _put_ib(client, {"TWS_PASSWORD": IB_PASSWORD})
        assert response.status_code == 503
        assert broker.calls == []

    def test_ib_store_write_failure_after_broker_write_says_so(
        self, client, broker, no_validator, monkeypatch
    ):
        from secret_store import SecretStoreError

        def _fail(self, values, actor=None):
            raise SecretStoreError("disk full")

        monkeypatch.setattr(SecretStore, "set_secrets", _fail)
        response = _put_ib(client, {"TWS_PASSWORD": IB_PASSWORD})
        assert response.status_code == 503
        detail = response.json()["detail"]
        assert detail["code"] == "CREDENTIAL_STORE_UNAVAILABLE"
        assert "Broker has the new password" in detail["message"]
        assert IB_PASSWORD not in response.text

    def test_ib_validator_never_invoked(self, client, broker, no_validator):
        # no_validator raises if called; a 200 proves it never ran, and a
        # second save right away is not throttled by the 5s validator cooldown.
        assert _put_ib(client, {"TWS_PASSWORD": IB_PASSWORD}).status_code == 200

    def test_ib_lock_held_409(self, client, broker, no_validator):
        modules = _route_module_instances()
        locks = [module._IB_ROTATE_LOCK for module in modules]
        for lock in locks:
            assert lock.acquire(blocking=False)
        try:
            response = _put_ib(client, {"TWS_PASSWORD": IB_PASSWORD})
        finally:
            for lock in locks:
                lock.release()
        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "GATEWAY_ROTATION_IN_PROGRESS"
        assert broker.calls == []

    def test_other_services_still_validate_then_store(self, client, valid_verdict, broker):
        response = client.put(
            "/credentials/unusual_whales", json={"values": {"UW_TOKEN": "uw-new-value"}}
        )
        assert response.status_code == 200
        assert valid_verdict and broker.calls == []
        assert "rotation" not in response.json()


class TestIbGatewayRotationOperatorJwtOnAppRole:
    """On the app host a trusted-local caller is not enough to rotate the
    Gateway password: like every other Gateway mutation there, it needs an
    operator JWT, and a refusal must reach the broker zero times."""

    @pytest.fixture(autouse=True)
    def app_role(self, monkeypatch):
        monkeypatch.setenv("RADON_HOST_ROLE", "app")
        monkeypatch.setenv("CLERK_JWKS_URL", "https://jwks.invalid/.well-known/jwks.json")

    def test_matcher_covers_the_gateway_credential_put_only(self):
        from types import SimpleNamespace

        from scripts.api import server

        def req(method, path):
            return SimpleNamespace(method=method, url=SimpleNamespace(path=path))

        assert server._is_app_role_gateway_mutation(req("PUT", "/credentials/ib_gateway")) is True
        assert server._is_app_role_gateway_mutation(req("PUT", "/credentials/ib_gateway/")) is True
        assert server._is_app_role_gateway_mutation(req("GET", "/credentials/ib_gateway")) is False
        assert server._is_app_role_gateway_mutation(req("PUT", "/credentials/exa")) is False
        assert server._is_app_role_gateway_mutation(req("POST", "/credentials/ib_gateway/validate")) is False

    def test_matcher_is_inert_off_the_app_role(self, monkeypatch):
        from types import SimpleNamespace

        from scripts.api import server

        monkeypatch.delenv("RADON_HOST_ROLE", raising=False)
        request = SimpleNamespace(method="PUT", url=SimpleNamespace(path="/credentials/ib_gateway"))
        assert server._is_app_role_gateway_mutation(request) is False

    def test_trusted_local_put_without_bearer_is_refused_before_the_broker(
        self, client, broker, no_validator
    ):
        response = _put_ib(client, {"TWS_PASSWORD": IB_PASSWORD})
        assert response.status_code == 401, response.text
        assert broker.calls == []
        assert _store().get_secret("TWS_PASSWORD") is None

    def test_operator_bearer_rotates_once(self, client, broker, no_validator, monkeypatch):
        from scripts.api import server

        seen = []

        async def _bearer(request):
            seen.append(request.headers.get("Authorization"))
            return {"sub": "user_operator"}

        monkeypatch.setattr(server, "verify_clerk_bearer", _bearer)
        response = client.put(
            "/credentials/ib_gateway",
            json={"values": {"TWS_PASSWORD": IB_PASSWORD}},
            headers={"Authorization": "Bearer synthetic-operator-token"},
        )
        assert response.status_code == 200, response.text
        assert seen == ["Bearer synthetic-operator-token"]
        assert len(broker.calls) == 1
        assert broker.calls[0][0] == {"TWS_PASSWORD": IB_PASSWORD}
