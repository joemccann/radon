"""CTA sync health ledger helpers."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Optional

from utils.atomic_io import atomic_save


PROJECT_DIR = Path(__file__).resolve().parent.parent.parent
CACHE_DIR = PROJECT_DIR / "data" / "menthorq_cache"
HEALTH_DIR = CACHE_DIR / "health"
STATUS_PATH = HEALTH_DIR / "cta-sync-latest.json"
LEGACY_STATUS_PATH = CACHE_DIR / "cta_sync_status.json"
SERVICE_HEALTH_DIR = PROJECT_DIR / "data" / "service_health"
SERVICE_STATUS_PATH = SERVICE_HEALTH_DIR / "cta-sync.json"
HISTORY_DIR = HEALTH_DIR / "history"
LOCK_DIR = PROJECT_DIR / "data" / "locks" / "cta-sync.lock"
ARTIFACT_DIR = PROJECT_DIR / "logs" / "menthorq_artifacts"
RETRYABLE_ERRORS = {
    "auth_rejected",
    "challenge_page",
    "timeout",
    "empty_payload",
    "navigation_failure",
}
DEFAULT_RETRY_BACKOFFS_SECONDS = [0, 120, 600]


def _load_json(path: Path) -> Optional[dict[str, Any]]:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None

    if isinstance(payload, dict):
        payload.pop("_checksum", None)
        return payload
    return None


def load_cta_sync_status(path: str | Path = STATUS_PATH) -> Optional[dict[str, Any]]:
    status_path = Path(path)
    loaded = _load_json(status_path)
    if loaded is not None:
        return loaded
    if status_path == STATUS_PATH:
        return _load_json(LEGACY_STATUS_PATH)
    return None


def write_cta_sync_status(payload: dict[str, Any], path: str | Path = STATUS_PATH) -> Path:
    status_path = Path(path)
    status_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_save(str(status_path), payload)
    if status_path == STATUS_PATH:
        LEGACY_STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
        atomic_save(str(LEGACY_STATUS_PATH), payload)
        SERVICE_STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
        atomic_save(str(SERVICE_STATUS_PATH), payload)
    return status_path


def write_cta_sync_history(payload: dict[str, Any], history_dir: str | Path = HISTORY_DIR) -> Path:
    directory = Path(history_dir)
    directory.mkdir(parents=True, exist_ok=True)
    run_id = str(payload.get("run_id") or payload.get("last_attempt_started_at") or "unknown")
    history_path = directory / f"cta-sync-{run_id}.json"
    atomic_save(str(history_path), payload)
    return history_path


def validate_cta_payload(payload: Optional[dict[str, Any]], target_date: str) -> tuple[bool, Optional[str]]:
    if not payload:
        return False, "empty_payload"
    if payload.get("date") != target_date:
        return False, "target_date_mismatch"

    tables = payload.get("tables")
    if not isinstance(tables, dict):
        return False, "missing_tables"

    required = ("main", "index", "commodity", "currency")
    if any(key not in tables for key in required):
        return False, "missing_tables"

    if all(not tables.get(key) for key in required):
        return False, "empty_tables"

    return True, None


def classify_sync_error(stderr: str) -> tuple[str, str]:
    message = sanitize_sync_message(stderr)
    lowered = message.lower()

    if "username or password was incorrect" in lowered or "login failed" in lowered or "unauthorized" in lowered:
        return "auth_rejected", message
    if "captcha" in lowered or "verify you are human" in lowered or "cloudflare" in lowered:
        return "challenge_page", message
    if "timeout" in lowered:
        return "timeout", message
    if "field" in lowered and "not found" in lowered:
        return "selector_failure", message
    if "no cta data extracted" in lowered or "empty payload" in lowered:
        return "empty_payload", message
    if "navigation failed" in lowered:
        return "navigation_failure", message
    if "failed to initialize menthorq client" in lowered:
        return "client_init_failed", message
    return "unexpected_failure", message


def is_retryable_error(error_type: str | None) -> bool:
    return bool(error_type and error_type in RETRYABLE_ERRORS)


def retry_backoffs_for_error(error_type: str | None) -> list[int]:
    if is_retryable_error(error_type):
        return DEFAULT_RETRY_BACKOFFS_SECONDS.copy()
    return [0]


def sanitize_sync_message(text: str, max_len: int = 240) -> str:
    collapsed = re.sub(r"\s+", " ", text).strip()
    if not collapsed:
        return "CTA sync failed without stderr output"
    return collapsed[:max_len]


def latest_artifacts(limit: int = 2, artifact_dir: str | Path = ARTIFACT_DIR) -> dict[str, str]:
    artifact_path = Path(artifact_dir)
    if not artifact_path.exists():
        return {}

    if artifact_path.is_dir() and (artifact_path / "context.json").exists():
        return {
            "dir": str(artifact_path),
            "context": str(artifact_path / "context.json"),
            "html": str(artifact_path / "page.html"),
            "screenshot": str(artifact_path / "page.png"),
        }

    candidate_dirs = sorted(
        [path for path in artifact_path.glob("cta-sync-*") if path.is_dir()],
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    selected: dict[str, str] = {}
    for directory in candidate_dirs[:limit]:
        context = directory / "context.json"
        html = directory / "page.html"
        screenshot = directory / "page.png"
        if context.exists() and "context" not in selected:
            selected["dir"] = str(directory)
            selected["context"] = str(context)
        if html.exists() and "html" not in selected:
            selected["html"] = str(html)
        if screenshot.exists() and "screenshot" not in selected:
            selected["screenshot"] = str(screenshot)
        if {"context", "html", "screenshot"}.issubset(selected):
            break
    return selected


def latest_available_cta_date(cache_dir: str | Path = CACHE_DIR) -> Optional[str]:
    directory = Path(cache_dir)
    files = sorted(directory.glob("cta_*.json"))
    if not files:
        return None
    return files[-1].stem.removeprefix("cta_")
