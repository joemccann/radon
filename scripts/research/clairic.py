"""Optional report-date inference through the private model gateway.

The date grammar lives in ``model_gateway.clairic_rules``. This module only
binds that grammar to the gateway transport. It does not call Cloudflare.
"""
from __future__ import annotations

from model_gateway.clairic_rules import (
    TIMEOUT_S,  # noqa: F401  re-exported; date tests pin the shared budget
    apply_date,
    build_request,
    today_et,
)
from model_gateway.client import GatewayError, request as gateway_request
from model_gateway.contracts import ClairicError, MissingCredentials


def fetch_report_date(pages, folder_date, env=None, opener=None):
    body = build_request(pages, folder_date)
    if not body["state"].strip():
        raise ClairicError("empty_state")
    try:
        reply = gateway_request("clairic", {"state": body["state"],
            "folder_date": body.get("reference_date"),
            "include_publisher": "publisher" in body["fields"]}, timeout_seconds=15)
    except GatewayError as exc:
        if exc.code == "missing_credentials":
            raise MissingCredentials() from exc
        raise ClairicError(exc.code) from exc
    return reply.get("date"), reply.get("confidence")


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
