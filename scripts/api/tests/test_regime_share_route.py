"""POST /regime/share forwards the page's live CRI reading to the generator.

2026-11-19: the page showed CRI 51 from live ticks while the cards showed the
12:00 snapshot (48). The page now posts its reading; the route validates it,
hands it to generate_regime_share.py as --live-overlay, and removes the file.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

SCRIPTS_DIR = Path(__file__).resolve().parents[2]
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from api import regime_share  # noqa: E402
from api.subprocess import ScriptResult  # noqa: E402

LIVE = {
    "vix": 28.54, "vvix": 127.56, "spy": 716.52, "cor1m": 30.70,
    "cor1m_5d_change": 13.04, "vvix_vix_ratio": 4.47, "spx_distance_pct": -5.72,
    "realized_vol": 23.48,
    "cri": {"score": 51, "level": "HIGH",
            "components": {"vix": 16.5, "vvix": 12.8, "correlation": 7.4, "momentum": 14.3}},
    "crash_trigger": {"conditions": {"spx_below_100d_ma": True, "cor1m_gt_60": False}},
}


@pytest.fixture
def captured(monkeypatch, tmp_path):
    calls = []

    async def fake_run_script(script, args, timeout):
        overlay = None
        if "--live-overlay" in args:
            path = Path(args[args.index("--live-overlay") + 1])
            overlay = json.loads(path.read_text())
            calls.append({"script": script, "args": args, "overlay": overlay, "path": path})
        else:
            calls.append({"script": script, "args": args, "overlay": None, "path": None})
        return ScriptResult(ok=True, data={"preview_path": "x.html"})

    monkeypatch.setattr(regime_share, "run_script", fake_run_script)
    monkeypatch.setattr(regime_share, "overlay_dir", lambda: tmp_path)
    return calls


def test_live_reading_reaches_the_generator(captured):
    overlay = regime_share.RegimeLiveOverlay.model_validate(LIVE)
    result = asyncio.run(regime_share.generate(overlay, card_type="regime"))
    assert result.ok
    (call,) = captured
    assert call["script"] == "generate_regime_share.py"
    assert call["args"][:2] == ["--json", "--no-open"]
    assert call["overlay"]["cri"]["score"] == 51
    assert call["overlay"]["cor1m"] == 30.70
    assert not call["path"].exists(), "overlay file must be removed after the run"


def test_no_reading_runs_the_snapshot(captured):
    asyncio.run(regime_share.generate(None, card_type="regime"))
    (call,) = captured
    assert "--live-overlay" not in call["args"]


def test_internals_card_type_is_passed(captured):
    asyncio.run(regime_share.generate(None, card_type="internals"))
    assert captured[0]["args"][-2:] == ["--card-type", "internals"]


@pytest.mark.parametrize("bad", [
    {"vix": "NaN"},
    {"vix": 1e9},
    {"cri": {"score": 500, "level": "HIGH", "components": {}}},
    {"cri": {"score": 50, "level": "<script>", "components": {}}},
    {"unknown": 1},
])
def test_rejects_untrusted_readings(bad):
    with pytest.raises(ValidationError):
        regime_share.RegimeLiveOverlay.model_validate(bad)


class TestServerRoutes:
    """The FastAPI routes accept an optional reading and refuse a bad one."""

    @pytest.fixture
    def client(self, monkeypatch):
        from fastapi.testclient import TestClient
        from api import auth, server

        monkeypatch.setattr(auth, "is_trusted_local_request", lambda request: True)
        monkeypatch.setattr(server, "is_trusted_local_request", lambda request: True)
        seen = []

        async def fake_generate(overlay, card_type):
            seen.append((overlay, card_type))
            return ScriptResult(ok=True, data={"preview_path": "x.html"})

        monkeypatch.setattr(server.regime_share_gen, "generate", fake_generate)
        return TestClient(server.app), seen

    def test_post_without_body_uses_the_snapshot(self, client):
        c, seen = client
        assert c.post("/regime/share").status_code == 200
        assert seen == [(None, "regime")]

    def test_post_with_reading_forwards_it(self, client):
        c, seen = client
        assert c.post("/regime/share", json=LIVE).status_code == 200
        overlay, card_type = seen[0]
        assert card_type == "regime"
        assert overlay.cri.score == 51

    def test_internals_forwards_its_reading(self, client):
        c, seen = client
        assert c.post("/internals/share", json=LIVE).status_code == 200
        assert seen[0][1] == "internals"

    def test_bad_reading_is_422_and_never_generates(self, client):
        c, seen = client
        assert c.post("/regime/share", json={"vix": 1e12}).status_code == 422
        assert seen == []
