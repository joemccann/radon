"""Async credential validator contract (scripts/credential_validators.py).

Every validator is exercised at the wire shape: URL, auth header/payload, and
the mapping HTTP status -> verdict. Network is always stubbed. validate()
must never raise: an unexpected exception is an "error" verdict, because a
vendor outage must not block saving a credential.
"""

import json
import subprocess
from types import SimpleNamespace

import pytest
import requests

import credential_validators as cv


class _Response:
    def __init__(self, status_code: int, text: str = ""):
        self.status_code = status_code
        self.text = text


@pytest.fixture()
def http(monkeypatch):
    """Capture requests.get/post; queue one canned response."""
    calls = []
    box = {"response": _Response(200)}

    def _get(url, headers=None, timeout=None, **kwargs):
        calls.append(SimpleNamespace(method="GET", url=url, headers=headers or {}, json=None, timeout=timeout))
        resp = box["response"]
        if isinstance(resp, Exception):
            raise resp
        return resp

    def _post(url, headers=None, json=None, timeout=None, **kwargs):
        calls.append(SimpleNamespace(method="POST", url=url, headers=headers or {}, json=json, timeout=timeout))
        resp = box["response"]
        if isinstance(resp, Exception):
            raise resp
        return resp

    monkeypatch.setattr(cv.requests, "get", _get)
    monkeypatch.setattr(cv.requests, "post", _post)
    return SimpleNamespace(calls=calls, box=box)


class TestHttpVerdictMapping:
    def test_anthropic_valid(self, http):
        result = cv.validate("anthropic", {"ANTHROPIC_API_KEY": "sk-ant-x"})
        assert result.status == "valid"
        (call,) = http.calls
        assert call.url == "https://api.anthropic.com/v1/models"
        assert call.headers["x-api-key"] == "sk-ant-x"
        assert call.timeout is not None

    def test_anthropic_unauthorized_is_invalid(self, http):
        http.box["response"] = _Response(401)
        result = cv.validate("anthropic", {"ANTHROPIC_API_KEY": "sk-bad"})
        assert result.status == "invalid"

    def test_forbidden_is_invalid(self, http):
        http.box["response"] = _Response(403)
        result = cv.validate("anthropic", {"ANTHROPIC_API_KEY": "sk-bad"})
        assert result.status == "invalid"

    def test_vendor_5xx_is_error_not_invalid(self, http):
        http.box["response"] = _Response(503)
        result = cv.validate("anthropic", {"ANTHROPIC_API_KEY": "sk-x"})
        assert result.status == "error"

    def test_network_failure_is_error(self, http):
        http.box["response"] = requests.ConnectionError("refused")
        result = cv.validate("anthropic", {"ANTHROPIC_API_KEY": "sk-x"})
        assert result.status == "error"

    def test_timeout_is_error(self, http):
        http.box["response"] = requests.Timeout("slow")
        result = cv.validate("anthropic", {"ANTHROPIC_API_KEY": "sk-x"})
        assert result.status == "error"


class TestWireShapes:
    def test_unusual_whales_bearer(self, http):
        cv.validate("unusual_whales", {"UW_TOKEN": "uw-tok"})
        (call,) = http.calls
        assert call.url.startswith("https://api.unusualwhales.com/api/")
        assert call.headers["Authorization"] == "Bearer uw-tok"

    def test_cerebras_models(self, http):
        cv.validate("cerebras", {"CEREBRAS_API_KEY": "csk-1"})
        (call,) = http.calls
        assert call.url == "https://api.cerebras.ai/v1/models"
        assert call.headers["Authorization"] == "Bearer csk-1"

    def test_xai_models(self, http):
        cv.validate("xai", {"XAI_API_KEY": "xai-1"})
        (call,) = http.calls
        assert call.url == "https://api.x.ai/v1/models"
        assert call.headers["Authorization"] == "Bearer xai-1"

    def test_exa_search_post(self, http):
        cv.validate("exa", {"EXA_API_KEY": "exa-1"})
        (call,) = http.calls
        assert call.method == "POST"
        assert call.url == "https://api.exa.ai/search"
        assert call.headers["x-api-key"] == "exa-1"
        assert call.json["numResults"] == 1

    def test_clerk_users_probe(self, http):
        cv.validate("clerk", {"CLERK_SECRET_KEY": "sk_live_1"})
        (call,) = http.calls
        assert call.url == "https://api.clerk.com/v1/users?limit=1"
        assert call.headers["Authorization"] == "Bearer sk_live_1"

    def test_equibles_bearer(self, http):
        cv.validate("equibles", {"EQUIBLES_API_KEY": "eq-1"})
        (call,) = http.calls
        assert call.url.startswith("https://api.equibles.com/v1")
        assert call.headers["Authorization"] == "Bearer eq-1"

    def test_artificial_analysis_key_header(self, http):
        cv.validate(
            "artificial_analysis", {"ARTIFICIAL_ANALYSIS_API_KEY": "aa-1"}
        )
        (call,) = http.calls
        assert call.url == "https://artificialanalysis.ai/api/v2/data/llms/models"
        assert call.headers["x-api-key"] == "aa-1"

    def test_pushover_validate_endpoint(self, http):
        cv.validate("pushover", {"PUSHOVER_USER": "u1", "PUSHOVER_TOKEN": "t1"})
        (call,) = http.calls
        assert call.method == "POST"
        assert call.url == "https://api.pushover.net/1/users/validate.json"
        assert call.json == {"token": "t1", "user": "u1"}

    def test_turso_pipeline_probe_converts_scheme(self, http):
        cv.validate(
            "turso",
            {
                "TURSO_DB_URL": "libsql://radon-x.aws-us-west-2.turso.io",
                "TURSO_AUTH_TOKEN": "ts-1",
            },
        )
        (call,) = http.calls
        assert call.method == "POST"
        assert call.url == "https://radon-x.aws-us-west-2.turso.io/v2/pipeline"
        assert call.headers["Authorization"] == "Bearer ts-1"
        assert call.json["requests"][0]["stmt"]["sql"] == "SELECT 1"


class TestDispatch:
    def test_missing_values_are_unchecked_not_invalid(self, http):
        result = cv.validate("pushover", {"PUSHOVER_TOKEN": "t1"})
        assert result.status == "unchecked"
        assert http.calls == []
        assert "PUSHOVER_USER" in result.message

    def test_unknown_service_is_unchecked(self, http):
        result = cv.validate("nope", {"X": "y"})
        assert result.status == "unchecked"

    def test_service_without_validator_is_unchecked(self, http):
        result = cv.validate("ib_flex", {"IB_FLEX_TOKEN": "123"})
        assert result.status == "unchecked"
        assert http.calls == []

    def test_validate_never_raises(self, monkeypatch):
        def _boom(*a, **k):
            raise RuntimeError("unexpected")

        monkeypatch.setitem(cv.VALIDATORS, "anthropic", _boom)
        result = cv.validate("anthropic", {"ANTHROPIC_API_KEY": "sk-x"})
        assert result.status == "error"

    def test_blocks_save_only_when_invalid(self):
        assert cv.ValidationResult("invalid", "no").blocks_save is True
        for status in ("valid", "error", "unchecked"):
            assert cv.ValidationResult(status, "").blocks_save is False


class TestSlowLoginValidators:
    def _proc(self, stdout="", returncode=0):
        return subprocess.CompletedProcess(
            args=[], returncode=returncode, stdout=stdout, stderr=""
        )

    def test_menthorq_runs_login_subprocess(self, monkeypatch):
        runs = []

        def _run(cmd, **kwargs):
            runs.append(SimpleNamespace(cmd=cmd, kwargs=kwargs))
            return self._proc(json.dumps({"status": "valid", "message": ""}))

        monkeypatch.setattr(cv.subprocess, "run", _run)
        result = cv.validate(
            "menthorq", {"MENTHORQ_USER": "u@x.com", "MENTHORQ_PASS": "pw"}
        )
        assert result.status == "valid"
        (run,) = runs
        assert run.cmd[-1] == "menthorq"
        assert "validate_login.py" in run.cmd[-2]
        env = run.kwargs["env"]
        assert env["MENTHORQ_USER"] == "u@x.com"
        assert env["MENTHORQ_PASS"] == "pw"
        assert run.kwargs["timeout"] == cv.SLOW_LOGIN_TIMEOUT_S

    def test_themarketear_invalid_verdict_passthrough(self, monkeypatch):
        monkeypatch.setattr(
            cv.subprocess,
            "run",
            lambda *a, **k: self._proc(
                json.dumps({"status": "invalid", "message": "still on login"})
            ),
        )
        result = cv.validate(
            "themarketear",
            {"THEMARKETEAR_EMAIL": "u@x.com", "THEMARKETEAR_PASSWORD": "pw"},
        )
        assert result.status == "invalid"
        assert "still on login" in result.message

    def test_subprocess_failure_is_error(self, monkeypatch):
        monkeypatch.setattr(
            cv.subprocess, "run", lambda *a, **k: self._proc("boom", returncode=1)
        )
        result = cv.validate(
            "menthorq", {"MENTHORQ_USER": "u", "MENTHORQ_PASS": "p"}
        )
        assert result.status == "error"

    def test_subprocess_timeout_is_error(self, monkeypatch):
        def _run(*a, **k):
            raise subprocess.TimeoutExpired(cmd="x", timeout=1)

        monkeypatch.setattr(cv.subprocess, "run", _run)
        result = cv.validate(
            "menthorq", {"MENTHORQ_USER": "u", "MENTHORQ_PASS": "p"}
        )
        assert result.status == "error"

    def test_garbage_stdout_is_error(self, monkeypatch):
        monkeypatch.setattr(
            cv.subprocess, "run", lambda *a, **k: self._proc("not json")
        )
        result = cv.validate(
            "menthorq", {"MENTHORQ_USER": "u", "MENTHORQ_PASS": "p"}
        )
        assert result.status == "error"


class TestTursoHostPin:
    def test_http_url_rejected(self, monkeypatch):
        monkeypatch.delenv("TURSO_DB_URL", raising=False)
        result = cv.validate(
            "turso",
            {
                "TURSO_DB_URL": "http://evil.example/db",
                "TURSO_AUTH_TOKEN": "tok",
            },
        )
        assert result.status == "invalid"
        assert "HTTPS" in result.message

    def test_host_mismatch_rejected(self, monkeypatch):
        monkeypatch.setenv(
            "TURSO_DB_URL", "libsql://radon-joemccann.aws-us-west-2.turso.io"
        )
        result = cv.validate(
            "turso",
            {
                "TURSO_DB_URL": "libsql://other-host.aws-us-west-2.turso.io",
                "TURSO_AUTH_TOKEN": "tok",
            },
        )
        assert result.status == "invalid"
        assert "host" in result.message.lower()


class TestValidatorRedaction:
    def test_subprocess_stderr_token_redacted(self, monkeypatch):
        monkeypatch.setattr(
            cv.subprocess,
            "run",
            lambda *a, **k: cv.subprocess.CompletedProcess(
                args=a[0],
                returncode=1,
                stdout="",
                stderr="login failed token=sk-ant-api03-deadbeef",
            ),
        )
        result = cv.validate(
            "menthorq", {"MENTHORQ_USER": "u", "MENTHORQ_PASS": "p"}
        )
        assert result.status == "error"
        assert "sk-ant-api03-deadbeef" not in result.message
        assert "[redacted-key]" in result.message
