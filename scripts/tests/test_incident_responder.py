"""Incident responder — pure core tests (selection, state, commands, cycle)."""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from incident_responder import (
    MAX_ANALYSIS_ATTEMPTS,
    build_analyze_command,
    build_sync_command,
    prepare_mirror_dir,
    load_state,
    mark_analyzed,
    run_cycle,
    select_new_open_incidents,
)

NOW = datetime(2026, 8, 4, 3, 0, 0, tzinfo=timezone.utc)


def write_incident(directory: Path, incident_id: str, status: str,
                   detected_at: str | None = None,
                   title: str | None = None) -> Path:
    path = directory / f"incident-{incident_id}.json"
    payload = {
        "schema": "radon.incident/1",
        "incident_id": incident_id,
        "case_id": "service-health-degraded",
        "severity": "P2",
        "status": status,
        "detected_at": detected_at or (NOW - timedelta(hours=1)).isoformat(),
    }
    if title is not None:
        payload["title"] = title
    path.write_text(json.dumps(payload))
    return path


class TestSelection:
    def test_selects_only_open_and_unanalyzed(self, tmp_path: Path):
        open_new = write_incident(tmp_path, "20260804T020000Z-alpha", "open")
        write_incident(tmp_path, "20260804T010000Z-bravo", "resolved")
        write_incident(tmp_path, "20260804T000000Z-charlie", "open")
        state = {"analyzed": {"20260804T000000Z-charlie": "2026-08-04T02:00:00+00:00"}}
        selected = select_new_open_incidents(tmp_path, state, now=NOW)
        assert [p.name for p in selected] == [open_new.name]

    def test_oldest_first_and_bounded(self, tmp_path: Path):
        write_incident(tmp_path, "20260804T020000Z-bravo", "open")
        write_incident(tmp_path, "20260804T010000Z-alpha", "open")
        write_incident(tmp_path, "20260804T030000Z-charlie", "open")
        selected = select_new_open_incidents(tmp_path, {"analyzed": {}}, limit=2, now=NOW)
        assert [p.name for p in selected] == [
            "incident-20260804T010000Z-alpha.json",
            "incident-20260804T020000Z-bravo.json",
        ]

    def test_fresh_incident_waits_out_the_transient_window(self, tmp_path: Path):
        """Self-resolving transients (mid-deploy marker mismatch, a writer
        erroring during the deploy restart window) auto-resolve within a
        watchdog cycle or two. Analyzing an incident younger than the
        min-age gate burns a headless-Claude run on noise (13:05Z
        2026-08-04: two transients, both resolved by 13:10Z)."""
        write_incident(tmp_path, "20260804T130501Z-xray", "open",
                       detected_at=(NOW - timedelta(minutes=3)).isoformat())
        aged = write_incident(tmp_path, "20260804T110000Z-yankee", "open",
                              detected_at=(NOW - timedelta(minutes=20)).isoformat())
        selected = select_new_open_incidents(tmp_path, {"analyzed": {}}, now=NOW)
        assert [p.name for p in selected] == [aged.name]

    def test_malformed_json_is_skipped(self, tmp_path: Path):
        (tmp_path / "incident-bad.json").write_text("{not json")
        write_incident(tmp_path, "20260804T020000Z-alpha", "open")
        selected = select_new_open_incidents(tmp_path, {"analyzed": {}}, now=NOW)
        assert len(selected) == 1


class TestState:
    def test_state_roundtrip(self, tmp_path: Path):
        state_path = tmp_path / "state.json"
        state = load_state(state_path)
        assert state == {"analyzed": {}, "attempts": {}}
        mark_analyzed(state_path, state, "20260804T020000Z-alpha", NOW)
        reloaded = load_state(state_path)
        assert reloaded["analyzed"]["20260804T020000Z-alpha"] == NOW.isoformat()


class TestAnalyzeBounds:
    def test_analysis_timeout_is_recorded_not_retried(self, tmp_path: Path):
        """A hung headless analysis must not crash the cycle unrecorded —
        an uncaught TimeoutExpired skips mark_analyzed, so the next cycle
        re-launches the same hung analysis forever."""
        import subprocess

        from incident_responder import ANALYZE_TIMEOUT_SECS, analyze

        with patch(
            "incident_responder.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd=["claude"], timeout=ANALYZE_TIMEOUT_SECS),
        ):
            result = analyze(tmp_path / "incident-x.json", tmp_path)
        assert "TIMED OUT" in result.text
        assert result.recorded is True

    def test_failed_analysis_is_not_recorded(self, tmp_path: Path):
        from incident_responder import analyze

        with patch("incident_responder.subprocess.run",
                   return_value=SimpleNamespace(returncode=1, stdout="", stderr="boom")):
            result = analyze(tmp_path / "incident-x.json", tmp_path)
        assert result.recorded is False
        assert "ANALYSIS FAILED" in result.text


def _fake_runner(claude_returncode: int):
    def run(cmd, **_kwargs):
        if cmd[0] == "claude":
            return SimpleNamespace(returncode=claude_returncode,
                                   stdout="diagnosis body", stderr="boom")
        return SimpleNamespace(returncode=0, stdout="", stderr="")
    return run


class TestCycleRetry:
    """A failed analysis must stay retryable — round 1 called mark_analyzed
    unconditionally, so one non-zero exit consumed the incident forever."""

    def _cycle(self, tmp_path: Path, monkeypatch, claude_returncode: int,
               incident_id: str = "20260804T020000Z-alpha") -> dict:
        monkeypatch.setenv("INCIDENT_RESPONDER_LOCAL_DIR", "data/incidents_remote")
        mirror = tmp_path / "data" / "incidents_remote"
        mirror.mkdir(parents=True, exist_ok=True)
        write_incident(mirror, incident_id, "open")
        with patch("incident_responder.subprocess.run",
                   side_effect=_fake_runner(claude_returncode)):
            run_cycle(tmp_path)
        return load_state(mirror / ".responder-state.json")

    def test_failed_analysis_stays_unanalyzed(self, tmp_path: Path, monkeypatch):
        state = self._cycle(tmp_path, monkeypatch, claude_returncode=1)
        assert state["analyzed"] == {}
        assert state["attempts"]["20260804T020000Z-alpha"] == 1

    def test_successful_analysis_is_recorded(self, tmp_path: Path, monkeypatch):
        state = self._cycle(tmp_path, monkeypatch, claude_returncode=0)
        assert "20260804T020000Z-alpha" in state["analyzed"]
        assert state["attempts"] == {}

    def test_retries_are_bounded(self, tmp_path: Path, monkeypatch):
        """Retryable must not mean forever: a permanently failing incident
        would relaunch a headless run every 10 minutes."""
        monkeypatch.setenv("INCIDENT_RESPONDER_LOCAL_DIR", "data/incidents_remote")
        mirror = tmp_path / "data" / "incidents_remote"
        mirror.mkdir(parents=True, exist_ok=True)
        write_incident(mirror, "20260804T020000Z-alpha", "open")
        for _ in range(MAX_ANALYSIS_ATTEMPTS):
            with patch("incident_responder.subprocess.run",
                       side_effect=_fake_runner(1)):
                run_cycle(tmp_path)
        state = load_state(mirror / ".responder-state.json")
        assert "20260804T020000Z-alpha" in state["analyzed"]

    def test_diagnosis_filename_is_derived_from_a_validated_id(
            self, tmp_path: Path, monkeypatch):
        """incident_id is remote-controlled; round 1 hardened the projection
        filename against it and left the diagnosis filename interpolating it
        raw two lines later."""
        monkeypatch.setenv("INCIDENT_RESPONDER_LOCAL_DIR", "data/incidents_remote")
        mirror = tmp_path / "data" / "incidents_remote"
        mirror.mkdir(parents=True, exist_ok=True)
        path = mirror / "incident-hostile.json"
        path.write_text(json.dumps({
            "incident_id": "../../../../tmp/radon-escape",
            "case_id": "service-health-degraded",
            "severity": "P2",
            "status": "open",
            "detected_at": (NOW - timedelta(hours=1)).isoformat(),
        }))
        with patch("incident_responder.subprocess.run", side_effect=_fake_runner(0)):
            run_cycle(tmp_path)
        assert not (tmp_path.parent / "radon-escape.diagnosis.md").exists()
        written = [p.name for p in mirror.glob("*.diagnosis.md")]
        assert written and all(name.startswith("unverified-") for name in written)

    def test_cycle_notifies_with_incident_description_and_card(
            self, tmp_path: Path, monkeypatch):
        monkeypatch.setenv("INCIDENT_RESPONDER_LOCAL_DIR", "data/incidents_remote")
        mirror = tmp_path / "data" / "incidents_remote"
        mirror.mkdir(parents=True, exist_ok=True)
        write_incident(
            mirror, "20260804T020000Z-alpha", "open",
            title="2 service_health row(s) degraded: uw-sweeps, cri-scan",
        )
        notes = []

        def capture(note, **_kwargs):
            notes.append(note)

        with patch("incident_responder.subprocess.run",
                   side_effect=_fake_runner(0)), \
             patch("incident_responder.notify", side_effect=capture):
            run_cycle(tmp_path)
        assert notes, "cycle must fire at least one notification"
        diagnosed = [n for n in notes if n.title == "Radon incident diagnosed"]
        assert diagnosed
        assert "uw-sweeps" in diagnosed[-1].body
        assert diagnosed[-1].open_path is not None
        assert diagnosed[-1].open_path.name.endswith(".incident.html")
        assert (mirror / diagnosed[-1].open_path.name).exists()


class TestCommands:
    def test_sync_command_mirrors_remote_dir(self, tmp_path):
        mirror = prepare_mirror_dir(tmp_path, "data/incidents_remote")
        cmd = build_sync_command("radon@ib-gateway", "/home/radon/radon/data/incidents", mirror)
        assert cmd[0] == "rsync"
        assert "radon@ib-gateway:/home/radon/radon/data/incidents/" in cmd
        assert cmd[-1] == f"{mirror}/"

    def test_sync_delete_never_wipes_responder_owned_files(self, tmp_path):
        """--delete removes local files absent on the remote; the dedup state
        and diagnoses live beside the mirror and must be protected or every
        cycle re-analyzes the same incident (5x duplicate-analysis bug,
        2026-08-04)."""
        mirror = prepare_mirror_dir(tmp_path, "data/incidents_remote")
        cmd = build_sync_command("r@h", "/remote", mirror)
        assert "--exclude=.responder-state.json" in cmd
        assert "--exclude=.responder.lock" in cmd
        assert "--exclude=*.diagnosis.md" in cmd
        assert "--exclude=*.incident.html" in cmd
        assert "--delete-excluded" not in cmd

    def test_mirror_delete_rejects_root_parent_symlink_and_unapproved_override(
        self, tmp_path
    ):
        for unsafe in ("/", "data", "data/other"):
            with pytest.raises(ValueError):
                prepare_mirror_dir(tmp_path, unsafe)

        (tmp_path / "outside").mkdir()
        (tmp_path / "data").symlink_to(tmp_path / "outside", target_is_directory=True)
        with pytest.raises(ValueError, match="symlink"):
            prepare_mirror_dir(tmp_path, "data/incidents_remote")

    def test_analyze_command_is_analyze_only(self):
        cmd = build_analyze_command(Path("/tmp/proj/x.projection.json"))
        assert cmd[0] == "claude"
        prompt = cmd[cmd.index("-p") + 1]
        assert prompt.startswith("/incident ")
        assert "--analyze-only" in prompt
        assert "/tmp/proj/x.projection.json" in prompt
