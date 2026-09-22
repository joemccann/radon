"""Codemap publishing must skip semantic no-ops before mutating remote PRs.

The shell is exercised with stubbed git, Python, GitHub and sleep commands.
No generator, suite, remote Git operation or GitHub mutation runs here.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "codemap_nightly.sh"


def run_runner(tmp_path: Path, *, check_rc: int = 0, publish_rc: int = 0):
    clone = tmp_path / "clone"
    clone.mkdir()
    binaries = tmp_path / "bin"
    binaries.mkdir()
    calls = tmp_path / "calls.jsonl"
    stub = binaries / "stub.py"
    stub.write_text(
        f"#!{sys.executable}\n" + r'''
import json
import os
from pathlib import Path
import sys

name, args = Path(sys.argv[0]).name, sys.argv[1:]
with open(os.environ["CALLS_FILE"], "a") as output:
    output.write(json.dumps([name, *args]) + "\n")
if name == "git":
    if args[:2] == ["diff", "--quiet"]:
        sys.exit(1)
    if args[:1] == ["rev-parse"]:
        print("01234567")
elif name == "python3.13" and args[:1] == ["scripts/nightly_publish.py"]:
    if args[1] == "check":
        sys.exit(int(os.environ["CHECK_RC"]))
    if args[1] == "publish":
        rc = int(os.environ["PUBLISH_RC"])
        print(json.dumps({"status": "published" if rc == 0 else "noop" if rc == 3 else "error"}))
        sys.exit(rc)
elif name == "gh" and args[:2] == ["pr", "list"]:
    print("42")
'''
    )
    stub.chmod(0o755)
    for name in ("git", "python3.13", "gh", "sleep"):
        (binaries / name).symlink_to(stub)
    env = {
        **os.environ,
        "PATH": f"{binaries}{os.pathsep}{os.environ.get('PATH', '')}",
        "RADON_CODEMAP_REPO": str(clone),
        "CALLS_FILE": str(calls),
        "CHECK_RC": str(check_rc),
        "PUBLISH_RC": str(publish_rc),
    }
    result = subprocess.run(["bash", str(SCRIPT)], env=env, capture_output=True, text=True, timeout=15)
    recorded = [json.loads(line) for line in calls.read_text().splitlines()]
    return result, recorded


def test_timestamp_only_refresh_does_not_publish_or_supersede(tmp_path: Path):
    result, calls = run_runner(tmp_path, check_rc=3)
    assert result.returncode == 0, result.stderr
    assert "0 PR(s), nothing to merge" in result.stdout
    assert ["python3.13", "scripts/nightly_publish.py", "check", "--base", "HEAD", "--index"] in calls
    assert not any(call[0] == "gh" for call in calls)
    assert not any(call[:2] in (["git", "commit"], ["git", "push"]) for call in calls)
    assert not any(call[:3] == ["git", "checkout", "-q"] for call in calls)


def test_classifier_error_stops_before_remote_mutations(tmp_path: Path):
    result, calls = run_runner(tmp_path, check_rc=1)
    assert result.returncode == 1
    assert not any(call[0] == "gh" or call[:2] == ["git", "push"] for call in calls)


def test_real_graph_change_uses_guarded_publish_before_superseding(tmp_path: Path):
    result, calls = run_runner(tmp_path)
    assert result.returncode == 0, result.stderr
    check = next(i for i, call in enumerate(calls) if call[:3] == ["python3.13", "scripts/nightly_publish.py", "check"])
    publish = next(i for i, call in enumerate(calls) if call[:3] == ["python3.13", "scripts/nightly_publish.py", "publish"])
    close = next(i for i, call in enumerate(calls) if call[:3] == ["gh", "pr", "close"])
    assert check < publish < close
    assert not any(call[:2] == ["git", "push"] for call in calls)
    assert "--body-file" in calls[publish]
    assert not any(call[:3] == ["gh", "pr", "create"] for call in calls)
    branch = calls[publish][calls[publish].index("--head") + 1]
    listing = next(call for call in calls if call[:3] == ["gh", "pr", "list"])
    assert f'.headRefName != "{branch}"' in listing[-1]
    assert any(call[:3] == ["gh", "pr", "checks"] for call in calls)
    assert any(call[:3] == ["gh", "pr", "merge"] for call in calls)


@pytest.mark.parametrize("publish_rc,expected_rc", [(3, 0), (1, 1)])
def test_publish_recheck_preserves_older_prs_when_nothing_is_published(tmp_path: Path, publish_rc: int, expected_rc: int):
    result, calls = run_runner(tmp_path, publish_rc=publish_rc)
    assert result.returncode == expected_rc, result.stderr
    assert not any(call[0] == "gh" for call in calls)
    if publish_rc == 3:
        assert "0 PR(s), nothing to merge" in result.stdout
