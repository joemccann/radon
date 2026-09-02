"""Entrypoint for radon-mcp.service: python -m scripts.mcp_hosted.serve"""
from __future__ import annotations

from scripts.mcp_hosted.server import mcp

if __name__ == "__main__":
    mcp.run(transport="streamable-http")
