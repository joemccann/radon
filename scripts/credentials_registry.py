#!/usr/bin/env python3
"""Declared surface of UI-manageable credentials (profile Credentials tab).

One registry of services, each owning the env-var fields the application
reads. Like the preferences registry, it must be honest: a service either has
a real validator in ``credential_validators`` or a ``note`` explaining why it
cannot be checked live (Flex throttle embargo, 2FA push, sigv4 signing).

Field ``secret`` marks how the UI renders the input (password vs text) — every
field is stored encrypted in the secret store regardless.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple

GROUP_ORDER: Tuple[str, ...] = (
    "Market Data",
    "AI Providers",
    "Infrastructure",
    "Alerts & News",
)


@dataclass(frozen=True)
class CredentialField:
    name: str
    label: str
    secret: bool = True
    placeholder: str = ""
    # False = the live check does not need this field (e.g. Clerk publishable
    # key: only the secret key can be probed against the Backend API).
    required_for_validation: bool = True


@dataclass(frozen=True)
class CredentialService:
    id: str
    label: str
    group: str
    fields: Tuple[CredentialField, ...]
    validator: Optional[str] = None
    slow: bool = False
    note: str = ""


SERVICES: Tuple[CredentialService, ...] = (
    # -- Market Data ---------------------------------------------------------
    CredentialService(
        id="unusual_whales",
        label="Unusual Whales",
        group="Market Data",
        fields=(
            CredentialField("UW_TOKEN", "API token", placeholder="uw_..."),
        ),
        validator="unusual_whales",
    ),
    CredentialService(
        id="menthorq",
        label="MenthorQ",
        group="Market Data",
        fields=(
            CredentialField("MENTHORQ_USER", "Email / username", secret=False),
            CredentialField("MENTHORQ_PASS", "Password"),
        ),
        validator="menthorq",
        slow=True,
        note="Checked with a real browser login. Expect up to a minute.",
    ),
    CredentialService(
        id="mdw",
        label="MarketDataWorks",
        group="Market Data",
        fields=(CredentialField("MDW_API_KEY", "API key"),),
        note="No public probe endpoint; the key is verified on first use.",
    ),
    CredentialService(
        id="equibles",
        label="Equibles",
        group="Market Data",
        fields=(CredentialField("EQUIBLES_API_KEY", "API key"),),
        validator="equibles",
    ),
    CredentialService(
        id="ib_flex",
        label="IB Flex",
        group="Market Data",
        fields=(
            CredentialField("IB_FLEX_TOKEN", "Flex token"),
            CredentialField("IB_FLEX_QUERY_ID", "Blotter query id", secret=False),
            CredentialField(
                "IB_FLEX_NAV_QUERY_ID", "NAV query id", secret=False
            ),
        ),
        note=(
            "Never live-validated: a probe is a real Flex request and the "
            "token has already taken a 24h-168h throttle embargo once."
        ),
    ),
    # -- AI Providers --------------------------------------------------------
    CredentialService(
        id="anthropic",
        label="Anthropic",
        group="AI Providers",
        fields=(
            CredentialField("ANTHROPIC_API_KEY", "API key", placeholder="sk-ant-..."),
        ),
        validator="anthropic",
    ),
    CredentialService(
        id="cerebras",
        label="Cerebras",
        group="AI Providers",
        fields=(CredentialField("CEREBRAS_API_KEY", "API key", placeholder="csk-..."),),
        validator="cerebras",
    ),
    CredentialService(
        id="xai",
        label="xAI",
        group="AI Providers",
        fields=(CredentialField("XAI_API_KEY", "API key", placeholder="xai-..."),),
        validator="xai",
    ),
    CredentialService(
        id="exa",
        label="Exa",
        group="AI Providers",
        fields=(CredentialField("EXA_API_KEY", "API key"),),
        validator="exa",
    ),
    CredentialService(
        id="artificial_analysis",
        label="Artificial Analysis",
        group="AI Providers",
        fields=(CredentialField("ARTIFICIAL_ANALYSIS_API_KEY", "API key"),),
        validator="artificial_analysis",
    ),
    # -- Infrastructure ------------------------------------------------------
    CredentialService(
        id="turso",
        label="Turso",
        group="Infrastructure",
        fields=(
            CredentialField(
                "TURSO_DB_URL",
                "Database URL",
                secret=False,
                placeholder="libsql://name-org.region.turso.io",
            ),
            CredentialField("TURSO_AUTH_TOKEN", "Auth token"),
        ),
        validator="turso",
    ),
    CredentialService(
        id="clerk",
        label="Clerk",
        group="Infrastructure",
        fields=(
            CredentialField("CLERK_SECRET_KEY", "Secret key", placeholder="sk_live_..."),
            CredentialField(
                "NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY",
                "Publishable key",
                secret=False,
                placeholder="pk_live_...",
                required_for_validation=False,
            ),
        ),
        validator="clerk",
    ),
    CredentialService(
        id="ib_gateway",
        label="IB Gateway login",
        group="Infrastructure",
        fields=(
            CredentialField("TWS_USERID", "IBKR username", secret=False),
            CredentialField("TWS_PASSWORD", "IBKR password"),
        ),
        note=(
            "Never live-validated: a login probe fires a real IBKR Mobile "
            "2FA push. Saving here does NOT rotate the Gateway password on "
            "the broker host — that still comes from TWS_PASSWORD_FILE / "
            "docker secrets until wired separately."
        ),
    ),
    CredentialService(
        id="backblaze",
        label="Backblaze B2 archive",
        group="Infrastructure",
        fields=(
            CredentialField(
                "RADON_ARCHIVE_S3_ENDPOINT", "S3 endpoint", secret=False
            ),
            CredentialField("RADON_ARCHIVE_S3_BUCKET", "Bucket", secret=False),
            CredentialField(
                "RADON_ARCHIVE_S3_ACCESS_KEY_ID", "Access key id", secret=False
            ),
            CredentialField("RADON_ARCHIVE_S3_SECRET_ACCESS_KEY", "Secret key"),
            CredentialField("RADON_ARCHIVE_S3_REGION", "Region", secret=False),
        ),
        note=(
            "No live probe: S3 requests need sigv4 signing, which is not "
            "worth a new dependency. Verified by the archive service."
        ),
    ),
    # -- Alerts & News -------------------------------------------------------
    CredentialService(
        id="pushover",
        label="Pushover",
        group="Alerts & News",
        fields=(
            CredentialField("PUSHOVER_USER", "User key", secret=False),
            CredentialField("PUSHOVER_TOKEN", "App token"),
        ),
        validator="pushover",
    ),
    CredentialService(
        id="themarketear",
        label="TheMarketEar",
        group="Alerts & News",
        fields=(
            CredentialField("THEMARKETEAR_EMAIL", "Email", secret=False),
            CredentialField("THEMARKETEAR_PASSWORD", "Password"),
        ),
        validator="themarketear",
        slow=True,
        note="Checked with a real browser login. Expect up to a minute.",
    ),
)

_BY_ID: Dict[str, CredentialService] = {service.id: service for service in SERVICES}
_FIELDS_BY_NAME: Dict[str, CredentialField] = {
    field_def.name: field_def
    for service in SERVICES
    for field_def in service.fields
}


def service_by_id(service_id: str) -> Optional[CredentialService]:
    return _BY_ID.get(service_id)


def fields_by_name() -> Dict[str, CredentialField]:
    return dict(_FIELDS_BY_NAME)


def all_field_names() -> Tuple[str, ...]:
    return tuple(_FIELDS_BY_NAME)
