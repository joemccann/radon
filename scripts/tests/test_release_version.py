"""Semver fold pinned at 0.7.0 / eb5e5206."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

import release_version as rv

BASE = (0, 7, 0)


def test_base_pin_is_the_0_7_0_commit() -> None:
    assert rv.BASE_SHA == "eb5e52066462374b3426c0ec874c6c9024bb5b8b"
    assert rv.BASE_VERSION == BASE


@pytest.mark.parametrize(
    ("subject", "body", "files", "kind"),
    [
        ("feat(web): show release", "", ["web/components/Header.tsx"], "minor"),
        ("fix: close the stale tab", "", ["scripts/api/server.py"], "patch"),
        ("feat(web)!: drop the old header", "", ["web/components/Header.tsx"], "major"),
        ("fix: rename a field", "BREAKING CHANGE: clients must reload", ["web/lib/releaseStatus.ts"], "major"),
        ("refactor: split the rail", "", ["web/components/Header.tsx"], "patch"),
        ("chore: refresh a timer", "", ["cloud/scripts/deploy.sh"], "patch"),
        ("docs: describe the chip", "", ["docs/brand-identity.md"], None),
        ("test: cover the fold", "", ["scripts/tests/test_release_version.py"], None),
        ("ci: watch the version", "", [".github/workflows/ci.yml"], None),
        ("feat: tests only", "", ["web/tests/release-status.test.tsx", "web/e2e/release-status.spec.ts"], None),
        ("chore: bump the recorded version", "", ["package.json", "web/package.json", "bun.lock"], None),
    ],
)
def test_classify(subject: str, body: str, files: list[str], kind: str | None) -> None:
    assert rv.classify(subject, body, files) == kind


def test_fold_applies_minor_then_patch_from_the_pin() -> None:
    commits = [
        ("feat(web): show release", "", ["web/components/ReleaseStatus.tsx"]),
        ("fix: keep the sha", "", ["web/app/api/version/route.ts"]),
        ("docs: note it", "", ["README.md"]),
    ]
    assert rv.format_version(rv.fold(commits, BASE)) == "0.8.1"


def test_a_breaking_runtime_change_from_0_7_0_is_1_0_0() -> None:
    commits = [("feat!: replace the client", "BREAKING CHANGE: bundle shape", ["web/lib/releaseStatus.ts"])]
    assert rv.format_version(rv.fold(commits, BASE)) == "1.0.0"


def _git(repo: Path, *args: str) -> str:
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "Radon Test",
        "GIT_AUTHOR_EMAIL": "radon-test@example.com",
        "GIT_COMMITTER_NAME": "Radon Test",
        "GIT_COMMITTER_EMAIL": "radon-test@example.com",
    }
    return subprocess.check_output(["git", "-C", str(repo), *args], text=True, env=env).strip()


def _commit(repo: Path, message: str, files: dict[str, str]) -> None:
    for name, body in files.items():
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
        _git(repo, "add", name)
    _git(repo, "commit", "-m", message)


def _repo(tmp_path: Path) -> tuple[Path, str]:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _commit(
        repo,
        "chore: pin 0.7.0",
        {"package.json": '{ "version": "0.7.0" }\n', "web/package.json": '{ "version": "0.7.0" }\n'},
    )
    return repo, _git(repo, "rev-parse", "HEAD")


def test_check_fails_until_the_package_version_matches_the_fold(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo, base = _repo(tmp_path)
    monkeypatch.setenv("RADON_RELEASE_BASE_SHA", base)
    monkeypatch.setenv("RADON_RELEASE_BASE_VERSION", "0.7.0")
    _commit(repo, "feat(web): show release", {"web/components/ReleaseStatus.tsx": "export {}\n"})
    with pytest.raises(SystemExit, match="require 0.8.0"):
        rv.check(repo)
    assert rv.main(["--write", "--repo", str(repo)]) == 0
    assert rv.check(repo) == "0.8.0"
    assert json.loads((repo / "package.json").read_text())["version"] == "0.8.0"
    assert json.loads((repo / "web" / "package.json").read_text())["version"] == "0.8.0"


def test_json_falls_back_to_package_json_without_the_base(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo, _base = _repo(tmp_path)
    monkeypatch.setenv("RADON_RELEASE_BASE_SHA", "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")
    identity = rv.build_identity(repo, "local")
    assert identity["version"] == "0.7.0"
    assert identity["source"] == "package"
    assert identity["builtAt"] == ""
    assert identity["channel"] == "local"


@pytest.mark.parametrize('history', ['missing', 'unrelated'])
def test_ci_version_keeps_package_identity_when_original_history_is_unavailable(tmp_path, monkeypatch, history):
    repo, _ = _repo(tmp_path)
    _commit(repo, 'feat(web): synthetic runtime', {'web/example.ts': 'export {}\n'})
    if history == 'unrelated':
        _git(repo, 'checkout', '--orphan', 'unrelated')
        _git(repo, 'commit', '-m', 'unrelated historical root')
        unavailable = _git(repo, 'rev-parse', 'HEAD')
        _git(repo, 'checkout', 'main')
    else:
        unavailable = 'a' * 40
    monkeypatch.setenv('RADON_RELEASE_BASE_SHA', unavailable)
    sha = _git(repo, 'rev-parse', 'HEAD')
    assert rv.ci_version(repo, sha) == '0.7.0'
    assert rv.build_identity(repo, 'production')['source'] == 'package'


@pytest.mark.parametrize('version', ['', 'v0.7.0', '0.7', '01.7.0', '0.7.0-extra', '0.7.0\ninjected'])
def test_ci_version_refuses_malformed_package_fallback(tmp_path, monkeypatch, version):
    repo, _ = _repo(tmp_path)
    monkeypatch.setenv('RADON_RELEASE_BASE_SHA', 'a' * 40)
    (repo/'web/package.json').write_text(json.dumps({'version': version}))
    with pytest.raises(ValueError):
        rv.ci_version(repo, _git(repo, 'rev-parse', 'HEAD'))


@pytest.mark.parametrize('sha', ['', 'abc1234', 'A' * 40, 'x' * 40, 'a' * 40 + '\ninjected'])
def test_ci_version_refuses_malformed_expected_sha(tmp_path, sha):
    repo, _ = _repo(tmp_path)
    with pytest.raises(ValueError):
        rv.ci_version(repo, sha)


def test_ci_version_refuses_another_commit_even_with_a_valid_label(tmp_path):
    repo, _ = _repo(tmp_path)
    with pytest.raises(ValueError):
        rv.ci_version(repo, 'b' * 40)


def test_ci_cli_emits_only_the_validated_build_label(tmp_path, monkeypatch, capsys):
    repo, _ = _repo(tmp_path)
    monkeypatch.setenv('RADON_RELEASE_BASE_SHA', 'a' * 40)
    sha = _git(repo, 'rev-parse', 'HEAD')
    assert rv.main(['--repo', str(repo), '--ci-version', '--expected-sha', sha]) == 0
    assert capsys.readouterr().out == '0.7.0\n'


def test_ci_version_refuses_an_unknown_baked_sha(tmp_path, monkeypatch):
    repo, _ = _repo(tmp_path)
    monkeypatch.setenv('RADON_RELEASE_BASE_SHA', 'a' * 40)
    monkeypatch.setattr(rv, 'git_sha', lambda repo: 'unknown')
    with pytest.raises(ValueError, match='build identity'):
        rv.ci_version(repo, _git(repo, 'rev-parse', 'HEAD'))
