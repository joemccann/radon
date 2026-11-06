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


# Live bodies from radon-model-gateway.service (2026-11-05), secrets-free.
CODEX_USAGE_LIMIT = (
    '{"error":{"type":"usage_limit_reached","message":"The usage limit has been reached",'
    '"plan_type":"pro","resets_at":1793994517}}'
)
AGY_QUOTA = (
    "warning: --mode plan has no effect in print mode\n"
    "error: Individual quota reached. Please upgrade your subscription to increase "
    "your limits. Resets in 24h34m35s. AGY_ERR_QUOTA"
)


def test_codex_resets_at_epoch_is_parsed():
    now = 1_793_990_000.0
    assert provider_cooldown.reset_seconds(CODEX_USAGE_LIMIT, None, now=now) == 1_793_994_517 - now


def test_antigravity_compound_resets_in_is_parsed():
    seconds = provider_cooldown.reset_seconds(AGY_QUOTA, None, now=1_700_000_000.0)
    assert seconds == 24 * 3600 + 34 * 60 + 35


def test_spelled_compound_duration_is_parsed():
    assert provider_cooldown.reset_seconds("try again in 1 hour 30 minutes", None, now=0.0) == 5400


def test_quota_exhaustion_is_recognised_without_matching_transient_overload():
    assert provider_cooldown.is_quota_exhausted(CODEX_USAGE_LIMIT)
    assert provider_cooldown.is_quota_exhausted(AGY_QUOTA)
    assert provider_cooldown.is_quota_exhausted("You exceeded your current quota")
    assert not provider_cooldown.is_quota_exhausted('{"type":"overloaded_error"}')
    assert not provider_cooldown.is_quota_exhausted("too many requests")


def test_quota_without_reset_backs_off_exponentially_to_a_cap(tmp_path, monkeypatch):
    env = {"RADON_PROVIDER_COOLDOWN_STATE": str(tmp_path / "cooldown.json")}
    now = [1_700_000_000.0]
    monkeypatch.setattr(provider_cooldown, "_now", lambda: now[0])
    waits = []
    for _ in range(9):
        waits.append(provider_cooldown.note_quota("antigravity", None, env))
        assert provider_cooldown.rate_limited("antigravity", env) is True
        now[0] += waits[-1] + 1
        assert provider_cooldown.rate_limited("antigravity", env) is False
    base = provider_cooldown.QUOTA_BACKOFF_BASE_S
    cap = provider_cooldown.QUOTA_BACKOFF_CAP_S
    assert (base, cap) == (300.0, 6 * 3600.0)
    assert waits[:3] == [base, base * 2, base * 4]
    assert waits[-1] == cap and max(waits) == cap


def test_success_clears_the_backoff_and_its_streak(tmp_path, monkeypatch):
    env = {"RADON_PROVIDER_COOLDOWN_STATE": str(tmp_path / "cooldown.json")}
    monkeypatch.setattr(provider_cooldown, "_now", lambda: 1_700_000_000.0)
    provider_cooldown.note_quota("codex", None, env)
    provider_cooldown.note_quota("codex", None, env)
    provider_cooldown.clear("codex", env)
    assert provider_cooldown.rate_limited("codex", env) is False
    assert "codex" not in json.loads(Path(env["RADON_PROVIDER_COOLDOWN_STATE"]).read_text())
    assert provider_cooldown.note_quota("codex", None, env) == provider_cooldown.QUOTA_BACKOFF_BASE_S


def test_a_parsed_reset_wins_over_the_exponential_fallback(tmp_path, monkeypatch):
    env = {"RADON_PROVIDER_COOLDOWN_STATE": str(tmp_path / "cooldown.json")}
    monkeypatch.setattr(provider_cooldown, "_now", lambda: 1_700_000_000.0)
    assert provider_cooldown.note_quota("antigravity", 88_475.0, env) == 88_475.0
