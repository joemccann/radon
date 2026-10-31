"""Private standalone model gateway. No provider credentials or arbitrary proxy routes leave it."""
from __future__ import annotations
import argparse
import base64
import binascii
import hashlib
import io
import json
import logging
import math
import os
import re
import select
import socket
import socketserver
import stat
import struct
import sys
import threading
import time
import uuid
from collections import OrderedDict
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler
from pathlib import Path
# The managed entrypoint is `python -m scripts.model_gateway.server` from
# the project root. Legacy shared script utilities use top-level imports,
# just as the API entrypoint does; do not depend on a caller's PYTHONPATH.
_SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from . import engine
from .client import DEFAULT_SOCKET, DEFAULT_CONTROL_SOCKET, CONTROL_OPERATIONS, OPERATIONS, MAX_REQUEST_BYTES, MAX_RESPONSE_BYTES, GatewayError
from .contracts import ModelLadderExhausted, ModelResponseError, ClairicError
from .lifecycle import RefreshManager

log = logging.getLogger("model_gateway")
PROVIDERS = frozenset(engine.MODEL_LADDER_ORDER) | {"slm-tagger"}
JSON_FIELDS = {"instruction", "system", "images", "model", "model_override", "providers", "max_tokens", "read_timeout", "max_response_bytes", "require_end_turn", "stream_anthropic", "log_prefix", "continuation", "accept", "purpose", "post_id", "taxonomy"}


def bounded(value, minimum, maximum, *, integer=False):
    if type(value) not in ((int,) if integer else (int, float)) or not math.isfinite(value) or not minimum <= value <= maximum:
        raise GatewayError("invalid_request", "Invalid model request limit", 400)
    return value


def safe_attempts(error):
    return [item for item in re.findall(r"([a-z][a-z0-9-]*:[A-Za-z0-9_:.-]{1,96})(?=\s|;|$)", str(error)) if item.split(":", 1)[0] in PROVIDERS]


def decode_image(value, media="image/png"):
    if not isinstance(value, str) or len(value) > 7_000_000 or media not in {"image/png", "image/jpeg", "image/webp", "image/gif"}:
        raise GatewayError("invalid_request", "Invalid model image", 400)
    from PIL import Image
    try:
        raw = base64.b64decode(value, validate=True)
        if not raw or len(raw) > 5_000_000:
            raise ValueError("image size")
        with Image.open(io.BytesIO(raw)) as image:
            if image.width * image.height > 16_000_000 or image.width <= 0 or image.height <= 0:
                raise ValueError("image pixels")
            image.load()
            output = io.BytesIO()
            image.convert("RGB").save(output, "PNG")
        if output.tell() > 5_000_000:
            raise ValueError("normalized image size")
        return output.getvalue()
    except (ValueError, OSError, binascii.Error, Image.DecompressionBombError) as exc:
        raise GatewayError("invalid_request", "Invalid model image", 400) from exc


class Gateway:
    def __init__(self, *, clock=time.monotonic):
        self.clock = clock
        self.sessions = OrderedDict()
        self.session_lock = threading.Lock()
        self.slots = threading.BoundedSemaphore(8)
        self.provider_locks = {name: threading.RLock() for name in PROVIDERS}
        self.refresh = RefreshManager(self.provider_locks)
        self.catalog_rows = []

    def _session(self, payload, budget):
        original = {key: value for key, value in payload.items() if key != "continuation"}
        fingerprint = hashlib.sha256(json.dumps(original, sort_keys=True, allow_nan=False).encode()).hexdigest()
        continuation = payload.get("continuation")
        with self.session_lock:
            self.sessions = OrderedDict((key, row) for key, row in self.sessions.items() if row["deadline"] > self.clock())
            if continuation is not None:
                if not isinstance(continuation, str) or len(continuation) > 64:
                    raise GatewayError("invalid_request", "Invalid model continuation", 400)
                row = self.sessions.get(continuation)
                if row is None or row["fingerprint"] != fingerprint:
                    raise GatewayError("invalid_continuation", "Model continuation is unavailable", 409)
                del self.sessions[continuation]  # Single-use continuation; no duplicate traversal.
                return row
        return {"fingerprint": fingerprint, "deadline": self.clock() + budget, "seen": set(), "attempted": []}

    def _save_session(self, row):
        token = uuid.uuid4().hex
        with self.session_lock:
            while len(self.sessions) >= 1024:
                self.sessions.popitem(last=False)
            self.sessions[token] = row
        return token

    def _scope(self, state, cancelled):
        parent = engine._ATTEMPT_STATE.get()
        if parent is not None:
            state["deadline"] = min(state["deadline"], parent.deadline)
        return engine.attempt_scope(deadline=state["deadline"], seen=state["seen"], clock=self.clock,
                                    cancelled=cancelled, locks=self.provider_locks, refresh=self.refresh.request_refresh)

    def _json(self, payload, cancelled):
        if set(payload) - JSON_FIELDS or not isinstance(payload.get("instruction"), str) or not 0 < len(payload["instruction"]) <= 1_000_000:
            raise GatewayError("invalid_request", "Invalid JSON model request", 400)
        if not isinstance(payload.get("system", ""), str) or len(payload.get("system", "")) > 200_000:
            raise GatewayError("invalid_request", "Invalid model system instruction", 400)
        providers = payload.get("providers")
        if providers is not None and (not isinstance(providers, list) or not providers or len(providers) > 8 or any(name not in PROVIDERS for name in providers)):
            raise GatewayError("invalid_request", "Invalid model providers", 400)
        budget = bounded(payload.get("read_timeout", 120), .1, 300)
        max_tokens = bounded(payload.get("max_tokens", 6000), 1, 32_000, integer=True)
        response_limit = bounded(payload.get("max_response_bytes", 2_000_000), 64, 2_000_000, integer=True)
        for field in ("require_end_turn", "stream_anthropic"):
            if field in payload and type(payload[field]) is not bool:
                raise GatewayError("invalid_request", "Invalid model option", 400)
        purpose = payload.get("purpose")
        if purpose not in (None, "newsfeed-tags") or payload.get("accept") not in (None, "tags", "distill"):
            raise GatewayError("invalid_request", "Unknown model request contract", 400)
        images = payload.get("images", [])
        if not isinstance(images, list) or len(images) > 16:
            raise GatewayError("invalid_request", "Invalid model images", 400)
        decoded = []
        for image in images:
            if not isinstance(image, dict) or set(image) != {"label", "data", "media_type"} or not isinstance(image["label"], str) or len(image["label"]) > 256:
                raise GatewayError("invalid_request", "Invalid model image fields", 400)
            decoded.append((image["label"], decode_image(image["data"], image["media_type"])))
        if sum(len(value) for _, value in decoded) > 10_000_000:
            raise GatewayError("request_too_large", "Model images exceed the service limit", 413)
        if decoded:
            providers = [name for name in (providers or engine.MODEL_LADDER_ORDER) if name not in {"antigravity", "cursor", "slm-tagger"}]
        model = payload.get("model_override", payload.get("model"))
        if model is not None and (not isinstance(model, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,127}", model)):
            raise GatewayError("invalid_request", "Invalid model selection", 400)
        state = self._session(payload, budget)
        accept = {"tags": engine.accept_tags_payload, "distill": engine.accept_distill_payload}.get(payload.get("accept"))
        post_id = payload.get("post_id")
        if post_id is not None and (not isinstance(post_id, str) or len(post_id) > 256):
            raise GatewayError("invalid_request", "Invalid model post identifier", 400)
        taxonomy = payload.get("taxonomy")
        if taxonomy is not None and (purpose != "newsfeed-tags" or not isinstance(taxonomy, list) or len(taxonomy) > 4096 or any(not isinstance(tag, str) or not 0 < len(tag) <= 128 for tag in taxonomy)):
            raise GatewayError("invalid_request", "Invalid tagger taxonomy", 400)
        def invoke(selection, *, auxiliary=False):
            scope_state = {**state, "seen": set()} if auxiliary else state
            with self._scope(scope_state, cancelled), engine.slm_taxonomy(taxonomy):
                return engine.complete_multimodal_json(payload["instruction"], decoded,
                    system=payload.get("system", ""), model_override=model, providers=selection,
                    max_tokens=max_tokens, read_timeout=budget, max_response_bytes=response_limit,
                    require_end_turn=payload.get("require_end_turn", True), stream_anthropic=payload.get("stream_anthropic", True),
                    accept=accept, log_prefix="model-gateway-json")
        shadow = None
        if purpose == "newsfeed-tags":
            if decoded or providers is not None or payload.get("accept") != "tags":
                raise GatewayError("invalid_request", "Invalid tagger routing contract", 400)
            from .slm import complete
            result, shadow = complete(payload, invoke)
        else:
            result = invoke(providers)
        state["attempted"].extend(result.attempted)
        return {"data": result.data, "text": result.text, "provider": result.provider, "model": result.model,
                "attempted": state["attempted"], "continuation": self._save_session(state), **({"shadow": shadow} if shadow else {})}

    def models(self):
        from .specialized import model_inventory
        return model_inventory()

    def warm_catalog(self):
        from .credentials import refresh_keys
        from .specialized import model_inventory
        try:
            refresh_keys()
            model_inventory(refresh=True)
        except Exception as exc:
            log.warning("Model metadata refresh unavailable: %s", type(exc).__name__)

    def status(self):
        from . import specialized
        providers = list(engine.wired_providers())
        return {"status": "ok", "providers": providers,
                "capabilities": {"json": bool(providers), "chat": bool(providers), "forecast": specialized.forecast_available(),
                                 "embeddings": True, "ocr": "nvidia" in providers}, "version": os.environ.get("RADON_DEPLOY_SHA", "unknown")}

    def handle(self, operation, payload, *, cancelled=lambda: False):
        if operation not in OPERATIONS or not isinstance(payload, dict):
            raise GatewayError("invalid_request", "Unknown model operation", 400)
        defaults = {"native": 270, "forecast": 300, "x-search": 300, "clairic": 12,
                    "ocr": 100, "refresh-catalog": 180, "refresh": 180,
                    "validate-credential": 12, "models": 10, "status": 10, "refresh-status": 10}
        field = {"chat": "timeoutMs", "json": "read_timeout", "native": "timeout_seconds", "ocr": "timeout_seconds"}.get(operation)
        default = 45_000 if operation == "chat" else defaults.get(operation, 120)
        budget = bounded(payload.get(field, default) if field else default, 0, 300_000 if operation == "chat" else 300)
        if operation == "chat":
            budget /= 1000
        if budget == 0:
            raise GatewayError("invalid_request", "Invalid model deadline", 400)
        # Credential lock/IPC admission spends the same deadline as inference.
        with engine.attempt_scope(deadline=self.clock() + budget, seen=set(), clock=self.clock,
                                  cancelled=cancelled, locks=self.provider_locks,
                                  refresh=self.refresh.request_refresh) as state:
            state.slice_seconds = budget
            return self._handle(operation, payload, cancelled=cancelled)

    def _handle(self, operation, payload, *, cancelled):
        if not self.slots.acquire(blocking=False):
            raise GatewayError("busy", "Model service is busy", 429)
        try:
            from .credentials import refresh_keys
            engine.scoped_timeout(1)
            refresh_keys()
            if operation == "json":
                return self._json(payload, cancelled)
            if operation in {"status", "models"}:
                if payload:
                    raise GatewayError("invalid_request", "Unexpected model inventory fields", 400)
                return self.status() if operation == "status" else self.models()
            if operation == "refresh":
                return self.refresh.start(payload)
            if operation == "refresh-status":
                return self.refresh.status(payload)
            if operation == "vision":
                if set(payload) - {"image", "prompt", "max_response_bytes"} or not isinstance(payload.get("prompt"), str):
                    raise GatewayError("invalid_request", "Invalid vision request", 400)
                image = decode_image(payload.get("image"))
                state = {"deadline": self.clock() + 120, "seen": set()}
                with self._scope(state, cancelled):
                    result = engine.extract_via_vision(image, payload["prompt"], max_response_bytes=bounded(payload.get("max_response_bytes", 2_000_000),64,2_000_000,integer=True))
                return {"rows": result.rows, "provider": result.provider, "model": result.model, "attempted": list(result.attempted)}
            if operation == "chat":
                from .chat import complete
                budget = bounded(payload.get("timeoutMs", 45_000), 100, 300_000) / 1000
                state = {"deadline": self.clock() + budget, "seen": set()}
                with self._scope(state, cancelled):
                    return complete(payload, catalog=self.models())
            if operation == "native":
                from .native import complete
                budget = bounded(payload.get("timeout_seconds", 270), 0, 300)
                if budget == 0:
                    raise GatewayError("invalid_request", "Invalid native deadline", 400)
                state = {"deadline": self.clock() + budget, "seen": set()}
                with self._scope(state, cancelled):
                    return complete(payload)
            from .specialized import dispatch
            budgets = {"forecast": 300, "ocr": payload.get("timeout_seconds", 100), "x-search": 300, "clairic": 12, "embed": 120, "validate-credential": 12, "refresh-catalog": 180}
            budget = bounded(budgets.get(operation, 120), .1, 300)
            state = {"deadline": self.clock() + budget, "seen": set()}
            lock_name = {"embed": "nvidia" if payload.get("dimensions") == 2048 else "embed", "ocr": "nvidia", "x-search": "grok", "clairic": "clairic", "forecast": "forecast"}.get(operation)
            lock = self.provider_locks.setdefault(lock_name, threading.RLock()) if lock_name else None
            with self._scope(state, cancelled) as scope:
                scope.slice_seconds = budget
                if lock is not None and not lock.acquire(timeout=engine.scoped_timeout(1)):
                    raise GatewayError("busy", "Model capability is busy", 429)
                try:
                    engine.scoped_timeout(budget)
                    result = dispatch(operation, payload)
                finally:
                    if lock is not None:
                        lock.release()
            if operation == "refresh-catalog":
                threading.Thread(target=self.warm_catalog, daemon=True).start()
            return result
        except ClairicError as exc:
            reason = exc.reason if re.fullmatch(r"(?:missing_credentials|empty_state|timeout|http_[0-9]{3}|transport|bad_response|api_error)", str(exc.reason)) else "bad_response"
            raise GatewayError(reason, "Report date model is unavailable", 503 if reason == "missing_credentials" else 502) from exc
        except ModelResponseError as exc:
            raise GatewayError(exc.code, "Model response could not be verified", 504 if exc.code == "deadline_exceeded" else 499 if exc.code == "cancelled" else 502) from exc
        except ModelLadderExhausted as exc:
            raise GatewayError("exhausted", "Available model providers were exhausted", 503, attempted=safe_attempts(exc)) from exc
        except (ValueError, TypeError, KeyError) as exc:
            raise GatewayError("invalid_request", "Invalid model request", 400) from exc
        finally:
            self.slots.release()


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    HEADER_TIMEOUT = 5.0
    def setup(self):
        super().setup()
        self.connection.settimeout(self.HEADER_TIMEOUT)
    def log_message(self, *args):
        return
    def _reply(self, status, value):
        data = json.dumps(value, allow_nan=False, separators=(",", ":")).encode()
        if len(data) > MAX_RESPONSE_BYTES:
            status, data = 502, b'{"error":{"code":"response_too_large"}}'
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(data)
        self.close_connection = True
    def _peer_uid(self):
        if hasattr(socket, "SO_PEERCRED"):
            _pid, uid, _gid = struct.unpack("3i", self.connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
            return uid
        if hasattr(self.connection, "getpeereid"):
            return self.connection.getpeereid()[0]
        return os.getuid()  # Private filesystem permissions on development platforms.
    def _authorized(self, operation):
        return self.server.permits(operation, self._peer_uid())
    def _cancelled(self):
        try:
            ready, _, _ = select.select([self.connection], [], [], 0)
            return bool(ready) and self.connection.recv(1, socket.MSG_PEEK) == b""
        except (OSError, ValueError):
            return True
    def do_GET(self):
        if not self._authorized("models" if self.path == "/v1/models" else "status"):
            self._reply(403, {"error": {"code": "forbidden"}})
            return
        if self.path not in {"/health", "/v1/status", "/v1/models"}:
            self._reply(404, {"error": {"code": "not_found"}})
            return
        try:
            self._reply(200, self.server.gateway.handle("models" if self.path == "/v1/models" else "status", {}))
        except GatewayError as exc:
            self._reply(exc.status, {"error": {"code": exc.code}})
    def do_POST(self):
        try:
            if not self._authorized(self.path[4:] if self.path.startswith("/v1/") else ""):
                raise GatewayError("forbidden", "Model service access denied", 403)
            if self.headers.get("Transfer-Encoding") is not None or self.headers.get_content_type() != "application/json":
                raise GatewayError("invalid_request", "Invalid model request framing", 400)
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= MAX_REQUEST_BYTES:
                raise GatewayError("request_too_large", "Model request exceeds service limit", 413)
            if not self.path.startswith("/v1/") or self.path[4:] not in OPERATIONS:
                raise GatewayError("not_found", "Unknown model operation", 404)
            self.connection.settimeout(10)
            data = self.rfile.read(length)
            if len(data) != length:
                raise GatewayError("invalid_request", "Incomplete model request", 400)
            payload = json.loads(data, parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
            result = self.server.gateway.handle(self.path[4:], payload, cancelled=self._cancelled)
            self._reply(202 if self.path == "/v1/refresh" else 200, result)
        except GatewayError as exc:
            self._reply(exc.status, {"error": {"code": exc.code, "attempted": list(exc.attempted)}})
        except (ValueError, UnicodeError):
            self._reply(400, {"error": {"code": "invalid_request"}})
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            return
        except Exception as exc:
            log.error("Model operation failed: %s", type(exc).__name__)
            self._reply(503, {"error": {"code": "unavailable"}})


class GatewayServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True
    block_on_close = False
    request_queue_size = 16
    MAX_CONNECTIONS = 24
    def __init__(self, path, gateway, *, management=False, tunnel_uid=None, tunnel_gid=None, client_uids=()):
        self.gateway = gateway
        self.allowed_uid = os.getuid()
        self.allowed_uids = {self.allowed_uid}
        self.management = management
        self._connections = threading.BoundedSemaphore(self.MAX_CONNECTIONS)
        if not isinstance(client_uids, (tuple, list)) or len(client_uids) > 16:
            raise ValueError("invalid model client identities")
        extra_uids = tuple(client_uids) + ((tunnel_uid,) if tunnel_uid is not None else ())
        shared = not management and bool(extra_uids)
        if extra_uids:
            if management or any(type(uid) is not int or not 0 < uid <= 4_294_967_295 or uid == self.allowed_uid for uid in extra_uids) or type(tunnel_gid) is not int or tunnel_gid <= 0:
                raise ValueError("invalid model tunnel identity")
            self.allowed_uids.update(extra_uids)
        parent = Path(path).parent
        if parent.is_symlink():
            raise OSError("model service socket directory must be private")
        parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        info = parent.stat()
        expected_mode = 0o710 if shared else 0o700
        if info.st_uid != self.allowed_uid or stat.S_IMODE(info.st_mode) not in {0o700, expected_mode}:
            raise OSError("model service socket directory must be private")
        if shared:
            os.chown(parent, -1, tunnel_gid)
            os.chmod(parent, expected_mode)
        if Path(path).exists() or Path(path).is_symlink():
            info = Path(path).lstat()
            if not stat.S_ISSOCK(info.st_mode) or info.st_uid != self.allowed_uid:
                raise OSError("refusing non-service socket path")
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as probe:
                probe.settimeout(.2)
                try:
                    probe.connect(str(path))
                except ConnectionRefusedError:
                    pass
                except FileNotFoundError:
                    pass
                else:
                    raise OSError("model service socket is active")
            Path(path).unlink(missing_ok=True)
        super().__init__(path, Handler)
        if shared:
            os.chown(path, -1, tunnel_gid)
        os.chmod(path, 0o660 if shared else 0o600)
        self._socket_inode = Path(path).stat().st_ino

    def permits(self, operation, uid):
        if uid not in self.allowed_uids or operation not in OPERATIONS:
            return False
        if self.management:
            return uid == self.allowed_uid and operation in CONTROL_OPERATIONS | {"status", "models"}
        return operation not in CONTROL_OPERATIONS

    def process_request(self, request, client_address):
        if not self._connections.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self._connections.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._connections.release()

    def server_close(self):
        super().server_close()
        path = Path(self.server_address)
        try:
            if path.is_socket() and path.stat().st_ino == self._socket_inode:
                path.unlink()
        except FileNotFoundError:
            pass


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--socket", default=os.environ.get("RADON_MODEL_GATEWAY_SOCKET", DEFAULT_SOCKET))
    parser.add_argument("--control-socket", default=os.environ.get("RADON_MODEL_CONTROL_SOCKET", DEFAULT_CONTROL_SOCKET))
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO)
    os.environ["RADON_ANTIGRAVITY_ISOLATED"] = "1"
    gateway = Gateway()
    threading.Thread(target=gateway.warm_catalog, daemon=True).start()
    tunnel_uid = os.environ.get("RADON_MODEL_TUNNEL_UID")
    tunnel_gid = os.environ.get("RADON_MODEL_TUNNEL_GID")
    raw_uids = os.environ.get("RADON_MODEL_CLIENT_UIDS", "")
    parts = raw_uids.split(",") if raw_uids else []
    if len(parts) > 16 or any(not re.fullmatch(r"[1-9][0-9]{0,9}", part) for part in parts):
        raise ValueError("invalid model client identities")
    if bool(tunnel_uid or parts) != bool(tunnel_gid):
        raise ValueError("incomplete model tunnel identity")
    server = GatewayServer(args.socket, gateway, tunnel_uid=int(tunnel_uid) if tunnel_uid else None,
                           tunnel_gid=int(tunnel_gid) if tunnel_gid else None, client_uids=tuple(map(int, parts)))
    control = GatewayServer(args.control_socket, gateway, management=True)
    worker = threading.Thread(target=control.serve_forever, kwargs={"poll_interval": .25}, daemon=True)
    worker.start()
    try:
        server.serve_forever(poll_interval=.25)
    finally:
        control.shutdown()
        control.server_close()
        server.server_close()

if __name__ == "__main__":
    sys.exit(main())
