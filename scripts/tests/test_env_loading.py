"""Verify IB_GATEWAY_HOST/PORT are loaded from env before defaults are snapshotted."""

import importlib.util
import os
import sys
from pathlib import Path
from unittest.mock import patch

MODULE_PATH = Path(__file__).resolve().parent.parent / "clients" / "ib_client.py"


def _load_ib_client_module(module_name: str):
    spec = importlib.util.spec_from_file_location(module_name, MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    sys.modules.pop(module_name, None)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def test_ib_client_default_host_reads_env():
    """After reloading ib_client with IB_GATEWAY_HOST set, DEFAULT_HOST must reflect it."""
    with patch.dict(os.environ, {"IB_GATEWAY_HOST": "test-cloud-host", "IB_GATEWAY_PORT": "9999"}):
        import dotenv

        with patch.object(dotenv, "load_dotenv", return_value=False):
            ib_client = _load_ib_client_module("tests._isolated_ib_client_env_set")
            assert ib_client.DEFAULT_HOST == "test-cloud-host"
            assert ib_client.DEFAULT_GATEWAY_PORT == 9999


def test_ib_client_default_host_fallback():
    """Without IB_GATEWAY_HOST in env, DEFAULT_HOST falls back to 127.0.0.1."""
    with patch.dict(os.environ, {}, clear=True):
        # Patch at dotenv module level so reload can't re-import the real one
        import dotenv

        with patch.object(dotenv, "load_dotenv", return_value=False):
            ib_client = _load_ib_client_module("tests._isolated_ib_client_env_fallback")
            assert ib_client.DEFAULT_HOST == "127.0.0.1"
            assert ib_client.DEFAULT_GATEWAY_PORT == 4001


def test_ib_client_loads_env_before_defaults_are_snapshotted():
    """ib_client must observe env populated by load_dotenv during import."""
    with patch.dict(os.environ, {}, clear=True):
        import dotenv

        def fake_load_dotenv(*args, **kwargs):
            os.environ["IB_GATEWAY_HOST"] = "dotenv-host"
            os.environ["IB_GATEWAY_PORT"] = "7777"
            return True

        with patch.object(dotenv, "load_dotenv", side_effect=fake_load_dotenv):
            ib_client = _load_ib_client_module("tests._isolated_ib_client_env_dotenv")
            assert ib_client.DEFAULT_HOST == "dotenv-host"
            assert ib_client.DEFAULT_GATEWAY_PORT == 7777


def test_ib_client_loads_only_dotenv():
    """ib_client reads the project .env once; no .env.ib-mode overlay exists."""
    with patch.dict(os.environ, {}, clear=True):
        import dotenv

        calls = []

        def fake_load_dotenv(path, *args, override=False, **kwargs):
            calls.append((str(path), override))
            os.environ["IB_GATEWAY_HOST"] = "dotenv-only-host"
            os.environ["IB_GATEWAY_PORT"] = "4321"
            return True

        with patch.object(dotenv, "load_dotenv", side_effect=fake_load_dotenv):
            ib_client = _load_ib_client_module("tests._isolated_ib_client_only_dotenv")
            assert len(calls) == 1, calls
            path, override = calls[0]
            assert path.endswith(".env") and not path.endswith(".env.ib-mode")
            assert not override
            assert ib_client.DEFAULT_HOST == "dotenv-only-host"
            assert ib_client.DEFAULT_GATEWAY_PORT == 4321
