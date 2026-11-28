"""Optional Cloudflare Workers AI report date for research.identify.

CLAIRIC_DATES_ENABLED and CLAIRIC_DATE_SANITY_ENABLED both default off.
Off, identify() keeps today's date ladder and writes no date_flag.
The HTTP call uses the stdlib (no new dependency). Credentials are
CLOUDFLARE_ACCOUNT_ID and CLOUDFLARE_API_TOKEN from the process environment
(/etc/radon/env on the research host). A missing credential, timeout, or
API error logs one line and leaves the ladder date unchanged.

The date grammar lives in ``clairic_rules``. This module is the native
Cloudflare transport.
"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.request

from .clairic_rules import (
    MAX_RESPONSE_BYTES,
    TIMEOUT_S,
    _enabled,
    apply_date,
    build_request,
    today_et,
)
from .contracts import ClairicError, MissingCredentials

API = "https://api.cloudflare.com/client/v4"
MODEL_PATH = "/ai/run/@cf/cloudflare/clairic"
_ACCOUNT_RE = re.compile(r"[0-9a-fA-F]{32}")


def dates_enabled(env=None):
    import os
    return _enabled("CLAIRIC_DATES_ENABLED", os.environ if env is None else env)


def sanity_enabled(env=None):
    import os
    return _enabled("CLAIRIC_DATE_SANITY_ENABLED", os.environ if env is None else env)


def _credentials(env):
    account = (env.get("CLOUDFLARE_ACCOUNT_ID") or "").strip()
    token = (env.get("CLOUDFLARE_API_TOKEN") or "").strip()
    if not _ACCOUNT_RE.fullmatch(account) or not token or any(c in token for c in "\r\n"):
        raise MissingCredentials()
    return account, token


def _endpoint(account):
    return f"{API}/accounts/{account}{MODEL_PATH}"


def fetch_report_date(pages, folder_date, env=None, opener=None):
    import os
    env = os.environ if env is None else env
    body = build_request(pages, folder_date)
    return fetch_request(body, env=env, opener=opener)


def fetch_request(body, env=None, opener=None):
    import os
    env = os.environ if env is None else env
    account, token = _credentials(env)
    if not body["state"].strip():
        raise ClairicError("empty_state")
    raw = json.dumps(body).encode()
    request = urllib.request.Request(
        _endpoint(account),
        data=raw,
        method="POST",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    open_url = opener or urllib.request.urlopen
    from .engine import scoped_timeout
    try:
        with open_url(request, timeout=scoped_timeout(TIMEOUT_S)) as response:
            payload = response.read(MAX_RESPONSE_BYTES + 1)
            scoped_timeout(TIMEOUT_S)
    except TimeoutError as exc:
        raise ClairicError("timeout") from exc
    except urllib.error.HTTPError as exc:
        raise ClairicError(f"http_{exc.code}") from exc
    except urllib.error.URLError as exc:
        reason = "timeout" if isinstance(getattr(exc, "reason", None), TimeoutError) else "transport"
        raise ClairicError(reason) from exc
    except OSError as exc:
        raise ClairicError("transport") from exc
    if len(payload) > MAX_RESPONSE_BYTES:
        raise ClairicError("bad_response")
    try:
        parsed = json.loads(payload.decode())
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ClairicError("bad_response") from exc
    return _report_date(parsed)


def _report_date(payload):
    if not isinstance(payload, dict):
        raise ClairicError("bad_response")
    if payload.get("success") is False:
        raise ClairicError("api_error")
    result = payload.get("result", payload)
    if not isinstance(result, dict):
        raise ClairicError("bad_response")
    answers = result.get("answers")
    if not isinstance(answers, dict):
        raise ClairicError("bad_response")
    report = answers.get("report_date") or {}
    if not isinstance(report, dict):
        raise ClairicError("bad_response")
    return report.get("value"), report.get("confidence")


def apply(found, page_text, folder_date, env=None):
    """Return (date tuple, date_flag or None). Flags off returns `found` unchanged and logs nothing."""
    return apply_date(
        found,
        page_text,
        folder_date,
        env,
        fetch=fetch_report_date,
        today=today_et,
    )
