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
            lambda service, state, error=None: rows.append((service, state, error)),
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
            lambda service, state, error=None: rows.append((service, state, error)),
        )
        monkeypatch.setattr(
            wd, "_REMOTE_CERT_ALERT",
            {"message": "ib-remote mTLS cert expires in 20 days", "critical": False},
        )
        wd._write_service_health("ok", None)
        service, state, error = rows[-1]
        assert state == "ok"
        assert "cert" in str(error).lower()
