"""Artificial Analysis language-model catalog pages.

The legacy ``/api/v2/data/*`` paths returned HTTP 410 on 2026-11-04.
``/api/v2/language/models/free`` accepts any valid key and returns the
fields the frontier basket needs, 200 rows per page.
"""

from __future__ import annotations

from typing import Any

LANGUAGE_MODELS_URL = "https://artificialanalysis.ai/api/v2/language/models/free"
MAX_CATALOG_PAGES = 25


class CatalogPageError(ValueError):
    """This page cannot be combined into a complete catalog."""


def next_catalog_page(payload: Any, *, requested_page: int) -> int | None:
    """Return the next 1-indexed page, or None when this page completes the list.

    A missing ``pagination`` object is a single complete page, which is the
    shape unit fixtures and the pre-2026-11 catalog used. A present object
    must name this request's page. ``has_more`` true past the page bound
    fails closed instead of returning a short catalog.
    """
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        raise CatalogPageError("Artificial Analysis catalog schema is invalid")
    if "status" in payload and payload.get("status") != 200:
        raise CatalogPageError("Artificial Analysis catalog schema is invalid")
    if "pagination" not in payload or payload.get("pagination") is None:
        if requested_page != 1:
            raise CatalogPageError("Artificial Analysis catalog pagination is invalid")
        return None
    pagination = payload.get("pagination")
    if not isinstance(pagination, dict):
        raise CatalogPageError("Artificial Analysis catalog pagination is invalid")
    current = pagination.get("page")
    if isinstance(current, bool) or not isinstance(current, int) or current != requested_page:
        raise CatalogPageError("Artificial Analysis catalog pagination is invalid")
    has_more = pagination.get("has_more")
    if has_more is False:
        return None
    if has_more is not True:
        raise CatalogPageError("Artificial Analysis catalog pagination is invalid")
    nxt = current + 1
    if nxt > MAX_CATALOG_PAGES:
        raise CatalogPageError("Artificial Analysis catalog pagination exceeded the page bound")
    return nxt
