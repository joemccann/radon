"""Tests for historical endpoint API key auth scoping."""

import os
import pytest
from unittest.mock import patch

from api.auth import verify_api_key, API_KEY_ALLOWED_PATHS


class FakeRequest:
    def __init__(self, path, headers=None):
        self.url = type("URL", (), {"path": path})()
        self.headers = headers or {}


class TestVerifyApiKey:
    def test_valid_key_on_allowed_path(self):
        with patch.dict(os.environ, {"MDW_API_KEY": "test-secret-key"}):
            req = FakeRequest("/historical/bars", {"X-API-Key": "test-secret-key"})
            result = verify_api_key(req)
            assert result is not None
            assert result["sub"] == "mdw-service"
            assert result["service"] is True

    def test_valid_key_on_disallowed_path(self):
        with patch.dict(os.environ, {"MDW_API_KEY": "test-secret-key"}):
            req = FakeRequest("/orders/place", {"X-API-Key": "test-secret-key"})
            result = verify_api_key(req)
            assert result is None

    def test_wrong_key(self):
        with patch.dict(os.environ, {"MDW_API_KEY": "test-secret-key"}):
            req = FakeRequest("/historical/bars", {"X-API-Key": "wrong-key"})
            result = verify_api_key(req)
            assert result is None

    def test_missing_key_header(self):
        with patch.dict(os.environ, {"MDW_API_KEY": "test-secret-key"}):
            req = FakeRequest("/historical/bars", {})
            result = verify_api_key(req)
            assert result is None

    def test_missing_env_var(self):
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("MDW_API_KEY", None)
            req = FakeRequest("/historical/bars", {"X-API-Key": "some-key"})
            result = verify_api_key(req)
            assert result is None

    def test_all_allowed_paths(self):
        with patch.dict(os.environ, {"MDW_API_KEY": "key"}):
            for path in API_KEY_ALLOWED_PATHS:
                req = FakeRequest(path, {"X-API-Key": "key"})
                assert verify_api_key(req) is not None, f"Path {path} should be allowed"

    def test_trading_paths_rejected(self):
        trading_paths = ["/orders/place", "/orders/cancel", "/orders/modify",
                         "/portfolio/sync", "/blotter", "/regime/scan"]
        with patch.dict(os.environ, {"MDW_API_KEY": "key"}):
            for path in trading_paths:
                req = FakeRequest(path, {"X-API-Key": "key"})
                assert verify_api_key(req) is None, f"Path {path} should be rejected"
