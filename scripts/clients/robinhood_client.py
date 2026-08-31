"""Robinhood trading-MCP client — READ-ONLY market-data failover.

Robinhood's only official agent surface is the Trading MCP server at
https://agent.robinhood.com/mcp/trading (OAuth 2.1 + PKCE, Streamable HTTP
JSON-RPC). There is no official equities REST SDK, and the unofficial
wrappers (robin-stocks, private api.robinhood.com scrapers) are off-limits.

Radon's use of this surface is deliberately narrow:

  - Rank: below IB, UW and the official Cboe feeds; ABOVE Yahoo. A ladder
    that used to end at Yahoo tries Robinhood first when configured.
  - READ-ONLY. Execution stays on Interactive Brokers. ``call_tool`` refuses
    anything outside the documented read-tool allowlist, so a place_* /
    cancel_* call can never leave this process even if a caller asks.
  - No dark pool, OTC, sweeps, GEX, or vol surface: Robinhood does not
    serve them. The option surface was probed live (2026-08-30 tool dump,
    67 tools): ``get_option_quotes`` takes ``instrument_ids`` (UUIDs) and
    returns real-time quotes plus the official prior-session close, with
    no greeks/IV/OI fields — so options are NBBO/last + prior-close
    failover only, never a greeks/surface source.
  - Unconfigured is a clean no-op: with neither an access token nor a
    refresh token, every module-level fetch helper returns empty
    immediately (no network, no raise) and the ladder falls through to
    Yahoo.

Token lifecycle — access tokens expire in ~3 days, so REFRESH IS MANDATORY
in production. Tokens persist in a 0600 JSON file (``ROBINHOOD_MCP_TOKEN_FILE``,
default ``data/rh_mcp_token.json``, gitignored; ``/etc/radon/rh-mcp.json`` on
the VPS) so the process can write rotated tokens back. Env vars bootstrap the
file on first use. Refresh runs against the official token endpoint
(``https://api.robinhood.com/oauth2/token/``, ``grant_type=refresh_token``,
form-encoded, public client — NO client secret; discovery:
https://agent.robinhood.com/.well-known/oauth-authorization-server/mcp/trading)
when the access token is missing, within REFRESH_SAFETY_WINDOW_S of expiry,
or after an MCP 401/403. An ``invalid_grant`` marks Robinhood unconfigured
for the rest of the process so ladders fall through to Yahoo without
crashing.

Env (documented in .env.example + docs/external-services.md):
  ROBINHOOD_MCP_URL            — optional; defaults to the official trading URL.
  ROBINHOOD_MCP_TOKEN_FILE     — optional; token persistence path (0600).
  ROBINHOOD_MCP_TOKEN          — bootstrap access token (optional if the
                                 file already holds one).
  ROBINHOOD_MCP_REFRESH_TOKEN  — bootstrap refresh token.
  ROBINHOOD_MCP_CLIENT_ID      — the OAuth public client_id that minted it
                                 (token_endpoint_auth_method=none).

Tokens are credentials: they must never appear in logs, reprs, or exception
text. ``_redact`` strips every known secret defensively from error strings.
"""
from __future__ import annotations

import fcntl
import json
import math
import os
import stat
import sys
import tempfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

import requests

DEFAULT_MCP_URL = "https://agent.robinhood.com/mcp/trading"
TOKEN_ENDPOINT = "https://api.robinhood.com/oauth2/token/"
# OAuth discovery document (informational; the endpoint above is the contract):
# https://agent.robinhood.com/.well-known/oauth-authorization-server/mcp/trading
DEFAULT_TOKEN_FILE = Path(__file__).resolve().parents[2] / "data" / "rh_mcp_token.json"
_PROTOCOL_VERSION = "2025-06-18"
_DEFAULT_TIMEOUT = 20
# The failover ladders budget ~30s per rung (portfolio_risk
# BACKFILL_SYMBOL_WORST_CASE_S). One symbol costs at most 4 POSTs
# (initialize + initialized + tools/call + one refresh), so the ladder
# helpers use this bound: 4 x 7s = 28s worst case, inside the rung budget.
LADDER_TIMEOUT_S = 7
_USER_AGENT = "radon/2.0"
# REL-173 (R-482): `timeout=` bounds each socket read only. A Streamable-HTTP
# server that keeps the response open with keepalive pings never trips it, so
# every body is read as a stream under a wall-clock deadline and a size cap.
RESPONSE_DEADLINE_FACTOR = 2.0
MAX_RESPONSE_BYTES = 4 * 1024 * 1024
# REL-173 (R-469): where a rotated refresh token is saved when the store
# cannot be committed AFTER the endpoint already spent the old one.
ROTATED_TOKEN_FALLBACK_DIR = Path(tempfile.gettempdir())
# REL-174 (R-485): the one spelling of this source in persisted rows, ladder
# heartbeats and the streaks payload (price_history_daily vocabulary).
RH_SOURCE = "rh"
# REL-174 (R-470, R-480, R-481): failure classes the ladders can surface.
# `auth` is structural and disables the rung for the process; the others open
# a circuit breaker for BREAKER_COOLDOWN_S so a dead or rate-limited MCP is
# hit once per scan, not once per symbol.
FAILURE_AUTH = "auth"
FAILURE_RATE_LIMITED = "rate_limited"
FAILURE_NETWORK = "network"
FAILURE_TOKEN_ENDPOINT = "token_endpoint"
BREAKER_COOLDOWN_S = 300.0
# robinhood_configured() re-reads the token file at most this often (R-484).
CONFIGURED_MEMO_S = 60.0

# Access tokens live ~3 days; refresh this far ahead of the recorded expiry.
REFRESH_SAFETY_WINDOW_S = 3600

# Documented ceiling on get_equity_quotes symbols per call.
EQUITY_QUOTES_BATCH_MAX = 20

# The documented READ tools Radon is allowed to call. Deny-by-default:
# anything not in this set — including every order-write tool the server
# may expose (place_*, cancel_*, …) — is refused client-side.
READ_ONLY_TOOLS = frozenset({
    # quotes / chains failover
    "get_equity_quotes",
    "get_equity_historicals",
    "get_equity_price_book",
    "get_option_chains",
    "get_option_instruments",
    "get_option_quotes",
    "get_option_historicals",
    "get_index_quotes",
    # crowding overlay
    "get_popular_watchlists",
    "get_watchlists",
    "get_watchlist_items",
    "get_scans",
    "get_scanner_filter_specs",
    "run_scan",
    # earnings backup
    "get_earnings_calendar",
    "get_earnings_results",
})


class RobinhoodClientError(Exception):
    """Base exception for Robinhood MCP failures."""


class RobinhoodNotConfiguredError(RobinhoodClientError):
    """Raised when a call is attempted with no access and no refresh token."""


class RobinhoodAuthError(RobinhoodClientError):
    """Token rejected (401/403) or the refresh grant failed."""


class RobinhoodRateLimitError(RobinhoodClientError):
    """HTTP 429 from the MCP: the rung is closed for BREAKER_COOLDOWN_S."""


class RobinhoodNetworkError(RobinhoodClientError):
    """Connect/timeout to the MCP: the rung is closed for BREAKER_COOLDOWN_S."""


class RobinhoodReadOnlyError(RobinhoodClientError):
    """A tool outside READ_ONLY_TOOLS was requested. Execution stays on IB."""


# A refresh that failed with a non-transient grant error (invalid_grant)
# cannot succeed again this process: treat Robinhood as unconfigured so
# every ladder falls through to Yahoo instead of hammering the endpoint.
_refresh_disabled = False


def _iter_body(resp: Any) -> Iterator[bytes]:
    """Body chunks as they ARRIVE. ``iter_content(8192)`` waits for a full
    8192 bytes, which a keepalive stream never delivers; urllib3's ``read1``
    returns whatever is available up to the chunk size."""
    raw = getattr(resp, "raw", None)
    read1 = getattr(raw, "read1", None)
    if callable(read1):
        while True:
            chunk = read1(8192)
            if not chunk:
                return
            yield chunk
        return
    yield from resp.iter_content(chunk_size=8192)


def _read_bounded(resp: Any, timeout: float, what: str) -> bytes:
    """Whole body under ``RESPONSE_DEADLINE_FACTOR * timeout`` wall-clock and
    ``MAX_RESPONSE_BYTES``. Closes the response either way."""
    deadline = time.monotonic() + RESPONSE_DEADLINE_FACTOR * float(timeout)
    chunks: List[bytes] = []
    size = 0
    try:
        for chunk in _iter_body(resp):
            if chunk:
                chunks.append(chunk)
                size += len(chunk)
            if size > MAX_RESPONSE_BYTES:
                raise RobinhoodClientError(
                    f"{what} response exceeded the {MAX_RESPONSE_BYTES} byte cap"
                )
            if time.monotonic() > deadline:
                raise RobinhoodClientError(
                    f"{what} response exceeded the {RESPONSE_DEADLINE_FACTOR * float(timeout):.0f}s "
                    "wall-clock deadline (keepalive stream never closed)"
                )
    except (requests.ConnectionError, requests.Timeout) as exc:
        raise RobinhoodClientError(f"{what} body read failed: {exc.__class__.__name__}") from None
    finally:
        try:
            resp.close()
        except Exception:  # noqa: BLE001 - best effort
            pass
    return b"".join(chunks)


_process_lock = threading.RLock()
_failure: Optional[Dict[str, Any]] = None
_breaker_until: float = 0.0
_shared_client: Optional["RobinhoodClient"] = None
_configured_memo: Optional[tuple[float, tuple, bool]] = None
_warned_paths: set = set()


def _disable_for_process(reason: str, failure_class: str = FAILURE_AUTH) -> None:
    """Structural failure: no retry in this process can help."""
    global _refresh_disabled, _failure
    with _process_lock:
        _refresh_disabled = True
        _failure = {"class": failure_class, "reason": reason, "at": time.time()}
    print(f"  Robinhood disabled for this process: {reason}", file=sys.stderr)


def _open_breaker(reason: str, failure_class: str) -> None:
    """Transient failure: close the rung for BREAKER_COOLDOWN_S (R-480)."""
    global _breaker_until, _failure
    with _process_lock:
        _breaker_until = time.monotonic() + BREAKER_COOLDOWN_S
        _failure = {"class": failure_class, "reason": reason, "at": time.time()}
    print(
        f"  Robinhood rung closed for {BREAKER_COOLDOWN_S:.0f}s ({failure_class}): {reason}",
        file=sys.stderr,
    )


def robinhood_available() -> bool:
    """False while disabled for the process or inside a breaker cooldown."""
    with _process_lock:
        return not _refresh_disabled and time.monotonic() >= _breaker_until


def robinhood_rung_error() -> Optional[Dict[str, Any]]:
    """The failure that currently closes the rung, or None when it is open.

    Ladders read this after a fetch to say WHY a ticker fell to Yahoo
    (``robinhood_degradation``) instead of heartbeating ``ok``/``yahoo``.
    """
    with _process_lock:
        if robinhood_available() or _failure is None:
            return None
        return dict(_failure)


def robinhood_degradation(
    service: str, sources: Dict[str, str], *, skip: Iterable[str] = ()
) -> Optional[Dict[str, Any]]:
    """Heartbeat ``error`` payload when the closed rung demoted tickers to Yahoo."""
    failure = robinhood_rung_error()
    if failure is None:
        return None
    skipped = set(skip)
    demoted = sorted(t for t, s in sources.items() if s == "yahoo" and t not in skipped)
    if not demoted:
        return None
    return {
        "class": failure["class"],
        "source": "robinhood",
        "message": (
            f"{service}: Robinhood rung failed ({failure['class']}: {failure['reason']}); "
            f"{', '.join(demoted)} served by Yahoo"
        ),
    }


def _reset_process_state() -> None:
    """Test hook: forget the breaker, the failure, the shared client, the memo."""
    global _refresh_disabled, _failure, _breaker_until, _shared_client, _configured_memo
    with _process_lock:
        _refresh_disabled = False
        _failure = None
        _breaker_until = 0.0
        if _shared_client is not None:
            try:
                _shared_client.close()
            except Exception:  # noqa: BLE001
                pass
        _shared_client = None
        _configured_memo = None
        _warned_paths.clear()


class RobinhoodTokenStore:
    """0600-file-backed token state with env bootstrap and OAuth refresh.

    Read paths never write. The env → file bootstrap happens on the first
    actual token RESOLUTION (a client about to talk to the MCP), and every
    refresh persists rotated tokens atomically (temp file + rename).
    """

    def __init__(self, path: Optional[str] = None):
        self._path = Path(
            path or os.environ.get("ROBINHOOD_MCP_TOKEN_FILE") or DEFAULT_TOKEN_FILE
        )
        self._state: Dict[str, Any] = self._read()

    @property
    def path(self) -> Path:
        return self._path

    def _read(self) -> Dict[str, Any]:
        """File state overlaid on env bootstrap values. Read-only."""
        state: Dict[str, Any] = {}
        try:
            mode = stat.S_IMODE(self._path.stat().st_mode)
            if mode & 0o077 and str(self._path) not in _warned_paths:
                # Never print values — only the path and the mode. Once per
                # process (R-484): garch/leap call this per ticker from 16 threads.
                _warned_paths.add(str(self._path))
                print(
                    f"  Robinhood token file {self._path} permissions "
                    f"{oct(mode)} are too open; expected 0600",
                    file=sys.stderr,
                )
        except OSError:
            pass
        try:
            loaded = json.loads(self._path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                state = loaded
        except (OSError, ValueError):
            state = {}
        for key, env in (
            ("access_token", "ROBINHOOD_MCP_TOKEN"),
            ("refresh_token", "ROBINHOOD_MCP_REFRESH_TOKEN"),
            ("client_id", "ROBINHOOD_MCP_CLIENT_ID"),
        ):
            if not state.get(key) and os.environ.get(env):
                state[key] = os.environ[env]
        state.setdefault("token_type", "Bearer")
        return state

    # ── introspection (never network) ────────────────────────────

    @property
    def access_token(self) -> str:
        return str(self._state.get("access_token") or "")

    @property
    def refresh_token(self) -> str:
        return str(self._state.get("refresh_token") or "")

    @property
    def client_id(self) -> str:
        return str(self._state.get("client_id") or "")

    def secrets(self) -> List[str]:
        """Every credential value that must never reach logs or errors."""
        return [s for s in (self.access_token, self.refresh_token) if s]

    def can_refresh(self) -> bool:
        return bool(self.refresh_token and self.client_id)

    def is_configured(self) -> bool:
        return bool(self.access_token) or self.can_refresh()

    def _expires_at(self) -> Optional[float]:
        value = self._state.get("expires_at")
        if value is None and self._state.get("expires_in") is not None:
            written = self._state.get("written_at")
            if written is not None:
                try:
                    return float(written) + float(self._state["expires_in"])
                except (TypeError, ValueError):
                    return None
            return None
        try:
            return float(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    def minted_recently(self, token: str, within_s: float = 60.0) -> bool:
        """True when ``token`` is the stored access token and it was written
        within ``within_s`` — i.e. it just came out of a refresh, so an MCP
        401 against it is structural and another refresh cannot help."""
        if not token or token != self.access_token:
            return False
        written = self._state.get("written_at")
        try:
            return written is not None and (time.time() - float(written)) < within_s
        except (TypeError, ValueError):
            return False

    def access_token_if_fresh(self, now: Optional[float] = None) -> Optional[str]:
        """The access token unless it is inside the refresh safety window.

        A token with no recorded expiry (env bootstrap) is used as-is; a
        stale one surfaces as an MCP 401 and the retry path refreshes.
        """
        if not self.access_token:
            return None
        expires_at = self._expires_at()
        moment = time.time() if now is None else now
        if expires_at is not None and expires_at - REFRESH_SAFETY_WINDOW_S <= moment:
            return None
        return self.access_token

    # ── persistence ──────────────────────────────────────────────

    def _tmp_path(self) -> Path:
        return self._path.with_name(self._path.name + ".tmp")

    def _open_persist_tmp(self) -> int:
        """Open the temp file the commit will rename over the store.

        Raises OSError when the store is not writable. Called BEFORE the
        token endpoint is hit so a single-use refresh token is never spent
        with nowhere to persist its rotation (R-469).
        """
        self._path.parent.mkdir(parents=True, exist_ok=True)
        return os.open(self._tmp_path(), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)

    def _discard_persist_tmp(self, fd: int) -> None:
        try:
            os.close(fd)
        except OSError:
            pass
        self._tmp_path().unlink(missing_ok=True)

    def _commit_persist(self, fd: int) -> None:
        """Write the state through ``fd`` and rename it over the store."""
        tmp = self._tmp_path()
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(self._state, handle, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, self._path)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
        os.chmod(self._path, 0o600)

    def persist(self) -> None:
        """Atomic 0600 write: temp file in the same directory, then rename."""
        self._commit_persist(self._open_persist_tmp())

    def _save_rotation_fallback(self) -> Path:
        """Last resort after the endpoint rotated the token but the store
        could not be committed: write the state somewhere writable, 0600."""
        ROTATED_TOKEN_FALLBACK_DIR.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(
            prefix="rh-mcp-rotated-", suffix=".json", dir=str(ROTATED_TOKEN_FALLBACK_DIR)
        )
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(self._state, handle, indent=2)
        os.chmod(name, 0o600)
        return Path(name)

    def bootstrap_file_if_missing(self) -> None:
        """First configured run: materialize the env tokens into the file."""
        if self.is_configured() and not self._path.exists():
            self._state["written_at"] = time.time()
            self.persist()

    # ── refresh (the one network path in this class) ─────────────

    @contextmanager
    def _exclusive_lock(self) -> Iterator[None]:
        """flock on <path>.lock — serializes refresh across processes AND
        threads (garch fans fetches across a 16-worker pool), so a
        single-use rotated refresh token is never spent twice."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = self._path.with_name(self._path.name + ".lock")
        fd = os.open(lock_path, os.O_WRONLY | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def refresh(
        self, timeout: int = _DEFAULT_TIMEOUT, invalid_access: Optional[str] = None
    ) -> str:
        """Mint a fresh access token via grant_type=refresh_token.

        Single-flight: an exclusive file lock serializes concurrent
        refreshers, and the state is re-read under the lock so a process
        that lost the race reuses the winner's rotated tokens instead of
        spending the (single-use) old refresh token a second time.
        ``invalid_access`` names an access token the MCP just rejected, so
        the re-read short-circuit cannot hand back the same known-bad token
        (a no-expiry bootstrap token otherwise looks "fresh").

        Public client: form-encoded refresh_token + client_id, NO client
        secret. A rotated refresh_token in the response replaces the old
        one. invalid_grant (or any 4xx) disables Robinhood for this process
        — the ladder falls through to Yahoo rather than crashing.
        """
        if _refresh_disabled:
            raise RobinhoodNotConfiguredError("Robinhood refresh disabled for this process")
        if not self.can_refresh():
            raise RobinhoodNotConfiguredError(
                "no ROBINHOOD_MCP_REFRESH_TOKEN / ROBINHOOD_MCP_CLIENT_ID to refresh with"
            )
        with self._exclusive_lock():
            self._state = self._read()
            already_fresh = self.access_token_if_fresh()
            if already_fresh and already_fresh != (invalid_access or ""):
                return already_fresh  # another process/thread won the race
            return self._refresh_locked(timeout)

    def _refresh_locked(self, timeout: int) -> str:
        # R-469: prove the store is writable BEFORE the endpoint spends the
        # single-use refresh token. A local write problem is not an auth
        # failure, so it neither disables the process nor touches the wire.
        try:
            tmp_fd = self._open_persist_tmp()
        except OSError as exc:
            raise RobinhoodClientError(
                f"Robinhood token store is not writable ({exc.strerror}: {self._path}); "
                "refusing to spend the single-use refresh token"
            ) from None
        try:
            return self._refresh_with_open_store(timeout, tmp_fd)
        except BaseException:
            # Every failure before the commit leaves the temp file behind.
            self._tmp_path().unlink(missing_ok=True)
            raise

    def _refresh_with_open_store(self, timeout: int, tmp_fd: int) -> str:
        try:
            resp = requests.post(
                TOKEN_ENDPOINT,
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": self.refresh_token,
                    "client_id": self.client_id,
                },
                headers={
                    "Content-Type": "application/x-www-form-urlencoded",
                    "User-Agent": _USER_AGENT,
                },
                timeout=timeout,
                stream=True,
            )
        except (requests.ConnectionError, requests.Timeout) as exc:
            self._discard_persist_tmp(tmp_fd)
            reason = f"Robinhood token endpoint unreachable: {_scrub(str(exc), self.secrets())}"
            _open_breaker(reason, FAILURE_TOKEN_ENDPOINT)
            raise RobinhoodClientError(reason) from None

        try:
            body = _read_bounded(resp, timeout, "Robinhood token endpoint")
        except RobinhoodClientError:
            self._discard_persist_tmp(tmp_fd)
            raise
        text = body.decode("utf-8", errors="replace")

        if resp.status_code >= 400:
            self._discard_persist_tmp(tmp_fd)
            # Scrub BEFORE truncating: a slice can cut a token in half and
            # leave a prefix the exact-match scrub would no longer find.
            detail = _scrub(text, self.secrets())[:200]
            if 400 <= resp.status_code < 500:
                # invalid_grant / invalid_client: retrying cannot help.
                _disable_for_process(
                    f"token refresh rejected (HTTP {resp.status_code}); "
                    "treating Robinhood as unconfigured"
                )
                raise RobinhoodAuthError(
                    f"Robinhood token refresh rejected (HTTP {resp.status_code}): {detail}"
                )
            reason = f"Robinhood token endpoint HTTP {resp.status_code}: {detail}"
            _open_breaker(reason, FAILURE_TOKEN_ENDPOINT)
            raise RobinhoodClientError(reason)

        try:
            payload = json.loads(text)
            if not isinstance(payload, dict):
                raise ValueError("not an object")
        except ValueError:
            self._discard_persist_tmp(tmp_fd)
            raise RobinhoodClientError(
                "Robinhood token endpoint returned a non-JSON body"
            ) from None
        access = payload.get("access_token")
        if not access:
            self._discard_persist_tmp(tmp_fd)
            raise RobinhoodClientError("Robinhood token response carried no access_token")

        now = time.time()
        self._state["access_token"] = access
        self._state["token_type"] = payload.get("token_type") or "Bearer"
        self._state["written_at"] = now
        if payload.get("expires_in") is not None:
            self._state["expires_in"] = payload["expires_in"]
            self._state["expires_at"] = now + float(payload["expires_in"])
        if payload.get("refresh_token"):
            self._state["refresh_token"] = payload["refresh_token"]
        try:
            self._commit_persist(tmp_fd)
        except OSError as exc:
            # The endpoint already rotated the token; disk still holds the
            # spent one. Save the rotation somewhere writable and stop this
            # process from refreshing again on the stale file.
            saved = self._save_rotation_fallback()
            _disable_for_process(
                f"rotated Robinhood token could not be persisted to {self._path} "
                f"({exc.strerror}); saved to {saved}"
            )
            raise RobinhoodClientError(
                f"Robinhood token rotated but {self._path} could not be written "
                f"({exc.strerror}); the new tokens are at {saved} — restore them to "
                f"{self._path} before the next run"
            ) from None
        return str(access)


def robinhood_configured() -> bool:
    """True when an access token OR refresh credentials are available.

    Never touches the network, and False for the rest of the process after
    a rejected refresh grant.
    """
    global _configured_memo
    if not robinhood_available():
        return False
    # Keyed on the env the store reads, so a credential change (operator
    # exports a token, a test swaps the file path) is never served stale.
    key = tuple(
        os.environ.get(name) or ""
        for name in (
            "ROBINHOOD_MCP_TOKEN_FILE",
            "ROBINHOOD_MCP_TOKEN",
            "ROBINHOOD_MCP_REFRESH_TOKEN",
            "ROBINHOOD_MCP_CLIENT_ID",
        )
    )
    with _process_lock:
        memo = _configured_memo
        now = time.monotonic()
        if memo is not None and memo[1] == key and now - memo[0] < CONFIGURED_MEMO_S:
            return memo[2]
        configured = RobinhoodTokenStore().is_configured()
        _configured_memo = (now, key, configured)
        return configured


class RobinhoodClient:
    """Minimal Streamable-HTTP MCP consumer for the official trading server.

    JSON-RPC over one POST per request; ``initialize`` runs lazily before the
    first ``tools/call`` and the returned ``Mcp-Session-Id`` (when the server
    issues one) rides on every later request. Expired/rejected access tokens
    are refreshed through the token store and the request retried once.
    """

    def __init__(
        self,
        url: Optional[str] = None,
        token: Optional[str] = None,
        timeout: int = _DEFAULT_TIMEOUT,
        token_file: Optional[str] = None,
    ):
        self._url = url or os.environ.get("ROBINHOOD_MCP_URL") or DEFAULT_MCP_URL
        # Explicit ctor token = static mode (tests); no store I/O, no refresh.
        self._explicit_token = token
        self._store = RobinhoodTokenStore(token_file) if token is None else None
        self._timeout = timeout
        self._session_id: Optional[str] = None
        self._initialized = False
        self._next_id = 0
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": _USER_AGENT})
        # Bodies are read by _read_bounded (deadline + cap), never by requests.
        self._session.stream = True

    def __repr__(self) -> str:  # never leak a token
        return f"RobinhoodClient(url={self._url!r}, configured={self.is_configured()})"

    def is_configured(self) -> bool:
        if self._explicit_token is not None:
            return bool(self._explicit_token)
        return not _refresh_disabled and self._store.is_configured()

    def close(self) -> None:
        self._session.close()

    def __enter__(self) -> "RobinhoodClient":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ── transport ────────────────────────────────────────────────

    def _secrets(self) -> List[str]:
        secrets = [self._explicit_token] if self._explicit_token else []
        if self._store is not None:
            secrets.extend(self._store.secrets())
        return [s for s in secrets if s]

    def _redact(self, text: str) -> str:
        return _scrub(text, self._secrets())

    def _resolve_token(self) -> str:
        """Current access token, refreshing through the store when needed."""
        if self._explicit_token is not None:
            if not self._explicit_token:
                raise RobinhoodNotConfiguredError("empty Robinhood token")
            return self._explicit_token
        self._store.bootstrap_file_if_missing()
        access = self._store.access_token_if_fresh()
        if access:
            return access
        if self._store.can_refresh():
            return self._store.refresh(timeout=self._timeout)
        if self._store.access_token:
            # No expiry metadata and nothing to refresh with: use it and let
            # a 401 surface as RobinhoodAuthError.
            return self._store.access_token
        raise RobinhoodNotConfiguredError(
            "no Robinhood access or refresh token configured; Robinhood is skipped"
        )

    def _headers(self, token: str) -> Dict[str, str]:
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": _PROTOCOL_VERSION,
        }
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        return headers

    def _post(self, payload: Dict[str, Any], _retry_auth: bool = True) -> Optional[Dict[str, Any]]:
        if not robinhood_available():
            failure = robinhood_rung_error() or {}
            raise RobinhoodNotConfiguredError(
                f"Robinhood rung closed ({failure.get('class', 'disabled')}): "
                f"{failure.get('reason', 'disabled for this process')}"
            )
        token = self._resolve_token()
        try:
            resp = self._session.post(
                self._url, json=payload, headers=self._headers(token), timeout=self._timeout
            )
        except (requests.ConnectionError, requests.Timeout) as exc:
            reason = f"Robinhood MCP unreachable: {self._redact(str(exc))}"
            _open_breaker(reason, FAILURE_NETWORK)
            raise RobinhoodNetworkError(reason) from None
        # The session is created with stream=True; read the body ourselves
        # under the wall-clock deadline and size cap (R-482).
        body = _read_bounded(resp, self._timeout, "Robinhood MCP")
        text = body.decode("utf-8", errors="replace")
        if resp.status_code in (401, 403):
            # Access token expired or revoked mid-flight: refresh once and
            # retry the same request with the new token.
            if (
                _retry_auth
                and self._store is not None
                and self._store.can_refresh()
                and not _refresh_disabled
            ):
                if self._store.minted_recently(token):
                    # The rejected token just came out of a refresh: another
                    # mint cannot help, and retrying per symbol would hammer
                    # the token endpoint across a whole scan.
                    _disable_for_process(
                        f"MCP rejected a freshly refreshed token (HTTP {resp.status_code})"
                    )
                    raise RobinhoodAuthError(
                        f"Robinhood MCP rejected the token (HTTP {resp.status_code})"
                    )
                self._store.refresh(timeout=self._timeout, invalid_access=token)
                return self._post(payload, _retry_auth=False)
            # Structural either way (R-470a): a token minted seconds ago was
            # rejected, or there is nothing to refresh with. Retrying per
            # symbol would hammer the endpoint for the rest of the scan, so
            # degrade the process to the unconfigured skip.
            _disable_for_process(
                f"MCP rejected the access token (HTTP {resp.status_code}) and "
                + ("a fresh refresh cannot help" if not _retry_auth else "no refresh credentials exist")
            )
            raise RobinhoodAuthError(f"Robinhood MCP rejected the token (HTTP {resp.status_code})")
        if resp.status_code == 429:
            reason = f"Robinhood MCP rate limited (HTTP 429): {self._redact(text)[:120]}"
            _open_breaker(reason, FAILURE_RATE_LIMITED)
            raise RobinhoodRateLimitError(reason)
        if resp.status_code >= 400:
            # Scrub BEFORE truncating (a slice can bisect a token).
            raise RobinhoodClientError(
                f"Robinhood MCP HTTP {resp.status_code}: {self._redact(text)[:300]}"
            )
        session_id = resp.headers.get("Mcp-Session-Id")
        if session_id:
            self._session_id = session_id
        if resp.status_code == 202 or not body:
            return None  # accepted notification
        content_type = resp.headers.get("Content-Type", "")
        if "text/event-stream" in content_type:
            return _parse_sse_response(text)
        try:
            parsed = json.loads(text)
        except ValueError:
            raise RobinhoodClientError(
                f"Robinhood MCP returned non-JSON body: {self._redact(text)[:200]}"
            ) from None
        return parsed if isinstance(parsed, dict) else None

    def _rpc(self, method: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        self._next_id += 1
        message = self._post({
            "jsonrpc": "2.0",
            "id": self._next_id,
            "method": method,
            "params": params or {},
        })
        if message is None:
            raise RobinhoodClientError(f"Robinhood MCP returned no response for {method}")
        if message.get("error"):
            error = message["error"]
            raise RobinhoodClientError(
                f"Robinhood MCP error on {method}: "
                f"{self._redact(str(error.get('message', error)))}"
            )
        return message.get("result") or {}

    def _ensure_initialized(self) -> None:
        if self._initialized:
            return
        if not self.is_configured():
            raise RobinhoodNotConfiguredError(
                "no Robinhood access or refresh token configured; Robinhood is skipped"
            )
        self._rpc("initialize", {
            "protocolVersion": _PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "radon", "version": "2.0"},
        })
        self._post({"jsonrpc": "2.0", "method": "notifications/initialized"})
        self._initialized = True

    # ── tools ────────────────────────────────────────────────────

    def call_tool(self, name: str, arguments: Optional[Dict[str, Any]] = None) -> Any:
        """Call one documented READ tool. Anything else is refused here,
        before any network I/O — Radon never places, cancels, or modifies
        anything through Robinhood; execution stays on IB."""
        if name not in READ_ONLY_TOOLS:
            raise RobinhoodReadOnlyError(
                f"tool {name!r} is not in the Robinhood read-only allowlist; "
                "Radon never writes through Robinhood (execution stays on IB)"
            )
        self._ensure_initialized()
        result = self._rpc("tools/call", {"name": name, "arguments": arguments or {}})
        if result.get("isError"):
            raise RobinhoodClientError(
                f"Robinhood tool {name} failed: "
                f"{self._redact(str(_tool_text(result))[:300])}"
            )
        structured = result.get("structuredContent")
        if structured is not None:
            return structured
        text = _tool_text(result)
        if text is not None:
            try:
                return json.loads(text)
            except ValueError:
                return text
        return result

    # ── convenience reads ────────────────────────────────────────

    def get_equity_quotes(self, symbols: List[str]) -> List[Dict[str, Any]]:
        """Batched quotes, chunked at the documented 20-symbol ceiling."""
        quotes: List[Dict[str, Any]] = []
        for start in range(0, len(symbols), EQUITY_QUOTES_BATCH_MAX):
            chunk = symbols[start:start + EQUITY_QUOTES_BATCH_MAX]
            payload = self.call_tool("get_equity_quotes", {"symbols": chunk})
            quotes.extend(_result_rows(payload))
        return quotes

    def get_equity_historicals(
        self, symbol: str, interval: str = "day", span: str = "year"
    ) -> List[Dict[str, Any]]:
        payload = self.call_tool(
            "get_equity_historicals",
            {"symbol": symbol, "interval": interval, "span": span},
        )
        return _result_rows(payload)

    def get_index_quotes(self, symbols: List[str]) -> List[Dict[str, Any]]:
        payload = self.call_tool("get_index_quotes", {"symbols": symbols})
        return _result_rows(payload)

    def get_popular_watchlists(self) -> List[Dict[str, Any]]:
        return _result_rows(self.call_tool("get_popular_watchlists"))

    def get_watchlist_items(self, watchlist_id: str) -> List[Dict[str, Any]]:
        return _result_rows(self.call_tool("get_watchlist_items", {"watchlist_id": watchlist_id}))

    def get_scans(self) -> List[Dict[str, Any]]:
        return _result_rows(self.call_tool("get_scans"))

    def run_scan(self, scan_id: str) -> List[Dict[str, Any]]:
        return _result_rows(self.call_tool("run_scan", {"scan_id": scan_id}))

    def fetch_daily_closes(self, symbol: str) -> Dict[str, float]:
        """date (YYYY-MM-DD) -> close from get_equity_historicals.

        Row/field names are probed defensively rather than assumed; any
        shape this cannot read yields {} and the ladder falls through to
        Yahoo.
        """
        return _closes_from_historicals(self.get_equity_historicals(symbol))


# ── payload parsing (fields probed live, never assumed) ──────────

_ROW_LIST_KEYS = ("data_points", "historicals", "results", "quotes", "data", "bars", "items")
_DATE_KEYS = ("begins_at", "date", "timestamp", "time", "session_date")
_CLOSE_KEYS = ("close_price", "close", "last_trade_price", "adjusted_close")


def _scrub(text: str, secrets: List[str]) -> str:
    for secret in secrets:
        if secret:
            text = text.replace(secret, "[REDACTED]")
    return text


def _tool_text(result: Dict[str, Any]) -> Optional[str]:
    for item in result.get("content") or []:
        if isinstance(item, dict) and item.get("type") == "text":
            return item.get("text")
    return None


def _result_rows(payload: Any) -> List[Dict[str, Any]]:
    """Normalize a tool payload to a list of row dicts."""
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if isinstance(payload, dict):
        for key in _ROW_LIST_KEYS:
            rows = payload.get(key)
            if isinstance(rows, list):
                return [row for row in rows if isinstance(row, dict)]
    return []


def _to_price(value: Any) -> Optional[float]:
    """A finite positive price, or None.

    Rejects booleans (``float(True) == 1.0`` would fabricate a $1 print)
    and non-finite floats (``float("inf") > 0`` is True and would poison
    every downstream return/vol calculation).
    """
    if isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(parsed) or parsed <= 0:
        return None
    return parsed


def _closes_from_historicals(rows: List[Dict[str, Any]]) -> Dict[str, float]:
    closes: Dict[str, float] = {}
    for row in rows:
        date = next((row[k] for k in _DATE_KEYS if row.get(k)), None)
        close = next((row[k] for k in _CLOSE_KEYS if row.get(k) is not None), None)
        if date is None:
            continue
        value = _to_price(close)
        if value is not None:
            closes[str(date)[:10]] = value
    return closes


def _parse_sse_response(body: str) -> Optional[Dict[str, Any]]:
    """Last JSON-RPC message carrying a result/error from an SSE body."""
    message: Optional[Dict[str, Any]] = None
    for line in body.splitlines():
        if not line.startswith("data:"):
            continue
        try:
            parsed = json.loads(line[len("data:"):].strip())
        except ValueError:
            continue
        if isinstance(parsed, dict) and ("result" in parsed or "error" in parsed):
            message = parsed
    return message


# ── module-level ladder helpers (unconfigured = clean empty) ──────


def fetch_robinhood_closes(symbols: List[str]) -> Dict[str, Dict[str, float]]:
    """Daily closes per symbol for the failover ladders.

    Ranked BELOW IB / UW / Cboe and ABOVE Yahoo. Returns {} immediately —
    no network — when neither an access token nor refresh credentials are
    configured (or after a rejected refresh grant), so those hosts fall
    straight through to Yahoo.
    """
    if not robinhood_configured():
        return {}
    out: Dict[str, Dict[str, float]] = {}
    # One client per process, calls serialized (R-480): the MCP session is
    # initialized once, and a 16-worker pool cannot fan 48 handshakes at a
    # dead endpoint. A closed rung ends the loop; a per-symbol error of ANY
    # shape skips only that symbol (R-470).
    with _process_lock:
        for symbol in symbols:
            if not robinhood_available():
                break
            try:
                closes = _client().fetch_daily_closes(symbol)
            except Exception as exc:  # noqa: BLE001 - the ladder falls through to Yahoo
                print(f"  Robinhood: {symbol} failed — {exc}", file=sys.stderr)
                continue
            if closes:
                out[symbol] = closes
                print(f"  Robinhood: {symbol} — {len(closes)} bars", file=sys.stderr)
    return out


def _client() -> "RobinhoodClient":
    """The process-wide ladder client (caller holds _process_lock)."""
    global _shared_client
    if _shared_client is None:
        _shared_client = RobinhoodClient(timeout=LADDER_TIMEOUT_S)
    return _shared_client


def fetch_robinhood_quote(symbol: str, *, index: bool = False) -> Optional[float]:
    """One last/quote value for the current-quote failover (IB → RH → Yahoo).

    None when unconfigured or the (unpublished) quote shape is unreadable.
    """
    if not robinhood_configured():
        return None
    try:
        with _process_lock:
            rh = _client()
            rows = (
                rh.get_index_quotes([symbol]) if index else rh.get_equity_quotes([symbol])
            )
    except Exception as exc:  # noqa: BLE001 - the ladder falls through to Yahoo
        print(f"  Robinhood quote failed for {symbol}: {exc}", file=sys.stderr)
        return None
    for row in rows:
        for key in ("last_trade_price", "last", "last_price", "price", "mark_price"):
            parsed = _to_price(row.get(key))
            if parsed is not None:
                return parsed
    return None
