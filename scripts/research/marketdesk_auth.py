"""MarketDesk (marketdesk.ai) session: Firebase refresh-token exchange, password fallback, server login.

marketdesk.ai signs users in with FirebaseUI (project zerohedge-prod), then the landing script posts the
Firebase ID token to POST /api/user/login {F: idToken}; the server answers {success: true} and sets its
session cookie. This module does the same without a browser sign-in:

1. warm: the stored Playwright storage state is still signed in -> nothing to do
2. refresh token (rotated copy in the session dir wins over the MARKETDESK_FIREBASE_REFRESH_TOKEN seed)
   -> securetoken exchange -> ID token -> /api/user/login through the browser context's request API,
   so the session cookie lands in that context
3. THEMARKETEAR_EMAIL / THEMARKETEAR_PASSWORD -> signInWithPassword -> same login POST. The operator's
   account currently has only the google.com provider, so this path fails until a password is restored.

Errors never carry tokens, emails or passwords: only fixed text and Firebase's upper-case error codes.
"""
from __future__ import annotations

from datetime import datetime, timezone
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
REFRESH_ENV = "MARKETDESK_FIREBASE_REFRESH_TOKEN"
TIMEOUT = 20
LOGIN_TIMEOUT_MS = 30_000
NAV_TIMEOUT_MS = 30_000
MAX_RESPONSE = 1024 * 1024
# The API key is referrer-restricted to the zerohedge web origins; send what the browser would.
BROWSER_HEADERS = {"Referer": BASE_URL + "/", "Origin": BASE_URL}
_CODE_RE = re.compile(r"[A-Z][A-Z0-9_]{2,63}")
_PASSWORD_DISABLED = {"INVALID_PASSWORD", "INVALID_LOGIN_CREDENTIALS", "PASSWORD_LOGIN_DISABLED"}


class MarketDeskAuthError(RuntimeError):
    """Message never contains tokens, emails or passwords."""


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


def storage_state_path(env=os.environ) -> Path:
    return session_dir(env) / "storage.json"


def existing_storage_state(env=os.environ) -> str | None:
    """Path to pass as new_context(storage_state=...), or None. A corrupt file is quarantined."""
    path = storage_state_path(env)
    if not path.is_file() or path.is_symlink():
        return None
    try:
        state = json.loads(path.read_text())
        if not isinstance(state, dict) or not isinstance(state.get("cookies", []), list):
            raise ValueError("bad storage state")
    except (ValueError, OSError):
        try:
            os.replace(path, path.with_name(f"storage.corrupt-{int(datetime.now().timestamp())}.json"))
        except OSError:
            pass
        return None
    return str(path)


def persist_storage_state(context, env=os.environ) -> Path:
    path = storage_state_path(env)
    state = context.storage_state(indexed_db=True)
    _atomic_private_write(path, json.dumps(state).encode())
    return path


def page_state(html: str) -> str:
    """'landing' (FirebaseUI sign-in), 'app' (signed-in shell) or 'unknown'."""
    from research.marketdesk_scrape import SELECTORS, parse_html

    root = parse_html(html)
    if root.select_one(SELECTORS["landing"]) is not None:
        return "landing"
    if root.select_one(SELECTORS["app_shell"]) is not None:
        return "app"
    return "unknown"


def is_authenticated(page) -> bool:
    from research.marketdesk_scrape import SELECTORS

    page.goto(BASE_URL + "/", wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
    try:
        page.wait_for_selector(f"{SELECTORS['landing']}, {SELECTORS['app_shell']}", timeout=NAV_TIMEOUT_MS)
    except Exception:
        pass
    return page_state(page.content()) == "app"


def _server_login(context, id_token: str) -> None:
    try:
        response = context.request.post(LOGIN_URL, data={"F": id_token}, timeout=LOGIN_TIMEOUT_MS)
    except Exception:
        # Playwright's message carries a call log with cookie / set-cookie headers: never keep it.
        raise MarketDeskAuthError("login_transport") from None
    try:
        payload = response.json()
    except Exception:
        payload = None
    if response.status != 200 or not isinstance(payload, dict) or payload.get("success") is not True:
        raise MarketDeskAuthError(f"login rejected (HTTP {response.status})")


def open_session(context, page, *, env=os.environ, post=None) -> None:
    """Make ``context`` signed in to marketdesk.ai or raise MarketDeskAuthError."""
    if is_authenticated(page):
        return

    failures: list[str] = []
    id_token = None
    candidates = []
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
        break

    if id_token is None:
        email = (env.get("THEMARKETEAR_EMAIL") or "").strip()
        password = (env.get("THEMARKETEAR_PASSWORD") or "").strip()
        if email and password:
            try:
                id_token = password_id_token(email, password, post=post)
            except MarketDeskAuthError as exc:
                failures.append(str(exc))

    if id_token is None:
        if not failures:
            raise MarketDeskAuthError("no MarketDesk credentials configured")
        raise MarketDeskAuthError("MarketDesk sign-in failed: " + "; ".join(failures))

    _server_login(context, id_token)
    if not is_authenticated(page):
        raise MarketDeskAuthError("not authenticated after login")
