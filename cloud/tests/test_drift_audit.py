"""Tests for scripts/drift_audit.py pure logic (no system access)."""

import importlib.util
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "drift_audit", ROOT / "scripts" / "drift_audit.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


da = _load_module()


REPO_UNIT_WITH_INLINE_STARTLIMIT = """\
[Unit]
Description=Radon FastAPI
# DUR-02: brake crash loops
StartLimitIntervalSec=300
StartLimitBurst=5

[Service]
Type=simple
ExecStart=/usr/bin/true
"""

LIVE_UNIT_WITHOUT_STARTLIMIT = """\
[Unit]
Description=Radon FastAPI

[Service]
Type=simple
ExecStart=/usr/bin/true
"""

LIVE_STARTLIMIT_DROPIN = """\
# DUR-02 live mirror of the repo's inline StartLimit settings.
[Unit]
StartLimitIntervalSec=300
StartLimitBurst=5
"""


class TestParseUnitText:
    def test_ignores_comments_and_blank_lines(self):
        items = da.parse_unit_text(REPO_UNIT_WITH_INLINE_STARTLIMIT)
        assert ("Unit", "Description", "Radon FastAPI") in items
        assert ("Unit", "StartLimitIntervalSec", "300") in items
        assert not any("DUR-02" in str(key) for (_, key, _) in items)

    def test_directives_are_section_scoped(self):
        items = da.parse_unit_text(LIVE_STARTLIMIT_DROPIN)
        assert ("Unit", "StartLimitIntervalSec", "300") in items
        assert ("Service", "StartLimitIntervalSec", "300") not in items

    def test_line_continuations_are_joined(self):
        items = da.parse_unit_text("[Service]\nExecStart=/bin/echo \\\n  hello\n")
        assert ("Service", "ExecStart", "/bin/echo hello") in items


class TestEffectiveCompare:
    def test_dur02_inline_vs_dropin_is_not_drift(self):
        live = da.merge_unit_counters(
            [LIVE_UNIT_WITHOUT_STARTLIMIT, LIVE_STARTLIMIT_DROPIN]
        )
        repo = da.merge_unit_counters([REPO_UNIT_WITH_INLINE_STARTLIMIT])
        assert da.unit_counter_diff(live, repo) == ""

    def test_directive_difference_is_drift(self):
        live = da.merge_unit_counters(["[Unit]\nAfter=docker.service\n"])
        repo = da.merge_unit_counters(
            ["[Unit]\nAfter=docker.service tailscaled.service\nWants=tailscaled.service\n"]
        )
        detail = da.unit_counter_diff(live, repo)
        assert "tailscaled" in detail
        assert "repo-only" in detail
        assert "live-only" in detail

    def test_identical_value_duplicated_by_dropin_is_not_drift(self):
        # Unit reinstalled with inline StartLimit while the older live
        # drop-in still carries the SAME values: idempotent in systemd
        # (last definition wins), so not drift.
        live = da.merge_unit_counters(
            [REPO_UNIT_WITH_INLINE_STARTLIMIT, LIVE_STARTLIMIT_DROPIN]
        )
        repo = da.merge_unit_counters([REPO_UNIT_WITH_INLINE_STARTLIMIT])
        assert da.unit_counter_diff(live, repo) == ""

    def test_comment_only_difference_is_not_drift(self):
        live = da.merge_unit_counters(["[Unit]\n# old comment\nAfter=docker.service\n"])
        repo = da.merge_unit_counters(["[Unit]\n# new comment\nAfter=docker.service\n"])
        assert da.unit_counter_diff(live, repo) == ""


class TestAllowlist:
    def test_parses_id_and_reason_skipping_comments(self):
        text = (
            "# comment line\n"
            "\n"
            "unit-mismatch:radon-ib-gateway.service repo adds tailscaled ordering\n"
            "not-installed:radon-llm-index.timer gated on AA signup\n"
        )
        allow = da.parse_allowlist(text)
        assert allow["unit-mismatch:radon-ib-gateway.service"].startswith("repo adds")
        assert "not-installed:radon-llm-index.timer" in allow
        assert len(allow) == 2


class TestUntrackedClassification:
    def test_beta_units_are_known_untracked(self):
        assert da.classify_untracked_unit("radon-beta-api.service") == "known-untracked"
        assert da.classify_untracked_unit("radon-beta-nextjs.service") == "known-untracked"

    def test_other_untracked_units_are_drift(self):
        assert da.classify_untracked_unit("radon-mystery.service") == "drift"


def test_git_repo_is_parent_of_cloud_root():
    assert da.GIT_REPO == da.REPO.parent


def test_symlinked_unit_is_not_a_canonical_regular_artifact(tmp_path, monkeypatch):
    services = tmp_path / "services"
    live = tmp_path / "systemd"
    services.mkdir()
    live.mkdir()
    (services / "radon-x.timer").write_text("[Timer]\nOnCalendar=hourly\n")
    legacy = tmp_path / "legacy.timer"
    legacy.write_text("[Timer]\nOnCalendar=hourly\n")
    (live / "radon-x.timer").symlink_to(legacy)
    monkeypatch.setattr(da, "REPO", tmp_path)
    monkeypatch.setattr(da, "SYSTEMD_DIR", live)
    drifts, known = [], []
    da._check_units(drifts, known)
    assert drifts[0]["id"] == "symlink-unit:radon-x.timer"


class TestEnvFileGuard:
    def test_audit_never_touches_env_files(self):
        assert da.is_env_path("/home/radon/radon-cloud/.env")
        assert da.is_env_path("/home/radon/radon-cloud/.env.beta")
        assert not da.is_env_path("/home/radon/radon-cloud/.env.example.notreally/file.txt")
        for live, repo_rel, _label in da.FILE_PAIRS:
            assert not da.is_env_path(live)
            assert not da.is_env_path(repo_rel)


def test_gateway_control_helper_is_drift_audited():
    assert (
        "/usr/local/bin/radon-ib-gateway-control",
        "scripts/ib-gateway-control.sh",
        "ib-gateway-control",
    ) in da.FILE_PAIRS


class TestSummary:
    def test_error_summary_is_compact_and_capped(self):
        drifts = [
            {"id": f"file-mismatch:thing{i}", "detail": "x" * 500} for i in range(20)
        ]
        summary = da.build_last_error(drifts, allowed={}, known_untracked=[])
        assert len(summary["summary"]) <= da.SUMMARY_CAP
        assert summary["drift_count"] == 20

    def test_clean_run_notes_known_untracked(self):
        summary = da.build_last_error(
            [], allowed={"not-installed:radon-llm-index.timer": "gated"},
            known_untracked=["radon-beta-api.service"],
        )
        assert summary["drift_count"] == 0
        assert "radon-beta-api.service" in summary["note"]
        assert summary["allowed_count"] == 1
