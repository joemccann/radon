#!/usr/bin/env python3
"""LLM model catalog - one frontier chat model per provider, daily.

Keeps the chat model picker honest: it lists exactly the providers whose
API key is present in this deployment's environment, at whatever model
each provider is currently shipping as its frontier. A provider with no
key is skipped silently - absence is a supported state, not a
misconfiguration - so GPT and Grok light up on their own the day their
key lands in ``/etc/radon/env`` (and ``systemctl restart radon-nextjs``
makes it visible to the picker).

Sources, each gated by its own key:
  anthropic  GET https://api.anthropic.com/v1/models?limit=100
             headers: x-api-key, anthropic-version: 2023-06-01
             keys: ANTHROPIC_API_KEY / CLAUDE_CODE_API_KEY / CLAUDE_API_KEY
  openai     GET https://api.openai.com/v1/models   (Bearer)
             key: OPENAI_API_KEY
  xai        GET https://api.x.ai/v1/language-models (Bearer), falling
             back to /v1/models - only the former carries modalities.
             keys: XAI_API_KEY / GROK_API_KEY

Operator overrides (each wins over discovery, so a bad heuristic is
recoverable without a deploy): ANTHROPIC_MODEL, OPENAI_MODEL,
XAI_MODEL / GROK_MODEL. These are the same variables web/lib/llm/
provider.ts already reads, so pinning one pins both halves.

Selection is a pure filter -> sort -> head per provider (see each
``select_*_frontier``); the HTTP call is a thin shell around it. Nothing
is ever written that a live response did not contain, except an explicit
operator override. A provider that errors, times out or rate-limits
keeps its EXISTING row - a failed poll never blanks a good one - but the
run's heartbeat is ``error`` naming the carried-forward providers, so a
key that 401s nightly pages instead of aging silently behind an ``ok``.
The previous row comes from Turso first, the JSON cache second.

Each provider poll has its own wall-clock budget (PROVIDER_BUDGET_S) and
Anthropic's cursor walk is capped (ANTHROPIC_MAX_PAGES), so one slow or
looping provider cannot consume the unit's TimeoutStartSec.

Output is dual-written to Turso llm_model_catalog + scan_snapshots and
data/llm_models.json (the fallback GET /api/models reads).

Usage:
    python3 scripts/refresh_model_catalog.py             # summary (stderr)
    python3 scripts/refresh_model_catalog.py --json      # JSON to stdout
"""
from __future__ import annotations

import argparse
import json
import os
import re
import signal
import sys
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator, Optional

# -- path setup ----------------------------------------------------
_SCRIPT_DIR = Path(__file__).resolve().parent
_PROJECT_DIR = _SCRIPT_DIR.parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

try:
    from dotenv import load_dotenv  # type: ignore[import-untyped]
    load_dotenv(_PROJECT_DIR / ".env")
    load_dotenv(_PROJECT_DIR / "web" / ".env")
except Exception:
    pass

from db import writer
from utils.ipv4_first import prefer_ipv4

prefer_ipv4()

# -- constants -----------------------------------------------------
SERVICE = "model-catalog"
LLM_MODELS_JSON = _PROJECT_DIR / "data" / "llm_models.json"

PROVIDERS = ("anthropic", "openai", "xai")

# Mirrors web/lib/llm/provider.ts exactly - the picker and the chat call
# must agree on what "this provider is configured" means.
PROVIDER_KEY_ENV: dict[str, tuple[str, ...]] = {
    "anthropic": ("ANTHROPIC_API_KEY", "CLAUDE_CODE_API_KEY", "CLAUDE_API_KEY"),
    "openai": ("OPENAI_API_KEY",),
    "xai": ("XAI_API_KEY", "GROK_API_KEY"),
}
PROVIDER_OVERRIDE_ENV: dict[str, tuple[str, ...]] = {
    "anthropic": ("ANTHROPIC_MODEL",),
    "openai": ("OPENAI_MODEL",),
    "xai": ("XAI_MODEL", "GROK_MODEL"),
}

ANTHROPIC_MODELS_URL = "https://api.anthropic.com/v1/models"
ANTHROPIC_VERSION = "2023-06-01"
OPENAI_MODELS_URL = "https://api.openai.com/v1/models"
XAI_LANGUAGE_MODELS_URL = "https://api.x.ai/v1/language-models"
XAI_MODELS_URL = "https://api.x.ai/v1/models"

USER_AGENT = "radon/2.0"
FETCH_TIMEOUT_S = 30
# Wall-clock bound per provider poll. urlopen's timeout bounds one socket
# operation, not the request, so a slow-drip page could otherwise run to
# the unit's TimeoutStartSec=300; three providers at this budget plus the
# Turso writes stay inside it.
PROVIDER_BUDGET_S = 60.0
# Anthropic lists ~10 models; 10 pages of 100 is already absurd, and a
# server that answers has_more with a repeating cursor must not spin.
ANTHROPIC_MAX_PAGES = 10


def _log(message: str) -> None:
    print(f"[{SERVICE}] {message}", file=sys.stderr)


def _iso_utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


# -- environment ---------------------------------------------------

def _first_env(names: tuple[str, ...]) -> Optional[str]:
    """First non-blank value among ``names``.

    Two-argument ``.get`` deliberately: cloud/tests/test_env_contract_parity.py
    treats a one-argument read as a required key, and every one of these is
    optional by design.
    """
    for name in names:
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return None


def provider_key(provider: str) -> Optional[str]:
    """API key for ``provider``, or None when this deployment has none."""
    return _first_env(PROVIDER_KEY_ENV[provider])


def provider_override(provider: str) -> Optional[str]:
    """Operator-pinned model id for ``provider``, or None."""
    return _first_env(PROVIDER_OVERRIDE_ENV[provider])


def preferred_provider() -> str:
    """Default-model precedence, mirroring provider.ts resolveProvider():
    xAI whenever its key is set, Anthropic otherwise."""
    return "xai" if provider_key("xai") else "anthropic"


# -- HTTP transport ------------------------------------------------

def _http_get_json(url: str, headers: dict[str, str]) -> dict[str, Any]:
    from urllib.request import Request, urlopen

    request = Request(url, headers={"User-Agent": USER_AGENT, **headers})
    with urlopen(request, timeout=FETCH_TIMEOUT_S) as response:
        return json.loads(response.read().decode("utf-8", "replace"))


@contextmanager
def _provider_budget() -> Iterator[None]:
    """Raise TimeoutError inside the block once PROVIDER_BUDGET_S elapses.

    SIGALRM, because it is the only bound that reaches into a blocking
    urlopen read (PEP 475 aborts the syscall when the handler raises). Main
    thread only - which this oneshot always is - and a no-op elsewhere.
    """
    if threading.current_thread() is not threading.main_thread():
        yield
        return

    def _expire(signum: int, frame: Any) -> None:
        raise TimeoutError(f"poll exceeded its {PROVIDER_BUDGET_S:g}s budget")

    restore = signal.signal(signal.SIGALRM, _expire)
    signal.setitimer(signal.ITIMER_REAL, PROVIDER_BUDGET_S)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, restore)


def fetch_anthropic_models(api_key: str) -> list[dict[str, Any]]:
    """Every page of GET /v1/models - the list is cursor-paginated."""
    headers = {"x-api-key": api_key, "anthropic-version": ANTHROPIC_VERSION}
    models: list[dict[str, Any]] = []
    url = f"{ANTHROPIC_MODELS_URL}?limit=100"
    for _ in range(ANTHROPIC_MAX_PAGES):
        payload = _http_get_json(url, headers)
        models.extend(payload.get("data") or [])
        if not payload.get("has_more") or not payload.get("last_id"):
            return models
        url = f"{ANTHROPIC_MODELS_URL}?limit=100&after_id={payload['last_id']}"
    raise RuntimeError(f"still has_more after {ANTHROPIC_MAX_PAGES} pages; cursor not advancing")


def fetch_openai_models(api_key: str) -> list[dict[str, Any]]:
    payload = _http_get_json(OPENAI_MODELS_URL, {"Authorization": f"Bearer {api_key}"})
    return list(payload.get("data") or [])


def fetch_xai_models(api_key: str) -> list[dict[str, Any]]:
    """/v1/language-models first - it is the only endpoint carrying
    modalities; /v1/models mixes image, video and voice SKUs into one
    list with no field to tell them apart."""
    headers = {"Authorization": f"Bearer {api_key}"}
    try:
        return list(_http_get_json(XAI_LANGUAGE_MODELS_URL, headers).get("models") or [])
    except Exception as exc:  # noqa: BLE001 - the id predicates still hold
        _log(f"xai: language-models unavailable ({exc}); falling back to /v1/models")
        return list(_http_get_json(XAI_MODELS_URL, headers).get("data") or [])


# -- frontier selection (pure) -------------------------------------

_DATED_SUFFIX = re.compile(r"-\d{8}$")
_ANTHROPIC_PREMIUM = re.compile(r"^claude-(fable|mythos)-")


def _version(prefix: str, model_id: str) -> Optional[float]:
    """Leading version as a FLOAT, never a semver tuple: grok-4.20 must
    read as 4.2 and lose to grok-4.6, not win it on a 20 > 6 compare."""
    match = re.match(rf"^{prefix}-(\d+(?:\.\d+)?)", model_id)
    return float(match.group(1)) if match else None


def select_anthropic_frontier(models: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
    """Newest undated general-chat Claude.

    Dated snapshots lose to the undated alias they pin; fable/mythos are
    excluded because they are 2x price, zero-data-retention-incompatible
    and reject `thinking: disabled` - a deliberate opt-in, not a default
    the catalog may pick on its own the day one ships after an Opus.
    """
    ranked = []
    for model in models:
        if model.get("type") != "model":
            continue
        model_id = str(model.get("id") or "")
        if not model_id.startswith("claude-"):
            continue
        if _DATED_SUFFIX.search(model_id) or _ANTHROPIC_PREMIUM.match(model_id):
            continue
        capabilities = model.get("capabilities") or {}
        if not (capabilities.get("image_input") or {}).get("supported"):
            continue
        created = _epoch(model.get("created_at"))
        if created is None:
            continue
        ranked.append(((-created, -int(model.get("max_input_tokens") or 0), model_id), model))
    return min(ranked, key=lambda pair: pair[0])[1] if ranked else None


def _epoch(created_at: Any) -> Optional[float]:
    try:
        return datetime.fromisoformat(str(created_at).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return None


_OPENAI_NON_CHAT = re.compile(
    r"(audio|realtime|transcribe|tts|image|search|embedding|moderation|vision)"
)
_OPENAI_NON_CONVERSATIONAL = re.compile(r"(codex|instruct)")
_OPENAI_CHEAP = re.compile(r"(mini|nano|luna)")
_OPENAI_DATED = re.compile(r"-\d{4}-\d{2}-\d{2}$")
_OPENAI_MMDD = re.compile(r"-\d{4}$")


def select_openai_frontier(models: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
    """Highest-version GPT chat model with no scheduled shutdown.

    ``created`` is not a ranking field - snapshots and aliases share and
    invert timestamps - so the version comes out of the id. An explicit
    flagship suffix beats the bare alias that points at it; -pro is
    Responses-oriented at ~6x price and -chat-latest silently re-points
    under you, so neither may be the picker default.
    """
    ranked = []
    for model in models:
        if model.get("object") != "model" or model.get("shutdown_date") is not None:
            continue
        model_id = str(model.get("id") or "")
        if not re.match(r"^gpt-\d", model_id):
            continue
        if _OPENAI_NON_CHAT.search(model_id) or _OPENAI_NON_CONVERSATIONAL.search(model_id):
            continue
        if "-preview" in model_id or _OPENAI_CHEAP.search(model_id):
            continue
        if _OPENAI_DATED.search(model_id) or _OPENAI_MMDD.search(model_id):
            continue
        if model_id.endswith("-pro") or model_id.endswith("-chat-latest"):
            continue
        version = _version("gpt", model_id)
        if version is None:
            continue
        tier = 3 if model_id.endswith("-sol") else 2 if model_id.endswith("-terra") else 0
        ranked.append(((-version, -tier, len(model_id), model_id), model))
    return min(ranked, key=lambda pair: pair[0])[1] if ranked else None


_XAI_NON_CHAT = re.compile(r"(imagine|voice|video|image|audio|embedding|tts)")
_XAI_CHEAP = re.compile(r"(fast|mini)")
_XAI_MMDD = re.compile(r"-\d{4}$")
_XAI_VARIANT = re.compile(r"-\d{4}-(reasoning|non-reasoning)$")


def select_xai_frontier(models: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
    """Highest-version general-chat Grok.

    Never ranked by context length: grok-4.3 carries 1M against the
    frontier's 500k, so a context sort picks the wrong model. Modality
    fields are checked only when present - /v1/models omits them, and the
    id predicates carry that path on their own.
    """
    ranked = []
    for model in models:
        model_id = str(model.get("id") or "")
        if not model_id.startswith("grok-"):
            continue
        inputs, outputs = model.get("input_modalities"), model.get("output_modalities")
        if inputs is not None and "text" not in inputs:
            continue
        if outputs is not None and "text" not in outputs:
            continue
        if _XAI_NON_CHAT.search(model_id) or _XAI_CHEAP.search(model_id):
            continue
        if _XAI_MMDD.search(model_id) or _XAI_VARIANT.search(model_id):
            continue
        if "multi-agent" in model_id or model_id.startswith("grok-build-"):
            continue
        version = _version("grok", model_id)
        if version is None:
            continue
        ranked.append(((-version, len(model_id), model_id), model))
    return min(ranked, key=lambda pair: pair[0])[1] if ranked else None


SELECTORS: dict[str, Callable[[list[dict[str, Any]]], Optional[dict[str, Any]]]] = {
    "anthropic": select_anthropic_frontier,
    "openai": select_openai_frontier,
    "xai": select_xai_frontier,
}


# -- payload -------------------------------------------------------

def label_for(model_id: str) -> str:
    """Terminal-cased picker label derived from the wire id."""
    return model_id.replace("-", " ").upper()


def build_output(
    *,
    rows: list[dict[str, Any]],
    scan_time: str,
    preferred: str,
) -> dict[str, Any]:
    """Snapshot payload / JSON fallback.

    ``models`` carries the same snake_cased columns as the Turso table, which
    is exactly what ``web/lib/llm/catalog.ts:toModelOption`` normalizes into an
    ``LlmModelOption`` from either tier - one shape, one reader.
    """
    models = [dict(row) for row in rows]
    by_provider = {model["provider"]: model["model_id"] for model in models}
    default_id = ""
    for provider in (preferred, *PROVIDERS):
        if provider in by_provider:
            default_id = by_provider[provider]
            break
    return {
        "scan_time": scan_time,
        "refreshed_at": scan_time,
        "defaultId": default_id,
        "models": models,
    }


def load_previous() -> dict[str, dict[str, Any]]:
    """Last good catalog by provider. Used only to carry a failing
    provider's row forward untouched.

    Turso first: the JSON cache is host-local and ephemeral on the VPS, and
    it is only rewritten on a run that resolved rows, so it lags Turso the
    moment a provider fails (R-456). The JSON seeds what Turso cannot
    answer - a cold table or an unreachable one.
    """
    previous: dict[str, dict[str, Any]] = {}
    try:
        payload = json.loads(LLM_MODELS_JSON.read_text())
    except Exception:  # noqa: BLE001 - a missing or corrupt cache is a cold start
        payload = {}
    for model in payload.get("models") or []:
        if model.get("provider") in PROVIDERS:
            previous[model["provider"]] = dict(model)
    try:
        turso_rows = writer.get_llm_model_catalog_rows()
    except Exception as exc:  # noqa: BLE001 - the JSON tier carries a Turso outage
        _log(f"llm_model_catalog read failed ({exc}); seeding from the JSON cache only")
        turso_rows = []
    for row in turso_rows:
        if row.get("provider") in PROVIDERS:
            previous[row["provider"]] = dict(row)
    return previous


# -- persistence ---------------------------------------------------

def _write_json_cache(payload: dict[str, Any]) -> None:
    LLM_MODELS_JSON.parent.mkdir(parents=True, exist_ok=True)
    tmp = LLM_MODELS_JSON.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2))
    os.replace(tmp, LLM_MODELS_JSON)


def _carry_forward_error(failed: dict[str, str]) -> Optional[dict[str, Any]]:
    if not failed:
        return None
    detail = "; ".join(f"{provider}: {reason}" for provider, reason in failed.items())
    return {
        "message": f"carried forward without a live poll - {detail}",
        "class": "provider_carry_forward",
        "providers": list(failed),
    }


def persist_result(
    payload: dict[str, Any],
    rows: list[dict[str, Any]],
    failed: Optional[dict[str, str]] = None,
) -> None:
    """Dual-write: Turso rows + snapshot + heartbeat, then the JSON
    fallback. Zero rows means every provider was keyless or failed, so
    nothing but the heartbeat is written - the last good Turso rows and
    JSON cache survive a total provider outage.

    ``failed`` maps each keyed provider that did NOT get a live row this run
    to its reason. Any entry makes the heartbeat ``error`` (R-455): an
    ``ok`` with today's finished_at would satisfy the 26h alarm while the
    catalog silently aged. Keyless providers are not failures.
    """
    scan_time = payload["scan_time"]
    error = _carry_forward_error(failed or {})
    state = "error" if error else "ok"
    writer.ensure_no_replica_for_writers()
    if not rows:
        _log("no provider rows resolved; leaving the existing catalog untouched")
        writer.record_service_health(SERVICE, state, finished_at=scan_time, error=error)
        return
    writer.upsert_llm_model_catalog_rows(rows, refreshed_at=scan_time)
    writer.upsert_scan_snapshot(SERVICE, scan_time, payload)
    writer.record_service_health(SERVICE, state, finished_at=scan_time, error=error)
    _write_json_cache(payload)


def _record_cycle_failure(scan_time: str, exc: BaseException) -> None:
    """Error heartbeat for a run that died before ``persist_result``. A row
    that never appears is invisible until the 26h window; best-effort, so a
    broken writer cannot mask the original failure."""
    try:
        writer.record_service_health(
            SERVICE,
            "error",
            finished_at=scan_time,
            error={"message": f"model-catalog cycle failed: {exc}", "class": "cycle_failed"},
        )
    except Exception as health_exc:  # noqa: BLE001 - best-effort mirror
        _log(f"error heartbeat non-fatal: {health_exc}")


# -- daily orchestration -------------------------------------------

# Lambdas, not direct references: the indirection resolves the module
# global at call time, so the tests can monkeypatch a single fetcher.
FETCHERS: dict[str, Callable[[str], list[dict[str, Any]]]] = {
    "anthropic": lambda key: fetch_anthropic_models(key),
    "openai": lambda key: fetch_openai_models(key),
    "xai": lambda key: fetch_xai_models(key),
}


def _row(provider: str, model_id: str, scan_time: str) -> dict[str, Any]:
    return {
        "provider": provider,
        "model_id": model_id,
        "display_name": label_for(model_id),
        "refreshed_at": scan_time,
    }


def resolve_provider_row(
    provider: str, key: str, scan_time: str, previous: dict[str, dict[str, Any]]
) -> tuple[Optional[dict[str, Any]], Optional[str]]:
    """One provider's catalog row: operator override, else discovery, else
    the previous row untouched. Returns ``(row, failure)``: ``row`` is None
    only on a cold-start failure; ``failure`` names why the row was carried
    forward instead of polled live, None when the poll succeeded."""
    override = provider_override(provider)
    if override:
        _log(f"{provider}: operator override {override}")
        return _row(provider, override, scan_time), None
    try:
        with _provider_budget():
            models = FETCHERS[provider](key)
    except Exception as exc:  # noqa: BLE001 - a bad poll must never blank a row
        _log(f"{provider}: model list failed ({exc}); keeping the existing row")
        return previous.get(provider), f"model list failed ({exc})"
    selected = SELECTORS[provider](models)
    if selected is None:
        _log(f"{provider}: {len(models)} models, none frontier; keeping the existing row")
        return previous.get(provider), f"{len(models)} models, none frontier"
    return _row(provider, str(selected["id"]), scan_time), None


def run(scan_time: Optional[str] = None) -> dict[str, Any]:
    scan_time = scan_time or _iso_utc_now()
    previous = load_previous()
    rows: list[dict[str, Any]] = []
    failed: dict[str, str] = {}
    for provider in PROVIDERS:
        key = provider_key(provider)
        if not key:
            continue
        row, failure = resolve_provider_row(provider, key, scan_time, previous)
        if row is not None:
            rows.append(row)
        if failure is not None:
            failed[provider] = failure
    payload = build_output(rows=rows, scan_time=scan_time, preferred=preferred_provider())
    persist_result(payload, rows, failed)
    return payload


# -- CLI -----------------------------------------------------------

def _print_summary(payload: dict[str, Any]) -> None:
    print(f"\nModel catalog - {len(payload['models'])} provider(s)", file=sys.stderr)
    for model in payload["models"]:
        marker = "*" if model["model_id"] == payload["defaultId"] else " "
        print(f"  {marker} {model['provider']:<10} {model['model_id']}", file=sys.stderr)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Refresh the chat model picker catalog (one frontier model per keyed provider)"
    )
    parser.add_argument("--json", action="store_true", help="Output JSON to stdout")
    args = parser.parse_args(argv)

    scan_time = _iso_utc_now()
    try:
        payload = run(scan_time)
    except Exception as exc:  # noqa: BLE001 - R-455/R-458: a crash must leave a row, not a traceback
        _log(f"cycle failed: {exc}")
        _record_cycle_failure(scan_time, exc)
        return 1
    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        _print_summary(payload)
    return 0


if __name__ == "__main__":
    sys.exit(main())
