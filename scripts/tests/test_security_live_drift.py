"""Weekly live-setup drift check: repo vs live, read-only, no model calls."""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import plistlib
import re
import subprocess

import pytest

import security_live_drift as sld
import install_security_live_drift as installer

REPO_ROOT = Path(__file__).resolve().parents[2]
BASELINE = json.loads((REPO_ROOT / "docs/security-live-baseline.json").read_text())
LOGIN = "operator-login@example.test"
KEY_MATERIAL = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFIXTUREKEYMATERIALdoNotLeak"


# ---------------------------------------------------------------- fixtures --

def _live_policy() -> dict:
    """The checked-in policy as the Tailscale API would serve it."""
    text = (REPO_ROOT / "cloud/tailscale/policy.hujson").read_text()
    return json.loads(json.dumps(sld.parse_hujson(text)).replace(sld.OPERATOR_PLACEHOLDER, LOGIN))


def _expected_ruleset(repo: str, ruleset_id: int) -> dict:
    for row in BASELINE["github"][repo]["rulesets"]:
        if row["id"] == ruleset_id:
            return copy.deepcopy(row)
    raise KeyError(ruleset_id)


def _github_world() -> dict:
    """gh api path -> payload, mirroring the committed baseline."""
    world: dict = {}
    for repo, expected in BASELINE["github"].items():
        meta = {"full_name": repo, "permissions": {"admin": True}}
        # GitHub omits the field on a private repo without Advanced Security.
        if expected["security_and_analysis"] is not None:
            meta["security_and_analysis"] = {
                name: {"status": status} for name, status in expected["security_and_analysis"].items()}
        world[f"repos/{repo}"] = meta
        world[f"repos/{repo}/rulesets"] = [
            {"id": row["id"], "name": row["name"], "enforcement": row["enforcement"]} for row in expected["rulesets"]
        ]
        for row in expected["rulesets"]:
            world[f"repos/{repo}/rulesets/{row['id']}"] = {**copy.deepcopy(row), "node_id": "x", "_links": {}}
        protection = expected["branch_protection"]
        if protection["protected"]:
            world[f"repos/{repo}/branches/main/protection"] = {
                "required_status_checks": {
                    "strict": protection["strict"],
                    "checks": [{"context": c, "app_id": a} for c, a in protection["required_checks"]],
                },
                "enforce_admins": {"enabled": protection["enforce_admins"]},
                "allow_force_pushes": {"enabled": protection["allow_force_pushes"]},
                "allow_deletions": {"enabled": protection["allow_deletions"]},
            }
        else:
            world[f"repos/{repo}/branches/main/protection"] = sld.GhError(404, "Branch not protected")
        world[f"repos/{repo}/keys?per_page=100"] = [
            {"id": 1000 + i, "title": key["title"], "read_only": key["read_only"], "key": KEY_MATERIAL,
             "url": "https://api.github.com/x", "verified": True}
            for i, key in enumerate(expected["deploy_keys"])
        ]
    return world


class Fakes:
    def __init__(self, *, acl=None, acl_error=None, probe=(0, "file=PRESENT\nmode=ENFORCING\nunit=ABSENT\npeers=PRESENT\n"), world=None):
        self.acl = _live_policy() if acl is None else acl
        self.acl_error = acl_error
        self.probe = probe
        self.world = _github_world() if world is None else world
        self.gh_calls: list[str] = []
        self.probe_calls = 0

    def tailscale_acl(self):
        if self.acl_error is not None:
            raise self.acl_error
        return json.dumps(self.acl)

    def trust_probe(self):
        self.probe_calls += 1
        return self.probe

    def gh(self, path):
        self.gh_calls.append(path)
        value = self.world.get(path, sld.GhError(404, "Not Found"))
        if isinstance(value, Exception):
            raise value
        return copy.deepcopy(value)


def _rows(fakes, checks=sld.ALL_CHECKS):
    return sld.run_checks(REPO_ROOT, BASELINE, fakes, checks)


def _by_check(rows):
    return {row["check"]: row for row in rows}


# ------------------------------------------------------------------ HuJSON --

def test_hujson_strips_line_and_block_comments_and_trailing_commas():
    text = '''// header
    {
      "a": [1, 2, 3,],  // trailing
      /* block
         comment */ "b": {"url": "http://x//y", "s": "/* not a comment */",},
      "c": "quote \\" // still string",
    }
    '''
    assert sld.parse_hujson(text) == {
        "a": [1, 2, 3],
        "b": {"url": "http://x//y", "s": "/* not a comment */"},
        "c": 'quote " // still string',
    }


def test_checked_in_policy_parses():
    policy = sld.parse_hujson((REPO_ROOT / "cloud/tailscale/policy.hujson").read_text())
    assert "grants" in policy and "tagOwners" in policy


# ----------------------------------------------------------- tailnet policy --

def test_policy_equal_when_live_binds_placeholder_and_reorders_grants():
    live = _live_policy()
    live["grants"] = list(reversed(live["grants"]))
    row = _by_check(_rows(Fakes(acl=live)))["tailnet_policy"]
    assert row["status"] == "ok", row


def test_policy_drift_on_extra_live_grant_never_echoes_acl_values():
    live = _live_policy()
    live["grants"].append({"src": ["*"], "dst": ["*"], "ip": ["*"]})
    row = _by_check(_rows(Fakes(acl=live)))["tailnet_policy"]
    assert row["status"] == "drift"
    assert row["counts"]["only_live"] == 1 and row["counts"]["only_repo"] == 0
    assert "grants" in " ".join(row["detail_private"])
    assert LOGIN not in json.dumps(row)


def test_policy_drift_when_operator_login_differs_between_occurrences():
    live = _live_policy()
    live["groups"]["group:operator"] = ["someone-else@example.test"]
    row = _by_check(_rows(Fakes(acl=live)))["tailnet_policy"]
    assert row["status"] == "drift"
    assert "someone-else" not in json.dumps(row) and LOGIN not in json.dumps(row)


def test_missing_tailscale_token_is_operator_required_and_exits_75(tmp_path, capsys):
    fakes = Fakes(acl_error=sld.AuthMissing("TAILSCALE_API_TOKEN"))
    code = sld.main(["--repo-root", str(REPO_ROOT), "--checks", "tailnet_policy", "--out", str(tmp_path / "r.md")], fetchers=fakes)
    assert code == 75
    out = capsys.readouterr().out
    assert "operator_required=1" in out


def test_live_fetcher_without_token_raises_auth_missing_without_network(monkeypatch):
    def boom(*_a, **_k):
        raise AssertionError("network touched without a token")
    monkeypatch.setattr(sld.urllib.request, "urlopen", boom)
    fetchers = sld.LiveFetchers(env={})
    with pytest.raises(sld.AuthMissing):
        fetchers.tailscale_acl()


def test_token_file_must_be_owner_only(tmp_path):
    token = tmp_path / "ts.token"
    token.write_text("tskey-api-fixture\n")
    token.chmod(0o644)
    with pytest.raises(sld.AuthMissing):
        sld.LiveFetchers(env={"TAILSCALE_API_TOKEN_FILE": str(token)}).tailscale_token()
    token.chmod(0o600)
    assert sld.LiveFetchers(env={"TAILSCALE_API_TOKEN_FILE": str(token)}).tailscale_token() == "tskey-api-fixture"


def test_tailscale_401_is_operator_required():
    row = _by_check(_rows(Fakes(acl_error=sld.HttpError(401))))["tailnet_policy"]
    assert row["status"] == "operator_required"
    assert "policy_file:read" in " ".join(row["detail_private"])


# --------------------------------------------------------------- trust mode --

def test_trust_probe_command_never_reads_the_value():
    cmd = sld.TRUST_PROBE_COMMAND
    assert "RADON_TAILNET_TRUST_MODE" in cmd and "/etc/radon/env" in cmd
    # Every reference to the file is a quiet grep or a grep piped into a
    # quiet grep; nothing prints a matched line, cats the file or echoes $.
    for forbidden in ("cat ", "printenv", "env |", "sed ", "awk ", "cut ", "echo $", "printf", "${", "source", ". /etc"):
        assert forbidden not in cmd, forbidden
    for segment in cmd.split(";"):
        if "grep" in segment and "| grep" not in segment:
            assert "grep -Eq" in segment or "grep -q" in segment, segment
    echoed = set(re.findall(r"echo ([^\s;]+)", cmd))
    assert echoed <= {"file=PRESENT", "file=ABSENT", "file=UNREADABLE", "mode=LOG", "mode=ENFORCING",
                      "unit=PRESENT", "unit=ABSENT", "peers=PRESENT", "peers=ABSENT"}


def test_live_trust_probe_runs_batchmode_root_ssh_with_the_fixed_command(monkeypatch):
    seen = {}

    class Done:
        returncode = 0
        stdout = "file=ABSENT\nunit=ABSENT\npeers=ABSENT\n"

    def fake_run(argv, **kwargs):
        seen["argv"] = argv
        return Done()

    monkeypatch.setattr(sld.subprocess, "run", fake_run)
    sld.LiveFetchers(env={}, app_host="radon-app").trust_probe()
    argv = seen["argv"]
    assert argv[0] == "/usr/bin/ssh" and "BatchMode=yes" in " ".join(argv)
    assert argv[-2:] == ["root@radon-app", sld.TRUST_PROBE_COMMAND]


def test_trust_mode_enforcing_is_ok():
    row = _by_check(_rows(Fakes()))["tailnet_trust"]
    assert row["status"] == "ok", row


@pytest.mark.parametrize("probe", [
    (0, "file=ABSENT\nunit=ABSENT\npeers=ABSENT\n"),
    (0, "file=PRESENT\nmode=LOG\nunit=ABSENT\npeers=PRESENT\n"),
])
def test_trust_mode_absent_or_log_is_drift(probe):
    row = _by_check(_rows(Fakes(probe=probe)))["tailnet_trust"]
    assert row["status"] == "drift"


@pytest.mark.parametrize("body, expected", [
    ("FOO=1\n", ["file=ABSENT", "peers=ABSENT", "unit=ABSENT"]),
    ("RADON_TAILNET_TRUST_MODE=log\n", ["file=PRESENT", "mode=LOG", "peers=ABSENT", "unit=ABSENT"]),
    ("RADON_TAILNET_TRUST_MODE='LOG'\n", ["file=PRESENT", "mode=LOG", "peers=ABSENT", "unit=ABSENT"]),
    ("RADON_TAILNET_TRUST_MODE=\n", ["file=PRESENT", "mode=LOG", "peers=ABSENT", "unit=ABSENT"]),
    ("RADON_TAILNET_TRUST_MODE=enforce\nRADON_TRUSTED_TAILNET_PEERS=100.64.0.9/32\n",
     ["file=PRESENT", "mode=ENFORCING", "peers=PRESENT", "unit=ABSENT"]),
    ("RADON_TAILNET_TRUST_MODE=enforce\nRADON_TAILNET_TRUST_MODE=log\n",
     ["file=PRESENT", "mode=LOG", "peers=ABSENT", "unit=ABSENT"]),
])
def test_trust_probe_command_classifies_in_a_real_shell(tmp_path, body, expected):
    env_file = tmp_path / "env"
    env_file.write_text(body)
    cmd = sld.TRUST_PROBE_COMMAND.replace("/etc/radon/env", str(env_file))
    # No radon-api unit on the test host: the unit check degrades to ABSENT.
    done = subprocess.run(["/bin/sh", "-c", cmd], capture_output=True, text=True, env={"PATH": "/usr/bin:/bin"})
    assert done.returncode == 0
    assert done.stdout.split() == expected
    assert "100.64" not in done.stdout and "enforce\n" not in done.stdout


def test_trust_probe_ssh_failure_is_operator_required():
    row = _by_check(_rows(Fakes(probe=(255, ""))))["tailnet_trust"]
    assert row["status"] == "operator_required"


def test_trust_probe_unexpected_output_is_error_and_never_echoed():
    row = _by_check(_rows(Fakes(probe=(0, "RADON_TAILNET_TRUST_MODE=sekrit-value\n"))))["tailnet_trust"]
    assert row["status"] == "error"
    assert "sekrit-value" not in json.dumps(row)


# ------------------------------------------------------------------- GitHub --

def test_github_matches_baseline():
    rows = _rows(Fakes(), checks=("github",))
    assert {row["status"] for row in rows} == {"ok"}, [r for r in rows if r["status"] != "ok"]
    assert len(rows) == 4 * len(BASELINE["github"])


def test_deploy_key_material_never_in_any_output(tmp_path, capsys):
    world = _github_world()
    world["repos/joemccann/radon/keys?per_page=100"].append(
        {"id": 77, "title": "rogue", "read_only": False, "key": KEY_MATERIAL})
    fakes = Fakes(world=world)
    out = tmp_path / "r.md"
    code = sld.main(["--repo-root", str(REPO_ROOT), "--checks", "github", "--json", "--out", str(out)], fetchers=fakes)
    assert code == 1
    blob = capsys.readouterr().out + out.read_text()
    assert "rogue" in out.read_text()
    assert "AAAAC3" not in blob and "ssh-ed25519" not in blob


def test_json_stdout_is_one_parseable_document(tmp_path, capsys):
    code = sld.main(["--repo-root", str(REPO_ROOT), "--checks", "github", "--json",
                     "--out", str(tmp_path / "r.md")], fetchers=Fakes(world=_github_world()))
    captured = capsys.readouterr()
    assert json.loads(captured.out)["rows"]
    assert captured.err.strip().startswith("radon-live-drift ")
    assert code == 0


def test_baseline_matches_documented_merge_gate():
    main = BASELINE["github"]["joemccann/radon"]
    protection = main["branch_protection"]
    assert protection["strict"] is True and protection["enforce_admins"] is True
    declared = json.loads((REPO_ROOT / ".github/required-status-checks.json").read_text())["contexts"]
    assert sorted(name for name, _ in protection["required_checks"]) == sorted(declared)
    assert all(type(app) is int and app > 0 for _, app in protection["required_checks"])
    assert {r["id"]: r["name"] for r in main["rulesets"]} == {24143098: "main-review", 24587201: "runner-bot-nightly-only"}


def test_ruleset_rule_change_is_drift():
    world = _github_world()
    path = "repos/joemccann/radon/rulesets/24143098"
    world[path]["rules"][0]["parameters"]["required_approving_review_count"] = 0
    row = _by_check(_rows(Fakes(world=world), checks=("github",)))["github:joemccann/radon:rulesets"]
    assert row["status"] == "drift"
    assert "24143098" in " ".join(row["detail_private"])


def test_lost_required_check_is_drift():
    world = _github_world()
    protection = world["repos/joemccann/radon/branches/main/protection"]
    protection["required_status_checks"]["checks"].pop()
    protection["enforce_admins"]["enabled"] = False
    row = _by_check(_rows(Fakes(world=world), checks=("github",)))["github:joemccann/radon:branch_protection"]
    assert row["status"] == "drift"
    assert row["counts"]["missing_checks"] == 1


def test_github_403_is_operator_required_naming_scope():
    world = _github_world()
    world["repos/joemccann/radon/keys?per_page=100"] = sld.GhError(403, "Resource not accessible")
    row = _by_check(_rows(Fakes(world=world), checks=("github",)))["github:joemccann/radon:deploy_keys"]
    assert row["status"] == "operator_required"
    assert "Administration: read" in " ".join(row["detail_private"])


def test_non_admin_token_is_operator_required_without_admin_calls():
    world = _github_world()
    world["repos/joemccann/radon"]["permissions"]["admin"] = False
    del world["repos/joemccann/radon"]["security_and_analysis"]
    fakes = Fakes(world=world)
    rows = _by_check(_rows(fakes, checks=("github",)))
    for sub in ("rulesets", "branch_protection", "deploy_keys", "secret_scanning"):
        assert rows[f"github:joemccann/radon:{sub}"]["status"] == "operator_required"
    assert not any(c.startswith("repos/joemccann/radon/keys") for c in fakes.gh_calls)


def test_push_protection_disabled_is_drift():
    world = _github_world()
    world["repos/joemccann/radon"]["security_and_analysis"]["secret_scanning_push_protection"]["status"] = "disabled"
    row = _by_check(_rows(Fakes(world=world), checks=("github",)))["github:joemccann/radon:secret_scanning"]
    assert row["status"] == "drift"


# ------------------------------------------------------------ output / exit --

def test_public_line_is_counts_only():
    world = _github_world()
    world["repos/joemccann/radon/keys?per_page=100"].append({"id": 424242, "title": "rogue", "read_only": False, "key": KEY_MATERIAL})
    rows = _rows(Fakes(world=world, probe=(255, "")))
    line = sld.public_line(rows, "2026-10-31T00:00:00Z")
    assert line.startswith("radon-live-drift ts=2026-10-31T00:00:00Z ok=")
    tokens = line.split()
    assert [t.split("=")[0] for t in tokens[1:]] == ["ts", "ok", "drift", "operator_required", "error"]
    for forbidden in ("radon-app", "joemccann", "424242", "rogue", "tag:", "100.", LOGIN):
        assert forbidden not in line


def test_exit_codes_all_ok_then_drift_then_incomplete(tmp_path, capsys):
    args = ["--repo-root", str(REPO_ROOT), "--out", str(tmp_path / "r.md")]
    assert sld.main(args, fetchers=Fakes()) == 0
    live = _live_policy()
    live["acls"] = [{"action": "accept", "src": ["*"], "dst": ["*:*"]}]
    assert sld.main(args, fetchers=Fakes(acl=live)) == 1
    assert sld.main(args, fetchers=Fakes(probe=(255, ""))) == 75
    with pytest.raises(SystemExit) as usage:
        sld.main(["--checks", "nonsense"], fetchers=Fakes())
    assert usage.value.code == 2


def test_report_follows_template_shape(tmp_path):
    out = tmp_path / "r.md"
    sld.main(["--repo-root", str(REPO_ROOT), "--checks", "github,tailnet_trust", "--out", str(out)], fetchers=Fakes(probe=(255, "")))
    text = out.read_text()
    lines = text.splitlines()
    assert lines[0].startswith("# radon-live-drift weekly ")
    assert lines[2] == "## Summary"
    assert "| Field | Value |" in text and "| Coverage limits |" in text
    assert "`tailnet_policy` (not selected)" in text and "`tailnet_trust` (operator_required)" in text
    assert "\u2014" not in text  # no em dashes
    assert oct(out.stat().st_mode & 0o777) == "0o600"


def test_offline_fixture_dir_round_trip(tmp_path, capsys):
    fixture = tmp_path / "fx"
    fixture.mkdir()
    (fixture / "tailscale_acl.json").write_text(json.dumps(_live_policy()))
    (fixture / "trust_probe.txt").write_text("0\nfile=PRESENT\nmode=ENFORCING\nunit=ABSENT\npeers=PRESENT\n")
    gh = fixture / "gh"
    gh.mkdir()
    for path, payload in _github_world().items():
        name = sld.fixture_name(path)
        if isinstance(payload, sld.GhError):
            (gh / f"{name}.error").write_text(f"{payload.status} {payload.message}")
        else:
            (gh / f"{name}.json").write_text(json.dumps(payload))
    code = sld.main(["--repo-root", str(REPO_ROOT), "--offline-fixture", str(fixture), "--out", str(tmp_path / "r.md")])
    assert code == 0, capsys.readouterr().out


# ---------------------------------------------------------------- installer --

def test_installer_writes_weekly_operator_launchagent(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(installer.subprocess, "run", lambda *a, **k: calls.append(a))
    home = tmp_path / "home"
    home.mkdir()
    destination = tmp_path / "share" / "radon-security-live-drift"
    destination.parent.mkdir()
    plist_path = installer.install(source_root=REPO_ROOT, destination=destination, home=home, python=Path("/usr/bin/python3"))
    config = plistlib.loads(plist_path.read_bytes())
    assert config["Label"] == "com.radon.security-live-drift"
    assert set(config["StartCalendarInterval"]) == {"Weekday", "Hour", "Minute"}
    assert "StartInterval" not in config and config.get("RunAtLoad") is not True
    args = config["ProgramArguments"]
    assert args[:2] == ["/usr/bin/python3", "-I"]
    current = destination / "current"
    assert args[2] == str(current / "scripts/security_live_drift.py")
    assert "--repo-root" in args and "--out-dir" in args and "--json" not in args
    for rel in ("scripts/security_live_drift.py", "docs/security-live-baseline.json", "cloud/tailscale/policy.hujson"):
        assert (current / rel).read_bytes() == (REPO_ROOT / rel).read_bytes()
    assert oct(plist_path.stat().st_mode & 0o777) == "0o600"
    assert not any("bootstrap" in " ".join(map(str, a[0])) for a in calls)


def test_installer_refuses_root(monkeypatch, tmp_path):
    monkeypatch.setattr(installer.os, "getuid", lambda: 0)
    with pytest.raises(PermissionError):
        installer.install(source_root=REPO_ROOT, destination=tmp_path / "d", home=tmp_path, python=Path("/usr/bin/python3"))


def test_installer_refuses_a_group_writable_destination(monkeypatch, tmp_path):
    monkeypatch.setattr(installer.subprocess, "run", lambda *a, **k: None)
    shared = tmp_path / "share"
    shared.mkdir()
    shared.chmod(0o775)
    with pytest.raises(PermissionError):
        installer.install(source_root=REPO_ROOT, destination=shared / "radon-security-live-drift",
                          home=tmp_path, python=Path("/usr/bin/python3"))
    assert not (shared / "radon-security-live-drift").exists()
