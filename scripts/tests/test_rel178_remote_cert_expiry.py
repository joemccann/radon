"""REL-178 (R-496): mTLS cert expiry is visible before it bites.

All three remote-control certs expire together at 825 days with no renewal
path; on that day every admin Gateway control 503s and nothing anywhere
carries a days-left field.
"""
from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


@pytest.fixture(scope="module")
def short_cert(tmp_path_factory) -> Path:
    """A self-signed cert that expired one hour ago."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "expired-test")])
    now = dt.datetime.now(dt.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(days=1))
        .not_valid_after(now - dt.timedelta(hours=1))
        .sign(key, hashes.SHA256())
    )
    path = tmp_path_factory.mktemp("certs") / "expired.pem"
    path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    return path


class TestCertExpiryHelper:
    def test_an_expired_cert_reports_nonpositive_days(self, short_cert):
        from utils.cert_expiry import cert_days_left

        days = cert_days_left(short_cert)
        assert days is not None and days <= 0

    def test_a_missing_file_is_none_not_a_crash(self, tmp_path):
        from utils.cert_expiry import cert_days_left

        assert cert_days_left(tmp_path / "absent.pem") is None


class TestBrokerHealthzCarriesTheField:
    def test_healthz_payload_exposes_days_left(self, short_cert, monkeypatch):
        from ib_gateway_remote import serve

        monkeypatch.setenv("RADON_IB_REMOTE_CERT", str(short_cert))
        payload = serve.healthz_payload()
        assert payload["ok"] is True
        assert payload["ib_remote_cert_days_left"] <= 0
        assert payload["ib_remote_cert_not_after"]


class TestAppHealthCarriesTheField:
    def test_remote_cert_summary(self, short_cert, monkeypatch):
        from api.ib_gateway import _remote_cert_summary

        monkeypatch.setenv("RADON_IB_REMOTE_CLIENT_CERT", str(short_cert))
        summary = _remote_cert_summary()
        assert summary is not None
        assert summary["cert_days_left"] <= 0


class TestWatchdogClassifies:
    def test_classification_bands(self):
        import ib_watchdog as wd

        assert wd.classify_remote_cert(None) is None
        assert wd.classify_remote_cert(200) is None
        assert wd.classify_remote_cert(29) == "warning"
        assert wd.classify_remote_cert(6) == "critical"
        assert wd.classify_remote_cert(0) == "critical"

    def test_a_critical_cert_escalates_the_health_row(self, monkeypatch):
        import ib_watchdog as wd

        rows: list[tuple] = []
        monkeypatch.setattr(
            wd, "_write_service_health_transport",
            lambda state, error=None: rows.append(("ib-watchdog", state, error)),
        )
        monkeypatch.setattr(
            wd, "_REMOTE_CERT_ALERT",
            {"message": "ib-remote mTLS cert expires in 3 days", "critical": True},
        )
        wd._write_service_health("ok", None)
        service, state, error = rows[-1]
        assert state == "error"
        assert "cert" in str(error).lower()

    def test_a_warning_cert_annotates_without_paging(self, monkeypatch):
        import ib_watchdog as wd

        rows: list[tuple] = []
        monkeypatch.setattr(
            wd, "_write_service_health_transport",
            lambda state, error=None: rows.append(("ib-watchdog", state, error)),
        )
        monkeypatch.setattr(
            wd, "_REMOTE_CERT_ALERT",
            {"message": "ib-remote mTLS cert expires in 20 days", "critical": False},
        )
        wd._write_service_health("ok", None)
        service, state, error = rows[-1]
        assert state == "ok"
        assert "cert" in str(error).lower()


class TestWatchdogCycleWiresTheCertIntoTheHealthRow:
    """T-425: the classifier and the alert were both pinned, the CALL was not.

    ``_run_cycle_steps`` is the only thing that moves a /health payload's
    ``gateway.remote.cert_days_left`` into ``_REMOTE_CERT_ALERT``. Every
    other case here either tests ``classify_remote_cert`` pure or patches
    ``_REMOTE_CERT_ALERT`` directly, so deleting the ``note_remote_cert``
    call left the file green and the cert would expire unwarned. Run one
    real cycle instead and read the row that comes out the far end.
    """

    @staticmethod
    def _healthy_payload(days_left):
        return {
            "ib_gateway": {
                "service_state": "active",
                "port_listening": True,
                "upstream_dead": False,
                "auth_state": "authenticated",
                "remote": {"cert_days_left": days_left},
            }
        }

    def _cycle_rows(self, tmp_path, monkeypatch, days_left):
        import ib_watchdog as wd

        monkeypatch.setattr(wd, "_REMOTE_CERT_ALERT", {})
        rows: list[tuple] = []
        monkeypatch.setattr(
            wd, "_write_service_health_transport",
            lambda state, error=None: rows.append((state, error)),
        )
        monkeypatch.setattr(
            wd, "fetch_health",
            lambda url, timeout: wd.GatewayState.from_health_payload(
                self._healthy_payload(days_left)
            ),
        )
        # No sockets: the direct probe is stubbed alive so the cycle takes the
        # plain healthy path and the row under test is the cert escalation only.
        monkeypatch.setattr(wd, "probe_gateway_direct", lambda *a, **k: wd.GATEWAY_ALIVE)
        monkeypatch.setattr(wd, "trigger_restart", lambda *a, **k: True)
        wd.run_cycle(
            state_path=tmp_path / "watchdog-state.json",
            dry_run=True,
            utcnow=lambda: dt.datetime(2026, 9, 4, 16, 0, tzinfo=dt.timezone.utc),
        )
        assert rows, "one cycle wrote no service-health row at all"
        return rows[-1]

    def test_a_three_day_cert_on_a_healthy_gateway_errors_the_row(
        self, tmp_path, monkeypatch
    ):
        state, error = self._cycle_rows(tmp_path, monkeypatch, 3)
        assert state == "error", (
            "a /health payload carrying gateway.remote.cert_days_left=3 must "
            "reach _REMOTE_CERT_ALERT through the cycle. If this is 'ok', the "
            "note_remote_cert(...) call in _run_cycle_steps is gone and the "
            "mTLS cert will expire with no page (REL-178)."
        )
        assert "cert" in str(error).lower()

    def test_a_far_off_cert_leaves_the_row_alone(self, tmp_path, monkeypatch):
        state, error = self._cycle_rows(tmp_path, monkeypatch, 400)
        assert state == "ok"
        assert "cert" not in str(error or "").lower()
