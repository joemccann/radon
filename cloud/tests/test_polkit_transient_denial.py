"""Polkit start grants must not authorize StartTransientUnit or an uninstalled unit."""
from __future__ import annotations

import re
from pathlib import Path

RULES = (
    Path(__file__).resolve().parents[2]
    / "cloud"
    / "config"
    / "polkit"
    / "50-radon-services.rules"
)
SERVICES = Path(__file__).resolve().parents[2] / "cloud" / "services"


def _text() -> str:
    return RULES.read_text(encoding="utf-8")


def _granted(text: str) -> set[str]:
    return set(re.findall(r'"([^"]+\.service)": true', text))


def test_uninstalled_llm_index_is_not_granted() -> None:
    text = _text()
    granted = _granted(text)
    assert "radon-llm-index.service" not in granted
    assert (SERVICES / "radon-llm-index.service").is_file()
    for unit in granted:
        assert (SERVICES / unit).is_file(), unit


def test_transient_start_is_denied_before_either_yes() -> None:
    text = _text()
    helper, _, _rest = text.partition("polkit.addRule")
    assert "function radonTransientStart" in helper
    assert 'indexOf("transient")' in helper
    assert 'message === ""' in helper
    assert text.count("radonTransientStart(action)") >= 2
    for chunk in text.split("polkit.addRule")[1:]:
        assert chunk.index("radonTransientStart(action)") < chunk.index(
            "return polkit.Result.YES"
        )


def _transient(verb: str | None, message: str | None) -> bool:
    """Mirror of radonTransientStart. The source test above pins the JS."""
    if verb not in {"start", "restart"}:
        return False
    text = message or ""
    return text == "" or "transient" in text


def test_decision_model_denies_transient_and_allows_file_start() -> None:
    granted = _granted(_text())
    assert _transient("start", "Authentication is required to start transient '$(unit)'.")
    assert _transient("start", "")
    assert not _transient(
        "start", "Authentication is required to start '$(unit)'."
    )
    assert not _transient(
        "reset-failed", "Authentication is required to reset-failed '$(unit)'."
    )
    sample = next(iter(granted))
    assert sample
    assert not _transient("start", "Authentication is required to start '$(unit)'.")
    assert "radon-llm-index.service" not in granted
