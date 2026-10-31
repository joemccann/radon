"""Gateway-only current model-key overlay from the keyholding credential broker."""
from __future__ import annotations
import os
import threading
from subscription_vault import connect_vault
from . import engine
from .client import GatewayError
from .contracts import ModelResponseError

MODEL_KEY_FIELDS = {"nvidia": "NVIDIA_API_KEY", "cerebras": "CEREBRAS_API_KEY", "cloudflare-token": "CLOUDFLARE_API_TOKEN", "cloudflare-account": "CLOUDFLARE_ACCOUNT_ID"}
_LOCK = threading.Lock()
_FALLBACK = {}


def refresh_keys():
    """Store wins immediately; a broker outage never silently selects stale keys."""
    path = os.environ.get("RADON_SUBSCRIPTION_VAULT_SOCKET")
    if not path:
        return  # Isolated tests/development may explicitly provide service env only.
    while True:
        engine.scoped_timeout(0)
        if _LOCK.acquire(timeout=engine.scoped_timeout(.02)):
            break
    try:
        engine.scoped_timeout(0)
        for field in MODEL_KEY_FIELDS.values():
            if field not in _FALLBACK:
                _FALLBACK[field] = os.environ.get(field)
        try:
            broker = connect_vault(path, wait_seconds=0, timeout_seconds=lambda: engine.scoped_timeout(10))
            current = {field: broker.model_key(provider) for provider, field in MODEL_KEY_FIELDS.items()}
            if any(value is not None and (not isinstance(value, str) or len(value) > 4096) for value in current.values()):
                raise ValueError("invalid model credential")
            engine.scoped_timeout(0)
        except ModelResponseError:
            raise
        except Exception as exc:
            engine.scoped_timeout(0)
            raise GatewayError("credential_store_unavailable", "Model credential store is unavailable", 503) from exc
        for field, value in current.items():
            effective = value or _FALLBACK[field]
            if effective:
                os.environ[field] = effective
            else:
                os.environ.pop(field, None)
    finally:
        _LOCK.release()
