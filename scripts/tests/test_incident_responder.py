"""Incident responder — pure core tests (selection, state, commands)."""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from incident_responder import (
    build_analyze_command,
    build_sync_command,
    load_state,
    mark_analyzed,
    select_new_open_incidents,
)

NOW = datetime(2026, 8, 4, 3, 0, 0, tzinfo=timezone.utc)


def write_incident(directory: Path, incident_id: str, status: str,
                   detected_at: str | None = None) -> Path:
    path = directory / f"incident-{incident_id}.json"
    path.write_text(json.dumps({
        "schema": "radon.incident/1",
        "incident_id": incident_id,
        "case_id": "service-health-degraded",
        "severity": "P2",
        "status": status,
        "detected_at": detected_at or (NOW - timedelta(hours=1)).isoformat(),
    }))
    return path


class TestSelection:
    def test_selects_only_open_and_unanalyzed(self, tmp_path: Path):
        open_new = write_incident(tmp_path, "20260804T020000Z-a", "open")
        write_incident(tmp_path, "20260804T010000Z-b", "resolved")
        write_incident(tmp_path, "20260804T000000Z-c", "open")
        state = {"analyzed": {"20260804T000000Z-c": "2026-08-04T02:00:00+00:00"}}
        selected = select_new_open_incidents(tmp_path, state, now=NOW)
        assert [p.name for p in selected] == [open_new.name]

    def test_oldest_first_and_bounded(self, tmp_path: Path):
        write_incident(tmp_path, "20260804T020000Z-b", "open")
        write_incident(tmp_path, "20260804T010000Z-a", "open")
        write_incident(tmp_path, "20260804T030000Z-c", "open")
        selected = select_new_open_incidents(tmp_path, {"analyzed": {}}, limit=2, now=NOW)
        assert [p.name for p in selected] == [
            "incident-20260804T010000Z-a.json",
            "incident-20260804T020000Z-b.json",
        ]

    def test_fresh_incident_waits_out_the_transient_window(self, tmp_path: Path):
        """Self-resolving transients (mid-deploy marker mismatch, a writer
        erroring during the deploy restart window) auto-resolve within a
        watchdog cycle or two. Analyzing an incident younger than the
        min-age gate burns a headless-Claude run on noise (13:05Z
        2026-08-04: two transients, both resolved by 13:10Z)."""
        write_incident(tmp_path, "20260804T130501Z-x", "open",
                       detected_at=(NOW - timedelta(minutes=3)).isoformat())
        aged = write_incident(tmp_path, "20260804T110000Z-y", "open",
                              detected_at=(NOW - timedelta(minutes=20)).isoformat())
        selected = select_new_open_incidents(tmp_path, {"analyzed": {}}, now=NOW)
        assert [p.name for p in selected] == [aged.name]

    def test_malformed_json_is_skipped(self, tmp_path: Path):
        (tmp_path / "incident-bad.json").write_text("{not json")
        write_incident(tmp_path, "20260804T020000Z-a", "open")
        selected = select_new_open_incidents(tmp_path, {"analyzed": {}}, now=NOW)
        assert len(selected) == 1


class TestState:
    def test_state_roundtrip(self, tmp_path: Path):
        state_path = tmp_path / "state.json"
        state = load_state(state_path)
        assert state == {"analyzed": {}}
        mark_analyzed(state_path, state, "20260804T020000Z-a", NOW)
        reloaded = load_state(state_path)
        assert reloaded["analyzed"]["20260804T020000Z-a"] == NOW.isoformat()


class TestAnalyzeBounds:
    def test_analysis_timeout_returns_failure_text_instead_of_raising(self, tmp_path: Path):
        """A hung headless analysis must not crash the cycle unrecorded —
        an uncaught TimeoutExpired skips mark_analyzed, so the next cycle
        re-launches the same hung analysis forever."""
        import subprocess
        from unittest.mock import patch

        from incident_responder import ANALYZE_TIMEOUT_SECS, analyze

        with patch(
            "incident_responder.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd=["claude"], timeout=ANALYZE_TIMEOUT_SECS),
        ):
            result = analyze(tmp_path / "incident-x.json", tmp_path)
        assert "TIMED OUT" in result


class TestCommands:
    def test_sync_command_mirrors_remote_dir(self):
        cmd = build_sync_command("radon@ib-gateway", "/home/radon/radon/data/incidents", Path("/tmp/mirror"))
        assert cmd[0] == "rsync"
        assert "radon@ib-gateway:/home/radon/radon/data/incidents/" in cmd
        assert cmd[-1] == "/tmp/mirror/"

    def test_sync_delete_never_wipes_responder_owned_files(self):
        """--delete removes local files absent on the remote; the dedup state
        and diagnoses live beside the mirror and must be protected or every
        cycle re-analyzes the same incident (5x duplicate-analysis bug,
        2026-08-04)."""
        cmd = build_sync_command("r@h", "/remote", Path("/tmp/mirror"))
        assert "--exclude=.responder-state.json" in cmd
        assert "--exclude=.responder.lock" in cmd
        assert "--exclude=*.diagnosis.md" in cmd
        assert "--delete-excluded" not in cmd

    def test_analyze_command_is_analyze_only(self):
        cmd = build_analyze_command(Path("/tmp/mirror/incident-x.json"))
        assert cmd[0] == "claude"
        prompt = cmd[cmd.index("-p") + 1]
        assert prompt.startswith("/incident ")
        assert "--analyze-only" in prompt
        assert "/tmp/mirror/incident-x.json" in prompt
