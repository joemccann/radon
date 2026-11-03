"""Operator pause switch and the live NVIDIA text default."""
from model_gateway.engine import _auth_for, _model_for

KEYS = {"NVIDIA_API_KEY": "nvapi-test", "CEREBRAS_API_KEY": "csk-test"}


def test_paused_provider_is_unkeyed_while_others_stay_wired():
    env = {**KEYS, "RADON_LADDER_PAUSED_PROVIDERS": "cerebras"}
    assert _auth_for("cerebras", env) is None
    assert _auth_for("nvidia", env).token == "nvapi-test"


def test_pause_list_tolerates_case_spaces_and_several_names():
    env = {**KEYS, "RADON_LADDER_PAUSED_PROVIDERS": " Cerebras , nvidia "}
    assert _auth_for("cerebras", env) is None
    assert _auth_for("nvidia", env) is None


def test_unset_or_empty_pause_list_changes_nothing():
    # conftest clears the shipped baseline so rung mechanics stay testable.
    for env in (KEYS, {**KEYS, "RADON_LADDER_PAUSED_PROVIDERS": ""}):
        assert _auth_for("cerebras", env).token == "csk-test"


def test_shipped_baseline_pauses_nvidia_and_cerebras_whatever_the_env_says(monkeypatch):
    """NVIDIA won 4 of ~1,550 requests in 3 days and its research figure match
    is unsafe (PR #1382); Cerebras is 402 account-wide. The host env still
    lists only cerebras, so the baseline must union with it, not be replaced."""
    from pathlib import Path
    import model_gateway.engine as engine
    assert 'PAUSED_BY_DEFAULT = frozenset({"cerebras", "nvidia"})' in Path(engine.__file__).read_text()
    monkeypatch.setattr(engine, "PAUSED_BY_DEFAULT", frozenset({"cerebras", "nvidia"}))
    for env in (KEYS, {**KEYS, "RADON_LADDER_PAUSED_PROVIDERS": "cerebras"},
                {**KEYS, "RADON_LADDER_PAUSED_PROVIDERS": ""}):
        assert engine._auth_for("nvidia", env) is None
        assert engine._auth_for("cerebras", env) is None
    assert "nvidia" not in engine.wired_providers(KEYS)


def test_nvidia_text_default_is_an_invocable_catalog_model():
    # nemotron-3-super-120b-a12b is listed but 404s "Function not found for
    # account" (2026-11-01); Ultra answered the same probe (PR #1379).
    assert _model_for("nvidia", {}, kind="text") == "nvidia/nemotron-3-ultra-550b-a55b"
