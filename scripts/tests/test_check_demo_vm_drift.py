"""Off-box drift check for the demo VM backend.

2026-10-18: the demo VM ran 02db257b, 847 commits behind main in scripts/, and
nothing noticed until the frontend logged /ai-cycle 404 x213. The check reads
the public /health ``revision`` and fails when it is missing, unknown to main,
or behind main's backend paths for longer than the allowed window.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / "scripts"))

from ci import check_demo_vm_drift as drift  # noqa: E402

DAY = 86_400
NOW = 1_800_000_000
SHA = "c" * 40


class TestEvaluate:
    def test_missing_revision_fails(self):
        problems = drift.evaluate(None, known=True, undeployed=[], now=NOW, max_days=7)
        assert problems and "no revision" in problems[0]

    def test_unknown_revision_fails(self):
        problems = drift.evaluate(SHA, known=False, undeployed=[], now=NOW, max_days=7)
        assert problems and "not an ancestor" in problems[0]

    def test_up_to_date_passes(self):
        assert drift.evaluate(SHA, known=True, undeployed=[], now=NOW, max_days=7) == []

    def test_behind_within_window_passes(self):
        undeployed = [("d" * 40, NOW - 2 * DAY), ("e" * 40, NOW - 1 * DAY)]
        assert drift.evaluate(SHA, known=True, undeployed=undeployed, now=NOW, max_days=7) == []

    def test_window_is_measured_from_the_oldest_undeployed_change(self):
        # A fresh commit on top must not reset the clock on an old one.
        undeployed = [("d" * 40, NOW - 9 * DAY), ("e" * 40, NOW - 60)]
        problems = drift.evaluate(SHA, known=True, undeployed=undeployed, now=NOW, max_days=7)
        assert len(problems) == 1
        assert "2 backend commit(s)" in problems[0]
        assert "9.0 days" in problems[0]
        assert "d" * 12 in problems[0]


def _git(repo: Path, *args: str, env: dict | None = None) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=True,
        env={**os.environ, **(env or {})},
    ).stdout.strip()


def _commit(repo: Path, path: str, when: int) -> str:
    target = repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(f"{path} {when}\n")
    _git(repo, "add", path)
    stamp = f"{when} +0000"
    _git(
        repo,
        "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false",
        "commit", "-q", "-m", f"{path} {when}",
        env={"GIT_AUTHOR_DATE": stamp, "GIT_COMMITTER_DATE": stamp},
    )
    return _git(repo, "rev-parse", "HEAD")


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    path = tmp_path / "repo"
    path.mkdir()
    _git(path, "init", "-q", "-b", "main")
    return path


class TestGitHistory:
    def test_undeployed_lists_only_backend_paths_oldest_first(self, repo):
        base = _commit(repo, "scripts/api/server.py", NOW - 10 * DAY)
        _commit(repo, "web/page.tsx", NOW - 9 * DAY)  # frontend: not drift
        a = _commit(repo, "requirements.txt", NOW - 8 * DAY)
        b = _commit(repo, "scripts/x.py", NOW - 1 * DAY)
        _commit(repo, "pyproject.toml.bak", NOW - 1 * DAY)  # not pyproject.toml
        result = drift.undeployed_commits(repo, base, "HEAD")
        assert result == [(a, NOW - 8 * DAY), (b, NOW - 1 * DAY)]

    def test_is_known_revision(self, repo):
        base = _commit(repo, "scripts/a.py", NOW - DAY)
        assert drift.is_known_revision(repo, base, "HEAD") is True
        assert drift.is_known_revision(repo, "f" * 40, "HEAD") is False


class TestFetchRevision:
    def test_extracts_revision(self):
        assert drift.parse_revision(json.dumps({"status": "ok", "revision": SHA})) == SHA

    @pytest.mark.parametrize("body", ['{"status":"ok"}', "not json", '{"revision": "abc"}', "[]"])
    def test_missing_or_invalid_is_none(self, body):
        assert drift.parse_revision(body) is None


class TestMain:
    def test_main_fails_on_missing_revision(self, repo, monkeypatch, capsys):
        _commit(repo, "scripts/a.py", NOW - DAY)
        monkeypatch.setattr(drift, "fetch_health", lambda url, timeout: '{"status":"ok"}')
        rc = drift.main(["--repo", str(repo), "--ref", "HEAD", "--url", "https://x/health"])
        assert rc == 1
        assert "no revision" in capsys.readouterr().out

    def test_main_passes_when_current(self, repo, monkeypatch, capsys):
        head = _commit(repo, "scripts/a.py", NOW - DAY)
        monkeypatch.setattr(drift, "fetch_health", lambda url, timeout: json.dumps({"revision": head}))
        rc = drift.main(["--repo", str(repo), "--ref", "HEAD", "--url", "https://x/health"])
        assert rc == 0
        assert "up to date" in capsys.readouterr().out

    def test_main_fails_when_stale(self, repo, monkeypatch, capsys):
        old = _commit(repo, "scripts/a.py", NOW - 30 * DAY)
        _commit(repo, "scripts/b.py", NOW - 20 * DAY)
        monkeypatch.setattr(drift, "fetch_health", lambda url, timeout: json.dumps({"revision": old}))
        monkeypatch.setattr(drift.time, "time", lambda: NOW)
        rc = drift.main(["--repo", str(repo), "--ref", "HEAD", "--url", "https://x/health", "--max-days", "7"])
        assert rc == 1
        out = capsys.readouterr().out
        assert "1 backend commit(s)" in out
        assert "deploy-demo-vm.sh" in out

    def test_main_fails_when_unreachable(self, repo, monkeypatch, capsys):
        _commit(repo, "scripts/a.py", NOW - DAY)

        def boom(url, timeout):
            raise OSError("connection refused")

        monkeypatch.setattr(drift, "fetch_health", boom)
        rc = drift.main(["--repo", str(repo), "--ref", "HEAD", "--url", "https://x/health"])
        assert rc == 1
        assert "unreachable" in capsys.readouterr().out


class TestWorkflowWiring:
    WORKFLOW = _ROOT / ".github" / "workflows" / "demo-vm-drift.yml"

    def test_workflow_runs_the_check_with_full_history(self):
        import yaml

        workflow = yaml.safe_load(self.WORKFLOW.read_text())
        job = workflow["jobs"]["drift"]
        steps = job["steps"]
        checkout = next(s for s in steps if s.get("uses", "").startswith("actions/checkout@"))
        # The lag is computed from main's history; a shallow clone cannot see it.
        assert checkout["with"]["fetch-depth"] == 0
        assert any("scripts/ci/check_demo_vm_drift.py" in (s.get("run") or "") for s in steps)
        # Off-box, public endpoint: no secrets, read-only token.
        assert "secrets." not in self.WORKFLOW.read_text()
        assert workflow["permissions"] == {"contents": "read"}
        # yaml parses the bare `on` key as True.
        triggers = workflow.get("on", workflow.get(True))
        assert "schedule" in triggers and "workflow_dispatch" in triggers
