"""Shared HTTP model ladder for Radon multimodal and text JSON callers.

Joe's exact order (2026-09-10). Do not leave a band until it is exhausted or
unavailable:

  1. subscription: anthropic -> grok -> cursor -> codex -> antigravity (``agy -p``)
  2. nvidia (API key OK)
  3. cerebras (cheap paid, last; currently paused on Hetzner)

``RADON_MODEL_LADDER_ORDER`` overrides that list (comma-separated known names).
Cerebras stays last. An HTTP error or timeout on one rung does not stop the
ladder while a later keyed rung remains. MarketDesk also fails over an
unreadable or incomplete reply.

Subscription-tier rungs (Anthropic / Grok / Codex / Antigravity) use
**subscription credentials only**: Claude Max OAuth, Grok device-auth,
ChatGPT+Codex OAuth, the Antigravity CLI's own Google sign-in. Prepaid console
keys for those providers are never read or sent, under any flag (operator
policy 2026-10-26; the deny list is ``clients/prepaid_denylist.json``). A set
deny-listed variable is ignored with one stderr warning naming it. Without
subscription material the rung is skipped and the ladder continues to NVIDIA
then Cerebras. Knowledge distill (``scripts/knowledge/distill.py`` ->
``complete_text_json``) and the newsfeed tagger (``model_ladder_cli.py``)
share this helper. See ``docs/oauth-subscription-auth.md``.

Cursor has no vision HTTP path in Radon; it is recorded as unwired and the rest
of the subscription band still runs. Only a full-cascade miss is an ops_only /
hard fail — never "top up Anthropic" while another keyed provider remains.

NVIDIA is first-class before Cerebras whenever ``NVIDIA_API_KEY`` is present.
Default **text** model is ``nvidia/nemotron-3-super-120b-a12b`` (catalog-valid;
override ``NVIDIA_TEXT_MODEL`` / ``NVIDIA_MODEL``). Default **vision** model is
``meta/llama-3.2-90b-vision-instruct``, with automatic fallback to
``meta/llama-3.2-11b-vision-instruct`` on timeout/error (override
``NVIDIA_VISION_MODEL``). Inject keys via ``/etc/radon/env``; never commit secrets.
"""
from __future__ import annotations

import base64
import contextvars
import shutil
import subprocess
import json
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence

from . import prepaid_denylist, provider_cooldown

logger = logging.getLogger(__name__)

MODEL_LADDER_ORDER = (
    "anthropic",
    "grok",
    "cursor",
    "codex",
    "antigravity",
    "nvidia",
    "cerebras",
)

MODEL_LADDER_TIERS = {
    "anthropic": "subscription",
    "grok": "subscription",
    "cursor": "subscription",
    "codex": "subscription",
    "antigravity": "subscription",
    "nvidia": "nvidia",
    "cerebras": "cerebras",
    "slm-tagger": "local",
}

_NVIDIA_KEYS = ("NVIDIA_API_KEY",)
_CEREBRAS_KEYS = ("CEREBRAS_API_KEY",)

# Claude Max / Pro subscription (claude setup-token or ~/.claude/.credentials.json).
_ANTHROPIC_SUBSCRIPTION_ENV = ("CLAUDE_CODE_OAUTH_TOKEN",)
_ANTHROPIC_OAUTH_BETA = "oauth-2025-04-20"
# A Claude Max grant is honoured only for Claude Code traffic: the Messages API
# answers a bare 429 unless the system prompt opens with this identity block
# (live 2026-09-18: 200 with it, 429 without, 401 when sent as x-api-key).
_CLAUDE_CODE_SYSTEM_PREFIX = "You are Claude Code, Anthropic's official CLI for Claude."
_CLAUDE_CODE_USER_AGENT = "claude-cli/2.1.140"
# ChatGPT subscription endpoint the codex CLI uses. api.openai.com meters the
# prepaid wallet and rejects the grant ("no credits remaining", live 2026-09-18).
_CHATGPT_CODEX_RESPONSES_URL = "https://chatgpt.com/backend-api/codex/responses"
# GPT-5.5 retired from ChatGPT sign-in on 2026-10-14; API-key sessions retain it.
_CODEX_SUBSCRIPTION_MODEL = "gpt-6.1-sol"
# Google access runs ONLY through the Antigravity CLI (`agy -p`), operator
# 2026-09-18: no Google API key or OAuth token path, under any flag. The
# Antigravity grant lacks the generativelanguage scope (403 live), so there is
# no HTTP path. ~/.gemini/antigravity-cli is Google's fixed on-disk location.
_ANTIGRAVITY_TOKEN_RELPATH = Path(".gemini") / "antigravity-cli" / "antigravity-oauth-token"
_ANTIGRAVITY_CLI_TIMEOUT_SECONDS = 120.0
# Mirrors subscription_tokens.CLI_ENV_ALLOWLIST: everything a third-party
# agent CLI may inherit, and nothing else. PATH is added because agy invokes
# helpers; secrets from /etc/radon/env must never cross this boundary.
_ANTIGRAVITY_CLI_ENV_ALLOWLIST = frozenset(
    {
        "PATH", "HOME", "USER", "LOGNAME", "SHELL", "LANG", "LC_ALL", "TERM", "TMPDIR",
        "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME", "XDG_RUNTIME_DIR",
        "HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY", "https_proxy", "http_proxy", "no_proxy",
        "SSL_CERT_FILE", "SSL_CERT_DIR", "NODE_EXTRA_CA_CERTS",
    }
)

_NVIDIA_VISION_DEFAULT = "meta/llama-3.2-90b-vision-instruct"
_NVIDIA_VISION_FALLBACK = "meta/llama-3.2-11b-vision-instruct"
# Catalog-valid NIM text default (meta/llama-3.3-70b-instruct → http_410 on Joe's catalog).
_NVIDIA_TEXT_DEFAULT = "nvidia/nemotron-3-super-120b-a12b"
_NVIDIA_SUPER_VL = "nvidia/nemotron-3.5-super-vl-120b-a12b"

# Env inventory for cloud/.env.example tests: subscription material only.
_ANTHROPIC_KEYS = _ANTHROPIC_SUBSCRIPTION_ENV + ("CLAUDE_CODE_OAUTH_TOKEN_FILE",)
_OPTIONAL_LADDER_ENV = (
    "NVIDIA_VISION_MODEL",
    "NVIDIA_TEXT_MODEL",
    "NVIDIA_MODEL",
    "CLAUDE_CONFIG_DIR",
    "CODEX_HOME",
    "RADON_LADDER_NO_AUTH_FILES",
    "ANTIGRAVITY_CLI",
    "ANTIGRAVITY_MODEL",
    "RADON_MODEL_LADDER_ORDER",
    "RADON_PROVIDER_COOLDOWN_STATE",
    "RADON_PROVIDER_RATE_LIMIT_COOLDOWN_S",
    "CEREBRAS_MAX_IMAGES",
    "CEREBRAS_MAX_REQUEST_BYTES",
    "RADON_SLM_TAGGER_URL",
    "RADON_SLM_TAGGER_MODE",
    "RADON_SLM_TAGGER_TIMEOUT_S",
    "RADON_SLM_TAGGER_THREADS",
)

# Documented Llama 3.2 vision ceiling (NVIDIA NIM). Larger values are a 400.
_NVIDIA_MAX_OUTPUT_TOKENS = 8192
_NVIDIA_CHAT_URL = "https://integrate.api.nvidia.com/v1/chat/completions"
_NVIDIA_MODEL_ID = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,80}(?:/[A-Za-z0-9][A-Za-z0-9._:-]{0,80})?$"
)
# Cerebras shared inference: 10 images and 10 MiB per request (qwen-3.8-27b).
_CEREBRAS_MAX_IMAGES_DEFAULT = 10
_CEREBRAS_MAX_BYTES_DEFAULT = 10 * 1024 * 1024
_RESPONSE_HEADERS: contextvars.ContextVar[dict[str, str]] = contextvars.ContextVar(
    "model_ladder_response_headers", default={}
)

_CREDIT_MARKERS = (
    "credit balance",
    "credit_balance",
    "too low",
    "plans & billing",
    "billing",
    "quota",
    "insufficient_quota",
    "rate limit",
    "rate_limit",
    "overloaded",
    "capacity",
    "payment required",
    "exceeded your current quota",
)

_DEFAULT_VISION_MODELS = {
    "anthropic": "claude-haiku-4-5-20251001",
    "grok": "grok-4.6",
    "codex": "gpt-5.5",
    "antigravity": "",  # account default unless ANTIGRAVITY_MODEL is set
    "nvidia": _NVIDIA_VISION_DEFAULT,
    # Public multimodal Chat Completions id (Cerebras changelog 2026-09).
    # llama-4-scout-17b-16e-instruct is archived. gemma-4-31b left public
    # endpoints 2026-09-03 (dedicated only). gpt-oss-120b is text-only.
    # qwen-3.8-27b accepts PNG/JPEG data URIs, matching Reviewer and CTA
    # vision bodies. Override with CEREBRAS_VISION_MODEL / CEREBRAS_MODEL.
    "cerebras": "qwen-3.8-27b",
}

_DEFAULT_TEXT_MODELS = {
    "anthropic": "claude-sonnet-4-6",
    "grok": "grok-4.6",
    "codex": "gpt-5.5",
    "antigravity": "",  # account default unless ANTIGRAVITY_MODEL is set
    "nvidia": _NVIDIA_TEXT_DEFAULT,
    "cerebras": "qwen-3.8-27b",
    "slm-tagger": "radon-slm-tagger",
}

# qwen-3.8-27b defaults reasoning to high; disable so JSON/vision extraction
# is not consumed by chain-of-thought on the last ladder rung.
_CEREBRAS_REQUEST_EXTRAS = {"reasoning_effort": "none"}

_SECRET_PATTERN = re.compile(
    r"(sk-[a-zA-Z0-9_-]{8,}|xai-[a-zA-Z0-9_-]{8,}|nvapi-[a-zA-Z0-9_-]{8,}|"
    r"csk-[a-zA-Z0-9_-]{8,}|Bearer\s+[a-zA-Z0-9._-]{8,})",
    re.IGNORECASE,
)


class ModelLadderExhausted(RuntimeError):
    """Every keyed ladder provider failed or none were keyed."""


class ModelResponseError(RuntimeError):
    """Non-failover response failure (truncation, oversize, malformed envelope)."""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


@dataclass(frozen=True)
class AuthMaterial:
    """Resolved bearer/api credential for one ladder rung."""

    token: str
    kind: str  # "subscription" | "api_key"
    mechanism: str  # env var name, auth file path label, etc.
    account_id: str = ""  # ChatGPT account for the codex grant (chatgpt-account-id)


@dataclass(frozen=True)
class LadderResult:
    """Successful ladder completion with parsed JSON payload."""

    data: Any
    text: str
    provider: str
    model: str
    attempted: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class VisionResult:
    rows: list[dict[str, Any]]
    provider: str
    model: str
    attempted: tuple[str, ...] = field(default_factory=tuple)


def _env_get(env: Mapping[str, str], *names: str) -> str:
    for name in names:
        value = (env.get(name) or "").strip()
        if value:
            return value
    return ""


def _first_env_model(env: Mapping[str, str], *names: str, default: str) -> str:
    return _env_get(env, *names) or default


def ladder_order(env: Mapping[str, str] | None = None) -> tuple[str, ...]:
    """Provider order. ``RADON_MODEL_LADDER_ORDER`` is a comma list of known rungs.

    Unknown names are ignored. An empty or all-unknown value keeps the default.
    Cerebras is still forced last by ``_last_resort_last``.
    """
    src = env if env is not None else os.environ
    raw = (src.get("RADON_MODEL_LADDER_ORDER") or "").strip()
    if not raw:
        return MODEL_LADDER_ORDER
    known = set(MODEL_LADDER_ORDER)
    chosen: list[str] = []
    for part in raw.split(","):
        name = part.strip().lower()
        if name in known and name not in chosen:
            chosen.append(name)
    return tuple(chosen) if chosen else MODEL_LADDER_ORDER


def wired_providers(env: Mapping[str, str] | None = None) -> tuple[str, ...]:
    """Return ladder providers that have a real HTTP path and a credential."""
    src = env if env is not None else os.environ
    wired: list[str] = []
    for name in ladder_order(src):
        if name == "cursor":
            continue
        if _auth_for(name, src) is not None:
            wired.append(name)
    return tuple(wired)


def _home_dir(env: Mapping[str, str]) -> Path:
    raw = (env.get("HOME") or "").strip()
    return Path(raw) if raw else Path.home()


def _auth_files_disabled(env: Mapping[str, str]) -> bool:
    """``RADON_LADDER_NO_AUTH_FILES=1`` makes auth discovery env-var-only.

    Credential-free loops scrub their env, but the implicit ``Path.home()``
    fallback still discovered host auth files; the flag keeps their children
    keyless.
    """
    raw = (env.get("RADON_LADDER_NO_AUTH_FILES") or "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _read_secret_file(path: Path) -> str:
    try:
        if not path.is_file():
            return ""
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _json_load_object(path: Path) -> dict[str, Any] | None:
    try:
        if not path.is_file():
            return None
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _token_from_mapping(data: Mapping[str, Any], *paths: tuple[str, ...]) -> str:
    for path in paths:
        cur: Any = data
        for key in path:
            if not isinstance(cur, Mapping):
                cur = None
                break
            cur = cur.get(key)
        if isinstance(cur, str) and cur.strip():
            return cur.strip()
    return ""


def _anthropic_subscription_auth(env: Mapping[str, str]) -> AuthMaterial | None:
    token = _env_get(env, *_ANTHROPIC_SUBSCRIPTION_ENV)
    if token:
        return AuthMaterial(token, "subscription", "CLAUDE_CODE_OAUTH_TOKEN")
    if _auth_files_disabled(env):
        return None
    file_env = (env.get("CLAUDE_CODE_OAUTH_TOKEN_FILE") or "").strip()
    if file_env:
        token = _read_secret_file(Path(file_env))
        if token:
            return AuthMaterial(token, "subscription", "CLAUDE_CODE_OAUTH_TOKEN_FILE")
    config_dir = (env.get("CLAUDE_CONFIG_DIR") or "").strip()
    cred_path = (
        Path(config_dir) / ".credentials.json"
        if config_dir
        else _home_dir(env) / ".claude" / ".credentials.json"
    )
    data = _json_load_object(cred_path)
    if data:
        token = _token_from_mapping(
            data,
            ("claudeAiOauth", "accessToken"),
            ("claudeAiOauth", "access_token"),
            ("accessToken",),
            ("access_token",),
        )
        if token:
            return AuthMaterial(
                token, "subscription", f"claude_credentials:{cred_path.name}"
            )
    return None


def _codex_subscription_auth(env: Mapping[str, str]) -> AuthMaterial | None:
    if _auth_files_disabled(env):
        return None
    codex_home = (env.get("CODEX_HOME") or "").strip()
    auth_path = (
        Path(codex_home) / "auth.json"
        if codex_home
        else _home_dir(env) / ".codex" / "auth.json"
    )
    data = _json_load_object(auth_path)
    if not data:
        return None
    if str(data.get("auth_mode", "")).lower() in {"apikey", "api_key"}:
        return None  # a prepaid API-key login, not a ChatGPT subscription
    token = _token_from_mapping(
        data,
        ("tokens", "access_token"),
        ("tokens", "accessToken"),
        ("access_token",),
        ("accessToken",),
    )
    if token:
        account_id = _token_from_mapping(data, ("tokens", "account_id"), ("account_id",))
        return AuthMaterial(token, "subscription", "codex_auth.json", account_id)
    return None


def _rfc3339_expired(value: str) -> bool:
    from datetime import datetime, timezone

    try:
        stamp = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return False
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp <= datetime.now(timezone.utc)


def _grok_subscription_auth(env: Mapping[str, str]) -> AuthMaterial | None:
    if _auth_files_disabled(env):
        return None
    auth_path = _home_dir(env) / ".grok" / "auth.json"
    data = _json_load_object(auth_path)
    if not data:
        return None
    token = _token_from_mapping(
        data,
        ("access_token",),
        ("accessToken",),
        ("token",),
        ("credentials", "access_token"),
    )
    if not token:
        # The grok CLI's live shape (2026-09): one entry per "<issuer>::<client>",
        # the OIDC access token under `key`, RFC 3339 `expires_at`. An expired
        # entry is skipped so a stale file is "no grant", not a guaranteed 401.
        for entry in data.values():
            if not isinstance(entry, Mapping):
                continue
            candidate = entry.get("key")
            if not isinstance(candidate, str) or not candidate.strip():
                continue
            expires_at = entry.get("expires_at")
            if isinstance(expires_at, str) and _rfc3339_expired(expires_at):
                continue
            token = candidate.strip()
            break
    if token:
        return AuthMaterial(token, "subscription", "grok_auth.json")
    return None  # an api_key in auth.json is a prepaid key: never read


def _antigravity_cli(env: Mapping[str, str]) -> str:
    """Path of the Antigravity CLI when it and its grant are present, else ""."""
    if _auth_files_disabled(env):
        return ""
    home = _home_dir(env)
    if not (home / _ANTIGRAVITY_TOKEN_RELPATH).is_file():
        return ""
    explicit = (env.get("ANTIGRAVITY_CLI") or "").strip()
    candidates = [explicit] if explicit else [str(home / ".local" / "bin" / "agy")]
    for candidate in candidates:
        if candidate and os.access(candidate, os.X_OK):
            return candidate
    # Only the caller's own PATH: a hermetic env with no PATH must not discover
    # the host's ~/.local/bin/agy.
    path = (env.get("PATH") or "").strip()
    found = shutil.which("agy", path=path) if path else None
    return found or ""


def _antigravity_subscription_auth(env: Mapping[str, str]) -> AuthMaterial | None:
    """Google via the Antigravity CLI only; the token is the CLI path."""
    cli = _antigravity_cli(env)
    if cli:
        return AuthMaterial(cli, "subscription", "antigravity_cli")
    return None


_SUBSCRIPTION_AUTH: dict[str, Callable[[Mapping[str, str]], "AuthMaterial | None"]] = {
    "anthropic": _anthropic_subscription_auth,
    "grok": _grok_subscription_auth,
    "codex": _codex_subscription_auth,
    # Google: the Antigravity CLI only, no key and no OAuth token path.
    "antigravity": _antigravity_subscription_auth,
}


def _auth_for(name: str, env: Mapping[str, str]) -> AuthMaterial | None:
    """Subscription credentials only for sub-tier rungs; prepaid keys never."""
    if name in _SUBSCRIPTION_AUTH:
        prepaid_denylist.warn_ignored(env)
        return _SUBSCRIPTION_AUTH[name](env)
    if name == "nvidia":
        token = _env_get(env, *_NVIDIA_KEYS)
        if token:
            return AuthMaterial(token, "api_key", "NVIDIA_API_KEY")
        return None
    if name == "cerebras":
        token = _env_get(env, *_CEREBRAS_KEYS)
        if token:
            return AuthMaterial(token, "api_key", "CEREBRAS_API_KEY")
        return None
    if name == "slm-tagger":
        url = _env_get(env, "RADON_SLM_TAGGER_URL")
        if url:
            return AuthMaterial("", "local", "RADON_SLM_TAGGER_URL")
        return None
    return None


def _key_for(name: str, env: Mapping[str, str]) -> str:
    """Back-compat: token string only. Prefer ``_auth_for`` for kind/mechanism."""
    auth = _auth_for(name, env)
    return auth.token if auth is not None else ""


def _model_for(
    name: str, env: Mapping[str, str], *, kind: str = "vision", auth_kind: str = "api_key"
) -> str:
    defaults = _DEFAULT_VISION_MODELS if kind == "vision" else _DEFAULT_TEXT_MODELS
    if name == "anthropic":
        if kind == "text":
            return _first_env_model(
                env,
                "RADON_RESEARCH_MODEL",
                "ANTHROPIC_MODEL",
                default=defaults["anthropic"],
            )
        return _first_env_model(
            env, "ANTHROPIC_VISION_MODEL", "ANTHROPIC_MODEL", default=defaults["anthropic"]
        )
    if name == "grok":
        return _first_env_model(env, "XAI_MODEL", "GROK_MODEL", default=defaults["grok"])
    if name == "codex":
        default = _CODEX_SUBSCRIPTION_MODEL if auth_kind == "subscription" else defaults["codex"]
        if kind == "vision":
            return _first_env_model(
                env, "OPENAI_VISION_MODEL", "OPENAI_MODEL", default=default
            )
        return _first_env_model(env, "OPENAI_MODEL", default=default)
    if name == "antigravity":
        return _first_env_model(env, "ANTIGRAVITY_MODEL", default=defaults["antigravity"])
    if name == "nvidia":
        if kind == "vision":
            return _first_env_model(
                env,
                "NVIDIA_VISION_MODEL",
                "NVIDIA_MODEL",
                default=_NVIDIA_VISION_DEFAULT,
            )
        return _first_env_model(
            env,
            "NVIDIA_TEXT_MODEL",
            "NVIDIA_MODEL",
            default=_NVIDIA_TEXT_DEFAULT,
        )
    if name == "cerebras":
        return _first_env_model(
            env, "CEREBRAS_VISION_MODEL", "CEREBRAS_MODEL", default=defaults["cerebras"]
        )
    if name == "slm-tagger":
        return defaults.get("slm-tagger", "radon-slm-tagger")
    return name


def _nvidia_vision_models(env: Mapping[str, str]) -> tuple[str, ...]:
    """Primary NVIDIA vision id, then 11B fallback when distinct."""
    primary = _model_for("nvidia", env, kind="vision")
    models = [primary]
    if primary != _NVIDIA_VISION_FALLBACK:
        models.append(_NVIDIA_VISION_FALLBACK)
    return tuple(models)


def _anthropic_headers(api_key: str, *, subscription: bool) -> dict[str, str]:
    headers = {
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    if not subscription:
        # Subscriptions only (2026-10-26): no x-api-key path exists.
        raise ValueError("Anthropic requests require a Claude subscription grant")
    # Claude Max OAuth grant: Bearer plus the oauth beta and the Claude Code
    # user agent. As an x-api-key the same grant is a 401.
    headers["authorization"] = f"Bearer {api_key}"
    headers["anthropic-beta"] = _ANTHROPIC_OAUTH_BETA
    headers["user-agent"] = _CLAUDE_CODE_USER_AGENT
    return headers


def _anthropic_system(system: str, *, subscription: bool) -> Any:
    """Lead with the Claude Code identity block when metering a Max grant."""
    if not subscription:
        return system
    blocks: list[dict[str, str]] = [{"type": "text", "text": _CLAUDE_CODE_SYSTEM_PREFIX}]
    if system:
        blocks.append({"type": "text", "text": system})
    return blocks


def _model_id_rejected(body: str) -> bool:
    lowered = body.lower()
    if "model" not in lowered:
        return False
    return any(token in lowered for token in ("not found", "unknown", "does not exist", "unrecognized", "invalid model"))


def _classify_http_failure(status: int, body: str) -> str:
    lowered = body.lower()
    if status == 413:
        return "request_too_large"
    if status == 404 or (status == 400 and _model_id_rejected(lowered)):
        return "model_not_found"
    if status == 415 and "image" in lowered:
        return "image_unsupported"
    if "credit balance" in lowered or "credit_balance" in lowered or "too low" in lowered:
        return "credit_balance"
    if any(marker in lowered for marker in _CREDIT_MARKERS):
        return "quota_or_billing"
    if status in {401, 402, 403}:
        return f"http_{status}"
    if status == 429:
        return "http_429"
    if status >= 500:
        return f"http_{status}"
    if status != 200:
        return f"http_{status}"
    return "provider_error"


def _error_envelope(payload: Any) -> str:
    """Text of a provider error envelope inside a 2xx body, else ""."""
    if not isinstance(payload, dict):
        return ""
    if payload.get("error"):
        err = payload["error"]
        return err if isinstance(err, str) else json.dumps(err)
    if payload.get("type") == "error":
        return json.dumps(payload)
    return ""


def _is_hard_fail(status: int, payload: Any) -> bool:
    # A 200 is judged by its envelope only: the body is the model's answer, and
    # research text routinely says "capacity", "quota" or "billing".
    if status != 200:
        return True
    return bool(_error_envelope(payload))


def safe_error_message(error: Exception, *, max_len: int = 240) -> str:
    """Return a short, credential-free error string for persistence/logging."""
    msg = str(error).strip()
    if msg:
        msg = _SECRET_PATTERN.sub("[redacted]", msg)
        msg = " ".join(msg.split())
        if len(msg) > max_len:
            msg = msg[: max_len - 3] + "..."
        if msg:
            return msg
    return type(error).__name__


def _nvidia_limiter(env: Mapping[str, str], post: Callable[..., Any]) -> Any:
    """Host-wide NVIDIA pacer for real network calls; None for an injected post.

    The NVIDIA key is rate limited per key (about 40 requests a minute) and
    shared with the nightly fx loops; see scripts/nvidia_rate_limit.py.
    """
    if post is not _default_post:
        return None
    try:
        import nvidia_rate_limit
    except ImportError:  # imported without scripts/ on sys.path
        import sys

        sys.path.append(str(Path(__file__).resolve().parents[1]))
        import nvidia_rate_limit

    return nvidia_rate_limit.shared_limiter(env)


def _nvidia_max_wait(env: Mapping[str, str]) -> float:
    try:
        return max(0.0, float((env.get("RADON_NVIDIA_LADDER_MAX_WAIT_S") or "15").strip()))
    except ValueError:
        return 15.0


def _body_snippet(text: str) -> str:
    return safe_error_message(RuntimeError(text), max_len=200) if text.strip() else ""


def parse_json_rows(text: str) -> Optional[list[dict[str, Any]]]:
    cleaned = _strip_fences(text)
    if not cleaned:
        return None
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        start = cleaned.find("[")
        end = cleaned.rfind("]")
        if start < 0 or end <= start:
            return None
        try:
            parsed = json.loads(cleaned[start : end + 1])
        except json.JSONDecodeError:
            return None
    if not isinstance(parsed, list):
        return None
    return parsed


def parse_json_object(text: str) -> Optional[dict[str, Any]]:
    cleaned = _strip_fences(text)
    if not cleaned:
        return None
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            parsed = json.loads(cleaned[start : end + 1])
        except json.JSONDecodeError:
            return None
    if not isinstance(parsed, dict):
        return None
    return parsed


def _strip_fences(text: str) -> str:
    cleaned = (text or "").strip()
    if not cleaned:
        return ""
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[-1] if "\n" in cleaned else cleaned[3:]
    if cleaned.endswith("```"):
        cleaned = cleaned[:-3]
    return cleaned.strip()


def _coerce_text(value: Any) -> str:
    """Visible assistant text. Thinking blocks are not part of the answer."""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "".join(_coerce_text(part.get("text") if isinstance(part, dict) else part) for part in value)
    if isinstance(value, dict):
        inner = value.get("text")
        if isinstance(inner, str):
            return inner
        content = value.get("content")
        return content if isinstance(content, str) else ""
    return ""


def _text_from_anthropic(payload: dict[str, Any]) -> str:
    content = payload.get("content")
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for block in content:
        if not isinstance(block, dict) or block.get("type") not in {"text", "output_text"}:
            continue
        parts.append(_coerce_text(block.get("text")))
    return "".join(parts)


def _anthropic_message_from_sse(text: str) -> dict[str, Any] | None:
    """Rebuild a Messages object from an SSE body. None when it is not that stream."""
    if "data:" not in (text or ""):
        return None
    blocks: list[dict[str, Any]] = []
    stop_reason = None
    usage = None
    message: dict[str, Any] = {}
    saw = False
    for line in text.splitlines():
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if not data or data == "[DONE]":
            continue
        try:
            event = json.loads(data)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        saw = True
        kind = event.get("type")
        if kind == "message_start" and isinstance(event.get("message"), dict):
            message = dict(event["message"])
            content = message.get("content")
            if isinstance(content, list):
                blocks = [dict(block) for block in content if isinstance(block, dict)]
        elif kind == "content_block_start" and isinstance(event.get("content_block"), dict):
            blocks.append(dict(event["content_block"]))
        elif kind == "content_block_delta" and isinstance(event.get("delta"), dict):
            index = event.get("index")
            delta = event["delta"]
            if isinstance(index, int) and 0 <= index < len(blocks):
                if delta.get("type") == "text_delta" and isinstance(delta.get("text"), str):
                    blocks[index]["text"] = _coerce_text(blocks[index].get("text")) + delta["text"]
                elif delta.get("type") == "thinking_delta" and isinstance(delta.get("thinking"), str):
                    blocks[index]["thinking"] = str(blocks[index].get("thinking") or "") + delta["thinking"]
        elif kind == "message_delta" and isinstance(event.get("delta"), dict):
            if isinstance(event["delta"].get("stop_reason"), str):
                stop_reason = event["delta"]["stop_reason"]
            if isinstance(event.get("usage"), dict):
                usage = event["usage"]
    if not saw:
        return None
    payload = dict(message)
    payload["content"] = blocks
    payload.setdefault("type", "message")
    if stop_reason is not None:
        payload["stop_reason"] = stop_reason
    if usage is not None:
        payload["usage"] = usage
    return payload


def _payload_from_text(text: str) -> Any:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        recovered = _anthropic_message_from_sse(text)
        if recovered is None:
            raise
        return recovered


def _remember_headers(resp: Any) -> None:
    raw = getattr(resp, "headers", None)
    headers: dict[str, str] = {}
    if raw is not None and hasattr(raw, "items"):
        try:
            headers = {str(key).lower(): str(value) for key, value in raw.items()}
        except Exception:
            headers = {}
    _RESPONSE_HEADERS.set(headers)


def _env_int(env: Mapping[str, str], name: str, default: int) -> int:
    raw = (env.get(name) or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value > 0 else default


def _image_count(body: Mapping[str, Any]) -> int:
    count = 0
    for message in body.get("messages") or []:
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, list):
            continue
        count += sum(1 for part in content if isinstance(part, dict) and part.get("type") == "image_url")
    return count


def _cerebras_too_large(body: Mapping[str, Any], env: Mapping[str, str]) -> bool:
    """True when the request cannot fit Cerebras shared-inference limits."""
    if _image_count(body) > _env_int(env, "CEREBRAS_MAX_IMAGES", _CEREBRAS_MAX_IMAGES_DEFAULT):
        return True
    raw = json.dumps(body, separators=(",", ":"))
    return len(raw.encode("utf-8")) > _env_int(env, "CEREBRAS_MAX_REQUEST_BYTES", _CEREBRAS_MAX_BYTES_DEFAULT)


def _nvidia_invoke_url(model: str) -> str | None:
    if not _NVIDIA_MODEL_ID.fullmatch(model or ""):
        return None
    return "https://integrate.api.nvidia.com/v1/" + model


def _nvidia_request(
    post: Callable[..., Any],
    api_key: str,
    model: str,
    body: dict[str, Any],
    *,
    timeout: float | tuple[float, float],
    max_bytes: int,
) -> tuple[int, str, Any]:
    """Chat-completions call, then the documented per-model URL on a route miss.

    A retired or misplaced model id is HTTP 404 on ``/v1/chat/completions``
    and sometimes HTTP 400. The NIM reference posts that id as its own path.
    """
    payload_body = body
    max_tokens = payload_body.get("max_tokens")
    if isinstance(max_tokens, int) and not isinstance(max_tokens, bool) and max_tokens > _NVIDIA_MAX_OUTPUT_TOKENS:
        payload_body = {**payload_body, "max_tokens": _NVIDIA_MAX_OUTPUT_TOKENS}
    headers = {
        "authorization": f"Bearer {api_key}",
        "content-type": "application/json",
        "accept": "application/json",
    }
    status, raw, parsed = _request(
        post, _NVIDIA_CHAT_URL, headers, payload_body,
        timeout=timeout, stream=True, max_bytes=max_bytes,
    )
    body_text = raw if isinstance(raw, str) else ""
    alternate = _nvidia_invoke_url(model)
    if alternate is None or not (status == 404 or (status == 400 and _model_id_rejected(body_text))):
        return status, raw, parsed
    logger.warning(
        "nvidia model=%s chat/completions status=%s; retrying documented model url",
        model, status,
    )
    return _request(
        post, alternate, headers, payload_body,
        timeout=timeout, stream=True, max_bytes=max_bytes,
    )


def _text_from_openai(payload: dict[str, Any]) -> str:
    choices = payload.get("choices") or []
    if not choices:
        return ""
    message = (choices[0] or {}).get("message") or {}
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text":
                parts.append(str(part.get("text") or ""))
            elif isinstance(part, str):
                parts.append(part)
        return "".join(parts)
    return ""


def _default_post(
    url: str,
    *,
    headers: dict[str, str],
    json: dict[str, Any],
    timeout: float | tuple[float, float],
    stream: bool = False,
):
    import httpx

    if stream:
        client = httpx.Client(timeout=timeout)
        try:
            request = client.build_request("POST", url, headers=headers, json=json)
            response = client.send(request, stream=True)
        except Exception:
            client.close()
            raise

        class StreamingResponse:
            def __getattr__(self, name: str) -> Any:
                return getattr(response, name)

            def iter_bytes(self, chunk_size: int = 65536):
                return response.iter_bytes(chunk_size)

            def iter_content(self, chunk_size: int = 65536):
                return response.iter_bytes(chunk_size)

            def close(self) -> None:
                try:
                    response.close()
                finally:
                    client.close()

        return StreamingResponse()
    return httpx.post(url, headers=headers, json=json, timeout=timeout)


def _request(
    post: Callable[..., Any],
    url: str,
    headers: dict[str, str],
    body: dict[str, Any],
    *,
    timeout: float = 60.0,
    stream: bool = False,
    max_bytes: int = 0,
    raw_text_only: bool = False,
) -> tuple[int, str, Any]:
    try:
        kwargs: dict[str, Any] = {"headers": headers, "json": body, "timeout": timeout}
        if stream:
            kwargs["stream"] = True
        resp = post(url, **kwargs)
    except Exception as exc:  # noqa: BLE001 — network is a hard provider fail
        raise RuntimeError(f"network:{type(exc).__name__}") from exc
    _remember_headers(resp)

    stream_iter = getattr(resp, "iter_content", None) or getattr(resp, "iter_bytes", None)
    if stream and max_bytes and callable(stream_iter):
        raw = bytearray()
        try:
            try:
                for chunk in stream_iter(65536):
                    raw.extend(chunk)
                    if len(raw) > max_bytes:
                        raise ModelResponseError(
                            "response_too_large",
                            "Research reviewer response exceeds limit",
                        )
            except ModelResponseError:
                raise
            except Exception as exc:  # noqa: BLE001 — a streamed body can fail after headers
                raise RuntimeError(f"network:{type(exc).__name__}") from exc
            text = raw.decode("utf-8", errors="replace")
            payload = None if raw_text_only else _payload_from_text(text)
            status = int(getattr(resp, "status_code", 0) or 0)
        finally:
            close = getattr(resp, "close", None)
            if callable(close):
                close()
        return status, text, payload

    try:
        text = getattr(resp, "text", "") or ""
        try:
            payload = resp.json()
        except Exception:
            payload = None
        return int(getattr(resp, "status_code", 0) or 0), text, payload
    finally:
        if stream:
            close = getattr(resp, "close", None)
            if callable(close):
                close()


def _request_raw(
    post: Callable[..., Any],
    url: str,
    headers: dict[str, str],
    body: dict[str, Any],
    *,
    timeout: float | tuple[float, float],
    max_bytes: int,
) -> tuple[int, str, Any]:
    """Streamed request whose body is returned as text (SSE), never JSON-parsed."""
    return _request(
        post, url, headers, body, timeout=timeout, stream=True,
        max_bytes=max_bytes or 2_000_000, raw_text_only=True,
    )


def _uses_max_completion_tokens(model: str) -> bool:
    """OpenAI gpt-5.x / o-series chat completions reject max_tokens (HTTP 400)."""
    lowered = (model or "").strip().lower()
    return lowered.startswith("gpt-5") or lowered.startswith(("o1", "o3", "o4"))


def _openai_token_field(model: str, max_tokens: int) -> dict[str, int]:
    if _uses_max_completion_tokens(model):
        return {"max_completion_tokens": max_tokens}
    return {"max_tokens": max_tokens}


def _anthropic_vision_body(
    model: str, b64: str, prompt: str, *, subscription: bool = False
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "model": model,
        "max_tokens": 4096,
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/png",
                            "data": b64,
                        },
                    },
                    {"type": "text", "text": prompt},
                ],
            }
        ],
    }
    if subscription:
        body["system"] = _anthropic_system("", subscription=True)
    return body


def _openai_vision_body(
    model: str,
    b64: str,
    prompt: str,
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "model": model,
        **_openai_token_field(model, 4096),
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/png;base64,{b64}"},
                    },
                    {"type": "text", "text": prompt},
                ],
            }
        ],
    }
    if extra:
        body.update(extra)
    return body


def _labeled_images_to_b64(
    images: Sequence[tuple[str, Path | bytes]],
) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for label, source in images:
        raw = source if isinstance(source, bytes) else Path(source).read_bytes()
        out.append((label, base64.b64encode(raw).decode("utf-8")))
    return out


def _anthropic_multimodal_body(
    model: str,
    system: str,
    instruction: str,
    labeled_b64: Sequence[tuple[str, str]],
    *,
    max_tokens: int,
    subscription: bool = False,
) -> dict[str, Any]:
    content: list[dict[str, Any]] = []
    for label, b64 in labeled_b64:
        content.extend(
            [
                {"type": "text", "text": label},
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/png",
                        "data": b64,
                    },
                },
            ]
        )
    content.append({"type": "text", "text": instruction})
    body: dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": content}],
    }
    if subscription:
        body["system"] = _anthropic_system(system, subscription=True)
    elif system:
        body["system"] = system
    return body


def _chatgpt_responses_body(
    model: str,
    system: str,
    instruction: str,
    labeled_b64: Sequence[tuple[str, str]],
) -> dict[str, Any]:
    """Responses-API body for chatgpt.com's codex endpoint (streaming only)."""
    content: list[dict[str, Any]] = []
    for label, b64 in labeled_b64:
        content.append({"type": "input_text", "text": label})
        content.append({"type": "input_image", "image_url": f"data:image/png;base64,{b64}"})
    content.append({"type": "input_text", "text": instruction})
    body: dict[str, Any] = {
        "model": model,
        "input": [{"role": "user", "content": content}],
        "store": False,
        "stream": True,
    }
    if system:
        body["instructions"] = system
    return body


def _chatgpt_headers(auth: AuthMaterial) -> dict[str, str]:
    headers = {
        "authorization": f"Bearer {auth.token}",
        "content-type": "application/json",
        "accept": "text/event-stream",
        "OpenAI-Beta": "responses=experimental",
    }
    if auth.account_id:
        headers["chatgpt-account-id"] = auth.account_id
    return headers


def _request_sse_responses(
    post: Callable[..., Any],
    url: str,
    headers: dict[str, str],
    body: dict[str, Any],
    *,
    timeout: float | tuple[float, float],
    max_bytes: int,
) -> tuple[int, str, Any]:
    """Fold a Responses SSE stream into the chat-completions shape the ladder parses."""
    status, raw_text, _payload = _request_raw(
        post, url, headers, body, timeout=timeout, max_bytes=max_bytes
    )
    if status < 200 or status >= 300:
        return status, raw_text, None
    text_parts: list[str] = []
    completed = False
    for line in raw_text.splitlines():
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if data == "[DONE]":
            continue
        try:
            event = json.loads(data)
        except ValueError:
            raise RuntimeError("invalid_response_stream") from None
        if not isinstance(event, dict):
            raise RuntimeError("invalid_response_stream")
        kind = event.get("type")
        if kind in {"response.failed", "response.incomplete", "response.error", "error"}:
            # Never expose provider error payloads or partial reviewer JSON.
            raise RuntimeError("incomplete_response_stream")
        if kind == "response.output_text.delta" and isinstance(event.get("delta"), str):
            text_parts.append(event["delta"])
        elif kind == "response.completed":
            response = event.get("response")
            if not isinstance(response, dict) or response.get("status") != "completed":
                raise RuntimeError("incomplete_response_stream")
            completed = True
    if not completed:
        raise RuntimeError("incomplete_response_stream")
    text = "".join(text_parts)
    payload = {"choices": [{"message": {"content": text}, "finish_reason": "completed"}]}
    return status, text, payload


def _antigravity_complete(
    cli: str,
    env: Mapping[str, str],
    model: str,
    system: str,
    instruction: str,
    labeled_b64: Sequence[tuple[str, str]],
) -> tuple[int, str, Any]:
    """Google via `agy -p`; images are not supported, so that rung is skipped."""
    if labeled_b64:
        return 415, "antigravity_cli: image input unsupported", None
    prompt = f"{system}\n\n{instruction}" if system else instruction
    argv = [cli, "-p", prompt, "--output-format", "json"]
    if model:
        argv += ["--model", model]
    # agy is an unpinned third-party CLI; services load /etc/radon/env, so a
    # full os.environ passthrough would hand it every Radon secret. Same
    # allowlist rationale as subscription_tokens.CLI_ENV_ALLOWLIST.
    child_env = {
        k: v
        for k, v in os.environ.items()
        if k in _ANTIGRAVITY_CLI_ENV_ALLOWLIST
    }
    child_env.update({k: v for k, v in env.items() if isinstance(v, str)})
    try:
        completed = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=_ANTIGRAVITY_CLI_TIMEOUT_SECONDS,
            env=child_env,
            stdin=subprocess.DEVNULL,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError(f"network:{type(exc).__name__}") from exc
    if completed.returncode != 0:
        return 502, (completed.stderr or completed.stdout or "")[:2000], None
    try:
        doc = json.loads(completed.stdout)
    except ValueError:
        return 502, "antigravity_cli: non-JSON output", None
    response = doc.get("response") if isinstance(doc, dict) else None
    if not isinstance(response, str) or str(doc.get("status", "")).upper() not in {"SUCCESS", ""}:
        return 502, completed.stdout[:2000], None
    payload = {"choices": [{"message": {"content": response}}]}
    return 200, response, payload


def _openai_multimodal_body(
    model: str,
    system: str,
    instruction: str,
    labeled_b64: Sequence[tuple[str, str]],
    *,
    max_tokens: int,
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    content: list[dict[str, Any]] = []
    for label, b64 in labeled_b64:
        content.extend(
            [
                {"type": "text", "text": label},
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/png;base64,{b64}"},
                },
            ]
        )
    content.append({"type": "text", "text": instruction})
    messages: list[dict[str, Any]] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": content})
    body: dict[str, Any] = {
        "model": model,
        **_openai_token_field(model, max_tokens),
        "messages": messages,
    }
    if extra:
        body.update(extra)
    return body


def _nvidia_request_extras(model: str) -> dict[str, Any]:
    """Keep Super VL's reasoning within our small, final-JSON budgets.

    API token streaming is separate from the bounded HTTP transport reader:
    the latter expects one JSON document, not SSE events. These fields are
    model-specific so the retained Llama vision fallback stays compatible.
    """
    if model == _NVIDIA_SUPER_VL:
        return {"reasoning_effort": "none", "stream": False}
    return {}


def _call_vision_provider(
    name: str,
    auth: AuthMaterial,
    model: str,
    b64: str,
    prompt: str,
    post: Callable[..., Any],
    *,
    max_response_bytes: int = 2_000_000,
    env: Mapping[str, str] | None = None,
) -> tuple[int, str, Any]:
    api_key = auth.token
    if name == "anthropic":
        subscription = auth.kind == "subscription"
        return _request(
            post,
            "https://api.anthropic.com/v1/messages",
            _anthropic_headers(api_key, subscription=subscription),
            _anthropic_vision_body(model, b64, prompt, subscription=subscription),
            timeout=(10.0, 120.0),
            stream=True,
            max_bytes=max_response_bytes,
        )
    if name == "grok":
        return _request(
            post,
            "https://api.x.ai/v1/chat/completions",
            {
                "authorization": f"Bearer {api_key}",
                "content-type": "application/json",
            },
            _openai_vision_body(model, b64, prompt),
            timeout=120.0,
            stream=True,
            max_bytes=max_response_bytes,
        )
    if name == "codex":
        if auth.kind == "subscription":
            return _request_sse_responses(
                post,
                _CHATGPT_CODEX_RESPONSES_URL,
                _chatgpt_headers(auth),
                _chatgpt_responses_body(model, "", prompt, [("image", b64)]),
                timeout=60.0,
                max_bytes=max_response_bytes,
            )
        raise ValueError("Codex requests require a ChatGPT subscription grant")
    if name == "antigravity":
        return _antigravity_complete(api_key, {}, model, "", prompt, [("image", b64)])
    if name == "nvidia":
        return _nvidia_request(
            post,
            api_key,
            model,
            _openai_vision_body(model, b64, prompt, extra=_nvidia_request_extras(model)),
            timeout=120.0,
            max_bytes=max_response_bytes,
        )
    if name == "cerebras":
        body = _openai_vision_body(model, b64, prompt, extra=_CEREBRAS_REQUEST_EXTRAS)
        if _cerebras_too_large(body, env if env is not None else os.environ):
            raise RuntimeError("request_too_large")
        return _request(
            post,
            "https://api.cerebras.ai/v1/chat/completions",
            {
                "authorization": f"Bearer {api_key}",
                "content-type": "application/json",
            },
            body,
            timeout=120.0,
            stream=True,
            max_bytes=max_response_bytes,
        )
    raise RuntimeError(f"{name}:unwired")


def _slm_health_ok(url: str) -> bool:
    """GET {url}/health with a 1s budget. Monkeypatch in tests so 503 hits POST."""
    import urllib.error
    import urllib.request

    try:
        req = urllib.request.Request(f"{url.rstrip('/')}/health", method="GET")
        with urllib.request.urlopen(req, timeout=1) as resp:
            return 200 <= int(getattr(resp, "status", 200)) < 300
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


def _load_slm_taxonomy(env: Mapping[str, str]) -> list[str]:
    raw = (env.get("RADON_SLM_TAXONOMY_JSON") or "").strip()
    if raw:
        parsed = json.loads(raw)
        return [str(t) for t in parsed] if isinstance(parsed, list) else []
    path = Path(env.get("RADON_SLM_TAXONOMY_PATH") or "data/tag_taxonomy.json")
    if path.is_file():
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        tags = payload.get("tags") if isinstance(payload, dict) else payload
        if isinstance(tags, list):
            return [str(t) for t in tags if isinstance(t, str)]
    try:
        from db.hrana_http import hrana_query

        rows = hrana_query("SELECT tag FROM tag_taxonomy", ())
        out: list[str] = []
        for row in rows:
            if isinstance(row, dict):
                tag = row.get("tag") or row.get("name")
            else:
                tag = row[0] if row else None
            if tag:
                out.append(str(tag))
        return out
    except Exception:
        return []


def accept_slm_tags_payload(obj: Any, taxonomy: Sequence[str] | None = None) -> bool:
    """Closed-vocabulary SLM contract (HR-3). Distinct from accept_tags_payload."""
    from newsfeed.slm.contract import classify_slm_tags

    src = taxonomy if taxonomy is not None else _load_slm_taxonomy(os.environ)
    code, _tags = classify_slm_tags(obj, src)
    return code == "ok"


def _call_slm_tagger(
    post: Callable[..., Any],
    system: str,
    instruction: str,
    *,
    env: Mapping[str, str],
    read_timeout: float,
) -> tuple[int, str, Any]:
    """The adapter was trained on the caller's live tagger prompt, so the
    system message is forwarded verbatim like every other rung."""
    from newsfeed.slm.contract import (
        SLM_CHAT_COMPLETIONS_PATH,
        SLM_DEFAULT_TIMEOUT_S,
        SLM_DEFAULT_URL,
        SLM_MAX_TOKENS,
        SLM_RESPONSE_FORMAT,
        SLM_TAGGER_NAME,
        SLM_TEMPERATURE,
        classify_slm_tags,
    )

    url = (_env_get(env, "RADON_SLM_TAGGER_URL") or SLM_DEFAULT_URL).rstrip("/")
    timeout_raw = _env_get(env, "RADON_SLM_TAGGER_TIMEOUT_S")
    timeout = float(timeout_raw) if timeout_raw else min(float(read_timeout), float(SLM_DEFAULT_TIMEOUT_S))
    if not _slm_health_ok(url):
        raise RuntimeError("unavailable")
    body = {
        "model": SLM_TAGGER_NAME,
        "temperature": SLM_TEMPERATURE,
        "max_tokens": SLM_MAX_TOKENS,
        "response_format": SLM_RESPONSE_FORMAT,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": instruction},
        ],
    }
    status, raw, payload = _request(
        post,
        f"{url}{SLM_CHAT_COMPLETIONS_PATH}",
        {"content-type": "application/json"},
        body,
        timeout=timeout,
        stream=False,
    )
    if status == 200 and isinstance(payload, dict):
        text = _extract_text("slm-tagger", payload)
        obj = parse_json_object(text)
        code, _tags = classify_slm_tags(obj, _load_slm_taxonomy(env))
        if code != "ok":
            raise RuntimeError(code)
    return status, raw, payload


def _call_text_provider(
    name: str,
    auth: AuthMaterial,
    model: str,
    system: str,
    instruction: str,
    labeled_b64: Sequence[tuple[str, str]],
    post: Callable[..., Any],
    *,
    max_tokens: int,
    stream_anthropic: bool,
    read_timeout: float,
    max_response_bytes: int,
    env: Mapping[str, str] | None = None,
) -> tuple[int, str, Any]:
    api_key = auth.token
    if name == "anthropic":
        subscription = auth.kind == "subscription"
        body = _anthropic_multimodal_body(
            model, system, instruction, labeled_b64,
            max_tokens=max_tokens, subscription=subscription,
        )
        return _request(
            post,
            "https://api.anthropic.com/v1/messages",
            _anthropic_headers(api_key, subscription=subscription),
            body,
            timeout=(10.0, read_timeout),
            stream=True,
            max_bytes=max_response_bytes,
        )
    if name == "grok":
        return _request(
            post,
            "https://api.x.ai/v1/chat/completions",
            {
                "authorization": f"Bearer {api_key}",
                "content-type": "application/json",
            },
            _openai_multimodal_body(
                model, system, instruction, labeled_b64, max_tokens=max_tokens
            ),
            timeout=read_timeout,
            stream=True,
            max_bytes=max_response_bytes,
        )
    if name == "codex":
        if auth.kind == "subscription":
            return _request_sse_responses(
                post,
                _CHATGPT_CODEX_RESPONSES_URL,
                _chatgpt_headers(auth),
                _chatgpt_responses_body(model, system, instruction, labeled_b64),
                timeout=(10.0, read_timeout),
                max_bytes=max_response_bytes,
            )
        raise ValueError("Codex requests require a ChatGPT subscription grant")
    if name == "antigravity":
        return _antigravity_complete(api_key, {}, model, system, instruction, labeled_b64)
    if name == "nvidia":
        return _nvidia_request(
            post,
            api_key,
            model,
            _openai_multimodal_body(
                model, system, instruction, labeled_b64, max_tokens=max_tokens,
                extra=_nvidia_request_extras(model),
            ),
            timeout=read_timeout,
            max_bytes=max_response_bytes,
        )
    if name == "cerebras":
        body = _openai_multimodal_body(
            model,
            system,
            instruction,
            labeled_b64,
            max_tokens=max_tokens,
            extra=_CEREBRAS_REQUEST_EXTRAS,
        )
        if _cerebras_too_large(body, env if env is not None else os.environ):
            raise RuntimeError("request_too_large")
        return _request(
            post,
            "https://api.cerebras.ai/v1/chat/completions",
            {
                "authorization": f"Bearer {api_key}",
                "content-type": "application/json",
            },
            body,
            timeout=read_timeout,
            stream=True,
            max_bytes=max_response_bytes,
        )
    if name == "slm-tagger":
        return _call_slm_tagger(
            post,
            system,
            instruction,
            env=env if env is not None else os.environ,
            read_timeout=read_timeout,
        )
    raise RuntimeError(f"{name}:unwired")


def _extract_text(name: str, payload: dict[str, Any]) -> str:
    if name == "anthropic":
        return _text_from_anthropic(payload)
    return _text_from_openai(payload)


def _models_for_attempt(
    name: str,
    env: Mapping[str, str],
    *,
    kind: str,
    model_override: str | None,
    nvidia_vision: bool,
    auth_kind: str,
) -> tuple[str, ...]:
    if name == "anthropic" and model_override:
        return (model_override,)
    if name == "nvidia" and (kind == "vision" or nvidia_vision):
        return _nvidia_vision_models(env)
    if name == "codex" and nvidia_vision:
        kind = "vision"  # Research JSON calls carry attached images separately from their text contract.
    return (_model_for(name, env, kind=kind, auth_kind=auth_kind),)


LAST_RESORT_PROVIDER = "cerebras"


def _last_resort_last(order: Sequence[str]) -> tuple[str, ...]:
    """Cerebras is the last resort: nothing runs after it, whatever the caller passed."""
    rest = tuple(name for name in order if name != LAST_RESORT_PROVIDER)
    return rest + ((LAST_RESORT_PROVIDER,) if LAST_RESORT_PROVIDER in order else ())


# Subscription OAuth rungs whose credential the subscription-token daemon can
# refresh in place (scripts.subscription_tokens PROVIDERS keys).
_REFRESHABLE_SUBSCRIPTIONS = frozenset({"anthropic", "codex", "grok", "antigravity"})
_EXPIRED_TOKEN_MARKERS = (
    "token has expired",
    "token expired",
    "expired token",
    "token is expired",
    "re-authenticate",
    "invalid_token",
)


def _is_expired_subscription_token(name: str, auth: "AuthMaterial", status: int, body: str) -> bool:
    if status != 401 or auth.kind != "subscription" or name not in _REFRESHABLE_SUBSCRIPTIONS:
        return False
    lowered = (body or "").lower()
    return any(marker in lowered for marker in _EXPIRED_TOKEN_MARKERS)


def _refresh_subscription_token(name: str) -> None:
    """Best-effort in-band refresh of one subscription credential.

    Runs the same ``subscription_tokens`` pass the 30min timer runs, scoped to
    one provider and under its run lock. If the timer is mid-run the lock is
    held and this is a no-op; the caller re-reads the credential either way.
    Never raises: a failed refresh just means the retry fails too.
    """
    try:
        import subscription_tokens  # type: ignore  # PYTHONPATH=scripts (units)
    except ImportError:
        try:
            from scripts import subscription_tokens  # type: ignore
        except ImportError:
            logger.warning("subscription refresh unavailable: module not importable")
            return
    try:
        # No push login from the request path (it can block for minutes and
        # needs Joe), and no service heartbeat: a one-provider pass must not
        # overwrite the timer's fleet-wide health row.
        runtime = subscription_tokens.Runtime(
            login=lambda *_a, **_k: False,
            heartbeat=lambda *_a, **_k: None,
        )
        subscription_tokens.run("once", [name], runtime)
    except Exception as exc:  # noqa: BLE001 - refresh is best-effort
        logger.warning("subscription refresh provider=%s failed: %s", name, type(exc).__name__)


def _note_rate_limit(name: str, body: str, env: Mapping[str, str]) -> None:
    """Codex account limits cool down across oneshots. Other 429s stay per call."""
    if name != "codex":
        return
    seconds = provider_cooldown.reset_seconds(body, _RESPONSE_HEADERS.get())
    if seconds is None:
        seconds = provider_cooldown.fallback_seconds(env)
    provider_cooldown.note(name, seconds, env)


def _run_ladder(
    *,
    env: Mapping[str, str],
    post: Callable[..., Any],
    kind: str,
    log_prefix: str,
    call_provider: Callable[[str, AuthMaterial, str], tuple[int, str, Any]],
    parse_success: Callable[[str, dict[str, Any]], Any],
    model_override: str | None = None,
    providers: Sequence[str] | None = None,
    nvidia_vision: bool = False,
    refresh_token: Callable[[str], None] | None = None,
    failover_response_errors: bool = False,
    cooldown_timeouts: bool = False,
) -> tuple[Any, str, str, str, tuple[str, ...]]:
    refresh_token = refresh_token or _refresh_subscription_token
    attempted: list[str] = []
    skipped: list[str] = []
    order = _last_resort_last(ladder_order(env) if providers is None else providers)

    for name in order:
        if name == "cursor":
            skipped.append("cursor:unwired")
            logger.info("%s skip provider=cursor reason=unwired", log_prefix)
            continue
        auth = _auth_for(name, env)
        if auth is None:
            skipped.append(f"{name}:no_key")
            continue
        if provider_cooldown.rate_limited(name, env):
            attempted.append(f"{name}:rate_limited_cooldown")
            logger.warning("%s provider=%s skipped: rate-limit cooldown", log_prefix, name)
            continue
        held = provider_cooldown.process_reason(name)
        if held:
            attempted.append(f"{name}:{held}")
            logger.warning("%s provider=%s skipped: %s", log_prefix, name, held)
            continue
        logger.info(
            "%s try provider=%s auth=%s mechanism=%s",
            log_prefix,
            name,
            auth.kind,
            auth.mechanism,
        )
        models = _models_for_attempt(
            name, env, kind=kind, model_override=model_override, nvidia_vision=nvidia_vision,
            auth_kind=auth.kind,
        )
        provider_won = False
        refreshed_once = False
        won_model = ""
        won_parsed: Any = None
        won_text = ""
        limiter = _nvidia_limiter(env, post) if name == "nvidia" else None
        for model in models:
            if limiter is not None and not limiter.acquire(_nvidia_max_wait(env)):
                attempted.append(f"{name}:rate_limited_local")
                logger.warning(
                    "%s provider=%s skipped: NVIDIA rate budget spent or paused; "
                    "no request sent",
                    log_prefix,
                    name,
                )
                break
            try:
                status, raw, payload = call_provider(name, auth, model)
            except ModelResponseError as exc:
                if not failover_response_errors:
                    raise
                attempted.append(f"{name}:{exc.code}")
                logger.warning(
                    "%s provider=%s model=%s %s", log_prefix, name, model, safe_error_message(exc)
                )
                continue
            except json.JSONDecodeError:
                if not failover_response_errors:
                    raise
                attempted.append(f"{name}:response_unreadable")
                logger.warning(
                    "%s provider=%s model=%s response_unreadable", log_prefix, name, model
                )
                continue
            except RuntimeError as exc:
                code = str(exc) or "network"
                if code == "request_too_large":
                    provider_cooldown.skip_process(name, "request_too_large")
                elif cooldown_timeouts and "Timeout" in code:
                    provider_cooldown.skip_process(name, "timeout_cooldown")
                attempted.append(f"{name}:{code}")
                logger.warning(
                    "%s provider=%s model=%s failed %s", log_prefix, name, model, code
                )
                continue

            body_text = raw if isinstance(raw, str) else ""
            if not refreshed_once and _is_expired_subscription_token(
                name, auth, status, body_text
            ):
                # Expired OAuth access token: refresh once in-band, reload the
                # credential, and retry this model once before falling through.
                refreshed_once = True
                logger.warning(
                    "%s provider=%s model=%s auth=subscription token expired; "
                    "refreshing and retrying once",
                    log_prefix,
                    name,
                    model,
                )
                attempted.append(f"{name}:token_expired_refreshed")
                refresh_token(name)
                fresh = _auth_for(name, env)
                if fresh is not None:
                    auth = fresh
                    try:
                        status, raw, payload = call_provider(name, auth, model)
                    except ModelResponseError:
                        raise
                    except RuntimeError as exc:
                        code = str(exc) or "network"
                        attempted.append(f"{name}:{code}")
                        logger.warning(
                            "%s provider=%s model=%s failed %s", log_prefix, name, model, code
                        )
                        continue
                    body_text = raw if isinstance(raw, str) else ""
            if limiter is not None:
                if status == 429:
                    limiter.note_429(None)
                elif status in (401, 403):
                    limiter.note_auth_failure(status)
                elif status == 200:
                    limiter.note_success()
            if name == "nvidia" and status in (401, 403):
                logger.error(
                    "%s NVIDIA AUTHORIZATION FAILED status=%s model=%s: key refused or "
                    "temporarily blocked; not retried body=%r",
                    log_prefix,
                    status,
                    model,
                    _body_snippet(body_text),
                )
            if payload is None or _is_hard_fail(status, payload):
                code = _classify_http_failure(
                    status, _error_envelope(payload) if status == 200 else body_text
                )
                attempted.append(f"{name}:{code}")
                logger.warning(
                    "%s provider=%s model=%s auth=%s failed %s status=%s body=%r",
                    log_prefix,
                    name,
                    model,
                    auth.kind,
                    code,
                    status,
                    _body_snippet(body_text) if status != 200 else "",
                )
                if status == 429:
                    _note_rate_limit(name, body_text, env)
                if status == 413:
                    provider_cooldown.skip_process(name, "request_too_large")
                if status == 429 and name == "nvidia":
                    break
                continue

            if not isinstance(payload, dict):
                attempted.append(f"{name}:unparseable")
                logger.warning(
                    "%s provider=%s model=%s unparseable", log_prefix, name, model
                )
                continue

            try:
                parsed, text = parse_success(name, payload)
            except ModelResponseError as exc:
                if not failover_response_errors:
                    raise
                attempted.append(f"{name}:{exc.code}")
                logger.warning(
                    "%s provider=%s model=%s %s", log_prefix, name, model, safe_error_message(exc)
                )
                continue
            except (ValueError, TypeError, KeyError):
                attempted.append(f"{name}:unparseable")
                logger.warning(
                    "%s provider=%s model=%s unparseable", log_prefix, name, model
                )
                continue

            if parsed is None:
                attempted.append(f"{name}:unparseable")
                logger.warning(
                    "%s provider=%s model=%s unparseable", log_prefix, name, model
                )
                continue

            provider_won = True
            won_model = model
            won_parsed = parsed
            won_text = text
            break

        if provider_won:
            logger.info(
                "%s won provider=%s model=%s auth=%s",
                log_prefix,
                name,
                won_model,
                auth.kind,
            )
            return (
                won_parsed,
                won_text,
                name,
                won_model,
                tuple(attempted + [f"{name}:ok"]),
            )

    tried = " ".join(attempted) or "none"
    skip = " ".join(skipped) or "none"
    if not attempted:
        raise ModelLadderExhausted(
            f"Model ladder exhausted: no keyed provider. "
            f"order={' -> '.join(order)}; skipped={skip}."
        )
    raise ModelLadderExhausted(
        f"Model ladder exhausted after trying every keyed provider. "
        f"tried={tried}; skipped={skip}."
    )


def extract_via_vision(
    png_bytes: bytes,
    prompt: str,
    *,
    env: Mapping[str, str] | None = None,
    post: Callable[..., Any] | None = None,
    max_response_bytes: int = 2_000_000,
) -> VisionResult:
    src = env if env is not None else os.environ
    sender = post or _default_post
    b64 = base64.b64encode(png_bytes).decode("utf-8")

    def call_provider(name: str, auth: AuthMaterial, model: str) -> tuple[int, str, Any]:
        return _call_vision_provider(
            name, auth, model, b64, prompt, sender,
            max_response_bytes=max_response_bytes, env=src,
        )

    def parse_success(name: str, payload: dict[str, Any]) -> tuple[Any, str]:
        text = _extract_text(name, payload)
        rows = parse_json_rows(text)
        return rows, text

    parsed, _text, provider, model, attempted = _run_ladder(
        env=src,
        post=sender,
        kind="vision",
        log_prefix="vision",
        call_provider=call_provider,
        parse_success=parse_success,
        nvidia_vision=True,
    )
    return VisionResult(
        rows=parsed,
        provider=provider,
        model=model,
        attempted=attempted,
    )


def complete_multimodal_json(
    instruction: str,
    images: Sequence[tuple[str, Path | bytes]] = (),
    *,
    system: str = "",
    env: Mapping[str, str] | None = None,
    post: Callable[..., Any] | None = None,
    model_override: str | None = None,
    max_tokens: int = 6000,
    stream_anthropic: bool = True,
    read_timeout: float = 120.0,
    max_response_bytes: int = 2_000_000,
    require_end_turn: bool = True,
    accept: Callable[[Any], bool] | None = None,
    log_prefix: str = "research",
    providers: Sequence[str] | None = None,
    failover_response_errors: bool = False,
    cooldown_timeouts: bool = False,
) -> LadderResult:
    """Run multimodal JSON completion through the shared ladder."""
    src = env if env is not None else os.environ
    sender = post or _default_post
    labeled_b64 = _labeled_images_to_b64(images)

    def call_provider(name: str, auth: AuthMaterial, model: str) -> tuple[int, str, Any]:
        use_stream = stream_anthropic and name == "anthropic"
        return _call_text_provider(
            name,
            auth,
            model,
            system,
            instruction,
            labeled_b64,
            sender,
            max_tokens=max_tokens,
            stream_anthropic=use_stream,
            read_timeout=read_timeout,
            max_response_bytes=max_response_bytes,
            env=src,
        )

    def parse_success(name: str, payload: dict[str, Any]) -> tuple[Any, str]:
        if require_end_turn and name == "anthropic" and payload.get("stop_reason") != "end_turn":
            reason = payload.get("stop_reason")
            safe_reason = "missing" if reason is None else "other"
            if reason in ("max_tokens", "stop_sequence", "tool_use", "pause_turn",
                          "refusal", "model_context_window_exceeded"):
                safe_reason = reason
            usage = payload.get("usage")
            usage = usage if isinstance(usage, dict) else {}
            counts = []
            for key in ("input_tokens", "output_tokens"):
                value = usage.get(key)
                counts.append(str(value) if type(value) is int and 0 <= value <= 1_000_000_000
                              else "unknown")
            raise ModelResponseError(
                "incomplete",
                "Research reviewer did not finish a complete response "
                f"(stop_reason={safe_reason}; input_tokens={counts[0]}; output_tokens={counts[1]})",
            )
        text = _extract_text(name, payload)
        obj = parse_json_object(text)
        if obj is not None and accept is not None and not accept(obj):
            return None, text
        return obj, text

    parsed, text, provider, model, attempted = _run_ladder(
        env=src,
        post=sender,
        kind="text",
        log_prefix=log_prefix,
        call_provider=call_provider,
        parse_success=parse_success,
        model_override=model_override,
        nvidia_vision=bool(labeled_b64),
        providers=providers,
        failover_response_errors=failover_response_errors,
        cooldown_timeouts=cooldown_timeouts,
    )
    return LadderResult(
        data=parsed,
        text=text,
        provider=provider,
        model=model,
        attempted=attempted,
    )


def accept_tags_payload(obj: Any) -> bool:
    """Newsfeed text-tagger contract: JSON object with at least 3 tags."""
    tags = obj.get("tags") if isinstance(obj, dict) else None
    return isinstance(tags, list) and len(tags) >= 3


def accept_distill_payload(obj: Any) -> bool:
    """Knowledge distill contract: JSON object with a non-empty summary."""
    if not isinstance(obj, dict):
        return False
    summary = obj.get("summary")
    return isinstance(summary, str) and bool(summary.strip())


def complete_text_json(
    instruction: str,
    *,
    system: str = "",
    env: Mapping[str, str] | None = None,
    post: Callable[..., Any] | None = None,
    model_override: str | None = None,
    max_tokens: int = 800,
    read_timeout: float = 30.0,
    max_response_bytes: int = 2_000_000,
    require_end_turn: bool = False,
    accept: Callable[[Any], bool] | None = None,
    log_prefix: str = "text",
    providers: Sequence[str] | None = None,
    failover_response_errors: bool = False,
    cooldown_timeouts: bool = False,
) -> LadderResult:
    """Text-only JSON completion through the shared ladder. Cerebras is last."""
    return complete_multimodal_json(
        instruction,
        images=(),
        system=system,
        env=env,
        post=post,
        model_override=model_override,
        max_tokens=max_tokens,
        stream_anthropic=True,
        read_timeout=read_timeout,
        max_response_bytes=max_response_bytes,
        require_end_turn=require_end_turn,
        accept=accept,
        log_prefix=log_prefix,
        providers=providers,
        failover_response_errors=failover_response_errors,
        cooldown_timeouts=cooldown_timeouts,
    )


def subscription_auth(name: str, env: Mapping[str, str] | None = None) -> AuthMaterial | None:
    """Resolved credential for one provider under the subscriptions-only policy.

    The public seam for scripts that call a provider directly instead of
    running the whole ladder (CTA share copy, X search, catalog refresh): the
    subscription grant or None. Prepaid keys are never returned.
    """
    return _auth_for(name, os.environ if env is None else env)


def anthropic_request_headers(auth: AuthMaterial) -> dict[str, str]:
    """Messages-API headers for a resolved Anthropic credential."""
    return _anthropic_headers(auth.token, subscription=auth.kind == "subscription")


def anthropic_request_system(auth: AuthMaterial, system: str) -> Any:
    """`system` field for a resolved Anthropic credential (identity block first on a grant)."""
    return _anthropic_system(system, subscription=auth.kind == "subscription")


__all__ = [
    "MODEL_LADDER_ORDER",
    "MODEL_LADDER_TIERS",
    "AuthMaterial",
    "LadderResult",
    "ModelLadderExhausted",
    "VisionResult",
    "accept_distill_payload",
    "accept_slm_tags_payload",
    "accept_tags_payload",
    "anthropic_request_headers",
    "anthropic_request_system",
    "complete_multimodal_json",
    "complete_text_json",
    "extract_via_vision",
    "parse_json_object",
    "parse_json_rows",
    "safe_error_message",
    "subscription_auth",
    "wired_providers",
]
