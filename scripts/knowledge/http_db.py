"""Knowledge-only DB-API subset over bounded Hrana HTTP v2.

Production store writes queue an entire conditional transaction and stream
close in one request. Losing a receipt cannot prevent the server from receiving
cleanup. The caller retries the whole prepared document after an ambiguous
COMMIT; standalone source reads close their stream in the same request.
"""
from __future__ import annotations

import base64
import json
import time
import urllib.request
from urllib.parse import urlsplit

from db.hrana_http import HranaHttpError, _encode_arg, _refuse_pytest_pollution
from health_service.turso_http import http_url_from_libsql, read_env

REQUEST_TIMEOUT = 4.0
MAX_REQUEST_BYTES = 8 * 1024 * 1024
MAX_TRANSACTION_STEPS = 4096
MAX_RESPONSE_BYTES = 8 * 1024 * 1024  # paginated source content, not tiny write receipts


class TransportError(HranaHttpError):
    """The stream is unusable; retry only a fresh, complete transaction."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # bearer credentials only follow validated protocol routing


def _cell(cell):
    kind = cell.get("type")
    if kind == "null":
        return None
    if kind == "integer":
        return int(cell["value"])
    if kind == "float":
        return float(cell["value"])
    if kind == "text":
        return cell["value"]
    if kind == "blob":
        return base64.b64decode(cell["base64"], validate=True)
    raise HranaHttpError("invalid Hrana value type")


class _Cursor:
    def __init__(self, result):
        if not isinstance(result, dict) or not isinstance(result.get("rows"), list):
            raise TransportError("invalid Hrana statement result")
        self._rows = [tuple(_cell(cell) for cell in row) for row in result["rows"]]
        self._offset = 0
        row_id = result.get("last_insert_rowid")
        self.lastrowid = int(row_id) if row_id is not None else None

    def fetchone(self):
        if self._offset == len(self._rows):
            return None
        row = self._rows[self._offset]
        self._offset += 1
        return row

    def fetchall(self):
        rows = self._rows[self._offset:]
        self._offset = len(self._rows)
        return rows


class Connection:
    def __init__(self):
        db_url, self._token = read_env()
        self._origin = http_url_from_libsql(db_url).rstrip("/")
        self._host = urlsplit(self._origin).hostname
        if not self._token or not self._host:
            raise HranaHttpError("TURSO_DB_URL / TURSO_AUTH_TOKEN not configured")
        self._validate_origin(self._origin)
        self._url = self._origin
        self._baton = None
        self._transaction = False
        self._poisoned = False
        self._opener = urllib.request.build_opener(_NoRedirect())

    def _validate_origin(self, value):
        url = urlsplit(value)
        host = url.hostname or ""
        trusted = host == self._host or (
            (self._host or "").endswith(".turso.io") and host.endswith(".turso.io")
        )
        if (url.scheme != "https" or not trusted or url.username or url.password
                or url.port not in (None, 443) or url.path not in ("", "/")
                or url.query or url.fragment):
            raise HranaHttpError("unsafe Hrana stream routing URL")

    def _request(self, requests, *, closing=False):
        _refuse_pytest_pollution()
        payload = json.dumps({"baton": self._baton, "requests": requests}).encode()
        if len(payload) > MAX_REQUEST_BYTES:
            raise HranaHttpError("Hrana request exceeds bounded size")
        request = urllib.request.Request(
            self._url.rstrip("/") + "/v2/pipeline", data=payload, method="POST",
            headers={"Content-Type": "application/json", "Authorization": "Bearer " + self._token},
        )
        deadline = time.monotonic() + REQUEST_TIMEOUT
        with self._opener.open(request, timeout=REQUEST_TIMEOUT) as response:
            chunks, size = [], 0
            while True:
                if time.monotonic() >= deadline:
                    raise TimeoutError("Hrana response deadline exhausted")
                chunk = response.read1(min(65536, MAX_RESPONSE_BYTES + 1 - size))
                if not chunk:
                    break
                size += len(chunk)
                if size > MAX_RESPONSE_BYTES:
                    raise HranaHttpError("Hrana response exceeds bounded size")
                chunks.append(chunk)
        body = json.loads(b"".join(chunks))
        if not isinstance(body, dict) or "baton" not in body:
            raise HranaHttpError("invalid Hrana stream response")
        # Save the new baton even when an individual statement failed.
        baton = body["baton"]
        if baton is not None and (not isinstance(baton, str) or not baton):
            raise HranaHttpError("invalid Hrana stream baton")
        self._baton = baton
        routing = body.get("base_url")
        if routing is not None:
            self._validate_origin(routing)
            self._url = routing.rstrip("/")
        results = body.get("results")
        if (not isinstance(results, list) or len(results) != len(requests)
                or any(not isinstance(item, dict) for item in results)):
            raise HranaHttpError("incomplete Hrana pipeline response")
        if closing and self._baton is not None:
            raise HranaHttpError("Hrana stream remained open after close")
        if not closing and self._baton is None and results[0].get("type") != "error":
            raise TransportError("Hrana stream closed during transaction")
        return results

    def _discard(self):
        self._poisoned = True
        if self._baton is not None:
            try:
                self._request([{"type": "close"}], closing=True)
            except Exception:
                pass  # retain original failure; the server expires abandoned streams
        self._baton = None
        self._transaction = False

    def execute(self, sql, args=()):
        if self._poisoned:
            raise TransportError("Hrana stream unusable; open a fresh connection")
        verb = sql.strip().split(None, 1)[0].upper()
        beginning = verb == "BEGIN"
        ending = verb in ("COMMIT", "ROLLBACK")
        if beginning and self._transaction:
            raise HranaHttpError("transaction already open")
        if ending and not self._transaction:
            raise HranaHttpError("no transaction is active")
        closing = ending or not (self._transaction or beginning)
        requests = [{"type": "execute", "stmt": {"sql": sql, "args": [_encode_arg(a) for a in args], "want_rows": True}}]
        if closing:
            requests.append({"type": "close"})
        try:
            results = self._request(requests, closing=closing)
        except Exception as exc:
            self._discard()
            if isinstance(exc, HranaHttpError):
                raise
            raise TransportError(f"{type(exc).__name__}: {exc}") from exc
        self._transaction = not closing
        if closing:
            self._baton = None
            self._url = self._origin
        first = results[0]
        if first.get("type") == "error":
            error = first.get("error")
            if not isinstance(error, dict):
                error = {}
            self._discard()
            raise HranaHttpError(f"{error.get('code', 'SQL_ERROR')}: {error.get('message', 'statement failed')}")
        try:
            response = first["response"]
            if first.get("type") != "ok" or response.get("type") != "execute":
                raise HranaHttpError("unexpected Hrana execution response")
            if closing and (results[-1].get("type") != "ok"
                            or results[-1].get("response", {}).get("type") != "close"):
                raise HranaHttpError("Hrana stream close was not confirmed")
            cursor = _Cursor(response["result"])
            if verb == "INSERT" and cursor.lastrowid is None:
                raise TransportError("INSERT receipt missing last_insert_rowid")
            return cursor
        except Exception:
            self._discard()
            raise

    def execute_transaction(self, statements):
        """Queue all dependent SQL and cleanup before awaiting any receipt.

        Losing the response can obscure COMMIT, but cannot prevent the server
        from receiving rollback/close. Replay the entire idempotent operation.
        """
        if self._poisoned or self._transaction or self._baton is not None:
            raise TransportError("atomic transaction requires a fresh stream")
        statements = list(statements)
        if len(statements) + 3 > MAX_TRANSACTION_STEPS:
            raise HranaHttpError("atomic transaction exceeds bounded step count")
        steps = [{"stmt": {"sql": "BEGIN IMMEDIATE", "want_rows": True}}]
        for sql, args in statements:
            steps.append({
                "condition": {"type": "ok", "step": len(steps) - 1},
                "stmt": {"sql": sql, "args": [_encode_arg(a) for a in args],
                         "want_rows": True},
            })
        commit_index = len(steps)
        steps.append({"condition": {"type": "ok", "step": commit_index - 1},
                      "stmt": {"sql": "COMMIT", "want_rows": True}})
        steps.append({"condition": {"type": "not", "cond": {"type": "ok", "step": commit_index}},
                      "stmt": {"sql": "ROLLBACK", "want_rows": True}})
        try:
            replies = self._request([
                {"type": "batch", "batch": {"steps": steps}},
                {"type": "close"},
            ], closing=True)
            first = replies[0]
            if first.get("type") == "error":
                error = first.get("error") or {}
                raise HranaHttpError(f"{error.get('code', 'SQL_ERROR')}: {error.get('message', 'batch failed')}")
            response = first.get("response", {})
            if first.get("type") != "ok" or response.get("type") != "batch":
                raise TransportError("unexpected Hrana batch receipt")
            result = response.get("result", {})
            outcomes, errors = result.get("step_results"), result.get("step_errors")
            if (not isinstance(outcomes, list) or not isinstance(errors, list)
                    or len(outcomes) != len(steps) or len(errors) != len(steps)):
                raise TransportError("incomplete Hrana transaction receipt")
            # Preserve the first SQL failure, not a later rollback failure.
            for error in errors:
                if error is not None:
                    if not isinstance(error, dict):
                        raise TransportError("invalid Hrana step error")
                    raise HranaHttpError(f"{error.get('code', 'SQL_ERROR')}: {error.get('message', 'statement failed')}")
            if any(not isinstance(outcome, dict) for outcome in outcomes[:commit_index + 1]):
                raise TransportError("transaction statements or COMMIT were not confirmed")
            if outcomes[-1] is not None:
                raise TransportError("unexpected rollback after successful COMMIT")
            if (replies[-1].get("type") != "ok"
                    or replies[-1].get("response", {}).get("type") != "close"):
                raise TransportError("transaction close was not confirmed")
            cursors = [_Cursor(outcome) for outcome in outcomes[:commit_index + 1]]
            return cursors[1:commit_index]
        except Exception as exc:
            self._discard()
            if isinstance(exc, HranaHttpError):
                raise
            raise TransportError(f"{type(exc).__name__}: {exc}") from exc
        finally:
            self._url = self._origin

    def commit(self):
        self.execute("COMMIT")

    def rollback(self):
        if self._poisoned:
            return
        if self._transaction:
            self.execute("ROLLBACK")

    def close(self):
        self._discard()
