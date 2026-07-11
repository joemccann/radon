"""Tests for CTA sync health + retry runtime helpers."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from utils import cta_sync


def completed(returncode: int, stdout: str = "", stderr: str = "") -> SimpleNamespace:
    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


class TestHealthRecordHelpers:
    def test_write_sync_health_record_persists_machine_readable_status(self, tmp_path: Path):
        status_path = tmp_path / "cta-sync.json"
        history_path = tmp_path / "cta-sync-history.jsonl"
        record = cta_sync.build_sync_health_record(
            target_date="2026-03-11",
            status="error",
            trigger="route",
            started_at="2026-03-11T22:05:00Z",
            finished_at="2026-03-11T22:05:31Z",
            duration_ms=31_000,
            attempt_count=2,
            cache_path=None,
            error_type="auth_rejected",
            error_excerpt="Your username or password was incorrect",
            artifact_log_path="logs/cta-sync-artifacts/cta-sync-20260311T220531.log",
        )

        cta_sync.write_sync_health_record(
            record,
            status_path=status_path,
            history_path=history_path,
        )

        written = json.loads(status_path.read_text())
        assert written["service"] == "cta-sync"
        assert written["status"] == "error"
        assert written["error_type"] == "auth_rejected"
        assert written["target_date"] == "2026-03-11"
        assert json.loads(history_path.read_text()) == record

    @pytest.mark.parametrize(
        ("stderr", "expected"),
        [
            ("Your username or password was incorrect", "auth_rejected"),
            ("Timeout 30000ms exceeded while waiting for selector", "timeout"),
            ("Login form fields not found on MenthorQ login page", "selector_failure"),
            ("FAILED: No MenthorQ data retrieved.", "empty_payload"),
            ("Cloudflare challenge presented", "challenge_page"),
        ],
    )
    def test_classify_sync_error(self, stderr: str, expected: str):
        assert cta_sync.classify_sync_error(stderr) == expected


class TestRunCtaSync:
    def test_run_cta_sync_records_success_without_subprocess_when_cache_already_exists(self, tmp_path: Path):
        cache_dir = tmp_path / "data" / "menthorq_cache"
        cache_dir.mkdir(parents=True)
        (cache_dir / "cta_2026-03-11.json").write_text(json.dumps({"date": "2026-03-11", "tables": {"main": []}}))

        calls: list[tuple[str, list[str]]] = []

        def fake_runner(*args, **kwargs):
            calls.append((args[0], kwargs.get("cwd")))
            return completed(0)

        exit_code = cta_sync.run_cta_sync(
            project_dir=tmp_path,
            python_bin="python3.9",
            now=datetime(2026, 3, 11, 16, 15),
            trigger="launchd",
            runner=fake_runner,
        )

        health = json.loads((tmp_path / "data" / "service_health" / "cta-sync.json").read_text())
        assert exit_code == 0
        assert calls == []
        assert health["status"] == "success"
        assert health["cache_path"].endswith("cta_2026-03-11.json")
        assert health["attempt_count"] == 0

    def test_run_cta_sync_retries_failures_and_records_last_error(self, tmp_path: Path):
        attempts: list[list[str]] = []
        sleeps: list[int] = []

        def fake_runner(args, **kwargs):
            attempts.append(args)
            return completed(1, stderr="Your username or password was incorrect")

        exit_code = cta_sync.run_cta_sync(
            project_dir=tmp_path,
            python_bin="python3.9",
            now=datetime(2026, 3, 11, 16, 15),
            trigger="route",
            max_attempts=3,
            backoff_seconds=(2, 5),
            runner=fake_runner,
            sleep_fn=sleeps.append,
        )

        health = json.loads((tmp_path / "data" / "service_health" / "cta-sync.json").read_text())
        assert exit_code == 1
        assert len(attempts) == 3
        assert sleeps == [2, 5]
        assert health["status"] == "error"
        assert health["attempt_count"] == 3
        assert health["error_type"] == "auth_rejected"
        assert "incorrect" in health["error_excerpt"]
