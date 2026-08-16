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
    def test_parses_id_expiry_and_reason_skipping_comments(self):
        import datetime

        text = (
            "# comment line\n"
            "\n"
            "unit-mismatch:radon-ib-gateway.service expires=2026-09-30 repo adds tailscaled ordering\n"
            "not-installed:radon-llm-index.timer expires=2026-12-31 gated on AA signup\n"
        )
        allow = da.parse_allowlist(text)
        entry = allow["unit-mismatch:radon-ib-gateway.service"]
        assert entry["expires"] == datetime.date(2026, 9, 30)
        assert entry["reason"].startswith("repo adds")
        assert allow["not-installed:radon-llm-index.timer"]["expires"] == datetime.date(2026, 12, 31)
        assert len(allow) == 2

    def test_entry_without_expiry_parses_with_none(self):
        allow = da.parse_allowlist("unit-mismatch:radon-x.service legacy reason only\n")
        assert allow["unit-mismatch:radon-x.service"]["expires"] is None
        assert allow["unit-mismatch:radon-x.service"]["reason"] == "legacy reason only"

    def test_malformed_expiry_parses_with_none(self):
        allow = da.parse_allowlist("unit-mismatch:radon-x.service expires=2026-13-45 bad date\n")
        assert allow["unit-mismatch:radon-x.service"]["expires"] is None


class TestAllowlistRatchet:
    """R-058: an allowlist entry is a bounded acknowledgment, not a permanent
    silencer. Expired or unmatched entries must degrade config-drift."""

    import datetime as _dt

    DRIFT_ID = "unit-mismatch:radon-api.service"

    def _drift(self):
        return {"id": self.DRIFT_ID, "detail": "live-only: ExecStart=/old"}

    def _entry(self, expires):
        return {self.DRIFT_ID: {"expires": expires, "reason": "pending privileged unit reinstall"}}

    def test_fresh_entry_still_suppresses(self):
        drifts, allowed = da.partition_allowlisted(
            [self._drift()], self._entry(self._dt.date(2026, 9, 15)),
            today=self._dt.date(2026, 8, 16),
        )
        assert drifts == []
        assert self.DRIFT_ID in allowed

    def test_expired_entry_degrades_and_names_the_unit(self):
        drifts, allowed = da.partition_allowlisted(
            [self._drift()], self._entry(self._dt.date(2026, 9, 15)),
            today=self._dt.date(2026, 9, 16),
        )
        assert allowed == {}
        assert len(drifts) == 1
        assert drifts[0]["id"] == self.DRIFT_ID
        assert "expired 2026-09-15" in drifts[0]["detail"]

    def test_expiry_day_itself_still_suppresses(self):
        drifts, allowed = da.partition_allowlisted(
            [self._drift()], self._entry(self._dt.date(2026, 9, 15)),
            today=self._dt.date(2026, 9, 15),
        )
        assert drifts == []
        assert self.DRIFT_ID in allowed

    def test_entry_without_expiry_is_never_honored(self):
        drifts, allowed = da.partition_allowlisted(
            [self._drift()], self._entry(None), today=self._dt.date(2026, 8, 16)
        )
        assert allowed == {}
        assert len(drifts) == 1
        assert drifts[0]["id"] == self.DRIFT_ID
        assert "no expires=" in drifts[0]["detail"]

    def test_unmatched_entry_is_reported_stale(self):
        drifts, allowed = da.partition_allowlisted(
            [], self._entry(self._dt.date(2026, 9, 15)), today=self._dt.date(2026, 8, 16)
        )
        assert allowed == {}
        assert drifts == [
            {
                "id": f"stale-allowlist:{self.DRIFT_ID}",
                "detail": "allowlist entry matches no observed drift; remove it",
            }
        ]


def test_gather_degrades_on_expired_allowlist_entry(monkeypatch):
    """Acceptance: expired entry -> the drift surfaces (main flips state=error)."""
    def inject_unit_drift(drifts, _known):
        drifts.append({"id": "unit-mismatch:radon-api.service", "detail": "live-only: X"})

    monkeypatch.setattr(da, "_compare_file_pair", lambda *args: None)
    monkeypatch.setattr(da, "_check_compose", lambda drifts: None)
    monkeypatch.setattr(da, "_check_units", inject_unit_drift)
    monkeypatch.setattr(da, "_check_sudoers", lambda drifts: None)
    monkeypatch.setattr(da, "_check_env_invariants", lambda drifts: None)
    monkeypatch.setattr(
        da, "_read_repo",
        lambda rel: "unit-mismatch:radon-api.service expires=2026-08-01 pending reinstall\n",
    )
    drifts, allowed, _known = da.gather()
    assert allowed == {}
    assert drifts and drifts[0]["id"] == "unit-mismatch:radon-api.service"
    assert "expired 2026-08-01" in drifts[0]["detail"]


def test_shipped_allowlist_entries_all_carry_expiries():
    conf = (ROOT / "config" / "drift-allowlist.conf").read_text()
    allow = da.parse_allowlist(conf)
    assert allow, "drift allowlist unexpectedly empty"
    missing = sorted(i for i, entry in allow.items() if entry["expires"] is None)
    assert missing == [], f"allowlist entries without expires=YYYY-MM-DD: {missing}"


class TestUntrackedClassification:
    def test_beta_units_are_known_untracked(self):
        assert da.classify_untracked_unit("radon-beta-api.service") == "known-untracked"
        assert da.classify_untracked_unit("radon-beta-nextjs.service") == "known-untracked"

    def test_other_untracked_units_are_drift(self):
        assert da.classify_untracked_unit("radon-mystery.service") == "drift"


def test_git_repo_is_parent_of_cloud_root():
    assert da.GIT_REPO == da.REPO.parent


class TestCloudRootInput:
    """The entrypoint is installed root-owned outside the checkout it audits.

    Deriving the compared tree from __file__ would make the relocated copy
    compare /usr/local against nothing, so the checkout root is an explicit
    input: argument first, then environment, then the canonical host path.
    """

    def test_argument_wins(self, tmp_path):
        assert da.resolve_cloud_root(
            ["drift_audit.py", str(tmp_path)],
            {"RADON_CLOUD_ROOT": "/somewhere/else"},
        ) == tmp_path

    def test_environment_is_the_fallback(self, tmp_path):
        assert da.resolve_cloud_root(
            ["drift_audit.py"], {"RADON_CLOUD_ROOT": str(tmp_path)}
        ) == tmp_path

    def test_default_is_the_canonical_checkout(self):
        assert da.resolve_cloud_root(["drift_audit.py"], {}) == da.DEFAULT_CLOUD_ROOT

    def test_a_nonexistent_root_is_refused(self, tmp_path):
        import pytest

        with pytest.raises(RuntimeError):
            da.resolve_cloud_root(["drift_audit.py", str(tmp_path / "missing")], {})

    def test_symlinked_checkout_root_is_refused(self, tmp_path):
        import pytest

        target = tmp_path / "target"
        target.mkdir()
        link = tmp_path / "cloud"
        link.symlink_to(target, target_is_directory=True)
        with pytest.raises(RuntimeError, match="symlink"):
            da.resolve_cloud_root(["drift_audit.py", str(link)], {})

    def test_setting_the_root_moves_the_compared_tree(self, tmp_path):
        original = da.REPO
        try:
            da.set_cloud_root(tmp_path)
            assert da.REPO == tmp_path
            assert da.GIT_REPO == tmp_path.parent
        finally:
            da.set_cloud_root(original)


class TestEnvKeysAreReadAsData:
    """Root reads two keys out of the radon-owned env file; it never inherits it.

    systemd would merge every line of an EnvironmentFile into root's
    environment, so an appended LD_PRELOAD or PATH line in a 0600 radon:radon
    file would be root code execution on the next timer tick.
    """

    def _env_file(self, tmp_path, body):
        path = tmp_path / ".env"
        path.write_text(body, encoding="utf-8")
        return path

    def test_only_allowlisted_keys_are_returned(self, tmp_path):
        path = self._env_file(
            tmp_path,
            "TURSO_DB_URL=libsql://db.example\n"
            "LD_PRELOAD=/home/radon/evil.so\n"
            "PATH=/home/radon/bin\n"
            "TURSO_AUTH_TOKEN=secret-token\n",
        )
        values = da.load_env_keys(path, da.DB_CREDENTIAL_KEYS)
        assert values == {
            "TURSO_DB_URL": "libsql://db.example",
            "TURSO_AUTH_TOKEN": "secret-token",
        }
        assert "LD_PRELOAD" not in values and "PATH" not in values

    def test_comments_blank_lines_and_quotes_are_handled(self, tmp_path):
        path = self._env_file(
            tmp_path,
            "# comment\n\nTURSO_AUTH_TOKEN='tok$en-with-dollar'\n",
        )
        values = da.load_env_keys(path, da.DB_CREDENTIAL_KEYS)
        assert values["TURSO_AUTH_TOKEN"] == "tok$en-with-dollar"

    def test_a_missing_file_is_not_fatal(self, tmp_path):
        assert da.load_env_keys(tmp_path / "absent", da.DB_CREDENTIAL_KEYS) == {}

    def test_process_environment_wins_over_the_file(self, tmp_path):
        path = self._env_file(
            tmp_path, "TURSO_DB_URL=libsql://file\nTURSO_AUTH_TOKEN=file-token\n"
        )
        resolved = da.resolve_db_credentials(
            {"TURSO_DB_URL": "libsql://exported", "RADON_ENV_FILE": str(path)}
        )
        assert resolved["TURSO_DB_URL"] == "libsql://exported"
        assert resolved["TURSO_AUTH_TOKEN"] == "file-token"

    def test_no_env_file_configured_falls_back_to_the_environment_alone(self):
        assert da.resolve_db_credentials({"TURSO_AUTH_TOKEN": "tok"}) == {
            "TURSO_AUTH_TOKEN": "tok"
        }


def test_gather_does_not_mix_general_git_dirtiness_into_config(monkeypatch):
    monkeypatch.setattr(da, "_check_repo_dirty", lambda _drifts: (_ for _ in ()).throw(
        AssertionError("general worktree state is not deployed config drift")
    ))
    monkeypatch.setattr(da, "_compare_file_pair", lambda *args: None)
    monkeypatch.setattr(da, "_check_compose", lambda drifts: None)
    monkeypatch.setattr(da, "_check_units", lambda drifts, known: None)
    monkeypatch.setattr(da, "_check_sudoers", lambda drifts: None)
    monkeypatch.setattr(da, "_check_env_invariants", lambda drifts: None)
    monkeypatch.setattr(da, "_read", lambda path: "")
    assert da.gather() == ([], {}, [])


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


def test_repo_artifact_reader_rejects_final_and_nested_symlinks(tmp_path, monkeypatch):
    repo = tmp_path / "cloud"
    repo.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    secret = outside / "secret"
    secret.write_text("root-only-secret")
    (repo / "safe").write_text("canonical")
    (repo / "linked-file").symlink_to(secret)
    (repo / "linked-dir").symlink_to(outside, target_is_directory=True)
    monkeypatch.setattr(da, "REPO", repo)

    assert da._read_repo("safe") == "canonical"
    assert da._read_repo("linked-file") is None
    assert da._read_repo("linked-dir/secret") is None
    assert da._read_repo("../outside/secret") is None


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

    def test_allowlisted_pending_is_reported_distinctly_from_clean(self):
        summary = da.build_last_error(
            [],
            allowed={"unit-mismatch:radon-api.service": "until 2026-09-15: pending reinstall"},
            known_untracked=[],
        )
        assert summary["allowlisted_pending"] == ["unit-mismatch:radon-api.service"]
        assert "allowlisted-pending" in summary["note"]
        assert "unit-mismatch:radon-api.service" in summary["note"]


def test_health_write_retries_transport_failures(monkeypatch):
    calls = []
    monkeypatch.setattr(da.time, "sleep", lambda seconds: None)

    def flaky(*args):
        calls.append(args)
        if len(calls) < 3:
            raise TimeoutError("slow")

    monkeypatch.setattr(da, "write_service_health", flaky)
    da.write_service_health_with_retry("ok", None, "start")
    assert len(calls) == 3


def test_clean_audit_fails_when_health_result_cannot_publish(monkeypatch):
    monkeypatch.setattr(da, "resolve_cloud_root", lambda *_args: da.REPO)
    monkeypatch.setattr(da, "gather", lambda: ([], {}, []))
    monkeypatch.setattr(
        da, "write_service_health_with_retry", lambda *_args: (_ for _ in ()).throw(TimeoutError("offline"))
    )
    assert da.main() == 1
