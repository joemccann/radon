"""Host-env isolation shared by every collection subtree under scripts/.

scripts/tests, scripts/api/tests and scripts/trade_blotter each have their own
conftest and do not load each other's; this is the one ancestor conftest
pytest applies to all of them (rootdir is the repo root). Fixtures live here
when covering one subtree and missing another re-creates the defect — T-317's
Turso strip covered scripts/tests only, so the api subtree still inherited
the host's credentials (T-368).
"""
import sys

import pytest

TURSO_CREDENTIAL_KEYS = ("TURSO_DB_URL", "TURSO_AUTH_TOKEN")
# host_role() reads RADON_HOST_ROLE from os.environ on EVERY call
# (scripts/api/services.py), so a split-role host exporting `app` flips
# every non-delta Gateway verdict. Scrubbed with the same strip. T-368.
HOST_ENV_KEYS = TURSO_CREDENTIAL_KEYS + ("RADON_HOST_ROLE",)


@pytest.fixture(autouse=True)
def _strip_turso_credentials(monkeypatch):
    """Keep every test's verdict independent of the host's env files.

    50 `scripts/**/*.py` producers (`cash_flow_sync.py`, `fetch_*.py`,
    `scanner.py`, `api/server.py`, ...) call `load_dotenv(web/.env)` at
    IMPORT, so collecting any of them copies the host's Turso credentials
    into `os.environ`. With credentials present, `flex_embargo` treats the
    durable store as configured, `db.hrana_http` refuses the connection under
    pytest, and `active_until` fails CLOSED: 22 CI-green tests then red with
    `FlexTokenLocked` on any host that has a `web/.env` (the weekend runners
    provision one). Tests about the durable store set the keys themselves.

    Both halves matter: the delenv covers producers imported at collection,
    the `load_dotenv` wrapper covers a producer first imported INSIDE a test
    body, which would otherwise re-populate the keys after setup. T-317.
    Hoisted from scripts/tests/conftest.py so scripts/api/tests (which loads
    `api/server.py`, itself a web/.env loader) gets the same strip. T-368.
    """
    def strip():
        for key in HOST_ENV_KEYS:
            monkeypatch.delenv(key, raising=False)

    strip()
    try:
        import dotenv
    except Exception:
        return
    real_load_dotenv = dotenv.load_dotenv

    def load_dotenv_then_strip(*args, **kwargs):
        loaded = real_load_dotenv(*args, **kwargs)
        strip()
        return loaded

    monkeypatch.setattr(dotenv, "load_dotenv", load_dotenv_then_strip)


ROBINHOOD_ENV_KEYS = (
    "ROBINHOOD_MCP_TOKEN",
    "ROBINHOOD_MCP_REFRESH_TOKEN",
    "ROBINHOOD_MCP_CLIENT_ID",
    "ROBINHOOD_MCP_URL",
)


@pytest.fixture(autouse=True)
def _isolate_robinhood_credentials(tmp_path, monkeypatch):
    """Every test starts with Robinhood unconfigured, regardless of host.

    `robinhood_configured()` reads the env keys AND the token store, whose
    path defaults to the checkout's `data/rh_mcp_token.json` — so on the
    operator host every "unconfigured means no network" verdict silently
    inverted: the default rung found real credentials and made real MCP
    calls (7 s timeout each). Pointing the token file at tmp_path and
    clearing the env makes unconfigured the deterministic default; tests
    about configured behavior set their own credentials. T-356.
    """
    for key in ROBINHOOD_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("ROBINHOOD_MCP_TOKEN_FILE", str(tmp_path / "rh-mcp-token.json"))
    # Reset process-wide client state (refresh kill switch, breaker, memoed
    # configured verdict, shared client) when the module is already loaded
    # under either import spelling; a fresh import starts clean anyway.
    for name in ("clients.robinhood_client", "scripts.clients.robinhood_client"):
        rh = sys.modules.get(name)
        if rh is None:
            continue
        monkeypatch.setattr(rh, "_refresh_disabled", False, raising=False)
        reset = getattr(rh, "_reset_process_state", None)
        if reset is not None:
            reset()
