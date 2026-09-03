"""x509 cert expiry inspection — stdlib first, openssl subprocess fallback.

REL-178 (R-496): the ib-gateway-remote mTLS certs (825-day self-signed) had
no expiry surfaced anywhere; on expiry day every admin Gateway control 503s
with CERTIFICATE_VERIFY_FAILED. Consumers: the broker daemon's /healthz, the
app /health remote section, and the ib_watchdog classifier.
"""
from __future__ import annotations

import ssl
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

_NOT_AFTER_FMT = "%b %d %H:%M:%S %Y %Z"


def cert_not_after(path: Path | str) -> Optional[str]:
    """The cert's notAfter as an openssl-style string, or None."""
    path = Path(path)
    if not path.is_file():
        return None
    try:
        # Private but long-stable CPython helper; decodes a PEM cert file.
        info = ssl._ssl._test_decode_cert(str(path))  # type: ignore[attr-defined]
        value = info.get("notAfter")
        if isinstance(value, str) and value:
            return value
    except Exception:  # noqa: BLE001 — fall through to openssl
        pass
    try:
        out = subprocess.run(
            ["openssl", "x509", "-enddate", "-noout", "-in", str(path)],
            capture_output=True, text=True, timeout=10,
        )
        if out.returncode == 0 and out.stdout.startswith("notAfter="):
            return out.stdout.split("=", 1)[1].strip()
    except Exception:  # noqa: BLE001 — no openssl either
        pass
    return None


def cert_days_left(path: Path | str, now: Optional[datetime] = None) -> Optional[float]:
    """Days until the cert at ``path`` expires (negative = expired), or None."""
    raw = cert_not_after(path)
    if not raw:
        return None
    try:
        expires = datetime.strptime(raw, _NOT_AFTER_FMT).replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    delta = expires - (now or datetime.now(timezone.utc))
    return delta.total_seconds() / 86400.0
