"""Private PDF and chart bytes. Registered behind the default API JWT middleware."""
from __future__ import annotations

import asyncio
import json
from fastapi import APIRouter, HTTPException, Response, Query
from research.assets import read_asset
from research.manifest import search_manifest

router = APIRouter()


@router.get("/newsfeed/research/files/{asset}")
async def research_file(asset: str):
    try:
        data = await asyncio.to_thread(read_asset, asset)
    except (ValueError, OSError):
        raise HTTPException(status_code=404, detail="Research asset unavailable") from None
    return Response(data, media_type={"png": "image/png", "pdf": "application/pdf", "json": "application/json"}[asset.rsplit(".", 1)[1]],
                    headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff",
                             "Content-Disposition": f'inline; filename="{asset}"',
                             "Content-Security-Policy": "sandbox"})


@router.get("/newsfeed/research/evidence/{asset}")
async def research_evidence(asset: str, response: Response, query: str | None = Query(default=None, min_length=1, max_length=200)):
    """Authenticated machine-readable original pages, or bounded literal hits."""
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    if not asset.endswith('.json'):
        raise HTTPException(status_code=404, detail="Research evidence unavailable")
    try:
        manifest = json.loads(await asyncio.to_thread(read_asset, asset))
    except (ValueError, OSError):
        raise HTTPException(status_code=404, detail="Research evidence unavailable") from None
    if query is not None:
        try:
            return {"schema_version": 1, "source_sha256": manifest['source_sha256'],
                    "trust": manifest['trust'], "query": query,
                    "results": search_manifest(manifest, query),
                    "ocr_pages_excluded": [p['page_number'] for p in manifest['pages'] if p['needs_ocr']]}
        except ValueError:
            raise HTTPException(status_code=400, detail="Query must contain words or numbers") from None
    return manifest
