"""Private PDF and chart bytes. Registered behind the default API JWT middleware."""
from __future__ import annotations

import asyncio
from fastapi import APIRouter, HTTPException, Response
from research.assets import read_asset

router = APIRouter()


@router.get("/newsfeed/research/files/{asset}")
async def research_file(asset: str):
    try:
        data = await asyncio.to_thread(read_asset, asset)
    except (ValueError, OSError):
        raise HTTPException(status_code=404, detail="Research asset unavailable") from None
    return Response(data, media_type="image/png" if asset.endswith(".png") else "application/pdf",
                    headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff",
                             "Content-Disposition": f'inline; filename="{asset}"',
                             "Content-Security-Policy": "sandbox"})
