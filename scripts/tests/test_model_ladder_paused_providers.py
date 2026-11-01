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
    for env in (KEYS, {**KEYS, "RADON_LADDER_PAUSED_PROVIDERS": ""}):
        assert _auth_for("cerebras", env).token == "csk-test"


def test_nvidia_text_default_is_an_invocable_catalog_model():
    # nemotron-3-super-120b-a12b is listed but 404s "Function not found for
    # account" (2026-11-01); Super VL answered the same probe.
    assert _model_for("nvidia", {}, kind="text") == "nvidia/nemotron-3.5-super-vl-120b-a12b"
