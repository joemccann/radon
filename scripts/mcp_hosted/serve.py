"""Entrypoint for radon-mcp.service: python -m scripts.mcp_hosted.serve"""
from __future__ import annotations

import uvicorn

from scripts.mcp_hosted.server import build_app, mcp

if __name__ == "__main__":
    # Mirrors FastMCP.run_streamable_http_async, but serves the bounded app.
    uvicorn.run(
        build_app(),
        host=mcp.settings.host,
        port=mcp.settings.port,
        log_level=mcp.settings.log_level.lower(),
    )
