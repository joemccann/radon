"""Unit tests for ``scripts/api/db_http.py`` — the bounded hrana access layer
that replaces sync libsql in the FastAPI process (DUR-09).

Pins the contract the routes depend on:
  - rows come back as positional tuples (drop-in for cursor.fetchall())
  - args encode per the hrana wire format (integers as strings, None → null)
  - every failure mode raises DbHttpError (callers own the fallback)
  - the socket timeout actually reaches urlopen
"""
from __future__ import annotations

import io
import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from api import db_http  # noqa: E402


def _pipeline_body(cols: list[str], rows: list[list[dict]]) -> dict:
    return {
        "results": [
            {
                "type": "ok",
                "response": {
                    "type": "execute",
                    "result": {"cols": [{"name": c} for c in cols], "rows": rows},
                },
            },
            {"type": "ok", "response": {"type": "close"}},
        ]
    }


class _FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


@pytest.fixture
def creds(monkeypatch):
    monkeypatch.setenv("TURSO_DB_URL", "libsql://example.turso.io")
    monkeypatch.setenv("TURSO_AUTH_TOKEN", "token")


class TestArgEncoding:
    def test_none_encodes_as_null(self):
        assert db_http._encode_arg(None) == {"type": "null"}

    def test_int_encodes_as_string_valued_integer(self):
        # hrana requires integer values as strings on the wire.
        assert db_http._encode_arg(180) == {"type": "integer", "value": "180"}

    def test_float_encodes_as_float(self):
        assert db_http._encode_arg(1.5) == {"type": "float", "value": 1.5}

    def test_str_encodes_as_text(self):
        assert db_http._encode_arg("2026-03-14") == {"type": "text", "value": "2026-03-14"}


class TestHranaExecute:
    def test_decodes_rows_as_tuples(self, creds):
        body = _pipeline_body(
            ["date", "index_value"],
            [
                [{"type": "text", "value": "2026-06-10"}, {"type": "float", "value": 1.05}],
                [{"type": "text", "value": "2026-06-11"}, {"type": "integer", "value": "2"}],
                [{"type": "text", "value": "2026-06-12"}, {"type": "null"}],
            ],
        )
        with patch.object(db_http, "_post_pipeline", return_value=body):
            rows = db_http.hrana_execute("SELECT date, index_value FROM t")
        assert rows == [("2026-06-10", 1.05), ("2026-06-11", 2), ("2026-06-12", None)]

    def test_missing_creds_raise(self, monkeypatch):
        monkeypatch.delenv("TURSO_DB_URL", raising=False)
        monkeypatch.delenv("TURSO_AUTH_TOKEN", raising=False)
        with pytest.raises(db_http.DbHttpError, match="not configured"):
            db_http.hrana_execute("SELECT 1")

    def test_statement_error_raises(self, creds):
        body = {
            "results": [
                {"type": "error", "error": {"message": "no such table: nope"}},
            ]
        }
        with patch.object(db_http, "_post_pipeline", return_value=body):
            with pytest.raises(db_http.DbHttpError, match="no such table"):
                db_http.hrana_execute("SELECT * FROM nope")

    def test_transport_error_wrapped_in_db_http_error(self, creds):
        with patch.object(db_http, "_post_pipeline", side_effect=TimeoutError("timed out")):
            with pytest.raises(db_http.DbHttpError, match="TimeoutError"):
                db_http.hrana_execute("SELECT 1")

    def test_timeout_and_args_reach_the_wire(self, creds):
        seen: dict = {}

        def fake_urlopen(req, timeout=None):
            seen["timeout"] = timeout
            seen["url"] = req.full_url
            seen["payload"] = json.loads(req.data.decode("utf-8"))
            seen["auth"] = req.get_header("Authorization")
            return _FakeResponse(json.dumps(_pipeline_body(["n"], [])).encode("utf-8"))

        with patch.object(db_http.urllib.request, "urlopen", fake_urlopen):
            db_http.hrana_execute("SELECT ? AS n", (42,), timeout=2.5)

        assert seen["timeout"] == 2.5
        assert seen["url"] == "https://example.turso.io/v2/pipeline"
        assert seen["auth"] == "Bearer token"
        stmt = seen["payload"]["requests"][0]["stmt"]
        assert stmt["sql"] == "SELECT ? AS n"
        assert stmt["args"] == [{"type": "integer", "value": "42"}]

    def test_default_timeout_is_bounded(self):
        assert 0 < db_http.HRANA_TIMEOUT_S <= 10
