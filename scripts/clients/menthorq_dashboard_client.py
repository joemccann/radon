"""Server-side client for MenthorQ's authenticated dashboard data API.

This integration is intentionally isolated from ``menthorq_client.py``.  That
client authenticates against the legacy ``menthorq.com`` WordPress site; the
dashboard establishes its Cognito/NextAuth session on ``dashboard.menthorq.io``
through a redirect-mediated WordPress login.

Authentication sources, in precedence order:

1. ``MENTHORQ_DASHBOARD_ACCESS_TOKEN`` (or an explicit constructor argument),
   intended as a short-lived operational override.
2. A dedicated Playwright storage-state file whose NextAuth cookie is exchanged
   through ``/api/auth/session`` for a current access token.
3. The existing WordPress credentials through the dashboard → WordPress →
   dashboard redirect flow. The resulting cross-domain session is persisted
   only in the dedicated dashboard storage state.

No response body, token, cookie, or upstream exception text is included in a
public exception message.
"""

from __future__ import annotations

import base64
import binascii
import json
import math
import os
import re
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

from utils.env_loader import load_env_file


_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
load_env_file(_PROJECT_ROOT / ".env")
DEFAULT_STORAGE_STATE_PATH = (
    _PROJECT_ROOT
    / "data"
    / "menthorq_dashboard"
    / "menthorq_dashboard_storage_state.json"
)
# Cookies that actually gate authentication; mirrors
# monitor_daemon/handlers/menthorq_session_check.py.
_AUTH_COOKIE_PREFIXES = ("cognito", "__Secure-authjs.session-token")
SESSION_URL = "https://dashboard.menthorq.io/api/auth/session"
BOOTSTRAP_URL = "https://dashboard.menthorq.io/en/options/exposure?symbol=MU"
API_ROOT = "https://gateway.menthorq.io/clickhouse-api/api/web/v1"
# A real Chrome UA: the WAF in front of menthorq.com serves a bot
# challenge to Playwright's default "HeadlessChrome" agent, which is
# why the automated re-login silently failed (2026-08-07).
_BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
)
DEFAULT_TIMEOUT_SECONDS = 20.0
# web/app/api/options/_shared.ts OPTIONS_PROXY_TIMEOUT_MS is 50_000.
# Request-path auth must finish under that so the operator gets FastAPI 503,
# not a proxy 504 (2026-08-20 /options/net-gex).
REQUEST_PATH_AUTH_BUDGET_SECONDS = 40.0
# The WordPress -> Cognito -> dashboard chain measured ~15s on the VPS.
DEFAULT_LOGIN_TIMEOUT_SECONDS = 25.0
_AUTH_FAILURE_EMBARGO_SECONDS = 300.0
_auth_embargo_until = 0.0
_auth_embargo_lock = threading.Lock()


def _auth_embargo_active() -> bool:
    with _auth_embargo_lock:
        return time.monotonic() < _auth_embargo_until


def _trip_auth_embargo() -> None:
    global _auth_embargo_until
    with _auth_embargo_lock:
        _auth_embargo_until = time.monotonic() + _AUTH_FAILURE_EMBARGO_SECONDS


def _reset_auth_embargo_for_tests() -> None:
    global _auth_embargo_until
    with _auth_embargo_lock:
        _auth_embargo_until = 0.0


_FREQUENCIES = {"eod", "intraday"}
_SYMBOL_RE = re.compile(r"^[A-Z][A-Z0-9.-]{0,9}$")
_CELL_FIELDS = (
    "strike_idx",
    "expiration_idx",
    "net_gex",
    "abs_gex",
    "net_dex",
    "abs_dex",
    "oi_call",
    "oi_put",
)
_LEVEL_FIELDS = (
    ("hvl", "HVL"),
    ("call_resistance", "CR"),
    ("put_support", "PS"),
    ("call_resistance_0dte", "CR 0DTE"),
    ("put_support_0dte", "PS 0DTE"),
    ("max_1d", "1D Max"),
    ("min_1d", "1D Min"),
)


class MenthorQDashboardError(Exception):
    """Base class for sanitized dashboard failures."""


class MenthorQDashboardAuthError(MenthorQDashboardError):
    """A dashboard access token could not be obtained or is expired."""


class MenthorQDashboardTimeoutError(MenthorQDashboardError):
    """The dashboard API did not respond within the configured timeout."""


class MenthorQDashboardUpstreamError(MenthorQDashboardError):
    """The dashboard API returned an unavailable/error response."""


class MenthorQDashboardPayloadError(MenthorQDashboardError):
    """The dashboard API returned a structurally invalid payload."""


def _finite_number(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("not numeric")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("not finite")
    return result


def _jwt_expiry(token: str) -> float | None:
    """Read an unsigned JWT expiry only to reject obviously stale tokens.

    Signature verification belongs to the upstream API.  Opaque operational
    tokens remain supported by returning ``None`` when the token is not JWT
    shaped.
    """

    try:
        payload_segment = token.split(".")[1]
        padded = payload_segment + "=" * (-len(payload_segment) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded).decode())
        expiry = payload.get("exp")
        return float(expiry) if expiry is not None else None
    except (
        IndexError,
        TypeError,
        ValueError,
        UnicodeDecodeError,
        binascii.Error,
        json.JSONDecodeError,
    ):
        return None


def _storage_state_expired(path: Path) -> bool:
    """True when the jar's auth cookies have provably lapsed.

    Only auth cookies count — a jar is full of analytics cookies that
    expire constantly and mean nothing. Cookies without an expiry
    (``expires <= 0``, i.e. session cookies) carry no deadline, so a jar
    made only of those is NEVER reported expired: absence of metadata is
    not evidence of expiry. Any read/parse problem also returns False so
    the browser path stays authoritative — this is a fast NEGATIVE check,
    never a new way to fail.
    """
    try:
        payload = json.loads(path.read_text())
        cookies = payload.get("cookies") if isinstance(payload, dict) else None
        if not isinstance(cookies, list):
            return False
        deadlines = [
            cookie["expires"]
            for cookie in cookies
            if isinstance(cookie, dict)
            and isinstance(cookie.get("name"), str)
            and cookie["name"].startswith(_AUTH_COOKIE_PREFIXES)
            and isinstance(cookie.get("expires"), (int, float))
            and cookie["expires"] > 0
        ]
        return bool(deadlines) and min(deadlines) <= time.time()
    except Exception:  # noqa: BLE001 — never let the fast path add failures
        return False


def _session_expired(value: Any) -> bool:
    if value is None:
        return False
    try:
        numeric = float(value)
        if numeric > 10_000_000_000:
            numeric /= 1000
        return numeric <= time.time()
    except (TypeError, ValueError):
        pass
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed.timestamp() <= time.time()
        except ValueError:
            return True
    return True


class MenthorQDashboardClient:
    """Fetch and normalize options exposure data without client-side secrets."""

    def __init__(
        self,
        *,
        access_token: str | None = None,
        storage_state_path: str | Path | None = None,
        http_session: Any | None = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        username: str | None = None,
        password: str | None = None,
    ) -> None:
        env_token = os.environ.get("MENTHORQ_DASHBOARD_ACCESS_TOKEN", "").strip()
        self._access_token = (access_token or env_token).strip() or None
        # An EXPLICIT access_token= is a caller assertion ("use this one"),
        # so an expired one fails loudly. The ENV override is ambient
        # operational config and degrades to the jar instead.
        self._token_is_explicit = bool((access_token or "").strip())
        configured_path = os.environ.get("MENTHORQ_DASHBOARD_STORAGE_STATE", "").strip()
        self._storage_state_path = Path(
            storage_state_path or configured_path or DEFAULT_STORAGE_STATE_PATH
        )
        self._http = http_session or requests.Session()
        self._timeout_seconds = max(1.0, float(timeout_seconds))
        self._login_timeout_seconds = min(
            max(self._timeout_seconds, DEFAULT_LOGIN_TIMEOUT_SECONDS),
            REQUEST_PATH_AUTH_BUDGET_SECONDS,
        )
        self._username = (username or os.environ.get("MENTHORQ_USER", "")).strip() or None
        self._password = (password or os.environ.get("MENTHORQ_PASS", "")).strip() or None

    def fetch_exposure(self, symbol: str, frequency: str = "eod") -> dict[str, Any]:
        """Fetch one uncached symbol/frequency cube plus its EOD level overlay."""

        if not _SYMBOL_RE.fullmatch(symbol):
            raise ValueError("symbol must be uppercase and provider-safe")
        if frequency not in _FREQUENCIES:
            raise ValueError("frequency must be eod or intraday")

        token = self._resolve_access_token()
        exposure_url = (
            f"{API_ROOT}/options/net-gex-by-expiration/{symbol}?frequency={frequency}"
        )
        levels_url = f"{API_ROOT}/gamma-levels/{symbol}/eod"
        exposure = self._request_json(exposure_url, token)
        levels = self._request_json(levels_url, token)
        return self._normalize(symbol, frequency, exposure, levels)

    def _resolve_access_token(self) -> str:
        if _auth_embargo_active():
            raise MenthorQDashboardAuthError("dashboard authentication is unavailable")
        try:
            return self._resolve_access_token_uncached()
        except MenthorQDashboardAuthError:
            _trip_auth_embargo()
            raise

    def _resolve_access_token_uncached(self) -> str:
        token = self._access_token
        # An operational override is a SHORTCUT, never a dead end: once it
        # lapses, fall through to the self-refreshing jar instead of failing
        # with a perfectly good session on disk (2026-08-07 — the 12h stopgap
        # token would otherwise have re-broken /options/net-gex on expiry).
        if token is not None and not self._token_is_explicit:
            expiry = _jwt_expiry(token)
            if expiry is not None and expiry <= time.time():
                token = None
        if token is None:
            if self._storage_state_path.is_file():
                self._ensure_private_storage_state()
                # A provably expired jar cannot mint a token, and launching
                # chromium to learn that costs ~14s (2026-08-07: two such
                # launches made every failing request take ~28s). The jar's
                # own cookie expiries answer it for free.
                if _storage_state_expired(self._storage_state_path):
                    token = None
                else:
                    try:
                        token = self._token_from_storage_state()
                    except MenthorQDashboardAuthError:
                        token = None
            if token is None:
                if not self._username or not self._password:
                    raise MenthorQDashboardAuthError("dashboard authentication is unavailable")
                token = self._bootstrap_dashboard_session()

        expiry = _jwt_expiry(token)
        if expiry is not None and expiry <= time.time():
            raise MenthorQDashboardAuthError("dashboard authentication is unavailable")
        return token

    def _ensure_private_storage_state(self) -> None:
        try:
            self._storage_state_path.chmod(0o600)
        except OSError as exc:
            raise MenthorQDashboardAuthError(
                "dashboard authentication is unavailable"
            ) from exc

    def _token_from_storage_state(self) -> str:
        """Exchange a dedicated dashboard cookie jar for a current token."""

        browser = None
        context = None
        try:
            from playwright.sync_api import sync_playwright

            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(
                    headless=True,
                    args=["--disable-blink-features=AutomationControlled"],
                )
                # Same WAF constraint as the bootstrap: without a real UA the
                # challenge returns HTML and response.json() raises. This is
                # the DURABLE path — it runs on every call — so a jar that
                # cannot be spent here is as broken as no jar at all.
                context = browser.new_context(
                    storage_state=str(self._storage_state_path),
                    user_agent=_BROWSER_USER_AGENT,
                    viewport={"width": 1440, "height": 900},
                )
                response = context.request.get(
                    SESSION_URL,
                    timeout=int(self._timeout_seconds * 1000),
                )
                if not response.ok:
                    raise MenthorQDashboardAuthError(
                        "dashboard authentication is unavailable"
                    )
                payload = response.json()
                if not isinstance(payload, dict):
                    raise MenthorQDashboardAuthError(
                        "dashboard authentication is unavailable"
                    )
                token = payload.get("accessToken")
                if not isinstance(token, str) or not token.strip():
                    raise MenthorQDashboardAuthError(
                        "dashboard authentication is unavailable"
                    )
                if _session_expired(payload.get("expiresAt")):
                    raise MenthorQDashboardAuthError(
                        "dashboard authentication is unavailable"
                    )
                self._persist_dashboard_storage_state(context)
                return token.strip()
        except MenthorQDashboardAuthError:
            raise
        except Exception as exc:
            raise MenthorQDashboardAuthError(
                "dashboard authentication is unavailable"
            ) from exc
        finally:
            if context is not None:
                try:
                    context.close()
                except Exception:
                    pass
            if browser is not None:
                try:
                    browser.close()
                except Exception:
                    pass

    def _bootstrap_dashboard_session(self) -> str:
        """Use the WordPress redirect login to establish a dashboard session.

        MenthorQ routes an unauthenticated dashboard request to its WordPress
        login form and returns through Cognito. Credentials are only entered in
        the server-side browser context; the resulting storage state is kept in
        the dedicated dashboard path, never sent to the web client.
        """

        browser = None
        context = None
        try:
            from playwright.sync_api import sync_playwright

            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(
                    headless=True,
                    args=["--disable-blink-features=AutomationControlled"],
                )
                # MenthorQ sits behind AWS WAF (the jar carries an
                # aws-waf-token). Playwright's default UA announces
                # "HeadlessChrome" and the login silently never completes —
                # the 2026-08-07 outage. A real UA completes it headlessly.
                context = browser.new_context(
                    user_agent=_BROWSER_USER_AGENT,
                    viewport={"width": 1440, "height": 900},
                )
                page = context.new_page()
                page.goto(
                    BOOTSTRAP_URL,
                    wait_until="domcontentloaded",
                    timeout=int(self._login_timeout_seconds * 1000),
                )
                username = page.locator('input[name="log"]')
                password = page.locator('input[name="pwd"]')
                submit = page.locator('input[name="wp-submit"]')
                login_timeout_ms = int(self._timeout_seconds * 1000)
                username.wait_for(state="visible", timeout=login_timeout_ms)
                password.wait_for(state="visible", timeout=login_timeout_ms)
                submit.wait_for(state="visible", timeout=login_timeout_ms)
                if username.count() != 1 or password.count() != 1 or submit.count() != 1:
                    raise MenthorQDashboardAuthError(
                        "dashboard authentication is unavailable"
                    )
                username.fill(self._username or "")
                password.fill(self._password or "")
                submit.click()

                # WordPress -> Cognito -> dashboard. /api/auth/session returns
                # null until that chain LANDS, so waiting for the destination
                # is what makes the poll below meaningful rather than a race.
                try:
                    page.wait_for_url(
                        "**dashboard.menthorq.io**",
                        timeout=int(self._login_timeout_seconds * 1000),
                    )
                except Exception as exc:
                    raise MenthorQDashboardAuthError(
                        "dashboard authentication is unavailable"
                    ) from exc

                deadline = time.monotonic() + self._timeout_seconds
                while time.monotonic() < deadline:
                    response = context.request.get(
                        SESSION_URL,
                        timeout=int(self._timeout_seconds * 1000),
                    )
                    if response.ok:
                        try:
                            payload = response.json()
                        except Exception:
                            payload = None
                        if isinstance(payload, dict):
                            token = payload.get("accessToken")
                            if (
                                isinstance(token, str)
                                and token.strip()
                                and not _session_expired(payload.get("expiresAt"))
                            ):
                                self._persist_dashboard_storage_state(context)
                                return token.strip()
                    page.wait_for_timeout(500)
                raise MenthorQDashboardAuthError("dashboard authentication is unavailable")
        except MenthorQDashboardAuthError:
            raise
        except Exception as exc:
            raise MenthorQDashboardAuthError(
                "dashboard authentication is unavailable"
            ) from exc
        finally:
            if context is not None:
                try:
                    context.close()
                except Exception:
                    pass
            if browser is not None:
                try:
                    browser.close()
                except Exception:
                    pass

    def _persist_dashboard_storage_state(self, context: Any) -> None:
        try:
            self._storage_state_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            self._storage_state_path.parent.chmod(0o700)
            context.storage_state(path=str(self._storage_state_path))
            self._ensure_private_storage_state()
        except OSError as exc:
            raise MenthorQDashboardAuthError(
                "dashboard authentication is unavailable"
            ) from exc

    def _request_json(self, url: str, token: str) -> dict[str, Any]:
        try:
            response = self._http.get(
                url,
                headers={
                    "Accept": "application/json",
                    "Authorization": f"Bearer {token}",
                },
                timeout=self._timeout_seconds,
            )
        except requests.Timeout as exc:
            raise MenthorQDashboardTimeoutError("dashboard provider timed out") from exc
        except requests.RequestException as exc:
            raise MenthorQDashboardUpstreamError(
                "dashboard provider is unavailable"
            ) from exc

        if response.status_code in {401, 403}:
            raise MenthorQDashboardAuthError("dashboard authentication is unavailable")
        if response.status_code < 200 or response.status_code >= 300:
            raise MenthorQDashboardUpstreamError("dashboard provider is unavailable")
        try:
            payload = response.json()
        except Exception as exc:
            raise MenthorQDashboardPayloadError("invalid exposure payload") from exc
        if not isinstance(payload, dict):
            raise MenthorQDashboardPayloadError("invalid exposure payload")
        return payload

    @staticmethod
    def _normalize(
        symbol: str,
        frequency: str,
        exposure: dict[str, Any],
        level_payload: dict[str, Any],
    ) -> dict[str, Any]:
        try:
            if exposure.get("ticker") != symbol or exposure.get("frequency") != frequency:
                raise ValueError("provider identity mismatch")
            source_time = exposure["timestamp"]
            if not isinstance(source_time, str) or not source_time:
                raise ValueError("missing timestamp")
            spot = _finite_number(exposure["spot_price"])
            if spot <= 0:
                raise ValueError("invalid spot")

            raw_strikes = exposure["strikes"]
            if not isinstance(raw_strikes, list) or not raw_strikes:
                raise ValueError("invalid strikes")
            strikes = [_finite_number(value) for value in raw_strikes]

            raw_expirations = exposure["expirations"]
            if not isinstance(raw_expirations, list) or not raw_expirations:
                raise ValueError("invalid expirations")
            expirations = []
            for expiration in raw_expirations:
                if not isinstance(expiration, dict):
                    raise ValueError("invalid expiration")
                date = expiration.get("expiration_date")
                dte = expiration.get("dte")
                if not isinstance(date, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
                    raise ValueError("invalid expiration date")
                if isinstance(dte, bool) or not isinstance(dte, int) or dte < 0:
                    raise ValueError("invalid dte")
                expirations.append({"expiration_date": date, "dte": dte})

            raw_cells = exposure["cells"]
            if not isinstance(raw_cells, dict):
                raise ValueError("invalid cells")
            arrays = {}
            for field in _CELL_FIELDS:
                values = raw_cells.get(field)
                if not isinstance(values, list):
                    raise ValueError("missing cell field")
                arrays[field] = values
            lengths = {len(values) for values in arrays.values()}
            if len(lengths) != 1:
                raise ValueError("parallel array length mismatch")

            strike_idx: list[int] = []
            expiration_idx: list[int] = []
            pairs: set[tuple[int, int]] = set()
            for strike_value, expiration_value in zip(
                arrays["strike_idx"], arrays["expiration_idx"]
            ):
                if isinstance(strike_value, bool) or not isinstance(strike_value, int):
                    raise ValueError("invalid strike index")
                if isinstance(expiration_value, bool) or not isinstance(expiration_value, int):
                    raise ValueError("invalid expiration index")
                if not 0 <= strike_value < len(strikes):
                    raise ValueError("strike index out of range")
                if not 0 <= expiration_value < len(expirations):
                    raise ValueError("expiration index out of range")
                pair = (strike_value, expiration_value)
                if pair in pairs:
                    raise ValueError("duplicate cell index")
                pairs.add(pair)
                strike_idx.append(strike_value)
                expiration_idx.append(expiration_value)

            numeric = {
                field: [_finite_number(value) for value in arrays[field]]
                for field in _CELL_FIELDS[2:]
            }
            for field in ("abs_dex", "oi_call", "oi_put"):
                if any(value < 0 for value in numeric[field]):
                    raise ValueError("negative absolute exposure or interest")

            if level_payload.get("ticker") not in {None, symbol}:
                raise ValueError("level provider identity mismatch")
            levels = []
            for key, label in _LEVEL_FIELDS:
                raw_value = level_payload.get(key)
                value = None if raw_value is None else _finite_number(raw_value)
                levels.append({"key": key, "label": label, "value": value})
        except (KeyError, TypeError, ValueError) as exc:
            raise MenthorQDashboardPayloadError("invalid exposure payload") from exc

        return {
            "schema_version": 1,
            "symbol": symbol,
            "source": "menthorq_dashboard",
            "source_time": source_time,
            "fetched_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "frequency": frequency,
            "spot": spot,
            "strikes": strikes,
            "expirations": expirations,
            "cells": {
                "strike_idx": strike_idx,
                "expiration_idx": expiration_idx,
                # MenthorQ's GEX cube arrives already denominated in plain USD
                # (same denomination as its DEX arrays), so normalization at
                # this provider boundary is a pass-through — no scaling.
                "net_gex": list(numeric["net_gex"]),
                # The live provider occasionally emits a negative value in its
                # ``abs_gex`` array. Preserve the metric's documented absolute
                # semantics at this trusted boundary rather than rejecting the
                # entire otherwise-valid exposure cube.
                "abs_gex": [abs(value) for value in numeric["abs_gex"]],
                "net_dex": numeric["net_dex"],
                "abs_dex": numeric["abs_dex"],
                "oi_call": numeric["oi_call"],
                "oi_put": numeric["oi_put"],
            },
            "levels": levels,
            "units": {
                "spot": "usd_per_share",
                "strike": "usd_per_share",
                "net_gex": "usd_per_1pct_move",
                "abs_gex": "usd_per_1pct_move",
                "net_dex": "usd",
                "abs_dex": "usd",
                "oi_call": "contracts",
                "oi_put": "contracts",
            },
            "complete": all(level["value"] is not None for level in levels),
        }
