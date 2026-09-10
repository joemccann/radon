"""T-368: host env must not decide api-subtree test verdicts.

`scripts/api/server.py` calls `load_dotenv(web/.env)` at IMPORT, so collecting
any api test copies the host's Turso credentials into `os.environ` — under
pytest `flex_embargo.active_until` then fails CLOSED (`FlexTokenLocked`).
`RADON_HOST_ROLE` is read from `os.environ` on every `services.host_role()`
call, so a host exporting `RADON_HOST_ROLE=app` (the split-role fleet) flips
every non-delta Gateway verdict. Both are scrubbed by the shared
`scripts/conftest.py` autouse; these tests pin that the strip reaches THIS
collection subtree, which has its own conftest and never had one.
"""
from __future__ import annotations

import os

import pytest

from scripts.api import server  # noqa: F401 - triggers load_dotenv(web/.env)
from scripts.api import services


@pytest.fixture(scope="module", autouse=True)
def _host_exports_app_role():
    """Simulate the split-role fleet: RADON_HOST_ROLE=app exported before
    pytest starts. Module-scoped, so it is in place BEFORE the per-test
    autouse strip runs — the strip must be what removes it."""
    already = os.environ.get("RADON_HOST_ROLE")
    os.environ["RADON_HOST_ROLE"] = "app"
    yield
    if already is None:
        os.environ.pop("RADON_HOST_ROLE", None)
    else:
        os.environ["RADON_HOST_ROLE"] = already


def test_turso_credentials_are_stripped_from_the_api_subtree():
    assert "TURSO_DB_URL" not in os.environ
    assert "TURSO_AUTH_TOKEN" not in os.environ


def test_host_role_is_combined_even_when_the_host_exports_app():
    assert os.environ.get("RADON_HOST_ROLE") is None
    assert services.host_role() == "combined"
