#!/usr/bin/env python3
"""Durable subscription-token vault + autonomous refresh for the agent CLIs.

Claude Code, OpenAI Codex, xAI Grok and Google Gemini authenticate against the
operator's *subscriptions*, storing an OAuth credential file under the ``radon``
home. Those files get deleted by host maintenance, expire when nothing exercises
the CLI, or land half-written. ``scripts/clients/model_ladder.py`` reads them
directly, so a dead file silently demotes the whole subscription band to metered
API keys.

This module seals each credential file, verbatim, into the EXISTING encrypted
secret store (``scripts/secret_store.py`` — no second crypto system), refreshes
expiring tokens autonomously, restores deleted ones from the vault, and pages
the operator only for the one case no daemon can fix: a revoked or fully expired
refresh token, which needs a browser login.

⛔ No token material is ever logged, raised, written to the sidecar, sent to
Pushover or recorded in ``service_health``. Use :func:`redact`.
"""

from __future__ import annotations

import argparse
import contextlib
import copy
import fcntl
import json
import logging
import os
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Optional, Sequence
from urllib import error as urllib_error
from urllib import parse as urllib_parse
from urllib import request as urllib_request

try:  # package import (python3.13 -m scripts.subscription_tokens)
    from .utils.atomic_io import atomic_save, verified_load
except ImportError:  # pragma: no cover - direct script execution
    from utils.atomic_io import atomic_save, verified_load  # type: ignore

log = logging.getLogger("subscription_tokens")

SERVICE_NAME = "subscription-tokens"
SIDECAR_PATH = Path("/var/lib/radon/subscription-tokens/state.json")
# R-127: the 30min timer and the runbook's manual --seal/--restore/--once must
# never refresh the same credential concurrently (a rotated refresh token
# invalidates the one the other run is presenting).
LOCK_PATH = Path("/run/lock/radon-subscription-tokens.lock")
EXPIRY_SKEW_SECONDS = 600
HTTP_TIMEOUT_SECONDS = 20
HTTP_RETRIES = 2
CLI_TIMEOUT_SECONDS = 60
PAGE_COOLDOWN = timedelta(hours=12)
ERROR_PAGE_AFTER = 3
PUSHOVER_URL = "https://api.pushover.net/1/messages.json"
PUSHOVER_TITLE = "radon subscription token"

# -- states -----------------------------------------------------------------
LIVE = "live"
REFRESHED = "refreshed"
RESTORED = "restored"
UNBOOTSTRAPPED = "unbootstrapped"
EXPIRING = "expiring"  # --check only: not live, but nothing was attempted
NEEDS_REAUTH = "needs_reauth"
STORE_UNAVAILABLE = "store_unavailable"
ERROR = "error"

HEALTHY_STATES = frozenset({LIVE, REFRESHED, RESTORED, UNBOOTSTRAPPED})

EXIT_OK = 0
EXIT_ATTENTION = 1
EXIT_CONFIG = 78  # repo convention for a configuration/store failure


class VaultUnavailable(RuntimeError):
    """The encrypted store could not be OPENED.

    R-621: distinct from an empty store. An empty store means nobody has sealed
    a credential yet; an unopenable store means we know nothing at all.
    """


def redact(value: Any) -> str:
    """The ONLY representation of token material allowed anywhere."""
    text = value if isinstance(value, str) else ""
    return f"<redacted len={len(text)}>"


# ---------------------------------------------------------------------------
# provider adapters
# ---------------------------------------------------------------------------


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _parse_rfc3339(value: Any) -> Optional[datetime]:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return _aware(datetime.fromisoformat(value.strip().replace("Z", "+00:00")))
    except ValueError:
        return None


def _parse_epoch_millis(value: Any) -> Optional[datetime]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return datetime.fromtimestamp(value / 1000.0, tz=timezone.utc)


def _iso_z(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


def _expires_at(now: datetime, resp: Mapping[str, Any]) -> datetime:
    try:
        seconds = int(resp.get("expires_in") or 3600)
    except (TypeError, ValueError):
        seconds = 3600
    return now + timedelta(seconds=seconds)


# -- grok: ~/.grok/auth.json, one entry keyed "<issuer>::<client_id>" --------


def _grok_entries(doc: Mapping[str, Any]) -> list:
    return [value for value in doc.values() if isinstance(value, dict)]


def _grok_entry(doc: Mapping[str, Any]) -> Optional[dict]:
    entries = _grok_entries(doc)
    return entries[0] if entries else None


def _grok_expiry(doc):
    # The file is a map, so it can hold more than one account. Report the
    # EARLIEST expiry: a provider that looks live because entry one was just
    # refreshed, while entry two quietly dies, is the silent demotion to
    # metered keys this whole module exists to prevent.
    expiries = [
        parsed
        for parsed in (_parse_rfc3339(e.get("expires_at")) for e in _grok_entries(doc))
        if parsed is not None
    ]
    return min(expiries) if expiries else None


def _grok_blocker(doc):
    # One refresh_token cannot heal two accounts, so refuse rather than refresh
    # the first entry and leave the second to expire unnoticed.
    if len(_grok_entries(doc)) > 1:
        return "multiple grok accounts in one credential file"
    return None


def _grok_refresh(doc):
    entry = _grok_entry(doc) or {}
    token = entry.get("refresh_token")
    return token if isinstance(token, str) and token else None


def _grok_issuer(doc):
    entry = _grok_entry(doc) or {}
    issuer = entry.get("oidc_issuer")
    client_id = entry.get("oidc_client_id")
    if isinstance(issuer, str) and issuer:
        return issuer, client_id if isinstance(client_id, str) else None
    return None


def _grok_apply(doc, resp, now):
    new = copy.deepcopy(doc)
    for key, entry in new.items():
        if not isinstance(entry, dict):
            continue
        if resp.get("access_token"):
            entry["key"] = resp["access_token"]
        if resp.get("refresh_token"):
            entry["refresh_token"] = resp["refresh_token"]
        entry["expires_at"] = _iso_z(_expires_at(now, resp))
        break
    return new


# -- anthropic: ~/.claude/.credentials.json ---------------------------------


def _anthropic_expiry(doc):
    oauth = doc.get("claudeAiOauth")
    if not isinstance(oauth, Mapping):
        return None
    return _parse_epoch_millis(oauth.get("expiresAt"))


def _anthropic_refresh(doc):
    oauth = doc.get("claudeAiOauth")
    if not isinstance(oauth, Mapping):
        return None
    token = oauth.get("refreshToken")
    return token if isinstance(token, str) and token else None


def _anthropic_apply(doc, resp, now):
    new = copy.deepcopy(doc)
    oauth = new.setdefault("claudeAiOauth", {})
    if resp.get("access_token"):
        oauth["accessToken"] = resp["access_token"]
    if resp.get("refresh_token"):
        oauth["refreshToken"] = resp["refresh_token"]
    oauth["expiresAt"] = int(_expires_at(now, resp).timestamp() * 1000)
    return new


# -- codex: ${CODEX_HOME:-~/.codex}/auth.json -------------------------------


def _codex_tokens(doc) -> Mapping[str, Any]:
    tokens = doc.get("tokens")
    return tokens if isinstance(tokens, Mapping) else {}


def _codex_expiry(doc):
    tokens = _codex_tokens(doc)
    explicit = _parse_rfc3339(tokens.get("expires_at")) or _parse_epoch_millis(
        tokens.get("expires_at")
    )
    if explicit:
        return explicit
    # codex records only when it last refreshed; its access token lives an hour.
    last = _parse_rfc3339(doc.get("last_refresh"))
    return last + timedelta(hours=1) if last else None


def _codex_refresh(doc):
    token = _codex_tokens(doc).get("refresh_token")
    return token if isinstance(token, str) and token else None


def _codex_apply(doc, resp, now):
    new = copy.deepcopy(doc)
    tokens = new.setdefault("tokens", {})
    if resp.get("access_token"):
        tokens["access_token"] = resp["access_token"]
    if resp.get("refresh_token"):
        tokens["refresh_token"] = resp["refresh_token"]
    if resp.get("id_token"):
        tokens["id_token"] = resp["id_token"]
    tokens["expires_at"] = _iso_z(_expires_at(now, resp))
    new["last_refresh"] = _iso_z(now)
    return new


# -- gemini: ~/.gemini/antigravity-cli/antigravity-oauth-token ---------------
#
# 2026-09-18: Google retired the Gemini CLI OAuth client for individuals
# ("This client is no longer supported for Gemini Code Assist for
# individuals ... migrate to the Antigravity suite"). The Antigravity CLI
# (`agy`) keeps its Google OAuth grant in a nested `token` object with an
# RFC 3339 `expiry` at nanosecond precision. `agy models` is a cheap
# authenticated call that refreshes the file in place, so it is the CLI-native
# refresh; the token endpoint is the fallback when `agy` is not on PATH.

# Public OAuth client id of the Antigravity CLI (installed-app client, no
# secret), read off its own login URL. Google's refresh grant requires it.
ANTIGRAVITY_CLIENT_ID = (
    "1071006060591-tmhssin2h21lcre235vtolojh4g403ep.apps.googleusercontent.com"
)


def _gemini_tokens(doc) -> Mapping[str, Any]:
    tokens = doc.get("token")
    return tokens if isinstance(tokens, Mapping) else {}


def _gemini_expiry(doc):
    return _parse_rfc3339(_gemini_tokens(doc).get("expiry"))


def _gemini_refresh(doc):
    token = _gemini_tokens(doc).get("refresh_token")
    return token if isinstance(token, str) and token else None


def _gemini_apply(doc, resp, now):
    new = copy.deepcopy(doc)
    tokens = new.setdefault("token", {})
    if resp.get("access_token"):
        tokens["access_token"] = resp["access_token"]
    if resp.get("refresh_token"):
        tokens["refresh_token"] = resp["refresh_token"]
    if resp.get("id_token"):
        new["id_token"] = resp["id_token"]
    tokens["expiry"] = _iso_z(_expires_at(now, resp))
    return new


def _no_issuer(_doc):
    return None


def _no_blocker(_doc):
    return None


@dataclass(frozen=True)
class Provider:
    """One row per CLI. All behaviour differences live in this table."""

    name: str
    dir_env: Optional[str]  # provider's own override, honoured before the default
    default_subdir: str
    filename: str
    token_url: Optional[str]  # well-known constant, used when the doc names no issuer
    read_expiry: Callable[[Mapping[str, Any]], Optional[datetime]]
    read_refresh: Callable[[Mapping[str, Any]], Optional[str]]
    read_issuer: Callable[[Mapping[str, Any]], Optional[tuple]]
    apply: Callable[[Mapping[str, Any], Mapping[str, Any], datetime], dict]
    cli_binary: Optional[str]
    # No vendor documents a non-interactive refresh subcommand today, so the
    # default runner declines. The hook stays so one can be adopted in a
    # one-line table change when a vendor ships it.
    cli_refresh_args: Optional[tuple]
    reauth_command: str
    # Returns a message when the doc is a shape this adapter refuses to refresh
    # in place; the provider then reports needs_reauth instead of half-healing.
    read_blocker: Callable[[Mapping[str, Any]], Optional[str]] = _no_blocker
    # Fixed public client id for the refresh grant when the doc names none.
    client_id: Optional[str] = None

    @property
    def secret_name(self) -> str:
        # The store validates ^[A-Z][A-Z0-9_]{0,63}$, so the registry names are
        # the spec's identifiers upper-cased.
        return f"SUBSCRIPTION_TOKEN_{self.name.upper()}"

    def path(self, env: Mapping[str, str]) -> Path:
        override = (env.get(self.dir_env) or "").strip() if self.dir_env else ""
        home = (env.get("HOME") or "").strip()
        base = Path(override) if override else (
            Path(home) if home else Path.home()
        ) / self.default_subdir
        return base / self.filename

    def token_request(
        self, doc: Mapping[str, Any], refresh_token: str
    ) -> Optional[dict]:
        """Form fields for the refresh grant; endpoint resolution is separate."""
        fields = {"grant_type": "refresh_token", "refresh_token": refresh_token}
        issuer = self.read_issuer(doc)
        client_id = (issuer[1] if issuer else None) or self.client_id
        if client_id:
            fields["client_id"] = client_id
        return fields


PROVIDERS: dict[str, Provider] = {
    "anthropic": Provider(
        name="anthropic",
        dir_env="CLAUDE_CONFIG_DIR",
        default_subdir=".claude",
        filename=".credentials.json",
        # docs/oauth-subscription-auth.md — Claude Code OAuth token endpoint.
        token_url="https://console.anthropic.com/v1/oauth/token",
        read_expiry=_anthropic_expiry,
        read_refresh=_anthropic_refresh,
        read_issuer=_no_issuer,
        apply=_anthropic_apply,
        cli_binary="claude",
        cli_refresh_args=None,
        reauth_command="ssh -t radon@ib-gateway 'claude auth login --claudeai'",
    ),
    "codex": Provider(
        name="codex",
        dir_env="CODEX_HOME",
        default_subdir=".codex",
        filename="auth.json",
        # docs/oauth-subscription-auth.md — Codex CLI device-auth token endpoint.
        token_url="https://auth.openai.com/oauth/token",
        read_expiry=_codex_expiry,
        read_refresh=_codex_refresh,
        read_issuer=_no_issuer,
        apply=_codex_apply,
        cli_binary="codex",
        cli_refresh_args=None,
        reauth_command="codex CLI absent on the VPS: log in elsewhere, copy auth.json, see docs/subscription-tokens.md",
    ),
    "grok": Provider(
        name="grok",
        dir_env=None,
        default_subdir=".grok",
        filename="auth.json",
        token_url=None,  # always OIDC discovery off the doc's oidc_issuer
        read_expiry=_grok_expiry,
        read_refresh=_grok_refresh,
        read_issuer=_grok_issuer,
        apply=_grok_apply,
        read_blocker=_grok_blocker,
        cli_binary="grok",
        cli_refresh_args=None,
        reauth_command="grok CLI absent on the VPS: log in elsewhere, copy auth.json, see docs/subscription-tokens.md",
    ),
    "gemini": Provider(
        name="gemini",
        dir_env=None,
        default_subdir=".gemini/antigravity-cli",
        filename="antigravity-oauth-token",
        # Google's published OAuth 2.0 token endpoint.
        token_url="https://oauth2.googleapis.com/token",
        read_expiry=_gemini_expiry,
        read_refresh=_gemini_refresh,
        read_issuer=_no_issuer,
        apply=_gemini_apply,
        cli_binary="agy",
        cli_refresh_args=("models",),
        client_id=ANTIGRAVITY_CLIENT_ID,
        reauth_command="ssh -t radon@ib-gateway '~/.local/bin/agy -p ok' then open the printed URL and paste the code within 60s, see docs/subscription-tokens.md",
    ),
}


# ---------------------------------------------------------------------------
# vault
# ---------------------------------------------------------------------------


class Vault:
    """Thin adapter over the existing encrypted secret store."""

    def __init__(self, store) -> None:
        self._store = store

    def get(self, provider: str) -> Optional[str]:
        return self._store.get_secret(PROVIDERS[provider].secret_name)

    def seal(self, provider: str, value: str) -> None:
        self._store.set_secret(
            PROVIDERS[provider].secret_name, value, SERVICE_NAME
        )


def open_vault(env: Mapping[str, str]) -> Vault:
    # The import is INSIDE the guard: a venv rebuilt without `cryptography`, or
    # any import-time error in secret_store.py, is "the store could not be
    # opened" (R-621), not a traceback that skips the sidecar, the heartbeat,
    # exit 78 and the page.
    try:
        try:
            from .secret_store import SecretStore
        except ImportError:  # pragma: no cover - direct script execution
            from secret_store import SecretStore  # type: ignore
        return Vault(SecretStore())
    except Exception as exc:  # noqa: BLE001 - every open failure is fatal here
        raise VaultUnavailable(f"{type(exc).__name__}: {exc}") from exc


# ---------------------------------------------------------------------------
# transport
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class HttpResponse:
    status: int
    body: dict


def http_request(
    method: str,
    url: str,
    *,
    form: Optional[dict] = None,
    json_body: Optional[dict] = None,
    timeout: int = HTTP_TIMEOUT_SECONDS,
) -> HttpResponse:
    data = None
    headers = {"Accept": "application/json"}
    if form is not None:
        data = urllib_parse.urlencode(form).encode("utf-8")
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    elif json_body is not None:
        data = json.dumps(json_body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    # Belt and braces: nothing in this module may put a refresh token on a
    # plaintext connection, whatever a credential doc or a discovery document
    # claims the endpoint is.
    if not url.startswith("https://"):
        raise ValueError("refusing a non-https request")
    req = urllib_request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib_request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "replace")
            return HttpResponse(resp.status, _loads_or_empty(raw))
    except urllib_error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace") if exc.fp else ""
        return HttpResponse(exc.code, _loads_or_empty(raw))


def _loads_or_empty(raw: str) -> dict:
    try:
        parsed = json.loads(raw)
    except ValueError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def send_pushover(payload: Mapping[str, Any]) -> None:
    data = urllib_parse.urlencode(dict(payload)).encode("utf-8")
    req = urllib_request.Request(PUSHOVER_URL, data=data, method="POST")
    with urllib_request.urlopen(req, timeout=HTTP_TIMEOUT_SECONDS) as resp:
        if resp.status >= 300:
            raise RuntimeError(f"pushover returned HTTP {resp.status}")


def record_heartbeat(service: str, state: str, **kwargs) -> None:
    try:
        from .db import writer
    except ImportError:  # pragma: no cover - direct script execution
        from db import writer  # type: ignore
    # The literal constant, not the parameter: the watchdog catalog-parity
    # scanner resolves record_service_health(SERVICE_NAME, ...) statically and
    # cannot follow a name through a call argument.
    assert service == SERVICE_NAME
    writer.record_service_health(SERVICE_NAME, state, **kwargs)


def default_run_cli(provider: Provider) -> bool:
    """CLI-native refresh, preferred because the CLI owns its file format."""
    if not provider.cli_binary or not provider.cli_refresh_args:
        return False
    try:
        completed = subprocess.run(
            [provider.cli_binary, *provider.cli_refresh_args],
            capture_output=True,
            timeout=CLI_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return completed.returncode == 0


# ---------------------------------------------------------------------------
# atomic credential write
# ---------------------------------------------------------------------------


def atomic_write_credential(path: Path, text: str) -> None:
    """Same-directory temp file, fsync, replace. Mode 0600, parent 0700."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.is_symlink():
        # os.replace would happily rename over the link, and every later write
        # would follow it out of the 0700 directory.
        raise OSError(f"{path.name} is a symlink; refusing to write a credential")
    # mkstemp: a private, randomly-named inode (O_EXCL|O_NOFOLLOW). A fixed
    # ".radon-tmp" name let a pre-placed symlink redirect the plaintext
    # credential, and let a second writer share the inode and interleave bytes
    # straight into the live file after the first one renamed it.
    fd, tmp_name = tempfile.mkstemp(
        dir=str(path.parent), prefix="." + path.name + ".", suffix=".radon-tmp"
    )
    tmp = Path(tmp_name)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
        dir_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _key_paths(obj: Any, prefix: tuple = ()) -> set:
    paths: set = set()
    if isinstance(obj, Mapping):
        for key, value in obj.items():
            here = prefix + (str(key),)
            paths.add(here)
            paths |= _key_paths(value, here)
    return paths


# ---------------------------------------------------------------------------
# runtime
# ---------------------------------------------------------------------------


@dataclass
class Runtime:
    env: Mapping[str, str] = field(default_factory=lambda: dict(os.environ))
    vault: Optional[Any] = None
    http: Callable[..., HttpResponse] = http_request
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc)
    which: Callable[[str], Optional[str]] = None  # type: ignore[assignment]
    run_cli: Callable[[Provider], bool] = default_run_cli
    sidecar_path: Path = SIDECAR_PATH
    lock_path: Path = LOCK_PATH
    notify: Callable[[Mapping[str, Any]], Any] = send_pushover
    heartbeat: Callable[..., None] = record_heartbeat
    sleep: Callable[[float], None] = time.sleep

    def __post_init__(self) -> None:
        if self.which is None:
            import shutil

            self.which = shutil.which


@dataclass
class ProviderResult:
    provider: str
    state: str
    expires_at: Optional[str] = None
    last_refresh_at: Optional[str] = None
    last_error: Optional[str] = None


# ---------------------------------------------------------------------------
# engine
# ---------------------------------------------------------------------------


class _Run:
    def __init__(self, rt: Runtime, mode: str, force: bool = False) -> None:
        self.rt = rt
        self.mode = mode
        self.force = force
        self.now = rt.now()
        self.discovery: dict[str, str] = {}  # process-local, per run
        self.sidecar = self._load_sidecar()
        self.page_failures: list[str] = []

    # -- sidecar ---------------------------------------------------------

    def _load_sidecar(self) -> dict:
        try:
            data = verified_load(str(self.rt.sidecar_path))
        except (FileNotFoundError, ValueError, json.JSONDecodeError):
            data = {}
        data.setdefault("providers", {})
        data.setdefault("pages", {})
        return data

    def save_sidecar(self, results: Sequence[ProviderResult]) -> None:
        if self.mode == "check":
            # --check is a read-only observation. Writing here reset
            # consecutive_error_count, so a monitoring poll interleaved with the
            # timer meant a permanently broken provider never reached the third
            # strike and never paged.
            return
        for result in results:
            prior = self.sidecar["providers"].get(result.provider, {})
            errors = int(prior.get("consecutive_error_count") or 0)
            errors = errors + 1 if result.state == ERROR else 0
            self.sidecar["providers"][result.provider] = {
                "state": result.state,
                "expires_at": result.expires_at,
                "last_refresh_at": result.last_refresh_at
                or prior.get("last_refresh_at"),
                "consecutive_error_count": errors,
                "last_error": result.last_error,
            }
        self.sidecar["updated_at"] = self.now.isoformat()
        try:
            atomic_save(str(self.rt.sidecar_path), self.sidecar)
        except OSError as exc:
            log.warning("sidecar write failed: %s", exc)

    def error_streak(self, provider: str) -> int:
        prior = self.sidecar["providers"].get(provider, {})
        return int(prior.get("consecutive_error_count") or 0)

    # -- paging ----------------------------------------------------------

    def maybe_page(self, result: ProviderResult) -> None:
        if result.state not in (NEEDS_REAUTH, STORE_UNAVAILABLE, ERROR):
            return
        if result.state == ERROR:
            # Streak includes this run only after save_sidecar; add it here.
            if self.error_streak(result.provider) + 1 < ERROR_PAGE_AFTER:
                return
        last = _parse_rfc3339(self.sidecar["pages"].get(result.provider))
        if last and self.now - last < PAGE_COOLDOWN:
            return
        user = (self.rt.env.get("PUSHOVER_USER") or "").strip()
        token = (self.rt.env.get("PUSHOVER_TOKEN") or "").strip()
        if not user or not token:
            self.page_failures.append("pushover credentials missing")
            return
        provider = PROVIDERS.get(result.provider)
        command = provider.reauth_command if provider else ""
        message = (
            f"{result.provider}: {result.state}"
            + (f" ({result.last_error})" if result.last_error else "")
            + (f"\nRe-auth: {command}" if command else "")
        )
        payload = {
            "token": token,
            "user": user,
            "title": PUSHOVER_TITLE,
            "message": message,
            "priority": 0,
        }
        try:
            self.rt.notify(payload)
        except Exception as exc:  # noqa: BLE001 - paging must not kill the run
            self.page_failures.append(f"pushover delivery failed: {type(exc).__name__}")
            return
        self.sidecar["pages"][result.provider] = self.now.isoformat()

    # -- endpoint resolution --------------------------------------------

    def token_endpoint(self, provider: Provider, doc: Mapping[str, Any]) -> Optional[str]:
        issuer = provider.read_issuer(doc)
        if issuer:
            base = issuer[0].rstrip("/")
            if base in self.discovery:
                return self.discovery[base]
            # The issuer is attacker-influenced input (it comes out of a file on
            # disk). Discovery is unauthenticated, so without these two checks a
            # single MITM gets the long-lived refresh token POSTed to a host of
            # its choosing, in the clear.
            parsed_issuer = urllib_parse.urlsplit(base)
            if parsed_issuer.scheme != "https" or not parsed_issuer.netloc:
                log.error("%s: issuer is not https; refusing discovery", provider.name)
                return None
            url = f"{base}/.well-known/openid-configuration"
            resp = self.rt.http("GET", url, timeout=HTTP_TIMEOUT_SECONDS)
            endpoint = resp.body.get("token_endpoint") if resp.status == 200 else None
            if not isinstance(endpoint, str) or not endpoint:
                return None
            parsed_endpoint = urllib_parse.urlsplit(endpoint)
            if (
                parsed_endpoint.scheme != "https"
                or parsed_endpoint.netloc != parsed_issuer.netloc
            ):
                log.error(
                    "%s: discovered token_endpoint is not https on the issuer host",
                    provider.name,
                )
                return None
            self.discovery[base] = endpoint
            return endpoint
        return provider.token_url

    # -- refresh ---------------------------------------------------------

    def post_refresh(self, url: str, fields: dict) -> HttpResponse:
        """Form-encoded first; one JSON retry on 400/415 (providers differ)."""
        resp = self._post_with_backoff(url, form=fields)
        if resp.status in (400, 415) and resp.body.get("error") != "invalid_grant":
            resp = self._post_with_backoff(url, json_body=fields)
        return resp

    def _post_with_backoff(self, url: str, **kwargs) -> HttpResponse:
        last: Optional[HttpResponse] = None
        for attempt in range(HTTP_RETRIES + 1):
            try:
                last = self.rt.http(
                    "POST", url, timeout=HTTP_TIMEOUT_SECONDS, **kwargs
                )
            except (urllib_error.URLError, OSError) as exc:
                last = HttpResponse(0, {"error": type(exc).__name__})
            if last.status < 500 and last.status != 0:
                return last
            if attempt < HTTP_RETRIES:
                self.rt.sleep(2**attempt)
        return last or HttpResponse(0, {})


def _parse_doc(raw: Optional[str]) -> Optional[dict]:
    if raw is None:
        return None
    try:
        doc = json.loads(raw)
    except ValueError:
        return None
    return doc if isinstance(doc, dict) else None


def _seal_guarded(
    run: "_Run", provider: Provider, raw: str, doc: Optional[Mapping[str, Any]]
) -> Optional[str]:
    """Seal ``raw`` unless that would destroy the last refreshable copy.

    There is exactly one vault slot per provider, so an unconditional seal of
    whatever merely parses (an emptied file after ``claude logout``, a truncated
    write) permanently loses the only credential that could still be refreshed.
    Returns an error message class, or None when the vault now holds ``raw``.
    """
    vault_raw = run.rt.vault.get(provider.name)
    if vault_raw == raw:
        return None
    if doc is None or provider.read_refresh(doc) is None:
        stored = _parse_doc(vault_raw)
        if stored is not None and provider.read_refresh(stored) is not None:
            return "refusing to seal a credential with no refresh token"
    try:
        run.rt.vault.seal(provider.name, raw)
    except Exception as exc:  # noqa: BLE001 - a store failure is not a lost run
        return f"vault seal failed: {type(exc).__name__}"
    return None


def _read_doc(path: Path) -> tuple[Optional[str], Optional[dict]]:
    try:
        raw = path.read_text(encoding="utf-8")
    except (FileNotFoundError, NotADirectoryError, IsADirectoryError, OSError):
        return None, None
    try:
        doc = json.loads(raw)
    except ValueError:
        return raw, None
    return raw, (doc if isinstance(doc, dict) else None)


def _evaluate(run: _Run, provider: Provider) -> ProviderResult:
    rt = run.rt
    path = provider.path(rt.env)
    raw, doc = _read_doc(path)
    restored = False

    if doc is None:
        vault_raw = rt.vault.get(provider.name)
        if vault_raw is None:
            if raw is None:
                return ProviderResult(provider.name, UNBOOTSTRAPPED)
            return ProviderResult(
                provider.name, ERROR, last_error="credential file is unparsable"
            )
        if run.mode == "check":
            return ProviderResult(provider.name, EXPIRING, last_error="file missing")
        if raw is not None and run.error_streak(provider.name) < 1:
            # The file exists but does not parse. The provider's own CLI writing
            # it right now looks exactly like this, and racing it with the older
            # vault copy loses a credential it just minted. Report once; restore
            # on the next run if it is still unparsable.
            return ProviderResult(
                provider.name, ERROR, last_error="credential file is unparsable"
            )
        try:
            atomic_write_credential(path, vault_raw)
        except OSError as exc:
            return ProviderResult(
                provider.name, ERROR, last_error=f"restore write failed: {type(exc).__name__}"
            )
        raw, doc = _read_doc(path)
        if doc is None:
            return ProviderResult(
                provider.name, ERROR, last_error="vault copy is unparsable"
            )
        restored = True

    expiry = provider.read_expiry(doc)
    expires_at = expiry.isoformat() if expiry else None
    fresh = expiry is not None and (expiry - run.now).total_seconds() > EXPIRY_SKEW_SECONDS

    if fresh:
        # Seal a live credential too. A freshly logged-in token stays live for
        # its whole first hour, and nothing else would put it in the vault
        # before then, so a wipe inside that window would lose it outright.
        seal_error = None
        if run.mode != "check" and raw is not None:
            seal_error = _seal_guarded(run, provider, raw, doc)
        state = RESTORED if restored else LIVE
        return ProviderResult(
            provider.name, state, expires_at=expires_at, last_error=seal_error
        )

    if run.mode == "check":
        return ProviderResult(
            provider.name, EXPIRING, expires_at=expires_at, last_error="token expiring"
        )

    # Seal the known-good bytes BEFORE anything can overwrite them.
    if raw is not None:
        _seal_guarded(run, provider, raw, doc)

    blocked = provider.read_blocker(doc)
    if blocked:
        return ProviderResult(
            provider.name, NEEDS_REAUTH, expires_at=expires_at, last_error=blocked
        )

    # CLI-native refresh first: the CLI is the authority on its own file.
    if provider.cli_binary and rt.which(provider.cli_binary):
        try:
            ok = rt.run_cli(provider)
        except Exception as exc:  # noqa: BLE001
            log.warning("%s CLI refresh failed: %s", provider.name, type(exc).__name__)
            ok = False
        if ok:
            new_raw, new_doc = _read_doc(path)
            if new_doc is not None:
                # Continue from what the CLI wrote. Falling through with the
                # pre-CLI document would os.replace it back over the file and
                # persist a refresh token the CLI may already have rotated away.
                raw, doc = new_raw, new_doc
                new_expiry = provider.read_expiry(new_doc)
                expires_at = new_expiry.isoformat() if new_expiry else expires_at
                if new_expiry and (new_expiry - run.now).total_seconds() > EXPIRY_SKEW_SECONDS:
                    if new_raw is not None:
                        _seal_guarded(run, provider, new_raw, new_doc)
                    return ProviderResult(
                        provider.name,
                        REFRESHED,
                        expires_at=new_expiry.isoformat(),
                        last_refresh_at=run.now.isoformat(),
                    )

    refresh_token = provider.read_refresh(doc)
    if not refresh_token:
        return ProviderResult(
            provider.name,
            NEEDS_REAUTH,
            expires_at=expires_at,
            last_error="no refresh token in credential file",
        )

    endpoint = run.token_endpoint(provider, doc)
    if not endpoint:
        return ProviderResult(
            provider.name,
            NEEDS_REAUTH,
            expires_at=expires_at,
            last_error="no token endpoint could be resolved",
        )

    fields = provider.token_request(doc, refresh_token)
    resp = run.post_refresh(endpoint, fields)

    if resp.status in (400, 401):
        return ProviderResult(
            provider.name,
            NEEDS_REAUTH,
            expires_at=expires_at,
            last_error="refresh token rejected by the provider",
        )
    if resp.status != 200 or not resp.body.get("access_token"):
        return ProviderResult(
            provider.name,
            ERROR,
            expires_at=expires_at,
            last_error=f"refresh failed with HTTP {resp.status}",
        )

    try:
        new_doc = provider.apply(doc, resp.body, run.now)
    except Exception as exc:  # noqa: BLE001
        return ProviderResult(
            provider.name, ERROR, expires_at=expires_at,
            last_error=f"apply failed: {type(exc).__name__}",
        )

    if not _key_paths(new_doc) >= _key_paths(doc):
        return ProviderResult(
            provider.name,
            ERROR,
            expires_at=expires_at,
            last_error="refreshed document dropped a key; refusing to write",
        )

    text = json.dumps(new_doc, indent=2) + "\n"

    current_raw, _current_doc = _read_doc(path)
    if current_raw != raw:
        # Somebody else rewrote the credential while we were refreshing.
        # Replacing it now would silently drop their token.
        return ProviderResult(
            provider.name,
            ERROR,
            expires_at=expires_at,
            last_error="credential file changed during refresh; not writing",
        )

    # Vault ahead of disk, always. The refresh token we just presented may have
    # been invalidated by this very POST, so a crash after the write but before
    # the seal would leave the vault holding a corpse.
    seal_error = _seal_guarded(run, provider, text, new_doc)
    try:
        atomic_write_credential(path, text)
    except OSError as exc:
        return ProviderResult(
            provider.name,
            ERROR,
            expires_at=expires_at,
            last_error=f"write failed: {type(exc).__name__}",
        )
    new_expiry = provider.read_expiry(new_doc)
    return ProviderResult(
        provider.name,
        REFRESHED,
        expires_at=new_expiry.isoformat() if new_expiry else None,
        last_refresh_at=run.now.isoformat(),
        last_error=seal_error,
    )


def _seal(run: _Run, provider: Provider) -> ProviderResult:
    path = provider.path(run.rt.env)
    raw, doc = _read_doc(path)
    if raw is None:
        return ProviderResult(provider.name, UNBOOTSTRAPPED)
    if doc is None:
        return ProviderResult(
            provider.name, ERROR, last_error="credential file is unparsable"
        )
    seal_error = _seal_guarded(run, provider, raw, doc)
    expiry = provider.read_expiry(doc)
    if seal_error:
        return ProviderResult(
            provider.name, ERROR,
            expires_at=expiry.isoformat() if expiry else None,
            last_error=seal_error,
        )
    return ProviderResult(
        provider.name, LIVE, expires_at=expiry.isoformat() if expiry else None
    )


def _restore(run: _Run, provider: Provider) -> ProviderResult:
    raw = run.rt.vault.get(provider.name)
    if raw is None:
        return ProviderResult(provider.name, UNBOOTSTRAPPED)
    path = provider.path(run.rt.env)

    # The runbook tells the operator to re-auth and THEN --restore to "sync" it.
    # Blindly installing the vault copy at that moment destroys the credential
    # they just created, unsealed and unrecoverable.
    on_disk_raw, on_disk_doc = _read_doc(path)
    if on_disk_raw is not None and on_disk_raw != raw:
        if on_disk_doc is None:
            if not run.force:
                return ProviderResult(
                    provider.name,
                    ERROR,
                    last_error="on-disk credential is unparsable; pass --force to overwrite",
                )
        else:
            disk_expiry = provider.read_expiry(on_disk_doc)
            vault_expiry = provider.read_expiry(_parse_doc(raw) or {})
            if disk_expiry is not None and (
                vault_expiry is None or disk_expiry > vault_expiry
            ):
                if not run.force:
                    return ProviderResult(
                        provider.name,
                        ERROR,
                        expires_at=disk_expiry.isoformat(),
                        last_error="on-disk credential is newer than the vault copy; pass --force",
                    )
                # --force means the operator has chosen the vault copy over the
                # newer file, so the vault slot keeps the copy being installed.
                log.warning(
                    "%s: --force overwrites a newer on-disk credential", provider.name
                )
    try:
        atomic_write_credential(path, raw)
    except OSError as exc:
        return ProviderResult(
            provider.name, ERROR, last_error=f"restore write failed: {type(exc).__name__}"
        )
    _, doc = _read_doc(path)
    expiry = provider.read_expiry(doc or {})
    return ProviderResult(
        provider.name, RESTORED, expires_at=expiry.isoformat() if expiry else None
    )


@contextlib.contextmanager
def run_lock(path: Path):
    """Exclusive, non-blocking. Yields False when another run already holds it.

    The 30-minute timer and the runbook's manual --once/--seal/--restore overlap
    routinely. Two runs presenting the same refresh token to a provider that
    rotates it means the second gets invalid_grant and pages needs_reauth for a
    credential that is in fact live.
    """
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_CLOEXEC, 0o600)
    except OSError as exc:
        log.warning("run lock unavailable (%s); continuing", type(exc).__name__)
        yield True
        return
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            yield False
            return
        yield True
    finally:
        os.close(fd)


def exit_code_for(states: Iterable[str]) -> int:
    states = list(states)
    if STORE_UNAVAILABLE in states:
        return EXIT_CONFIG
    if all(state in HEALTHY_STATES for state in states):
        return EXIT_OK
    return EXIT_ATTENTION


def _health_state(states: Iterable[str]) -> str:
    return "ok" if all(s in HEALTHY_STATES for s in states) else "error"


def run(
    mode: str,
    provider_names: Optional[Sequence[str]] = None,
    runtime: Optional[Runtime] = None,
    json_output: bool = False,
    force: bool = False,
) -> dict:
    rt = runtime or Runtime()
    with run_lock(rt.lock_path) as acquired:
        if not acquired:
            report = {
                "service": SERVICE_NAME,
                "mode": mode,
                "generated_at": rt.now().isoformat(),
                "providers": [],
                "page_failures": [],
                "skipped": "another run holds the lock",
                "exit_code": EXIT_OK,
            }
            if json_output:
                print(json.dumps(report, indent=2, sort_keys=True))
            else:
                print("skipped: another run holds the lock")
            return report
        return _run_locked(mode, provider_names, rt, json_output, force)


def _run_locked(
    mode: str,
    provider_names: Optional[Sequence[str]],
    rt: Runtime,
    json_output: bool,
    force: bool,
) -> dict:
    names = list(provider_names or PROVIDERS.keys())
    active = _Run(rt, mode, force)

    results: list[ProviderResult] = []
    if rt.vault is None:
        try:
            rt.vault = open_vault(rt.env)
        except VaultUnavailable as exc:
            log.error("secret store unavailable: %s", exc)
            results = [
                ProviderResult(
                    name, STORE_UNAVAILABLE, last_error="secret store could not be opened"
                )
                for name in names
            ]

    if not results:
        handler = {"seal": _seal, "restore": _restore}.get(mode, _evaluate)
        for name in names:
            provider = PROVIDERS[name]
            try:
                results.append(handler(active, provider))
            except VaultUnavailable:
                results.append(
                    ProviderResult(
                        name, STORE_UNAVAILABLE,
                        last_error="secret store could not be opened",
                    )
                )
            except Exception as exc:  # noqa: BLE001 - one provider never kills the run
                log.error("%s evaluation failed: %s", name, type(exc).__name__)
                results.append(
                    ProviderResult(
                        name, ERROR, last_error=f"unhandled {type(exc).__name__}"
                    )
                )

    for result in results:
        active.maybe_page(result)
    active.save_sidecar(results)

    states = [r.state for r in results]
    report = {
        "service": SERVICE_NAME,
        "mode": mode,
        "generated_at": active.now.isoformat(),
        "providers": [vars(r) for r in results],
        "page_failures": active.page_failures,
        "exit_code": exit_code_for(states),
    }

    try:
        rt.heartbeat(
            SERVICE_NAME,
            _health_state(states),
            finished_at=active.now.isoformat(),
            error=(
                None
                if _health_state(states) == "ok"
                else {
                    "message": ", ".join(
                        f"{r.provider}={r.state}"
                        for r in results
                        if r.state not in HEALTHY_STATES
                    )
                }
            ),
        )
    except Exception as exc:  # noqa: BLE001 - a heartbeat never fails the run
        log.warning("service_health write failed: %s", type(exc).__name__)

    if json_output:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        for result in results:
            line = f"{result.provider}: {result.state}"
            if result.expires_at:
                line += f" (expires {result.expires_at})"
            if result.last_error:
                line += f" [{result.last_error}]"
            print(line)
    return report


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="scripts.subscription_tokens")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--check", action="store_true", help="report only, no writes")
    group.add_argument("--once", action="store_true", help="evaluate + heal, then exit")
    group.add_argument("--seal", metavar="PROVIDER", choices=sorted(PROVIDERS))
    group.add_argument("--restore", metavar="PROVIDER", choices=sorted(PROVIDERS))
    parser.add_argument("--json", action="store_true", help="machine-readable report")
    parser.add_argument(
        "--force",
        action="store_true",
        help="--restore only: overwrite an on-disk credential newer than the vault copy",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    if args.seal:
        mode, names = "seal", [args.seal]
    elif args.restore:
        mode, names = "restore", [args.restore]
    else:
        mode, names = ("check" if args.check else "once"), None
    return run(mode, names, None, json_output=args.json, force=args.force)["exit_code"]


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
