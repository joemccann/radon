"""Persisted provider cooldowns: a 429 must skip the next process, not retry.

The ladder stores only the provider name and an expiry epoch. A Codex 429
whose reset is days away must not be retried on every 5-minute oneshot.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from clients import provider_cooldown


def test_relative_reset_in_the_body_wins_over_a_shorter_header(tmp_path):
    seconds = provider_cooldown.reset_seconds(
        "Rate limit reached, try again in 2 days",
        {"Retry-After": "60"},
        now=1_700_000_000.0,
    )
    assert seconds == 2 * 86400


def test_retry_after_http_date_is_used_when_the_body_is_silent():
    now = 1_700_000_000.0
    seconds = provider_cooldown.reset_seconds(
        "too many requests",
        {"retry-after": "Thu, 16 Nov 2023 00:00:00 GMT"},
        now=now,
    )
    assert seconds is not None and seconds > 0


def test_note_survives_a_new_process_via_the_state_file(tmp_path):
    env = {"RADON_PROVIDER_COOLDOWN_STATE": str(tmp_path / "cooldown.json")}
    provider_cooldown.note("codex", 3600, env)
    provider_cooldown.clear_process_state()
    assert provider_cooldown.rate_limited("codex", env) is True
    assert provider_cooldown.rate_limited("anthropic", env) is False
    payload = json.loads(Path(env["RADON_PROVIDER_COOLDOWN_STATE"]).read_text())
    assert payload["codex"] > time.time()


def test_a_write_failure_still_skips_this_process(tmp_path, monkeypatch):
    env = {"RADON_PROVIDER_COOLDOWN_STATE": str(tmp_path / "missing" / "cooldown.json")}
    monkeypatch.setattr(provider_cooldown, "_persist", lambda *a, **k: (_ for _ in ()).throw(OSError("disk")))
    provider_cooldown.note("codex", 3600, env)
    assert provider_cooldown.rate_limited("codex", env) is True


def test_docs_match_the_engine_cooldown_contract():
    """The engine persists only a body-named reset; no env fallback is read."""
    root = Path(__file__).resolve().parents[2]
    engine = (root / "scripts/model_gateway/engine.py").read_text()
    assert "provider_cooldown.reset_seconds(body_text, None)" in engine
    assert "fallback_seconds" not in engine
    research = (root / "docs/dropbox-research.md").read_text()
    assert "RADON_PROVIDER_RATE_LIMIT_COOLDOWN_S" not in research
    assert "A bare 429 persists nothing" in research
    gateway = (root / "docs/model-gateway.md").read_text()
    assert "A 429 whose body names a reset records `clients.provider_cooldown`" in gateway
    example = (root / "cloud/.env.example").read_text()
    assert "RADON_PROVIDER_RATE_LIMIT_COOLDOWN_S" not in example
    assert "RADON_PROVIDER_COOLDOWN_STATE=" in example
