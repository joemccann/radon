"""The Python app image and the pinned requirements must carry a trust store
that no longer honours the eMudhra/emSign intermediates whose private keys
were recovered in October 2026.

pip ships its own vendored certifi, so an OS ca-certificates update does not
reach `pip install`; pip 26.2.2 is the first release vendoring certifi
2026.10.19. The app's own HTTP clients (requests, httpx) read the standalone
certifi package, which requirements.txt previously left unpinned.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DOCKERFILE = REPO / "docker" / "app" / "Dockerfile.python"
REQUIREMENTS = REPO / "requirements.txt"

MIN_PIP = (26, 2, 2)
MIN_CERTIFI = (2026, 10, 19)


def _version(spec: str) -> tuple[int, ...]:
    return tuple(int(part) for part in spec.split("."))


def test_python_image_upgrades_pip_before_installing_requirements() -> None:
    text = DOCKERFILE.read_text(encoding="utf-8")
    match = re.search(r'pip install[^\n\\]*--upgrade[^\n]*?"pip>=([0-9.]+)"', text)
    assert match, "Dockerfile.python must upgrade pip itself (its vendored certifi) before installing requirements"
    assert _version(match.group(1)) >= MIN_PIP
    upgrade_at = match.start()
    requirements_at = text.index("-r requirements.txt")
    assert upgrade_at < requirements_at, "pip must be upgraded before requirements are installed"


def test_requirements_pin_certifi_at_or_after_emudhra_fix() -> None:
    match = re.search(r"^certifi==([0-9.]+)$", REQUIREMENTS.read_text(encoding="utf-8"), re.M)
    assert match, "requirements.txt must pin certifi so the image and the host venv agree on the trust store"
    assert _version(match.group(1)) >= MIN_CERTIFI
