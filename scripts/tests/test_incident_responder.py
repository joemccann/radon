"""Incident responder — pure core tests (selection, state, commands)."""

import json
from datetime import datetime, timezone
from pathlib import Path

from incident_responder import (
    build_analyze_command,
    build_sync_command,
    load_state,
    mark_analyzed,
    select_new_open_incidents,
)

NOW = datetime(2026, 8, 4, 3, 0, 0, tzinfo=timezone.utc)


def write_incident(directory: Path, incident_id: str, status: str) -> Path:
    path = directory / f"incident-{incident_id}.json"
    path.write_text(json.dumps({
        "schema": "radon.incident/1",
        "incident_id": incident_id,
        "case_id": "service-health-degraded",
        "severity": "P2",
        "status": status,
    }))
    return path


class TestSelection:
    def test_selects_only_open_and_unanalyzed(self, tmp_path: Path):
        open_new = write_incident(tmp_path, "20260804T020000Z-a", "open")
        write_incident(tmp_path, "20260804T010000Z-b", "resolved")
        write_incident(tmp_path, "20260804T000000Z-c", "open")
        state = {"analyzed": {"20260804T000000Z-c": "2026-08-04T02:00:00+00:00"}}
        selected = select_new_open_incidents(tmp_path, state)
        assert [p.name for p in selected] == [open_new.name]

    def test_oldest_first_and_bounded(self, tmp_path: Path):
        write_incident(tmp_path, "20260804T020000Z-b", "open")
        write_incident(tmp_path, "20260804T010000Z-a", "open")
        write_incident(tmp_path, "20260804T030000Z-c", "open")
        selected = select_new_open_incidents(tmp_path, {"analyzed": {}}, limit=2)
        assert [p.name for p in selected] == [
            "incident-20260804T010000Z-a.json",
            "incident-20260804T020000Z-b.json",
        ]

    def test_malformed_json_is_skipped(self, tmp_path: Path):
        (tmp_path / "incident-bad.json").write_text("{not json")
        write_incident(tmp_path, "20260804T020000Z-a", "open")
        selected = select_new_open_incidents(tmp_path, {"analyzed": {}})
        assert len(selected) == 1


class TestState:
    def test_state_roundtrip(self, tmp_path: Path):
        state_path = tmp_path / "state.json"
        state = load_state(state_path)
        assert state == {"analyzed": {}}
        mark_analyzed(state_path, state, "20260804T020000Z-a", NOW)
        reloaded = load_state(state_path)
        assert reloaded["analyzed"]["20260804T020000Z-a"] == NOW.isoformat()


class TestCommands:
    def test_sync_command_mirrors_remote_dir(self):
        cmd = build_sync_command("radon@ib-gateway", "/home/radon/radon/data/incidents", Path("/tmp/mirror"))
        assert cmd[0] == "rsync"
        assert "radon@ib-gateway:/home/radon/radon/data/incidents/" in cmd
        assert cmd[-1] == "/tmp/mirror/"

    def test_analyze_command_is_analyze_only(self):
        cmd = build_analyze_command(Path("/tmp/mirror/incident-x.json"))
        assert cmd[0] == "claude"
        prompt = cmd[cmd.index("-p") + 1]
        assert prompt.startswith("/incident ")
        assert "--analyze-only" in prompt
        assert "/tmp/mirror/incident-x.json" in prompt
