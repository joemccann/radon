"""The last-rung ladder key must not gate the deploy preflight.

2026-09-18: `CEREBRAS_API_KEY` was removed from `/etc/radon/env` at 03:01 UTC
after the prepaid Cerebras wallet failed `quota_or_billing` on every call and
PR #510 stopped the ladder from routing through prepaid sub-rung wallets.
`cloud/config/required-env.txt` still listed the key, so every main deploy
from 283bee39 on died in `check-env.py` with `missing required vars:
CEREBRAS_API_KEY` before staging a release. `docs/operations.md` has called
the key "optional, last-rung model ladder" all along, and
`model_ladder._env_get` returns "" and skips the rung when it is unset.
"""
from pathlib import Path

import pytest

CLOUD = Path(__file__).resolve().parents[1]
CONTRACT = CLOUD / "config" / "required-env.txt"


def _contract_keys() -> set[str]:
    return {
        line.strip()
        for line in CONTRACT.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }


def test_the_cerebras_key_is_not_a_deploy_preflight_requirement():
    assert "CEREBRAS_API_KEY" not in _contract_keys()


def test_the_cerebras_key_is_an_exempt_optional_ladder_key():
    from tests.test_env_contract_parity import EXEMPT

    assert "CEREBRAS_API_KEY" in EXEMPT
    assert EXEMPT["CEREBRAS_API_KEY"].strip()


def test_the_ladder_skips_the_rung_without_the_key(monkeypatch):
    import sys

    sys.path.insert(0, str(CLOUD.parent / "scripts"))
    try:
        from clients import model_ladder
    finally:
        sys.path.pop(0)
    assert model_ladder._env_get({}, *model_ladder._CEREBRAS_KEYS) == ""
