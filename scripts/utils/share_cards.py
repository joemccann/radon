"""Where generated X share cards live.

data/ is the only checkout path the app-plane containers can write, and it is
bind-mounted into both radon-api (which runs the generators) and radon-nextjs
(which serves the preview). The rest of the checkout is root-owned in the image.
"""
from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SHARE_CARDS_DIR = PROJECT_ROOT / "data" / "share_cards"


def ensure_share_cards_dir() -> Path:
    SHARE_CARDS_DIR.mkdir(parents=True, exist_ok=True)
    return SHARE_CARDS_DIR
