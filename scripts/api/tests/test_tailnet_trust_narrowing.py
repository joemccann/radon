"""Tailnet trust narrowing (Ops Plane step 2B).

`is_local_or_tailnet` trusted the whole Tailscale CGNAT block, so any node
admitted to the tailnet skipped Clerk on every FastAPI route. The trust is
now an explicit /32 list, `RADON_TRUSTED_TAILNET_PEERS`, rolled out in two
steps:

- `RADON_TAILNET_TRUST_MODE` unset / `log` (default): behaviour unchanged,
  every tailnet peer outside the list is logged as "would refuse".
- `enforce`: only listed peers are trusted. An empty or unparseable list
  fails closed to loopback-only. Any unknown mode value is treated as
  enforce.

The Hetzner private-net /health scope (`is_private_net_probe`) is untouched.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest

from api import auth


LAPTOP = "100.98.36.17"
MINI = "100.87.184.89"
STRANGER = "100.81.139.56"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("RADON_TRUSTED_TAILNET_PEERS", raising=False)
    monkeypatch.delenv("RADON_TAILNET_TRUST_MODE", raising=False)
    auth._tailnet_refusal_logged.clear()
    yield
    auth._tailnet_refusal_logged.clear()


def _req(host, headers=None):
    return SimpleNamespace(client=SimpleNamespace(host=host), headers=headers or {})


def _refusal_lines(caplog):
    return [r.getMessage() for r in caplog.records if "tailnet peer" in r.getMessage()]


class TestLogOnlyDefault:
    def test_unlisted_tailnet_peer_still_trusted(self):
        assert auth.is_local_or_tailnet(STRANGER) is True
        assert auth.is_trusted_local_request(_req(STRANGER)) is True

    def test_unlisted_peer_is_logged_as_would_refuse(self, monkeypatch, caplog):
        monkeypatch.setenv("RADON_TRUSTED_TAILNET_PEERS", LAPTOP)
        with caplog.at_level(logging.WARNING, logger="radon.auth"):
            assert auth.is_local_or_tailnet(STRANGER) is True
        lines = _refusal_lines(caplog)
        assert len(lines) == 1
        assert STRANGER in lines[0]
        assert "would refuse" in lines[0]

    def test_listed_peer_is_not_logged(self, monkeypatch, caplog):
        monkeypatch.setenv("RADON_TRUSTED_TAILNET_PEERS", f"{LAPTOP}/32, {MINI}")
        with caplog.at_level(logging.WARNING, logger="radon.auth"):
            assert auth.is_local_or_tailnet(LAPTOP) is True
            assert auth.is_local_or_tailnet(MINI) is True
        assert _refusal_lines(caplog) == []

    def test_log_is_rate_limited_per_peer(self, caplog):
        with caplog.at_level(logging.WARNING, logger="radon.auth"):
            for _ in range(5):
                auth.is_local_or_tailnet(STRANGER)
            auth.is_local_or_tailnet(LAPTOP)
        assert len(_refusal_lines(caplog)) == 2

    def test_explicit_log_mode_matches_default(self, monkeypatch):
        monkeypatch.setenv("RADON_TAILNET_TRUST_MODE", "log")
        assert auth.is_local_or_tailnet(STRANGER) is True

    def test_non_tailnet_hosts_unchanged(self):
        assert auth.is_local_or_tailnet("127.0.0.1") is True
        assert auth.is_local_or_tailnet("::1") is True
        assert auth.is_local_or_tailnet("10.0.0.4") is False
        assert auth.is_local_or_tailnet("8.8.8.8") is False
        assert auth.is_local_or_tailnet(None) is False


class TestEnforce:
    def test_listed_peer_trusted_unlisted_refused(self, monkeypatch):
        monkeypatch.setenv("RADON_TAILNET_TRUST_MODE", "enforce")
        monkeypatch.setenv("RADON_TRUSTED_TAILNET_PEERS", f"{LAPTOP}/32 {MINI}/32")
        assert auth.is_local_or_tailnet(LAPTOP) is True
        assert auth.is_local_or_tailnet(MINI) is True
        assert auth.is_local_or_tailnet(STRANGER) is False
        assert auth.is_trusted_local_request(_req(STRANGER)) is False
        assert auth.is_trusted_local_request(_req(LAPTOP)) is True

    def test_refusal_is_logged(self, monkeypatch, caplog):
        monkeypatch.setenv("RADON_TAILNET_TRUST_MODE", "enforce")
        monkeypatch.setenv("RADON_TRUSTED_TAILNET_PEERS", LAPTOP)
        with caplog.at_level(logging.WARNING, logger="radon.auth"):
            auth.is_local_or_tailnet(STRANGER)
        lines = _refusal_lines(caplog)
        assert len(lines) == 1 and "refused" in lines[0]

    def test_loopback_always_trusted(self, monkeypatch):
        monkeypatch.setenv("RADON_TAILNET_TRUST_MODE", "enforce")
        assert auth.is_local_or_tailnet("127.0.0.1") is True
        assert auth.is_local_or_tailnet("::1") is True

    def test_forwarded_listed_peer_still_not_trusted(self, monkeypatch):
        monkeypatch.setenv("RADON_TAILNET_TRUST_MODE", "enforce")
        monkeypatch.setenv("RADON_TRUSTED_TAILNET_PEERS", LAPTOP)
        req = _req(LAPTOP, {"X-Forwarded-For": "8.8.8.8"})
        assert auth.is_trusted_local_request(req) is False

    def test_unknown_mode_fails_closed(self, monkeypatch):
        monkeypatch.setenv("RADON_TAILNET_TRUST_MODE", "enforced")
        monkeypatch.setenv("RADON_TRUSTED_TAILNET_PEERS", LAPTOP)
        assert auth.is_local_or_tailnet(STRANGER) is False
        assert auth.is_local_or_tailnet(LAPTOP) is True

    def test_mode_is_case_insensitive(self, monkeypatch):
        monkeypatch.setenv("RADON_TAILNET_TRUST_MODE", " LOG ")
        assert auth.is_local_or_tailnet(STRANGER) is True


class TestEmptyAndInvalidList:
    def test_enforce_with_empty_list_is_loopback_only(self, monkeypatch):
        monkeypatch.setenv("RADON_TAILNET_TRUST_MODE", "enforce")
        monkeypatch.setenv("RADON_TRUSTED_TAILNET_PEERS", "")
        assert auth.is_local_or_tailnet(LAPTOP) is False
        assert auth.is_local_or_tailnet("127.0.0.1") is True

    def test_enforce_with_unset_list_is_loopback_only(self, monkeypatch):
        monkeypatch.setenv("RADON_TAILNET_TRUST_MODE", "enforce")
        assert auth.is_local_or_tailnet(LAPTOP) is False

    @pytest.mark.parametrize(
        "entry",
        [
            "100.64.0.0/10",  # a subnet, not a /32
            "100.98.36.0/24",
            "10.0.0.4",  # not a tailnet address
            "8.8.8.8/32",
            "not-an-ip",
            "fd7a:115c:a1e0::1",  # v6 tailnet: not in scope for the /32 list
        ],
    )
    def test_invalid_entries_are_ignored(self, monkeypatch, entry):
        monkeypatch.setenv("RADON_TAILNET_TRUST_MODE", "enforce")
        monkeypatch.setenv("RADON_TRUSTED_TAILNET_PEERS", entry)
        assert auth.trusted_tailnet_peers() == frozenset()
        assert auth.is_local_or_tailnet(LAPTOP) is False

    def test_invalid_entry_does_not_poison_valid_ones(self, monkeypatch):
        monkeypatch.setenv("RADON_TAILNET_TRUST_MODE", "enforce")
        monkeypatch.setenv("RADON_TRUSTED_TAILNET_PEERS", f"100.64.0.0/10,{LAPTOP}")
        assert auth.is_local_or_tailnet(LAPTOP) is True
        assert auth.is_local_or_tailnet("100.64.0.1") is False


class TestPrivateNetScopeUnchanged:
    def test_private_net_probe_unaffected_by_enforce(self, monkeypatch):
        monkeypatch.setenv("RADON_TAILNET_TRUST_MODE", "enforce")
        assert auth.is_private_net_probe(_req("10.0.0.4")) is True
        assert auth.is_trusted_local_request(_req("10.0.0.4")) is False
