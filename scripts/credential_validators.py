#!/usr/bin/env python3
"""Async credential checks behind the profile Credentials tab.

``validate(service_id, values)`` returns a verdict and NEVER raises:

  * ``valid``     — the vendor accepted the credential.
  * ``invalid``   — the vendor rejected it (401/403, failed login). The only
                    verdict that blocks a save.
  * ``error``     — the check itself failed (network, vendor 5xx, timeout).
                    Saving proceeds: a vendor outage must not lock the
                    operator out of rotating a key.
  * ``unchecked`` — no validator declared, or the field set is incomplete.

HTTP probes are the cheapest authenticated endpoint each vendor offers.
MenthorQ and TheMarketEar need a real browser login, run as a subprocess
(``scripts/validate_login.py``) so Playwright never blocks or crashes the
API process; callers surface the declared ``slow`` flag as a delay notice.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, Optional
from urllib.parse import urlparse

import requests

try:
    import credential_redaction
except ImportError:  # pragma: no cover
    from scripts import credential_redaction

try:
    import credentials_registry
except ImportError:  # pragma: no cover - depends on which root is on sys.path
    from scripts import credentials_registry

logger = logging.getLogger("radon.credential_validators")

HTTP_TIMEOUT_S = 10.0
SLOW_LOGIN_TIMEOUT_S = 90.0

_SCRIPTS_DIR = Path(__file__).resolve().parent
_VALIDATE_LOGIN = _SCRIPTS_DIR / "validate_login.py"

_STATUSES = ("valid", "invalid", "error", "unchecked")


@dataclass(frozen=True)
class ValidationResult:
    status: str
    message: str = ""

    @property
    def blocks_save(self) -> bool:
        return self.status == "invalid"

    def to_dict(self) -> Dict[str, str]:
        return scrub_validation_message({"status": self.status, "message": self.message})


def scrub_validation_message(payload: Dict[str, str]) -> Dict[str, str]:
    return credential_redaction.scrub_credential_text(payload)


def _verdict_from_status_code(code: int, vendor: str) -> ValidationResult:
    if 200 <= code < 300:
        return ValidationResult("valid")
    if code in (401, 403):
        return ValidationResult(
            "invalid", f"{vendor} rejected the credential (HTTP {code})"
        )
    return ValidationResult("error", f"{vendor} answered HTTP {code}")


def _get(url: str, headers: Dict[str, str], vendor: str) -> ValidationResult:
    response = requests.get(url, headers=headers, timeout=HTTP_TIMEOUT_S)
    return _verdict_from_status_code(response.status_code, vendor)


def _post(
    url: str, headers: Dict[str, str], payload: dict, vendor: str
) -> ValidationResult:
    response = requests.post(
        url, headers=headers, json=payload, timeout=HTTP_TIMEOUT_S
    )
    return _verdict_from_status_code(response.status_code, vendor)


# -- HTTP validators ---------------------------------------------------------


def _validate_anthropic(values: Dict[str, str]) -> ValidationResult:
    return _get(
        "https://api.anthropic.com/v1/models",
        {
            "x-api-key": values["ANTHROPIC_API_KEY"],
            "anthropic-version": "2023-06-01",
        },
        "Anthropic",
    )


def _validate_unusual_whales(values: Dict[str, str]) -> ValidationResult:
    return _get(
        "https://api.unusualwhales.com/api/market/economic-calendar",
        {"Authorization": f"Bearer {values['UW_TOKEN']}"},
        "Unusual Whales",
    )


def _validate_cerebras(values: Dict[str, str]) -> ValidationResult:
    return _get(
        "https://api.cerebras.ai/v1/models",
        {"Authorization": f"Bearer {values['CEREBRAS_API_KEY']}"},
        "Cerebras",
    )


def _validate_xai(values: Dict[str, str]) -> ValidationResult:
    return _get(
        "https://api.x.ai/v1/models",
        {"Authorization": f"Bearer {values['XAI_API_KEY']}"},
        "xAI",
    )


def _validate_exa(values: Dict[str, str]) -> ValidationResult:
    return _post(
        "https://api.exa.ai/search",
        {"x-api-key": values["EXA_API_KEY"]},
        {"query": "ping", "numResults": 1},
        "Exa",
    )


def _validate_clerk(values: Dict[str, str]) -> ValidationResult:
    return _get(
        "https://api.clerk.com/v1/users?limit=1",
        {"Authorization": f"Bearer {values['CLERK_SECRET_KEY']}"},
        "Clerk",
    )


def _validate_equibles(values: Dict[str, str]) -> ValidationResult:
    return _get(
        "https://api.equibles.com/v1/stocks/profile?ticker=AAPL",
        {"Authorization": f"Bearer {values['EQUIBLES_API_KEY']}"},
        "Equibles",
    )


def _validate_artificial_analysis(values: Dict[str, str]) -> ValidationResult:
    return _get(
        "https://artificialanalysis.ai/api/v2/data/llms/models",
        {"x-api-key": values["ARTIFICIAL_ANALYSIS_API_KEY"]},
        "Artificial Analysis",
    )


def _validate_pushover(values: Dict[str, str]) -> ValidationResult:
    response = requests.post(
        "https://api.pushover.net/1/users/validate.json",
        headers={},
        json={"token": values["PUSHOVER_TOKEN"], "user": values["PUSHOVER_USER"]},
        timeout=HTTP_TIMEOUT_S,
    )
    if response.status_code == 200:
        return ValidationResult("valid")
    if 400 <= response.status_code < 500:
        return ValidationResult(
            "invalid", f"Pushover rejected the pair (HTTP {response.status_code})"
        )
    return ValidationResult("error", f"Pushover answered HTTP {response.status_code}")


def _validate_turso(values: Dict[str, str]) -> ValidationResult:
    raw_url = values["TURSO_DB_URL"].strip()
    if raw_url.startswith("http://"):
        return ValidationResult("invalid", "Turso URL must use HTTPS")
    url = raw_url
    if url.startswith("libsql://"):
        url = "https://" + url[len("libsql://") :]
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.netloc:
        return ValidationResult("invalid", "Turso URL must be a libsql:// or https:// URL")
    configured = os.environ.get("TURSO_DB_URL", "").strip()
    if configured:
        cfg = configured
        if cfg.startswith("libsql://"):
            cfg = "https://" + cfg[len("libsql://") :]
        expected_host = urlparse(cfg).netloc
        if expected_host and parsed.netloc != expected_host:
            return ValidationResult(
                "invalid",
                "Turso URL host does not match the configured database host",
            )
    return _post(
        url.rstrip("/") + "/v2/pipeline",
        {"Authorization": f"Bearer {values['TURSO_AUTH_TOKEN']}"},
        {
            "requests": [
                {"type": "execute", "stmt": {"sql": "SELECT 1"}},
                {"type": "close"},
            ]
        },
        "Turso",
    )


# -- Slow browser-login validators -------------------------------------------


def _run_login_subprocess(
    service_id: str, env_overlay: Dict[str, str]
) -> ValidationResult:
    env = dict(os.environ)
    env.update(env_overlay)
    proc = subprocess.run(
        [sys.executable, str(_VALIDATE_LOGIN), service_id],
        env=env,
        capture_output=True,
        text=True,
        timeout=SLOW_LOGIN_TIMEOUT_S,
    )
    if proc.returncode != 0:
        detail = scrub_validation_message(
            {"message": (proc.stderr or proc.stdout or "").strip()[-200:]}
        )["message"]
        return ValidationResult(
            "error", f"login check for {service_id} failed to run: {detail}"
        )
    verdict = json.loads(proc.stdout.strip())
    status = verdict.get("status")
    if status not in _STATUSES:
        raise ValueError(f"login check returned unknown status {status!r}")
    return ValidationResult(
        status,
        scrub_validation_message({"message": str(verdict.get("message", ""))})["message"],
    )


def _validate_menthorq(values: Dict[str, str]) -> ValidationResult:
    return _run_login_subprocess(
        "menthorq",
        {
            "MENTHORQ_USER": values["MENTHORQ_USER"],
            "MENTHORQ_PASS": values["MENTHORQ_PASS"],
        },
    )


def _validate_themarketear(values: Dict[str, str]) -> ValidationResult:
    return _run_login_subprocess(
        "themarketear",
        {
            "THEMARKETEAR_EMAIL": values["THEMARKETEAR_EMAIL"],
            "THEMARKETEAR_PASSWORD": values["THEMARKETEAR_PASSWORD"],
        },
    )


VALIDATORS: Dict[str, Callable[[Dict[str, str]], ValidationResult]] = {
    "anthropic": _validate_anthropic,
    "unusual_whales": _validate_unusual_whales,
    "cerebras": _validate_cerebras,
    "xai": _validate_xai,
    "exa": _validate_exa,
    "clerk": _validate_clerk,
    "equibles": _validate_equibles,
    "artificial_analysis": _validate_artificial_analysis,
    "pushover": _validate_pushover,
    "turso": _validate_turso,
    "menthorq": _validate_menthorq,
    "themarketear": _validate_themarketear,
}


def _missing_fields(service, values: Dict[str, str]) -> list:
    return [
        field.name
        for field in service.fields
        if field.required_for_validation
        and not str(values.get(field.name, "") or "").strip()
    ]


def validate(service_id: str, values: Dict[str, str]) -> ValidationResult:
    """Check the credential set for one service. Never raises."""
    service = credentials_registry.service_by_id(service_id)
    if service is None or service.validator is None:
        return ValidationResult("unchecked", "no live check for this service")
    missing = _missing_fields(service, values)
    if missing:
        return ValidationResult(
            "unchecked",
            "waiting for the rest of the set: " + ", ".join(sorted(missing)),
        )
    checker = VALIDATORS[service.validator]
    try:
        return checker(values)
    except requests.Timeout:
        return ValidationResult("error", f"{service.label} check timed out")
    except requests.RequestException as exc:
        return ValidationResult(
            "error", f"{service.label} check failed: {exc.__class__.__name__}"
        )
    except subprocess.TimeoutExpired:
        return ValidationResult("error", f"{service.label} login check timed out")
    except Exception as exc:  # noqa: BLE001 - verdict, never a 500
        logger.warning("validator %s crashed: %s", service_id, exc)
        return ValidationResult(
            "error", f"{service.label} check crashed: {exc.__class__.__name__}"
        )
