"""/health exposes the deployed git revision so backend drift is observable.

The demo VM is shipped by cloud/scripts/deploy-demo-vm.sh, which writes the
deployed commit SHA into ``<app>/REVISION``. /health surfaces it to every
caller, including the public, unauthenticated one, so the off-box drift
check (scripts/ci/check_demo_vm_drift.py) can compare it against main. A SHA
is not sensitive; nothing else is added to the public payload. Hosts deployed
without a REVISION file (prod) keep the exact liveness-only shape.
"""

import os
import sys
from types import SimpleNamespace

import pytest

_scripts_dir = os.path.join(os.path.dirname(__file__), os.pardir, os.pardir)
sys.path.insert(0, os.path.abspath(_scripts_dir))

from scripts.api import server

SHA = "0123456789abcdef0123456789abcdef01234567"


def _request(host, extra_headers=None):
    return SimpleNamespace(
        client=SimpleNamespace(host=host),
        headers=extra_headers or {},
        url=SimpleNamespace(path="/health"),
    )


class TestReadRevision:
    def test_reads_a_40_hex_sha(self, tmp_path):
        path = tmp_path / "REVISION"
        path.write_text(SHA + "\n")
        assert server.read_deploy_revision(path) == SHA

    def test_missing_file_is_none(self, tmp_path):
        assert server.read_deploy_revision(tmp_path / "REVISION") is None

    @pytest.mark.parametrize(
        "content",
        ["", "main\n", SHA[:12], SHA.upper(), SHA + "00", "<script>" + SHA[:32]],
    )
    def test_anything_but_a_full_lowercase_sha_is_none(self, tmp_path, content):
        # The value reaches an unauthenticated response: never echo arbitrary
        # file content, only a validated SHA.
        path = tmp_path / "REVISION"
        path.write_text(content)
        assert server.read_deploy_revision(path) is None

    def test_default_path_is_the_app_root(self):
        # In the demo image server.py lives at /app/scripts/api/server.py and
        # the deploy writes /app/REVISION.
        expected = os.path.abspath(
            os.path.join(os.path.dirname(server.__file__), os.pardir, os.pardir, "REVISION")
        )
        assert os.path.abspath(str(server.DEPLOY_REVISION_PATH)) == expected


class TestHealthRevision:
    @pytest.mark.asyncio
    async def test_public_caller_gets_revision_and_nothing_else(self, monkeypatch):
        async def _should_not_run(*args, **kwargs):
            raise AssertionError("check_ib_gateway must not run for public callers")

        monkeypatch.setattr(server, "check_ib_gateway", _should_not_run)
        monkeypatch.setattr(server, "DEPLOY_REVISION", SHA)

        result = await server.health(_request("8.8.8.8"))

        assert result == {"status": "ok", "revision": SHA}

    @pytest.mark.asyncio
    async def test_proxied_caller_gets_revision(self, monkeypatch):
        monkeypatch.setattr(server, "DEPLOY_REVISION", SHA)
        result = await server.health(_request("127.0.0.1", {"X-Forwarded-For": "8.8.8.8"}))
        assert result == {"status": "ok", "revision": SHA}

    @pytest.mark.asyncio
    async def test_no_revision_file_keeps_liveness_only_shape(self, monkeypatch):
        monkeypatch.setattr(server, "DEPLOY_REVISION", None)
        result = await server.health(_request("8.8.8.8"))
        assert result == {"status": "ok"}

    @pytest.mark.asyncio
    async def test_trusted_caller_gets_revision_in_full_payload(self, monkeypatch):
        async def _gw(*args, **kwargs):
            return {"auth_state": "authenticated"}

        monkeypatch.setattr(server, "check_ib_gateway", _gw)
        monkeypatch.setattr(server, "ib_pool", SimpleNamespace(status=lambda: {}))
        monkeypatch.setattr(server, "DEPLOY_REVISION", SHA)

        result = await server.health(_request("127.0.0.1"))

        assert result["revision"] == SHA
        assert result["ib_gateway"]["auth_state"] == "authenticated"

    @pytest.mark.asyncio
    async def test_trusted_caller_without_revision_has_no_key(self, monkeypatch):
        async def _gw(*args, **kwargs):
            return {"auth_state": "authenticated"}

        monkeypatch.setattr(server, "check_ib_gateway", _gw)
        monkeypatch.setattr(server, "ib_pool", SimpleNamespace(status=lambda: {}))
        monkeypatch.setattr(server, "DEPLOY_REVISION", None)

        result = await server.health(_request("127.0.0.1"))

        assert "revision" not in result
