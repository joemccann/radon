"""Bounded private model-service transport. Consumers never receive provider credentials."""
from __future__ import annotations

import http.client
import json
import math
import os
import re
import socket
import threading
from typing import Any

SOCKET_ENV = "RADON_MODEL_GATEWAY_SOCKET"
DEFAULT_SOCKET = "/run/radon-model-gateway/gateway.sock"
CONTROL_SOCKET_ENV = "RADON_MODEL_CONTROL_SOCKET"
DEFAULT_CONTROL_SOCKET = "/run/radon-model-control/control.sock"
CONTROL_OPERATIONS = frozenset({"refresh", "refresh-status", "refresh-catalog", "validate-credential"})
MAX_REQUEST_BYTES = 16 * 1024 * 1024
MAX_RESPONSE_BYTES = 4 * 1024 * 1024
OPERATIONS = frozenset({"json", "vision", "chat", "embed", "ocr", "x-search", "forecast", "models", "status",
                        "validate-credential", "refresh-catalog", "refresh", "refresh-status", "native", "clairic"})


MODEL_SECRET_ENV = frozenset({
    "ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN", "CLAUDE_API_KEY",
    "OPENAI_API_KEY", "CODEX_OAUTH_TOKEN", "CODEX_ACCESS_TOKEN", "CODEX_REFRESH_TOKEN",
    "XAI_API_KEY", "XAI_OAUTH_TOKEN", "GROK_OAUTH_TOKEN", "GROK_API_KEY", "GROK_AUTH", "GROK_CODE_XAI_API_KEY",
    "NVIDIA_API_KEY", "CEREBRAS_API_KEY", "GOOGLE_API_KEY", "GEMINI_API_KEY",
    "CLOUDFLARE_API_TOKEN", "CLOUDFLARE_ACCOUNT_ID",
})


def scrub_model_secrets():
    """App processes never retain model grants, even when store bootstrap fails."""
    for name in MODEL_SECRET_ENV:
        os.environ.pop(name, None)


class GatewayError(RuntimeError):
    """Safe service failure; messages never include prompts, credentials or upstream bodies."""
    def __init__(self, code: str, message: str = "Model service request failed", status: int = 503, attempted=()):
        self.code = code if re.fullmatch(r"[a-z][a-z0-9_]{0,63}", code) else "gateway_error"
        self.status = status
        self.attempted = tuple(item for item in attempted if isinstance(item, str) and re.fullmatch(r"[a-z][a-z0-9-]*:[A-Za-z0-9_:.-]{1,96}", item))
        super().__init__(message)


class UnixHTTPConnection(http.client.HTTPConnection):
    def __init__(self, path: str, timeout: float):
        super().__init__("localhost", timeout=timeout)
        self.socket_path = path
        self.cancelled = None

    def connect(self) -> None:
        if self.cancelled is not None and self.cancelled():
            raise GatewayError("cancelled", "Model request was cancelled", 499)
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock = sock
        sock.settimeout(self.timeout)
        try:
            sock.connect(self.socket_path)
            if self.cancelled is not None and self.cancelled():
                raise GatewayError("cancelled", "Model request was cancelled", 499)
        except BaseException:
            sock.close()
            if self.sock is sock:
                self.sock = None
            raise


def request(operation: str, payload: dict[str, Any] | None = None, *, timeout_seconds: float = 120, cancelled=None) -> dict[str, Any]:
    """Send one operation over the local Unix socket; never call a vendor on failure."""
    if operation not in OPERATIONS or payload is not None and not isinstance(payload, dict):
        raise GatewayError("invalid_request", "Invalid model service request", 400)
    if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (float, int)) or not math.isfinite(timeout_seconds) or not 0 < timeout_seconds <= 1800:
        raise GatewayError("invalid_request", "Invalid model service deadline", 400)
    try:
        body = json.dumps(payload or {}, allow_nan=False, separators=(",", ":")).encode()
    except (ValueError, TypeError) as exc:
        raise GatewayError("invalid_request", "Invalid model service payload", 400) from exc
    if len(body) > MAX_REQUEST_BYTES:
        raise GatewayError("request_too_large", "Model request exceeds the service limit", 413)
    path = os.environ.get(CONTROL_SOCKET_ENV, DEFAULT_CONTROL_SOCKET) if operation in CONTROL_OPERATIONS else os.environ.get(SOCKET_ENV, DEFAULT_SOCKET)
    if not path.startswith("/") or "\x00" in path or len(path) > 100:
        raise GatewayError("unavailable", "Model service is unavailable", 503)
    conn = UnixHTTPConnection(path, float(timeout_seconds))
    conn.cancelled = cancelled
    stopped = threading.Event()
    def watch_cancel():
        while not stopped.wait(.05):
            if cancelled():
                sock = conn.sock
                if sock is not None:
                    try:
                        sock.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass
                conn.close()
                return
    if cancelled is not None:
        if cancelled():
            raise GatewayError("cancelled", "Model request was cancelled", 499)
        threading.Thread(target=watch_cancel, daemon=True).start()
    try:
        conn.request("POST", "/v1/" + operation, body=body,
                     headers={"Content-Type": "application/json", "Connection": "close"})
        if cancelled is not None and cancelled():
            raise GatewayError("cancelled", "Model request was cancelled", 499)
        response = conn.getresponse()
        raw = response.read(MAX_RESPONSE_BYTES + 1)
        if len(raw) > MAX_RESPONSE_BYTES:
            raise GatewayError("response_too_large", "Model response exceeds the service limit", 502)
        try:
            value = json.loads(raw)
        except (ValueError, UnicodeDecodeError) as exc:
            raise GatewayError("invalid_response", "Model service returned an invalid response", 502) from exc
        if not isinstance(value, dict):
            raise GatewayError("invalid_response", "Model service returned an invalid response", 502)
        if response.status != 200 and response.status != 202:
            error = value.get("error")
            code = error.get("code") if isinstance(error, dict) else "gateway_error"
            # Never trust a returned message with source contents or provider diagnostics.
            raise GatewayError(code if isinstance(code, str) else "gateway_error",
                               "Model service request failed", response.status, error.get("attempted", ()) if isinstance(error, dict) else ())
        if cancelled is not None and cancelled():
            raise GatewayError("cancelled", "Model request was cancelled", 499)
        return value
    except GatewayError:
        raise
    except (OSError, http.client.HTTPException) as exc:
        if cancelled is not None and cancelled():
            raise GatewayError("cancelled", "Model request was cancelled", 499) from exc
        raise GatewayError("unavailable", "Model service is unavailable", 503) from exc
    finally:
        stopped.set()
        conn.close()
