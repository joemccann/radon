"""Open PRs are brought up to date with main without the operator clicking Update branch."""
import json
import plistlib
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import pr_branch_autoupdate as autoupdate  # noqa: E402

REPO = "joemccann/radon"
SHA = "a" * 40


def _row(number, **overrides):
    row = {"number": number, "headRefOid": f"{number:040d}", "isDraft": False,
           "isCrossRepository": False, "baseRefName": "main", "author": {"login": "joemccann"}}
    row.update(overrides)
    return row


class FakeGh:
    def __init__(self, rows, behind=None, failing_updates=(), list_rc=0):
        self.rows, self.behind = rows, behind or {}
        self.failing_updates, self.list_rc = set(failing_updates), list_rc
        self.calls = []

    def __call__(self, args):
        self.calls.append(args)
        if args[:2] == ["pr", "list"]:
            return self.list_rc, json.dumps(self.rows)
        if args[0] == "api" and "/compare/" in args[1]:
            head = args[1].rsplit("...", 1)[1]
            return 0, str(self.behind.get(head, 0))
        if args[:3] == ["api", "--method", "PUT"]:
            number = int(args[3].split("/")[-2])
            return (1, "") if number in self.failing_updates else (0, "{}")
        raise AssertionError(f"unexpected gh call {args}")

    def updates(self):
        return [c for c in self.calls if c[:3] == ["api", "--method", "PUT"]]


def test_a_pr_behind_main_is_updated_at_its_exact_head():
    gh = FakeGh([_row(7, headRefOid=SHA)], behind={SHA: 3})

    results = autoupdate.update_behind(gh)

    assert gh.updates() == [["api", "--method", "PUT", f"repos/{REPO}/pulls/7/update-branch",
                             "-f", f"expected_head_sha={SHA}"]]
    assert results == [{"pr": 7, "status": "updated"}]


def test_a_pr_level_with_main_is_left_alone():
    gh = FakeGh([_row(7)], behind={})

    assert autoupdate.update_behind(gh) == []
    assert gh.updates() == []


def test_drafts_forks_dependabot_and_other_bases_are_never_touched():
    rows = [_row(1, isDraft=True), _row(2, isCrossRepository=True),
            _row(3, author={"login": "app/dependabot"}), _row(4, baseRefName="release")]
    gh = FakeGh(rows, behind={r["headRefOid"]: 5 for r in rows})

    assert autoupdate.update_behind(gh) == []
    assert [c for c in gh.calls if c[:2] != ["pr", "list"]] == []


def test_a_conflicting_pr_is_reported_and_the_rest_still_update():
    rows = [_row(1), _row(2)]
    gh = FakeGh(rows, behind={r["headRefOid"]: 1 for r in rows}, failing_updates={1})

    results = autoupdate.update_behind(gh)

    assert results == [{"pr": 1, "status": "update-failed"}, {"pr": 2, "status": "updated"}]


def test_a_failed_pr_list_exits_nonzero(capsys):
    gh = FakeGh([], list_rc=1)

    assert autoupdate.main(gh) == 1
    assert gh.updates() == []


def test_main_prints_one_json_line_per_update(capsys):
    gh = FakeGh([_row(9, headRefOid=SHA)], behind={SHA: 2})

    assert autoupdate.main(gh) == 0
    lines = capsys.readouterr().out.splitlines()
    assert [json.loads(line) for line in lines] == [{"pr": 9, "status": "updated"}]


def test_the_agent_runs_the_installed_copy_every_five_minutes():
    raw = (ROOT / "config/com.radon.pr-branch-autoupdate.plist").read_bytes()
    plist = plistlib.loads(raw)
    assert plist["Label"] == "com.radon.pr-branch-autoupdate"
    assert plist["StartInterval"] == 300
    assert plist["ProgramArguments"][-1] == "__HOME__/.local/share/radon/pr-autoupdate/pr_branch_autoupdate.py"
    assert "-I" in plist["ProgramArguments"]


def test_the_setup_script_installs_a_copy_outside_the_checkout():
    script = (ROOT / "scripts/setup_pr_branch_autoupdate.sh").read_text()
    assert subprocess.run(["bash", "-n", str(ROOT / "scripts/setup_pr_branch_autoupdate.sh")]).returncode == 0
    assert "scripts/pr_branch_autoupdate.py" in script
    assert ".local/share/radon/pr-autoupdate" in script
