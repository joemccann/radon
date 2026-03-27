"""Verify IB_GATEWAY_HOST/PORT are loaded from env before defaults are snapshotted."""

import importlib
import os
from unittest.mock import patch


def test_ib_client_default_host_reads_env():
    """After reloading ib_client with IB_GATEWAY_HOST set, DEFAULT_HOST must reflect it."""
    with patch.dict(os.environ, {"IB_GATEWAY_HOST": "test-cloud-host", "IB_GATEWAY_PORT": "9999"}):
        from scripts.clients import ib_client

        importlib.reload(ib_client)
        assert ib_client.DEFAULT_HOST == "test-cloud-host"
        assert ib_client.DEFAULT_GATEWAY_PORT == 9999


def test_ib_client_default_host_fallback():
    """Without IB_GATEWAY_HOST in env, DEFAULT_HOST falls back to 127.0.0.1."""
    env = os.environ.copy()
    env.pop("IB_GATEWAY_HOST", None)
    env.pop("IB_GATEWAY_PORT", None)
    with patch.dict(os.environ, env, clear=True):
        # Patch at dotenv module level so reload can't re-import the real one
        import dotenv

        with patch.object(dotenv, "load_dotenv", return_value=False):
            from scripts.clients import ib_client

            importlib.reload(ib_client)
            assert ib_client.DEFAULT_HOST == "127.0.0.1"
            assert ib_client.DEFAULT_GATEWAY_PORT == 4001


def test_ib_client_loads_from_dotenv_file():
    """ib_client.DEFAULT_HOST picks up IB_GATEWAY_HOST from .env file (integration)."""
    from scripts.clients import ib_client

    importlib.reload(ib_client)
    # .env has IB_GATEWAY_HOST=ib-gateway — verify load_dotenv worked
    assert ib_client.DEFAULT_HOST == "ib-gateway"
    assert ib_client.DEFAULT_GATEWAY_PORT == 4001
