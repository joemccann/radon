"""Share cards must land in the bind-mounted data/ tree.

The app-plane Python image keeps the checkout root-owned; only data/ (bind
mount, shared with the Next.js container) and logs/ are writable by the
runtime user. Writing under reports/ raised PermissionError in production
(POST /regime/share -> 502 "Share generation failed").
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
PROJECT_ROOT = SCRIPTS.parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

EXPECTED_DIR = PROJECT_ROOT / "data" / "share_cards"


def test_share_cards_dir_is_under_bind_mounted_data():
    from utils.share_cards import SHARE_CARDS_DIR

    assert SHARE_CARDS_DIR == EXPECTED_DIR


@pytest.mark.parametrize(
    "module_name",
    ["generate_regime_share", "generate_gex_share", "generate_vcg_share", "generate_cta_share"],
)
def test_every_generator_writes_share_cards_dir(module_name):
    module = importlib.import_module(module_name)

    assert module.REPORTS_DIR == EXPECTED_DIR


def test_ensure_creates_missing_parents(tmp_path, monkeypatch):
    from utils import share_cards

    target = tmp_path / "data" / "share_cards"
    monkeypatch.setattr(share_cards, "SHARE_CARDS_DIR", target)

    assert share_cards.ensure_share_cards_dir() == target
    assert target.is_dir()


def test_api_share_content_reads_share_cards_dir(tmp_path, monkeypatch):
    import asyncio

    from api import server

    monkeypatch.setattr(server, "SHARE_CARDS_DIR", tmp_path)
    (tmp_path / "tweet-gex-2026-11-17.html").write_text("<p>card</p>", encoding="utf-8")

    response = asyncio.run(server.share_content(type="gex", name="tweet-gex-2026-11-17.html"))

    assert response.body == b"<p>card</p>"


def test_api_share_content_refuses_a_link_planted_under_a_card_name(tmp_path, monkeypatch):
    import asyncio

    from fastapi import HTTPException

    from api import server

    monkeypatch.setattr(server, "SHARE_CARDS_DIR", tmp_path)
    outside = tmp_path / "outside.txt"
    outside.write_text("not a card", encoding="utf-8")
    (tmp_path / "tweet-gex-2026-11-19.html").symlink_to(outside)
    (tmp_path / "tweet-gex-2026-11-18.html").mkdir()

    for name in ("tweet-gex-2026-11-19.html", "tweet-gex-2026-11-18.html"):
        with pytest.raises(HTTPException) as excinfo:
            asyncio.run(server.share_content(type="gex", name=name))
        assert excinfo.value.status_code == 404


def test_api_share_content_does_not_block_on_a_fifo_planted_under_a_card_name(tmp_path, monkeypatch):
    import asyncio
    import os

    from fastapi import HTTPException

    from api import server

    monkeypatch.setattr(server, "SHARE_CARDS_DIR", tmp_path)
    os.mkfifo(tmp_path / "tweet-gex-2026-11-19.html")

    async def bounded():
        return await asyncio.wait_for(
            asyncio.to_thread(asyncio.run, server.share_content(type="gex", name="tweet-gex-2026-11-19.html")),
            timeout=5,
        )

    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(bounded())
    assert excinfo.value.status_code == 404
