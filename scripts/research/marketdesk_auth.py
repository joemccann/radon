"""MarketDesk (marketdesk.ai) session over plain HTTP: Firebase token -> server login -> cookie session.

marketdesk.ai signs users in with FirebaseUI (project zerohedge-prod), then posts the Firebase ID token
to POST /api/user/login {F: idToken}; the server answers {success: true} and sets the httpOnly session
cookie ``A`` (about 60 days). Every API call and PDF download after that rides on the cookie. Verified
live 2026-10-26: no browser is needed.

ID token sources, in order:
1. refresh token (rotated copy in the session dir wins over the MARKETDESK_FIREBASE_REFRESH_TOKEN seed)
   -> securetoken exchange
2. THEMARKETEAR_EMAIL / THEMARKETEAR_PASSWORD -> signInWithPassword. The operator's account has only
   the google.com provider, so this path fails until a password is restored.

A signed-out API call answers HTTP 200 {"success": false}; a signed-out /files/* download answers 302
elsewhere. Both trigger one re-login and one retry. A signed-in /files/<id>/blob answers 302 to
https://tsN.marketdesk.ai/files/<id>/preview (the file servers share the .marketdesk.ai cookie); that
one hop is followed, nothing else. Errors never carry tokens, cookies, emails or passwords.
"""
from __future__ import annotations

from datetime import datetime, timezone
from http.cookiejar import CookieJar
import json
import os
from pathlib import Path
import re
import tempfile
import urllib.error
import urllib.parse
import urllib.request

BASE_URL = "https://marketdesk.ai"
FIREBASE_API_KEY = "AIzaSyCf8fXJkGM9Jgtzl6sdaMLe_ipwtmWdvNc"  # public web client key, zerohedge-prod  # gitleaks:allow
REFRESH_URL = "https://securetoken.googleapis.com/v1/token?key=" + FIREBASE_API_KEY
PASSWORD_URL = ("https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword?key="
                + FIREBASE_API_KEY)
LOGIN_URL = BASE_URL + "/api/user/login"
LIMITS_PATH = "/api/user/limits"
REFRESH_ENV = "MARKETDESK_FIREBASE_REFRESH_TOKEN"
TIMEOUT = 20
DOWNLOAD_TIMEOUT = 60
MAX_RESPONSE = 4 * 1024 * 1024
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/155.0 Safari/537.36"
# The API key is referrer-restricted to the zerohedge web origins; send what the browser would.
BROWSER_HEADERS = {"Referer": BASE_URL + "/", "Origin": BASE_URL}
_CODE_RE = re.compile(r"[A-Z][A-Z0-9_]{2,63}")
_PASSWORD_DISABLED = {"INVALID_PASSWORD", "INVALID_LOGIN_CREDENTIALS", "PASSWORD_LOGIN_DISABLED"}
# The account's PDF preview allowance is spent; marketdesk.ai resets it on its own schedule.
PREVIEW_QUOTA = "preview_quota"
TRANSIENT_CODES = frozenset({"unauthenticated", "http_transport", "http_429", PREVIEW_QUOTA})
FILE_SERVER_RE = re.compile(r"ts\d{1,2}\.marketdesk\.ai")
REDIRECTS = (301, 302, 303, 307, 308)


class MarketDeskAuthError(RuntimeError):
    """Sign-in failed. Message never contains tokens, emails or passwords."""


class MarketDeskError(RuntimeError):
    """Short machine-readable reason ('unauthenticated', 'http_404', 'too_large', ...). Never a URL or body."""

    @property
    def transient(self) -> bool:
        """True when a retry can succeed without a code change; such failures spend no attempt."""
        code = str(self)
        return code in TRANSIENT_CODES or bool(re.fullmatch(r"http_5\d\d", code))


def _research_dir(env) -> Path:
    return Path(env.get("RADON_RESEARCH_DIR") or "/var/lib/radon/research")


def session_dir(env=os.environ) -> Path:
    path = _research_dir(env) / "marketdesk"
    if path.is_symlink():
        raise MarketDeskAuthError("session directory must not be a symlink")
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.is_symlink() or not path.is_dir():
        raise MarketDeskAuthError("invalid session directory")
    os.chmod(path, 0o700)
    return path


def _atomic_private_write(path: Path, data: bytes) -> None:
    if path.is_symlink():
        raise MarketDeskAuthError("refusing to write through a symlink")
    fd, temporary = tempfile.mkstemp(prefix=".md-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as out:
            out.write(data)
            out.flush()
            os.fsync(out.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


# ---- Firebase REST -------------------------------------------------------------------------

def _default_post(url: str, body: bytes, headers: dict) -> tuple[int, bytes]:
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            return response.status, response.read(MAX_RESPONSE)
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, exc.read(MAX_RESPONSE)
        except OSError:
            return exc.code, b""
    except (urllib.error.URLError, TimeoutError, OSError):
        raise MarketDeskAuthError("Firebase transport unavailable or timed out") from None


def _call(post, url, body, headers) -> tuple[int, dict, str | None]:
    status, raw = (post or _default_post)(url, body, {**BROWSER_HEADERS, **headers})
    try:
        payload = json.loads(raw or b"{}")
    except (ValueError, TypeError):
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    code = None
    error = payload.get("error")
    message = error.get("message") if isinstance(error, dict) else None
    if isinstance(message, str):
        head = message.split(":")[0].strip()
        code = head if _CODE_RE.fullmatch(head) else "UNRECOGNIZED"
    return status, payload, code


def exchange_refresh_token(refresh_token: str, *, post=None) -> tuple[str, str]:
    """Return (id_token, refresh_token); Firebase may rotate the refresh token."""
    if not refresh_token or not refresh_token.strip():
        raise MarketDeskAuthError("refresh token missing")
    refresh_token = refresh_token.strip()
    body = urllib.parse.urlencode({"grant_type": "refresh_token", "refresh_token": refresh_token}).encode()
    status, payload, code = _call(post, REFRESH_URL, body, {"Content-Type": "application/x-www-form-urlencoded"})
    id_token = payload.get("id_token")
    if status != 200 or not isinstance(id_token, str) or not id_token:
        raise MarketDeskAuthError(f"refresh token rejected ({code or f'HTTP {status}'})")
    rotated = payload.get("refresh_token")
    return id_token, rotated if isinstance(rotated, str) and rotated else refresh_token


def password_id_token(email: str, password: str, *, post=None) -> str:
    if not email or not password:
        raise MarketDeskAuthError("password credentials missing")
    body = json.dumps({"email": email, "password": password, "returnSecureToken": True}).encode()
    status, payload, code = _call(post, PASSWORD_URL, body, {"Content-Type": "application/json"})
    id_token = payload.get("idToken")
    if status == 200 and isinstance(id_token, str) and id_token:
        return id_token
    if code in _PASSWORD_DISABLED:
        raise MarketDeskAuthError(f"password sign-in disabled ({code})")
    raise MarketDeskAuthError(f"password sign-in failed ({code or f'HTTP {status}'})")


# ---- refresh-token storage -----------------------------------------------------------------

def _token_file(env) -> Path:
    return session_dir(env) / "firebase.json"


def _stored_refresh_token(env) -> str | None:
    path = _token_file(env)
    if not path.is_file() or path.is_symlink():
        return None
    try:
        token = json.loads(path.read_text()).get("refresh_token")
    except (ValueError, OSError, AttributeError):
        return None
    return token.strip() if isinstance(token, str) and token.strip() else None


def _env_refresh_token(env) -> str | None:
    token = (env.get(REFRESH_ENV) or "").strip()
    return token or None


def current_refresh_token(env=os.environ) -> str | None:
    """The rotated token in the session dir wins; else the env seed."""
    return _stored_refresh_token(env) or _env_refresh_token(env)


def save_refresh_token(token: str, env=os.environ) -> None:
    if not token or not token.strip():
        raise MarketDeskAuthError("refusing to store an empty refresh token")
    record = {"refresh_token": token.strip(), "saved_at": datetime.now(timezone.utc).isoformat()}
    _atomic_private_write(_token_file(env), json.dumps(record).encode())


def obtain_id_token(env=os.environ, *, post=None) -> str:
    """A fresh Firebase ID token: rotated file, then env seed (a stale file must not block a new seed),
    then the password fallback."""
    failures: list[str] = []
    candidates: list[str] = []
    for token in (_stored_refresh_token(env), _env_refresh_token(env)):
        if token and token not in candidates:
            candidates.append(token)
    for token in candidates:
        try:
            id_token, rotated = exchange_refresh_token(token, post=post)
        except MarketDeskAuthError as exc:
            failures.append(str(exc))
            continue
        save_refresh_token(rotated, env)
        return id_token

    email = (env.get("THEMARKETEAR_EMAIL") or "").strip()
    password = (env.get("THEMARKETEAR_PASSWORD") or "").strip()
    if email and password:
        try:
            return password_id_token(email, password, post=post)
        except MarketDeskAuthError as exc:
            failures.append(str(exc))
    if not failures:
        raise MarketDeskAuthError("no MarketDesk credentials configured")
    raise MarketDeskAuthError("MarketDesk sign-in failed: " + "; ".join(failures))


# ---- cookie session ------------------------------------------------------------------------

class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """A redirect surfaces as HTTPError: on marketdesk.ai a 302 means the session is gone."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D102
        return None


def build_opener(jar: CookieJar | None = None):
    handlers = [_NoRedirect()]
    if jar is not None:
        handlers.insert(0, urllib.request.HTTPCookieProcessor(jar))
    return urllib.request.build_opener(*handlers)


class MarketDeskSession:
    """Cookie session on https://marketdesk.ai. ``opener`` and ``post`` are injectable for tests."""

    def __init__(self, env=os.environ, *, opener=None, post=None):
        self.env = env
        self.post = post
        self.jar = CookieJar()
        self.opener = opener or build_opener(self.jar)

    def _send(self, method: str, url: str, body: bytes | None, headers: dict, *, limit: int,
              timeout: int) -> tuple[int, bytes, str, str]:
        """(status, body, content type, Location). HTTP errors and redirects come back as a status."""
        request = urllib.request.Request(url, data=body, method=method,
                                         headers={"User-Agent": USER_AGENT, **BROWSER_HEADERS, **headers})
        try:
            with self.opener.open(request, timeout=timeout) as response:
                declared = str(response.headers.get("Content-Length", "") or "").strip()
                if declared.isdigit() and int(declared) > limit:
                    raise MarketDeskError("too_large")
                data = response.read(limit + 1)
                content_type = str(response.headers.get("Content-Type", "") or "")
                status = response.status
        except urllib.error.HTTPError as exc:
            location = str((exc.headers or {}).get("Location", "") or "")
            exc.close()
            return exc.code, b"", "", location
        except MarketDeskError:
            raise
        except (urllib.error.URLError, TimeoutError, OSError, ValueError):
            raise MarketDeskError("http_transport") from None
        if len(data) > limit:
            raise MarketDeskError("too_large")
        return status, data, content_type.split(";")[0].strip().lower(), ""

    @staticmethod
    def _url(path: str) -> str:
        if not isinstance(path, str) or not path.startswith("/") or path.startswith("//"):
            raise MarketDeskError("bad_path")
        return BASE_URL + path

    def login(self, id_token: str | None = None) -> None:
        token = id_token or obtain_id_token(self.env, post=self.post)
        try:
            status, data, _, _ = self._send("POST", LOGIN_URL, json.dumps({"F": token}).encode(),
                                         {"Content-Type": "application/json"}, limit=MAX_RESPONSE, timeout=TIMEOUT)
        except MarketDeskError:
            raise MarketDeskAuthError("login_transport") from None
        try:
            payload = json.loads(data or b"null")
        except ValueError:
            payload = None
        if status != 200 or not isinstance(payload, dict) or payload.get("success") is not True:
            raise MarketDeskAuthError(f"login rejected (HTTP {status})")

    def api(self, method: str, path: str, body=None):
        """JSON API call. A signed-out answer ({"success": false}) re-logs in once and retries."""
        url = self._url(path)
        data = None if body is None else json.dumps(body).encode()
        headers = {"Accept": "application/json"}
        if data is not None:
            headers["Content-Type"] = "application/json"
        for attempt in (1, 2):
            status, raw, _, _ = self._send(method, url, data, headers, limit=MAX_RESPONSE, timeout=TIMEOUT)
            if status != 200:
                raise MarketDeskError(f"http_{status}")
            try:
                payload = json.loads(raw)
            except ValueError:
                raise MarketDeskError("bad_response") from None
            if isinstance(payload, dict) and payload.get("success") is False:
                if attempt == 1:
                    self._relogin()
                    continue
                raise MarketDeskError("unauthenticated")
            return payload
        raise MarketDeskError("unauthenticated")  # unreachable; keeps type checkers honest

    @staticmethod
    def _file_server_url(location: str, path: str) -> str | None:
        """The signed-in hop: https://tsN.marketdesk.ai/files/<same id>/preview, else None."""
        target = urllib.parse.urlsplit(urllib.parse.urljoin(BASE_URL + path, location))
        match = re.fullmatch(r"/files/([A-Za-z0-9]{8,16})/blob", path)
        if (target.scheme != "https" or target.port not in (None, 443) or target.username or target.password
                or not FILE_SERVER_RE.fullmatch(target.hostname or "") or match is None
                or target.path != f"/files/{match.group(1)}/preview"):
            return None
        return urllib.parse.urlunsplit(target)

    def blob(self, path: str, *, limit: int) -> tuple[bytes, str]:
        """Binary GET (PDF): one allowlisted hop to the file server. Any other redirect or a 401/403
        re-logs in once and retries."""
        url = self._url(path)
        for attempt in (1, 2):
            status, data, content_type, location = self._send("GET", url, None, {}, limit=limit,
                                                              timeout=DOWNLOAD_TIMEOUT)
            hop = self._file_server_url(location, path) if status in REDIRECTS else None
            if hop is not None:
                status, data, content_type, _ = self._send("GET", hop, None, {}, limit=limit,
                                                           timeout=DOWNLOAD_TIMEOUT)
            if status in (*REDIRECTS, 401, 403):
                if attempt == 1:
                    self._relogin()
                    continue
                if self._same_site(location, path) and not self.previews_available():
                    raise MarketDeskError(PREVIEW_QUOTA)
                raise MarketDeskError("unauthenticated")
            if status != 200:
                raise MarketDeskError(f"http_{status}")
            return data, content_type
        raise MarketDeskError("unauthenticated")

    @staticmethod
    def _same_site(location: str, path: str) -> bool:
        """A bounce back into marketdesk.ai itself (a spent allowance sends /blob to /files/<id>)."""
        target = urllib.parse.urlsplit(urllib.parse.urljoin(BASE_URL + path, location))
        return bool(location) and target.scheme == "https" and target.hostname == "marketdesk.ai"

    def previews_available(self) -> bool:
        """False only when /api/user/limits says the account's PDF preview allowance is spent."""
        limits = self.api("GET", LIMITS_PATH)
        return not (isinstance(limits, dict) and limits.get("previews") is False)

    def _relogin(self) -> None:
        try:
            self.login()
        except MarketDeskAuthError:
            raise MarketDeskError("unauthenticated") from None


def open_session(env=os.environ, *, opener=None, post=None) -> MarketDeskSession:
    """A signed-in session or MarketDeskAuthError."""
    session = MarketDeskSession(env, opener=opener, post=post)
    session.login()
    return session
