"""Bounded, explicit subscriptions for the personal Advanced feed."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

DEFAULT_DIRECTORY = Path(__file__).resolve().parents[2] / "data" / "uw_stream"
BASE_CHANNELS = ("flow-alerts", "off_lit_trades", "market_tide", "interval_flow")
GLOBAL_OPTIONAL = frozenset({"news", "risk_reversal_skew", "sec:filings", "sec:insider_trades", "sec:13f_alerts", "sec:13f_filings", "sec:13dg_filings", "sec:company_reports", "sec:497_filings"})
SCOPED = frozenset({"net_flow", "gex", "gex_strike", "gex_strike_expiry", "iv_term_structure", "interpolated_iv", "greek_flow", "greek_flow_expiry", "periscope", "futures_blocks"})
TICKER = re.compile(r"^[A-Z][A-Z0-9.\-]{0,15}$")


def validate_channels(channels: list[str] | tuple[str, ...]) -> tuple[str, ...]:
    result = []
    for value in channels:
        channel = value.strip()
        if channel in BASE_CHANNELS or channel in GLOBAL_OPTIONAL:
            pass
        else:
            family, separator, ticker = channel.partition(":")
            valid_symbol = bool(TICKER.fullmatch(ticker)) or (family == "futures_blocks" and bool(re.fullmatch(r"[A-Z0-9][A-Z0-9.:\-]{0,31}", ticker)))
            if not separator or family not in SCOPED or not valid_symbol:
                raise ValueError("Unsupported or unscoped research channel")
            if family == "periscope" and ticker not in {"SPX", "VIX", "XSP", "NANOS"}:
                raise ValueError("Periscope requires a supported index")
        if channel not in result:
            result.append(channel)
    if not result or len(result) > 64:
        raise ValueError("Research manifest must contain 1 to 64 channels")
    return tuple(result)


@dataclass(frozen=True)
class Config:
    enabled: bool = False
    channels: tuple[str, ...] = BASE_CHANNELS
    directory: Path = DEFAULT_DIRECTORY
    queue_size: int = 1024
    flush_seconds: float = 1.0
    max_frame_bytes: int = 512 * 1024
    max_raw_rows: int = 100_000
    retention_seconds: int = 86_400
    max_disk_bytes: int = 1024 * 1024 * 1024
    min_free_bytes: int = 512 * 1024 * 1024

    @classmethod
    def from_env(cls) -> "Config":
        # Tuning intentionally bounded in code. The operator only selects
        # activation and the explicit manifest, not unbounded queues/tapes.
        manifest = os.environ.get("RADON_UW_STREAM_CHANNELS", ",".join(BASE_CHANNELS))
        return cls(
            enabled=os.environ.get("RADON_UW_STREAM_ENABLED") == "1",
            channels=validate_channels(manifest.split(",")),
        )


def hydrate_settings() -> list[str]:
    """Profile's UW service wins over env; a configured store fails closed.

    Only this provider's registered names are exported. No unrelated provider
    credential is read or inherited merely to start a research collector.
    """
    if not os.environ.get("RADON_SECRET_STORE_PATH"):
        return []
    from credentials_registry import service_by_id
    from secret_store import SecretStore
    service = service_by_id("unusual_whales")
    if service is None:
        raise ValueError("UW credential registry unavailable")
    store = SecretStore()
    exported = []
    for field in service.fields:
        value = store.get_secret(field.name)
        if value is not None:
            if not isinstance(value, str) or any(c in value for c in ("\x00", "\r", "\n")):
                raise ValueError("UW setting is not a valid environment value")
            os.environ[field.name] = value
            exported.append(field.name)
    return exported
