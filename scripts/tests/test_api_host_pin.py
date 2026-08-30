"""F6 round 2 — the Host pin must stay a pin without amputating real callers.

The rebinding guard added in round 1 wired Starlette's ``TrustedHostMiddleware``
straight onto a static name list. That middleware matches whole hosts (or a
single leading ``*.``) and does so case-sensitively after a naive
``host.split(":")[0]``, which broke three things that production actually does:

  * ``cloud/services/radon-api.service`` documents the tailnet IP-literal path
    (``--host 0.0.0.0`` so the cloud-thin laptop reaches FastAPI at
    ``100.112.32.16:8321`` over Tailscale). A CIDR cannot be expressed as a
    name pattern, so every tailnet call started returning 400.
  * ``Host: LOCALHOST:8321`` returned 400 — host names are case-insensitive.
  * The ``"::1"`` entry was dead: an IPv6 literal arrives as ``[::1]:8321``.

And one hole stayed open: ``RADON_ALLOWED_HOSTS="*"`` is accepted by Starlette
and disables the pin entirely.

Allowing the CGNAT range does NOT weaken the rebinding defense: a rebind needs a
NAME the attacker controls in DNS, and an IP literal resolves only to itself.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


def _client() -> TestClient:
    from scripts.api.server import app

    return TestClient(app, client=("127.0.0.1", 51234))


class TestLegitimateHostFormsAreServed:
    @pytest.mark.parametrize(
        "host",
        [
            "100.112.32.16:8321",  # the documented Tailscale path for cloud-thin
            "100.64.0.1",  # first address of the CGNAT range
            "100.127.255.254:8321",  # last address of the CGNAT range
            "LOCALHOST:8321",  # host names are case-insensitive
            "App.Radon.Run",
            "[::1]:8321",  # how an IPv6 literal actually arrives
            "localhost:8321",
            "127.0.0.1:8321",
            "10.0.0.2:8321",  # Hetzner private-net broker -> app /health
            "10.0.0.4",
            "app.radon.run",
            "ib-gateway:8321",
        ],
    )
    def test_host_is_accepted(self, host):
        res = _client().get("/health", headers={"Host": host})
        assert res.status_code == 200, f"legitimate Host {host} was rejected"


class TestRebindingIsStillRefused:
    @pytest.mark.parametrize(
        "host",
        [
            "rebind.attacker.example",
            "REBIND.ATTACKER.EXAMPLE",  # case must not smuggle a name through
            "100.128.0.1",  # one address past the CGNAT range
            "100.63.255.255",  # one address before it
            "203.0.113.9:8321",  # a public literal is not a tailnet peer
            "10.1.0.1:8321",  # adjacent RFC1918, not radon-private
            "172.17.0.1:8321",  # docker0
            "[2001:db8::1]:8321",  # a routable IPv6 literal is not loopback
        ],
    )
    def test_host_is_rejected(self, host):
        res = _client().get("/health", headers={"Host": host})
        assert res.status_code == 400, (
            f"Host {host} was served — an attacker-controlled hostname "
            "re-resolved to 127.0.0.1 is same-origin with this API."
        )

    def test_retired_beta_hostname_is_rejected(self):
        res = _client().get("/health", headers={"Host": "beta.radon.run"})
        assert res.status_code == 400, "beta.radon.run was never a production caller"


class TestWildcardCannotDisableThePin:
    def test_env_wildcard_is_dropped(self):
        from scripts.api.server import parse_allowed_hosts_env

        assert parse_allowed_hosts_env("*") == []
        assert parse_allowed_hosts_env(" * , radon.internal ") == ["radon.internal"]

    def test_configured_list_never_contains_a_wildcard(self, monkeypatch):
        """The static list plus any env extension, as actually installed."""
        from scripts.api import server

        assert "*" not in server._ALLOWED_HOSTS
