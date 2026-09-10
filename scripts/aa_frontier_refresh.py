#!/usr/bin/env python3
"""Refresh the fixed Artificial Analysis frontier-price basket.

The basket contains one benchmarked text frontier model per major provider.
Selection is deterministic and only a complete, validated catalog can replace
the encrypted last-known-good value used by ``scripts.ai_cycle``.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import math
import os
import re
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Protocol

import requests

from scripts.ai_cycle.collectors import URLS
from scripts.secret_store import SecretStore
from scripts.utils.atomic_io import atomic_save, verified_load

SERVICE = "aa-frontier-basket"
API_KEY_SECRET = "ARTIFICIAL_ANALYSIS_API_KEY"
BASKET_SECRET = "RADON_AI_CYCLE_AA_BASKET"
ACTOR = "aa-frontier-refresh"
ALGORITHM_VERSION = "aa-frontier-v1"
CREATOR_IDS = {
    "openai": "e67e56e3-15cd-43db-b679-da4660a69f41",
    "anthropic": "f0aa413f-e8ae-4fcd-9c48-0e049f4f3128",
    "google": "faddc6d9-2c14-445f-9b28-56726f59c793",
    "xai": "a1e3ddcf-d3e4-44a5-9e8f-029a69850875",
    "deepseek": "58b835bf-4c87-4f87-a846-df4b692c6e7d",
    "alibaba": "d874d370-74d3-4fa0-ba00-5272f92f946b",
    "mistral": "b5c0639a-cc9c-443b-a07e-bae6b7088933",
    "meta": "e1694725-0192-4e54-b1b8-c97e816c6cbe",
}
DEFAULT_PROVIDERS = tuple(CREATOR_IDS)
DEFAULT_STATE_PATH = Path.home() / ".radon/ai-cycle/aa-frontier-refresh.json"
DEFAULT_LOCK_PATH = Path.home() / ".radon/ai-cycle/aa-frontier-refresh.lock"
MINIMUM_CATALOG_ROWS = 100
FETCH_TIMEOUT_SECONDS = 30
_SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,127}$")
_UUID = re.compile(r"^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$")
_NON_TEXT_OR_UNSTABLE = re.compile(
    r"(?:\bpreview\b|\bexperimental\b|(?:^|-)exp(?:-|$)|\balpha\b|\bbeta\b|"
    r"\bvision\b|\bimage\b|\baudio\b|\bembedding\b|\bembed\b|\brerank\b|"
    r"\bmoderation\b|\bguard\b|\bsearch\b|\bcoding\b|\bcode\b|\bmath\b|\bocr\b|"
    r"\btranslation\b|(?:^|-)vl(?:-|$))",
    re.IGNORECASE,
)


class CatalogError(ValueError):
    """The publisher response cannot safely produce a complete basket."""


class SecretWriter(Protocol):
    def get_secret(self, name: str) -> str | None: ...

    def set_secret(self, name: str, value: str, actor: str) -> None: ...


@dataclass(frozen=True)
class FrontierModel:
    provider: str
    creator_id: str
    model_id: str
    slug: str
    release_date: str
    score: float


def _finite_positive(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    parsed = float(value)
    return parsed if math.isfinite(parsed) and parsed > 0 else None


def _candidate(row: Any, provider: str, today: date) -> FrontierModel | None:
    if not isinstance(row, dict):
        return None
    creator = row.get("model_creator")
    if not isinstance(creator, dict) or creator.get("id") != CREATOR_IDS[provider]:
        return None
    model_id, slug, name = row.get("id"), row.get("slug"), row.get("name")
    if not isinstance(model_id, str) or not _UUID.fullmatch(model_id):
        return None
    if not isinstance(slug, str) or not _SLUG.fullmatch(slug):
        return None
    if not isinstance(name, str) or _NON_TEXT_OR_UNSTABLE.search(f"{name} {slug}"):
        return None
    try:
        released = date.fromisoformat(row["release_date"])
    except (KeyError, TypeError, ValueError):
        return None
    if released > today:
        return None
    pricing = row.get("pricing")
    evaluations = row.get("evaluations")
    if not isinstance(pricing, dict) or not isinstance(evaluations, dict):
        return None
    if _finite_positive(pricing.get("price_1m_input_tokens")) is None:
        return None
    if _finite_positive(pricing.get("price_1m_output_tokens")) is None:
        return None
    score = _finite_positive(evaluations.get("artificial_analysis_intelligence_index"))
    if score is None:
        return None
    return FrontierModel(provider, CREATOR_IDS[provider], model_id, slug, released.isoformat(), score)


def _rank(candidate: FrontierModel) -> tuple[int, float, int, str]:
    return (
        -date.fromisoformat(candidate.release_date).toordinal(),
        -candidate.score,
        len(candidate.slug),
        candidate.model_id,
    )


def select_frontier_models(
    payload: Any,
    *,
    today: date | None = None,
    providers: tuple[str, ...] = DEFAULT_PROVIDERS,
    minimum_rows: int = 1,
    previous: list[dict[str, Any]] | None = None,
) -> tuple[FrontierModel, ...]:
    """Resolve stable model identities, retaining frontier incumbents.

    A provider's frontier is the set scoring at least 90% of its current best.
    Within that band, a strictly newer release replaces the incumbent. Stable
    model IDs prevent an upstream slug rename from forking cohort identity.
    """
    if not isinstance(payload, dict) or payload.get("status") != 200 or not isinstance(payload.get("data"), list):
        raise CatalogError("Artificial Analysis catalog schema is invalid")
    rows = payload["data"]
    if len(rows) < minimum_rows:
        raise CatalogError("Artificial Analysis catalog is unexpectedly incomplete")
    ids = [row.get("id") for row in rows if isinstance(row, dict) and isinstance(row.get("id"), str)]
    slugs = [row.get("slug") for row in rows if isinstance(row, dict) and isinstance(row.get("slug"), str)]
    if len(ids) != len(set(ids)) or len(slugs) != len(set(slugs)):
        raise CatalogError("Artificial Analysis catalog contains duplicate model identity")

    prior_by_provider = {
        item.get("provider"): item.get("model_id") for item in previous or [] if isinstance(item, dict)
    }
    selected: list[FrontierModel] = []
    effective_today = today or datetime.now(timezone.utc).date()
    for provider in providers:
        candidates = [
            candidate for row in rows if (candidate := _candidate(row, provider, effective_today)) is not None
        ]
        if not candidates:
            raise CatalogError(f"No eligible frontier model for provider {provider}")
        frontier_floor = max(candidate.score for candidate in candidates) * 0.9
        frontier = [candidate for candidate in candidates if candidate.score >= frontier_floor]
        ranked = sorted(frontier, key=_rank)
        incumbent = next(
            (candidate for candidate in frontier if candidate.model_id == prior_by_provider.get(provider)),
            None,
        )
        choice = ranked[0]
        if incumbent is not None and date.fromisoformat(choice.release_date) <= date.fromisoformat(
            incumbent.release_date
        ):
            choice = incumbent
        selected.append(choice)
    if len({candidate.model_id for candidate in selected}) != len(selected):
        raise CatalogError("Frontier basket contains duplicate model identities")
    return tuple(selected)


def select_frontier_basket(
    payload: Any,
    *,
    today: date | None = None,
    providers: tuple[str, ...] = DEFAULT_PROVIDERS,
    minimum_rows: int = 1,
    previous: list[dict[str, Any]] | None = None,
) -> tuple[str, ...]:
    return tuple(
        candidate.slug
        for candidate in select_frontier_models(
            payload,
            today=today,
            providers=providers,
            minimum_rows=minimum_rows,
            previous=previous,
        )
    )


def refresh_basket(
    store: SecretWriter,
    payload: Any,
    *,
    today: date | None = None,
    minimum_rows: int = 1,
    previous: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Validate fully, then replace the encrypted basket only when changed."""
    result = _selection_result(
        payload,
        today=today,
        minimum_rows=minimum_rows,
        previous=previous,
    )
    prior = store.get_secret(BASKET_SECRET)
    if prior == result["basket"]:
        result["status"] = "unchanged"
    else:
        store.set_secret(BASKET_SECRET, result["basket"], actor=ACTOR)
        if store.get_secret(BASKET_SECRET) != result["basket"]:
            raise CatalogError("Encrypted basket read-back verification failed")
        result["status"] = "updated"
    return result


def _selection_result(
    payload: Any,
    *,
    today: date | None,
    minimum_rows: int,
    previous: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    selected = select_frontier_models(
        payload,
        today=today,
        minimum_rows=minimum_rows,
        previous=previous,
    )
    return {
        "basket": ",".join(candidate.slug for candidate in selected),
        "providers": len(selected),
        "models": [asdict(candidate) for candidate in selected],
    }


def fetch_catalog(api_key: str, *, session: Any = requests, timeout: int = FETCH_TIMEOUT_SECONDS) -> dict[str, Any]:
    try:
        response = session.get(URLS["artificial-analysis"], headers={"x-api-key": api_key}, timeout=timeout)
    except requests.RequestException as exc:
        raise CatalogError("Artificial Analysis catalog transport failed") from exc
    if response.status_code != 200:
        raise CatalogError(f"Artificial Analysis catalog returned HTTP {response.status_code}")
    try:
        payload = response.json()
    except (TypeError, ValueError) as exc:
        raise CatalogError("Artificial Analysis catalog returned invalid JSON") from exc
    if not isinstance(payload, dict):
        raise CatalogError("Artificial Analysis catalog schema is invalid")
    return payload


def load_state(state_path: Path) -> dict[str, Any] | None:
    try:
        state = verified_load(str(state_path))
    except (FileNotFoundError, OSError, ValueError, json.JSONDecodeError, TypeError):
        return None
    return state if isinstance(state, dict) else None


@contextmanager
def exclusive_lock(path: Path) -> Iterator[bool]:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch(mode=0o600, exist_ok=True)
    os.chmod(path, 0o600)
    with path.open("a+", encoding="utf-8") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        try:
            yield True
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def run_once(
    *,
    store: SecretWriter,
    session: Any = requests,
    state_path: Path = DEFAULT_STATE_PATH,
    today: date | None = None,
    force: bool = False,
) -> dict[str, Any]:
    effective_today = today or datetime.now(timezone.utc).date()
    current = store.get_secret(BASKET_SECRET)
    state = load_state(state_path)
    if (
        not force
        and state is not None
        and state.get("algorithm_version") == ALGORITHM_VERSION
        and state.get("completed_date") == effective_today.isoformat()
        and state.get("basket") == current
    ):
        return {"status": "already-current", "basket": current, "providers": len(DEFAULT_PROVIDERS)}
    api_key = store.get_secret(API_KEY_SECRET)
    if not api_key:
        raise CatalogError("Artificial Analysis API key is not configured")
    payload = fetch_catalog(api_key, session=session)
    previous = state.get("models") if state and state.get("algorithm_version") == ALGORITHM_VERSION else None
    prior_catalog_rows = state.get("catalog_rows", 0) if state else 0
    if isinstance(prior_catalog_rows, bool) or not isinstance(prior_catalog_rows, int):
        prior_catalog_rows = 0
    minimum_rows = max(MINIMUM_CATALOG_ROWS, int(prior_catalog_rows * 0.8))
    result = _selection_result(
        payload,
        today=effective_today,
        minimum_rows=minimum_rows,
        previous=previous,
    )
    current_status = "unchanged" if current == result["basket"] else "updated"
    atomic_save(
        str(state_path),
        {
            "algorithm_version": ALGORITHM_VERSION,
            "completed_date": effective_today.isoformat(),
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "catalog_hash": hashlib.sha256(
                json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
            ).hexdigest(),
            "catalog_rows": len(payload["data"]),
            "basket": result["basket"],
            "providers": result["providers"],
            "models": result["models"],
        },
    )
    os.chmod(state_path, 0o600)
    if current != result["basket"]:
        store.set_secret(BASKET_SECRET, result["basket"], actor=ACTOR)
        if store.get_secret(BASKET_SECRET) != result["basket"]:
            raise CatalogError("Encrypted basket read-back verification failed")
    return {"status": current_status, **result}


def _write_health(state: str, started: str, error: dict[str, str] | None = None) -> None:
    from scripts.db.hrana_http import write_service_health_http

    write_service_health_http(
        SERVICE,
        state,
        started_at=started,
        finished_at=datetime.now(timezone.utc).isoformat(),
        error=error,
        timeout=8,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE_PATH)
    parser.add_argument("--lock", type=Path, default=DEFAULT_LOCK_PATH)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args(argv)
    started = datetime.now(timezone.utc).isoformat()
    try:
        with exclusive_lock(args.lock) as acquired:
            if not acquired:
                print(json.dumps({"status": "busy"}))
                return 0
            result = run_once(store=SecretStore(), state_path=args.state, force=args.force)
        _write_health("ok", started)
        print(json.dumps(result, sort_keys=True))
        return 0
    except CatalogError as exc:
        error = {"message": str(exc)}
    except Exception:
        error = {"message": "Artificial Analysis frontier refresh failed"}
    try:
        _write_health("error", started, error)
    except Exception:
        pass
    print(json.dumps({"status": "error", "reason": error["message"]}, sort_keys=True))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
