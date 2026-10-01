"""Cerebras is the last-resort rung of every ladder: nothing is ever tried after it."""
from __future__ import annotations

import json
import shlex
from pathlib import Path

import pytest

from clients.model_ladder import MODEL_LADDER_ORDER, ModelLadderExhausted, complete_text_json
from clients.vision_cascade import VISION_CASCADE_ORDER
from newsfeed.slm.modes import SLM_PREFER_ORDER, SLM_PRIMARY_ORDER

pytestmark = pytest.mark.usefixtures("isolated_model_credentials")

LOOPS = Path(__file__).resolve().parents[1] / "runner" / "loops"


def _loop_agents(path: Path) -> list[str]:
    for line in path.read_text().splitlines():
        if line.startswith("AGENTS="):
            return shlex.split(line.split("=", 1)[1])[0].split()
    return []


def _assert_cerebras_last(order, rung="cerebras"):
    order = list(order)
    assert rung in order
    assert order.index(rung) == len(order) - 1, order


@pytest.mark.parametrize(
    "order",
    [MODEL_LADDER_ORDER, VISION_CASCADE_ORDER, SLM_PREFER_ORDER, SLM_PRIMARY_ORDER],
    ids=["model_ladder", "vision_cascade", "slm_prefer", "slm_primary"],
)
def test_python_ladders_end_in_cerebras(order):
    _assert_cerebras_last(order)
    assert order.index("nvidia") < order.index("cerebras")


def test_nightly_loop_ladders_end_in_fx_cerebras():
    loops = sorted(LOOPS.glob("*.env"))
    with_cerebras = [p for p in loops if any("cerebras" in r for r in _loop_agents(p))]
    assert with_cerebras, "expected at least one loop with a cerebras rung"
    for path in with_cerebras:
        agents = _loop_agents(path)
        _assert_cerebras_last(agents, "fx:cerebras")
        assert agents.index("fx:nvidia") < agents.index("fx:cerebras"), path.name


def test_caller_order_cannot_put_a_rung_after_cerebras(monkeypatch, tmp_path):
    """A providers= override is still walked with cerebras moved to the end."""
    monkeypatch.setenv("HOME", str(tmp_path))
    urls: list[str] = []

    def post(url, **_kwargs):
        urls.append(url)

        class _R:
            status_code = 503
            text = json.dumps({"error": "down"})

            def json(self):
                return {"error": "down"}

        return _R()

    env = {"NVIDIA_API_KEY": "nvapi-test", "CEREBRAS_API_KEY": "csk-test"}
    with pytest.raises(ModelLadderExhausted) as exc:
        complete_text_json("x", env=env, post=post, providers=["cerebras", "nvidia"])
    assert "cerebras" in urls[-1], urls
    assert all("cerebras" not in u for u in urls[:-1]), urls
    assert "nvidia" in str(exc.value) and "cerebras" in str(exc.value)
