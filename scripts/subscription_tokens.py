#!/usr/bin/env python3
"""Durable subscription-token vault + autonomous refresh for the agent CLIs.

Claude Code, OpenAI Codex, xAI Grok and Google Antigravity authenticate against
the operator's *subscriptions*, storing an OAuth credential file under the
``radon`` home. Those files get deleted by host maintenance, expire when nothing
exercises the CLI, or land half-written. ``scripts/clients/model_ladder.py``
reads them directly, so a dead file silently demotes the whole subscription band
to metered API keys.

This module seals each credential file, verbatim, into the EXISTING encrypted
secret store (``scripts/secret_store.py`` — no second crypto system), refreshes
expiring tokens autonomously, restores deleted ones from the vault, proves each
login with a real model call once a day (which also keeps the grant from going
stale), and when a grant is truly dead starts the CLI's own login and pages the
operator the link, so the fix is one tap and not a file copy.

⛔ No token material is ever logged, raised, written to the sidecar, sent to
Pushover or recorded in ``service_health``. Use :func:`redact`.
"""

from __future__ import annotations

import argparse
import base64
import contextlib
import copy
import errno
import fcntl
import json
import logging
import os
import queue
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
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
# Kept beside the sidecar in the radon-owned state dir: /run/lock is 1777, so
# any local user could pre-hold the flock and turn every fire into a silent
# exit-0 skip.
LOCK_PATH = SIDECAR_PATH.parent / "run.lock"
EXPIRY_SKEW_SECONDS = 600
HTTP_TIMEOUT_SECONDS = 20
HTTP_RETRIES = 2
HTTP_USER_AGENT = "radon-subscription-tokens"
PAGE_COOLDOWN = timedelta(hours=12)
ERROR_PAGE_AFTER = 3
PUSHOVER_URL = "https://api.pushover.net/1/messages.json"
PUSHOVER_TITLE = "radon subscription token"
# Pushover's documented field limits. A Google consent URL is longer than the
# url field, so it rides in the message, which clients auto-link.
PUSHOVER_URL_FIELD_MAX = 512
PUSHOVER_MESSAGE_MAX = 1024

# Codex's own guidance is a weekly exercise of the login, Google retires a
# refresh token after six idle months and grok defaults to a 30-day credential.
# Daily is far inside all three and costs one one-word reply per subscription.
KEEPALIVE_INTERVAL = timedelta(hours=24)
PROBE_PROMPT = "Reply with the single word ok"
# A logged-out codex retries for ~20s and an agent turn can take a minute.
PROBE_TIMEOUT_SECONDS = 150
PROBE_OUTPUT_BYTES = 65536
# A login no probe has been able to prove for this long stops being called
# live: a vendor that rewords its auth error makes every probe "inconclusive".
KEEPALIVE_UNPROVEN_LIMIT = timedelta(hours=72)
# Discovery + the form and JSON POST envelopes; no refresh starts with less.
REFRESH_BUDGET_SECONDS = 170
# One login per run, bounded, because it holds the oneshot and the run lock.
LOGIN_WAIT_SECONDS = 600
# Too short to read a push and approve it: page the command instead.
MIN_LOGIN_WAIT_SECONDS = 120
# The unit kills the oneshot at TimeoutStartSec=1700, under its 30min slot. CLI
# work is only started while it can finish inside this, so a slow run ends with
# a sidecar and a heartbeat and never with a SIGKILL.
RUN_BUDGET_SECONDS = 1500
# grok and agy install here, which systemd's PATH does not carry.
LOCAL_BIN_DIRS = (".local/bin", ".grok/bin")
# Everything a third-party agent CLI may inherit. /etc/radon/env carries metered
# API keys that OUTRANK the subscription login (so a probe would bill the key
# and prove nothing about the login) plus every other Radon secret.
CLI_ENV_ALLOWLIST = (
    "HOME", "USER", "LOGNAME", "SHELL", "LANG", "LC_ALL", "TERM", "TMPDIR",
    "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME", "XDG_RUNTIME_DIR",
    "HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY", "https_proxy", "http_proxy", "no_proxy",
    "SSL_CERT_FILE", "SSL_CERT_DIR", "NODE_EXTRA_CA_CERTS",
)

PROBE_OK = "ok"
PROBE_AUTH_FAILED = "auth_failed"
PROBE_FAILED = "failed"  # inconclusive: a usage cap, an outage, a timeout

# The only refresh responses that mean the grant itself is dead. Anything else
# in the 4xx range is a request the provider could not parse, which is a bug
# here and says nothing about the operator's login.
DEAD_GRANT_CODES = frozenset(
    {
        "invalid_grant",
        "token_expired",
        "refresh_token_expired",
        "refresh_token_reused",
        "refresh_token_invalidated",
    }
)
# The provider refused OUR client id, whatever the status: a retired public
# client is fixed in this table, never by the operator logging in again.
CLIENT_REJECTED_CODES = frozenset({"invalid_client", "unauthorized_client"})

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


class DiscoveryUnavailable(RuntimeError):
    """OIDC discovery did not answer. An outage, never a reason to log in again."""


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


def _parse_epoch_seconds(value: Any) -> Optional[datetime]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        return datetime.fromtimestamp(value, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        # An absurd claim is "no expiry known", not a provider stuck in error.
        return None


def _parse_epoch_millis(value: Any) -> Optional[datetime]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return _parse_epoch_seconds(value / 1000.0)


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


def _jwt_expiry(token: Any) -> Optional[datetime]:
    """The ``exp`` claim, unverified: a scheduling hint, never an auth decision."""
    if not isinstance(token, str) or token.count(".") != 2:
        return None
    payload = token.split(".")[1]
    try:
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    except ValueError:
        return None
    return _parse_epoch_seconds(claims.get("exp") if isinstance(claims, dict) else None)


def _codex_expiry(doc):
    tokens = _codex_tokens(doc)
    # codex writes no expiry field; its access token is a JWT that lives ten
    # days. Guessing last_refresh + 1h refreshed fifty minutes after every login.
    explicit = (
        _jwt_expiry(tokens.get("access_token"))
        or _parse_rfc3339(tokens.get("expires_at"))
        or _parse_epoch_millis(tokens.get("expires_at"))
    )
    if explicit:
        return explicit
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
    # Only a key codex itself wrote: this is codex's file, parsed by codex.
    if "expires_at" in tokens:
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
    # The cheapest real authenticated call. No vendor ships a "refresh" subcommand, but
    # every CLI refreshes its own file on use, so this one command is the
    # CLI-native refresh, the liveness proof and the keepalive.
    probe_args: tuple
    # Lower-cased fragments of what the CLI prints when the LOGIN is the
    # problem, each captured from the real binary against a throwaway home.
    auth_failure_markers: tuple
    reauth_command: str
    # Public OAuth client id for the refresh grant (docs/oauth-subscription-auth.md).
    # The three endpoints that take one answer 400 invalid_request without it.
    client_id: Optional[str] = None
    # A login the CLI can finish with no input on its stdin: it prints a link,
    # the operator approves it in any browser, the CLI writes the file. None
    # for a paste-the-code-back login, which no push can complete.
    login_args: Optional[tuple] = None
    login_hosts: tuple = ()
    login_needs_code: bool = False
    # Returns a message when the doc is a shape this adapter refuses to refresh
    # in place; the provider then reports needs_reauth instead of half-healing.
    read_blocker: Callable[[Mapping[str, Any]], Optional[str]] = _no_blocker

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
        probe_args=("-p", PROBE_PROMPT, "--max-turns", "1"),
        auth_failure_markers=(
            "not logged in",
            "login expired",
            "please run /login",
            "oauth token has expired",
            "authentication_error",
        ),
        # `claude setup-token` only PRINTS a token; this is the command that
        # writes .credentials.json. It wants the code pasted back, so there is
        # no push login for claude.
        reauth_command="ssh -t radon@ib-gateway 'claude auth login --claudeai'",
        client_id="9d1c250a-e61b-44d9-88ed-5944d1962f5e",
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
        probe_args=("exec", "--skip-git-repo-check", PROBE_PROMPT),
        auth_failure_markers=(
            "401 unauthorized",
            "not logged in",
            "token_expired",
            "please try signing in again",
        ),
        reauth_command="ssh -t radon@ib-gateway 'codex login --device-auth'",
        client_id="app_EMoamEEZ73f0CkXaXp7hrann",
        login_args=("login", "--device-auth"),
        login_hosts=("auth.openai.com",),
        login_needs_code=True,  # the device URL is fixed; the code is separate
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
        probe_args=("-p", PROBE_PROMPT),
        auth_failure_markers=("not signed in", "grok login"),
        reauth_command="ssh -t radon@ib-gateway '~/.local/bin/grok login --device-auth'",
        login_args=("login", "--device-auth"),
        login_hosts=("accounts.x.ai",),
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
        # Not a model call: `agy models` is a sub-second authenticated request
        # that rewrites the token file, so it refreshes and proves the grant.
        probe_args=("models",),
        auth_failure_markers=(
            "please sign in",  # `agy models`
            "authentication required",  # `agy -p`
            "authentication failed",
        ),
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
    # An explicit agent: edge bot filters routinely refuse "Python-urllib".
    headers = {"Accept": "application/json", "User-Agent": HTTP_USER_AGENT}
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


# ---------------------------------------------------------------------------
# the provider's own CLI: probe + login
# ---------------------------------------------------------------------------


def _home(env: Mapping[str, str]) -> Path:
    home = (env.get("HOME") or "").strip()
    return Path(home) if home else Path.home()


def cli_search_path(env: Mapping[str, str]) -> str:
    local = [str(_home(env) / subdir) for subdir in LOCAL_BIN_DIRS]
    inherited = (env.get("PATH") or os.defpath).split(os.pathsep)
    return os.pathsep.join(local + inherited)


def cli_env(provider: Provider, env: Mapping[str, str]) -> dict:
    """The environment a provider CLI runs in: an allowlist, never a scrub."""
    allowed = CLI_ENV_ALLOWLIST + ((provider.dir_env,) if provider.dir_env else ())
    child = {key: env[key] for key in allowed if env.get(key)}
    child["HOME"] = str(_home(env))
    child["PATH"] = cli_search_path(env)
    return child


@contextlib.contextmanager
def _provider_cli(
    provider: Provider,
    binary: str,
    args: Sequence[str],
    env: Mapping[str, str],
    stdout: Any = subprocess.PIPE,
):
    """Run the CLI in its own session and an empty directory; always reap it.

    An agent CLI spawns helpers (node, language servers) and must never start
    inside the repo. The whole process GROUP is killed on the way out, even when
    the CLI itself already exited: a helper it left behind would otherwise
    outlive the run and race the directory cleanup.
    """
    with tempfile.TemporaryDirectory(
        prefix="radon-subscription-cli-", ignore_cleanup_errors=True
    ) as workdir:
        proc = subprocess.Popen(
            [binary, *args],
            stdin=subprocess.DEVNULL,
            stdout=stdout,
            stderr=subprocess.STDOUT,
            env=cli_env(provider, env),
            cwd=workdir,
            start_new_session=True,
        )
        try:
            yield proc
        finally:
            with contextlib.suppress(OSError):
                os.killpg(proc.pid, signal.SIGKILL)
            with contextlib.suppress(OSError, subprocess.SubprocessError):
                proc.wait(timeout=5)


def default_probe(
    provider: Provider,
    binary: str,
    env: Mapping[str, str],
    timeout: int = PROBE_TIMEOUT_SECONDS,
) -> str:
    """One real model call. The output is classified and then discarded."""
    # Output goes to a file and the wait is on the PROCESS: a helper that keeps
    # the CLI's stdout open would hold a pipe read until the timeout and turn a
    # successful call into a failure, and a chatty CLI cannot fill a pipe.
    try:
        with tempfile.TemporaryFile() as captured, _provider_cli(
            provider, binary, provider.probe_args, env, stdout=captured
        ) as proc:
            returncode = proc.wait(timeout=timeout)
            captured.seek(0)
            output = captured.read(PROBE_OUTPUT_BYTES)
    except (OSError, subprocess.TimeoutExpired):
        return PROBE_FAILED
    if returncode == 0:
        return PROBE_OK
    lowered = output.decode("utf-8", "replace").lower()
    if any(marker in lowered for marker in provider.auth_failure_markers):
        return PROBE_AUTH_FAILED
    return PROBE_FAILED


@dataclass(frozen=True)
class LoginPrompt:
    url: str
    code: Optional[str]


# CSI colour codes and OSC sequences (OSC 8 is how a terminal hyperlink is
# written, and it carries a second copy of the URL wrapped in escape bytes).
_ANSI = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b\[[0-9;?]*[A-Za-z]")
# Both must be followed by whitespace: output arrives in arbitrary chunks, and a
# link or code cut off mid-read must never be the one that gets pushed.
_URL = re.compile(r"https://[^\s\x00-\x1f\x7f\"'<>]+(?=\s)")
_SENTENCE_PUNCTUATION = ".,;)]"
_DEVICE_CODE = re.compile(r"(?<![A-Za-z0-9-])[A-Z0-9]{4}-[A-Z0-9]{4,6}(?=\s)")


def parse_login_prompt(provider: Provider, output: str) -> Optional[LoginPrompt]:
    """The login link a CLI printed, or None until it has printed all of it.

    The link goes to the operator's phone, so only a URL on the provider's own
    login host is ever returned: CLI output is not a trusted source of links.
    """
    text = _ANSI.sub("", output).replace("\r", "")
    url = next(
        (
            candidate
            for candidate in (
                found.rstrip(_SENTENCE_PUNCTUATION) for found in _URL.findall(text)
            )
            if urllib_parse.urlsplit(candidate).hostname in provider.login_hosts
        ),
        None,
    )
    if url is None:
        return None
    # The code the link itself carries outranks any look-alike in a banner.
    in_url = urllib_parse.parse_qs(urllib_parse.urlsplit(url).query).get("user_code")
    match = _DEVICE_CODE.search(text.replace(url, " "))
    code = in_url[0] if in_url else (match.group(0) if match else None)
    if provider.login_needs_code and code is None:
        return None
    return LoginPrompt(url, code)


def _page_body(result: "ProviderResult", prompt: Optional[LoginPrompt]) -> dict:
    """Pushover message fields. With a prompt, the page IS the login link."""
    provider = PROVIDERS.get(result.provider)
    command = provider.reauth_command if provider else ""
    headline = f"{result.provider}: {result.state}" + (
        f" ({result.last_error})" if result.last_error else ""
    )
    if prompt is None:
        return {"message": headline + (f"\nRe-auth: {command}" if command else "")}
    steps = "Tap the link to sign in" + (
        f", then enter code {prompt.code}." if prompt.code else "."
    )
    fallback = f"\nFallback: {command}" if command else ""
    if len(prompt.url) <= PUSHOVER_URL_FIELD_MAX:
        return {
            "message": f"{headline}\n{steps}{fallback}",
            "url": prompt.url,
            "url_title": f"Sign in to {result.provider}",
        }
    message = f"{steps}\n{prompt.url}"
    if len(message) > PUSHOVER_MESSAGE_MAX:
        return _page_body(result, None)
    for optional in (fallback, f"\n{headline}"):
        if len(message) + len(optional) <= PUSHOVER_MESSAGE_MAX:
            message += optional
    return {"message": message}


def _pump_output(stream, chunks: "queue.Queue[Optional[str]]") -> None:
    # Raw reads, not lines: a CLI that ends its prompt with a spinner and no
    # newline would otherwise sit unread until the login timed out.
    with contextlib.suppress(ValueError, OSError):
        while chunk := os.read(stream.fileno(), 4096):
            chunks.put(chunk.decode("utf-8", "replace"))
    chunks.put(None)


def default_login(
    provider: Provider,
    binary: str,
    env: Mapping[str, str],
    on_prompt: Callable[[LoginPrompt], Any],
    wait_seconds: float,
) -> bool:
    """Start the CLI's own login, hand its link to ``on_prompt``, await approval."""
    if not provider.login_args:
        return False
    deadline = time.monotonic() + wait_seconds
    try:
        with _provider_cli(provider, binary, provider.login_args, env) as proc:
            chunks: "queue.Queue[Optional[str]]" = queue.Queue()
            threading.Thread(
                target=_pump_output, args=(proc.stdout, chunks), daemon=True
            ).start()
            seen, prompted = "", False
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                try:
                    chunk = chunks.get(timeout=min(remaining, 1.0))
                except queue.Empty:
                    continue
                if chunk is None:
                    break
                if prompted:
                    continue
                seen += chunk
                prompt = parse_login_prompt(provider, seen)
                if prompt is not None:
                    prompted = True
                    on_prompt(prompt)
            try:
                return proc.wait(timeout=max(deadline - time.monotonic(), 0.1)) == 0
            except subprocess.TimeoutExpired:
                return False
    except OSError:
        return False


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
    probe: Callable[[Provider, str, Mapping[str, str]], str] = default_probe
    login: Callable[..., bool] = default_login
    sidecar_path: Path = SIDECAR_PATH
    lock_path: Path = LOCK_PATH
    notify: Callable[[Mapping[str, Any]], Any] = send_pushover
    heartbeat: Callable[..., None] = record_heartbeat
    sleep: Callable[[float], None] = time.sleep
    monotonic: Callable[[], float] = time.monotonic

    def __post_init__(self) -> None:
        if self.which is None:
            self.which = lambda binary: shutil.which(
                binary, path=cli_search_path(self.env)
            )


@dataclass
class ProviderResult:
    provider: str
    state: str
    expires_at: Optional[str] = None
    last_refresh_at: Optional[str] = None
    last_error: Optional[str] = None
    last_probe_at: Optional[str] = None
    probe_inconclusive: bool = False


# ---------------------------------------------------------------------------
# engine
# ---------------------------------------------------------------------------


def _unproven_since(result: ProviderResult, prior: Mapping[str, Any], now: datetime) -> Optional[str]:
    """When the current streak of inconclusive keepalive probes began."""
    if result.last_probe_at:
        return None
    if result.probe_inconclusive:
        return prior.get("unproven_since") or now.isoformat()
    return prior.get("unproven_since")


class _Run:
    def __init__(self, rt: Runtime, mode: str, force: bool = False) -> None:
        self.rt = rt
        self.mode = mode
        self.force = force
        self.now = rt.now()
        self.discovery: dict[str, str] = {}  # process-local, per run
        self.sidecar = self._load_sidecar()
        self.page_failures: list[str] = []
        self.login_attempted = False
        self.login_deferred: set[str] = set()
        self.started = rt.monotonic()

    def seconds_left(self) -> float:
        return RUN_BUDGET_SECONDS - (self.rt.monotonic() - self.started)

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
                "last_probe_at": result.last_probe_at or prior.get("last_probe_at"),
                "unproven_since": _unproven_since(result, prior, self.now),
            }
        self.sidecar["updated_at"] = self.now.isoformat()
        try:
            atomic_save(str(self.rt.sidecar_path), self.sidecar)
        except OSError as exc:
            log.warning("sidecar write failed: %s", exc)

    def error_streak(self, provider: str) -> int:
        prior = self.sidecar["providers"].get(provider, {})
        return int(prior.get("consecutive_error_count") or 0)

    # -- keepalive -------------------------------------------------------

    def unproven_since(self, provider: str) -> Optional[datetime]:
        prior = self.sidecar["providers"].get(provider, {})
        return _parse_rfc3339(prior.get("unproven_since"))

    def is_keepalive_due(self, provider: str) -> bool:
        prior = self.sidecar["providers"].get(provider, {})
        last = _parse_rfc3339(prior.get("last_probe_at"))
        return last is None or self.now - last >= KEEPALIVE_INTERVAL

    def probe(self, provider: Provider) -> Optional[str]:
        """The probe outcome, or None when no probe could be run at all."""
        binary = self.rt.which(provider.cli_binary) if provider.cli_binary else None
        if not binary or self.seconds_left() < PROBE_TIMEOUT_SECONDS:
            return None
        try:
            return self.rt.probe(provider, binary, self.rt.env)
        except Exception as exc:  # noqa: BLE001 - a probe never fails the run
            log.warning("%s probe failed: %s", provider.name, type(exc).__name__)
            return PROBE_FAILED

    # -- paging ----------------------------------------------------------

    def is_page_due(self, result: ProviderResult, ignore_cooldown: bool = False) -> bool:
        if result.state not in (NEEDS_REAUTH, STORE_UNAVAILABLE, ERROR):
            return False
        if result.state == ERROR:
            # Streak includes this run only after save_sidecar; add it here.
            if self.error_streak(result.provider) + 1 < ERROR_PAGE_AFTER:
                return False
        last = _parse_rfc3339(self.sidecar["pages"].get(result.provider))
        return ignore_cooldown or not (last and self.now - last < PAGE_COOLDOWN)

    def pushover_credentials(self) -> Optional[tuple]:
        user = (self.rt.env.get("PUSHOVER_USER") or "").strip()
        token = (self.rt.env.get("PUSHOVER_TOKEN") or "").strip()
        return (user, token) if user and token else None

    def maybe_page(self, result: ProviderResult) -> None:
        # --check is an observation: it stores no cooldown, so it would re-page
        # on every poll.
        if self.mode == "check" or result.provider in self.login_deferred:
            return
        if self.is_page_due(result):
            self.page(result)

    def page(self, result: ProviderResult, prompt: Optional[LoginPrompt] = None) -> None:
        credentials = self.pushover_credentials()
        if credentials is None:
            self.page_failures.append("pushover credentials missing")
            return
        user, token = credentials
        payload = {
            "token": token,
            "user": user,
            "title": PUSHOVER_TITLE,
            "priority": 0,
            **_page_body(result, prompt),
        }
        try:
            self.rt.notify(payload)
        except Exception as exc:  # noqa: BLE001 - paging must not kill the run
            self.page_failures.append(f"pushover delivery failed: {type(exc).__name__}")
            return
        self.sidecar["pages"][result.provider] = self.now.isoformat()

    # -- push login ------------------------------------------------------

    def login_binary(self, result: ProviderResult) -> Optional[str]:
        """The CLI to log in with, when a push login can fix this result."""
        provider = PROVIDERS.get(result.provider)
        if result.state != NEEDS_REAUTH or provider is None or not provider.login_args:
            return None
        return self.rt.which(provider.cli_binary) if provider.cli_binary else None

    def login_wait(self) -> Optional[float]:
        """How long a login may hold this run, or None when that is too short."""
        wait = min(LOGIN_WAIT_SECONDS, self.seconds_left())
        return wait if wait >= MIN_LOGIN_WAIT_SECONDS else None

    def login_by_push(
        self, result: ProviderResult, binary: str, on_prompt: Callable[[LoginPrompt], Any]
    ) -> bool:
        wait = self.login_wait()
        if wait is None:
            return False
        self.login_attempted = True
        try:
            return bool(
                self.rt.login(
                    PROVIDERS[result.provider], binary, self.rt.env, on_prompt, wait
                )
            )
        except Exception as exc:  # noqa: BLE001 - a login never fails the run
            log.warning("%s login failed: %s", result.provider, type(exc).__name__)
            return False

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
                # The issuer being down says nothing about the login.
                raise DiscoveryUnavailable(f"discovery answered HTTP {resp.status}")
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
        # A dead grant is final. Presenting the same refresh token again would
        # let the second answer decide, and can trip reuse detection.
        if resp.status in (400, 415) and not _is_dead_grant(resp):
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


def _is_usable(run: _Run, provider: Provider, doc: Mapping[str, Any]) -> bool:
    expiry = provider.read_expiry(doc)
    return expiry is not None and (expiry - run.now).total_seconds() > EXPIRY_SKEW_SECONDS


def _error_code(resp: HttpResponse) -> Optional[str]:
    error = resp.body.get("error")
    code = error.get("code") if isinstance(error, Mapping) else error
    return code if isinstance(code, str) else None


def _is_dead_grant(resp: HttpResponse) -> bool:
    code = _error_code(resp)
    if code in CLIENT_REJECTED_CODES:
        return False
    return resp.status == 401 or (resp.status == 400 and code in DEAD_GRANT_CODES)


def _keepalive(
    run: _Run, provider: Provider, path: Path, result: ProviderResult
) -> ProviderResult:
    """Prove a credential that LOOKS live with one real call, once a day.

    An expiry field in the future says nothing about a revoked grant or a lapsed
    subscription, and an unexercised refresh token is what goes stale.
    """
    if not run.is_keepalive_due(provider.name):
        return result
    outcome = run.probe(provider)
    if outcome is None:
        return result
    if outcome == PROBE_AUTH_FAILED:
        return ProviderResult(
            provider.name,
            NEEDS_REAUTH,
            expires_at=result.expires_at,
            last_error="credential looks live but the provider rejected it",
        )
    probed_raw, probed_doc = _read_doc(path)
    if probed_raw is not None and probed_doc is not None:
        # The CLI may have rotated the refresh token, whether or not the model
        # call then succeeded; the vault must never be left holding the old one.
        result.last_error = _seal_guarded(run, provider, probed_raw, probed_doc)
        probed_expiry = provider.read_expiry(probed_doc)
        result.expires_at = probed_expiry.isoformat() if probed_expiry else result.expires_at
    if outcome == PROBE_OK:
        result.last_probe_at = run.now.isoformat()
        return result
    # A usage cap or a vendor outage: not evidence about the login. Left
    # unstamped so the next run asks again, but not forever.
    result.probe_inconclusive = True
    unproven_since = run.unproven_since(provider.name)
    if unproven_since and run.now - unproven_since >= KEEPALIVE_UNPROVEN_LIMIT:
        result.state = ERROR
        result.last_error = "login unproven: every keepalive probe since %s was inconclusive" % (
            unproven_since.date().isoformat()
        )
        return result
    result.last_error = result.last_error or "keepalive probe inconclusive"
    return result


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

    if _is_usable(run, provider, doc):
        # Seal a live credential too. A freshly logged-in token stays live for
        # its whole first hour, and nothing else would put it in the vault
        # before then, so a wipe inside that window would lose it outright.
        seal_error = None
        if run.mode != "check" and raw is not None:
            seal_error = _seal_guarded(run, provider, raw, doc)
        state = RESTORED if restored else LIVE
        result = ProviderResult(
            provider.name, state, expires_at=expires_at, last_error=seal_error
        )
        return _keepalive(run, provider, path, result) if run.mode == "once" else result

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

    # CLI-native refresh first: the CLI is the authority on its own file, and a
    # real model call refreshes it as a side effect.
    outcome = run.probe(provider)
    if outcome is not None:
        new_raw, new_doc = _read_doc(path)
        if new_raw is not None and new_doc is not None:
            # Continue from what the CLI wrote, WHATEVER the probe reported: a
            # CLI that rotated its refresh token and then hit a usage cap still
            # rotated it. Falling through with the pre-CLI document would
            # present a token that no longer exists (reuse detection can revoke
            # the whole family) and then os.replace it back over the file.
            raw, doc = new_raw, new_doc
            _seal_guarded(run, provider, new_raw, new_doc)
            new_expiry = provider.read_expiry(new_doc)
            expires_at = new_expiry.isoformat() if new_expiry else expires_at
            if outcome != PROBE_AUTH_FAILED and _is_usable(run, provider, new_doc):
                return ProviderResult(
                    provider.name,
                    REFRESHED,
                    expires_at=expires_at,
                    last_refresh_at=run.now.isoformat(),
                    last_probe_at=run.now.isoformat() if outcome == PROBE_OK else None,
                )

    refresh_token = provider.read_refresh(doc)
    if not refresh_token:
        return ProviderResult(
            provider.name,
            NEEDS_REAUTH,
            expires_at=expires_at,
            last_error="no refresh token in credential file",
        )

    if run.seconds_left() < REFRESH_BUDGET_SECONDS:
        return ProviderResult(
            provider.name, ERROR, expires_at=expires_at,
            last_error="run budget exhausted before the refresh; next run retries",
        )

    try:
        endpoint = run.token_endpoint(provider, doc)
    except DiscoveryUnavailable as exc:
        return ProviderResult(
            provider.name, ERROR, expires_at=expires_at, last_error=str(exc)
        )
    if not endpoint:
        return ProviderResult(
            provider.name,
            NEEDS_REAUTH,
            expires_at=expires_at,
            last_error="no token endpoint could be resolved",
        )

    fields = provider.token_request(doc, refresh_token)
    resp = run.post_refresh(endpoint, fields)

    if _is_dead_grant(resp):
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


def _heal_by_push(run: _Run, result: ProviderResult) -> ProviderResult:
    """Turn a needs_reauth page into a login link the operator taps once."""
    binary = run.login_binary(result)
    if (
        run.mode != "once"
        or binary is None
        or not run.is_page_due(result)
        or run.pushover_credentials() is None
    ):
        return result
    if run.login_attempted:
        # Its turn comes next run. A plain page now would stamp the 12h cooldown
        # and, with a fixed evaluation order, starve it of a link for good.
        run.login_deferred.add(result.provider)
        return result
    is_approved = run.login_by_push(
        result, binary, lambda prompt: run.page(result, prompt)
    )
    return _evaluate(run, PROVIDERS[result.provider]) if is_approved else result


def _reauth(run: _Run, provider: Provider) -> ProviderResult:
    """Operator-started login (a missed link, a first bootstrap). Vault-free.

    It runs from a bare shell, where the store key is unavailable by design, so
    it only reports the file; the next timer run seals it.
    """
    needs_login = ProviderResult(provider.name, NEEDS_REAUTH)
    binary = run.login_binary(needs_login)
    if binary is None:
        return ProviderResult(
            provider.name, ERROR,
            last_error=f"no push login for this provider; run: {provider.reauth_command}",
        )

    def announce(prompt: LoginPrompt) -> None:
        # stderr: stdout is the report, which --json promises is one document.
        print(
            f"{provider.name}: open {prompt.url}" + (f" code {prompt.code}" if prompt.code else ""),
            file=sys.stderr,
        )
        run.page(needs_login, prompt)

    if not run.login_by_push(needs_login, binary, announce):
        return ProviderResult(provider.name, NEEDS_REAUTH, last_error="login was not approved")
    _raw, doc = _read_doc(provider.path(run.rt.env))
    if doc is None or not _is_usable(run, provider, doc):
        return ProviderResult(provider.name, ERROR, last_error="login finished but wrote no usable credential")
    expiry = provider.read_expiry(doc)
    return ProviderResult(provider.name, LIVE, expires_at=expiry.isoformat() if expiry else None)


class RunLockUnavailable(RuntimeError):
    """Serialization could not be established; no credential work is safe."""


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
        raise RunLockUnavailable("subscription refresh lock unavailable") from exc
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            if exc.errno not in (errno.EACCES, errno.EAGAIN):
                raise RunLockUnavailable("subscription refresh lock unavailable") from exc
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
    try:
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
    except RunLockUnavailable:
        message = "subscription refresh lock unavailable"
        log.error(message)
        report = {
            "service": SERVICE_NAME,
            "mode": mode,
            "generated_at": rt.now().isoformat(),
            "providers": [],
            "page_failures": [],
            "error": message,
            "exit_code": EXIT_CONFIG,
        }
        try:
            rt.heartbeat(SERVICE_NAME, "error", finished_at=report["generated_at"], error={"message": message})
        except Exception as exc:  # same best-effort health transport as a normal run
            log.warning("service_health write failed: %s", type(exc).__name__)
        if json_output:
            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            print(message)
        return report


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
    if rt.vault is None and mode != "reauth":
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
        handler = {"seal": _seal, "restore": _restore, "reauth": _reauth}.get(
            mode, _evaluate
        )
        for name in names:
            provider = PROVIDERS[name]
            try:
                results.append(_heal_by_push(active, handler(active, provider)))
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
    group.add_argument(
        "--reauth",
        metavar="PROVIDER",
        choices=sorted(PROVIDERS),
        help="start the provider's push login now, ignoring the page cooldown",
    )
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
    elif args.reauth:
        mode, names = "reauth", [args.reauth]
    else:
        mode, names = ("check" if args.check else "once"), None
    return run(mode, names, None, json_output=args.json, force=args.force)["exit_code"]


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
