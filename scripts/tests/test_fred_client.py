"""FRED HTTP failures must not carry the API key into stderr or the exception."""
from __future__ import annotations

import sys
import urllib.error
from io import BytesIO

import pytest

from clients import fred_client


_KEY = "abc+def/secret"


class _Response:
    def __init__(self, status: int, url: str) -> None:
        self.status_code = status
        self.url = url

    def raise_for_status(self) -> None:
        raise RuntimeError(f"403 Client Error: Forbidden for url: {self.url}")

    def json(self) -> dict:
        raise AssertionError("error body is not parsed")


class _Requests:
    @staticmethod
    def get(url, params, timeout):
        assert params["api_key"] == _KEY
        encoded = "abc%2Bdef%2Fsecret"
        return _Response(
            403,
            "https://api.stlouisfed.org/fred/series/observations"
            f"?series_id=DGS3MO&api_key={encoded}&file_type=json",
        )


def test_requests_http_error_omits_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "requests", _Requests)
    with pytest.raises(RuntimeError) as caught:
        fred_client._fetch_fred_observations("DGS3MO", _KEY)
    message = str(caught.value)
    assert _KEY not in message
    assert "abc%2Bdef" not in message
    assert "403" in message
    assert caught.value.__cause__ is None


def test_urllib_http_error_omits_request_url(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(req, timeout):
        raise urllib.error.HTTPError(req.full_url, 401, "Unauthorized", hdrs=None, fp=BytesIO(b""))

    real_import = __import__

    def guarded(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "requests":
            raise ImportError("blocked")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr("builtins.__import__", guarded)
    monkeypatch.setattr("urllib.request.urlopen", boom)
    with pytest.raises(RuntimeError) as caught:
        fred_client._fetch_fred_observations("DGS3MO", _KEY)
    assert _KEY not in str(caught.value)
    assert "401" in str(caught.value)
    assert caught.value.__cause__ is None


def test_risk_free_log_redacts_query_key(monkeypatch: pytest.MonkeyPatch, capsys, tmp_path) -> None:
    monkeypatch.setenv("FRED_API_KEY", _KEY)
    monkeypatch.setattr(fred_client, "CACHE_PATH", tmp_path / "risk_free_rate.json")

    def boom(*_args, **_kwargs):
        raise RuntimeError(
            "403 Client Error: Forbidden for url: "
            "https://api.stlouisfed.org/fred/series/observations?series_id=DGS3MO"
            f"&api_key={_KEY}&file_type=json"
        )

    monkeypatch.setattr(fred_client, "fetch_dgs3mo_series", boom)
    rate, warning = fred_client.get_risk_free_rate()
    err = capsys.readouterr().err
    assert rate == 0.0
    assert warning
    assert _KEY not in err
    assert "api_key=[redacted]" in err
