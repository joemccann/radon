"""REL-052 tranche C — R-114, R-115, R-116.

Fault injection for three ops-plane bounds the 2026-08-22 delta left open:

* R-114: the retired `radon-beta` sudoers fragment red-flags `config-drift`
  forever because only *units* have a known-untracked classification.
* R-115: the "a fix has deployed since" oneshot rerun keys off a marker that
  every green deploy touches, so a unit failing for an environmental reason
  is re-run once per unrelated merge to main, indefinitely.
* R-116: both weekend plists drive the same clone with no mutual exclusion,
  so a late audit and remediate can `git clean -fdq` each other mid-write.
"""
import importlib.util
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, REPO / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


da = _load("drift_audit_relc", "cloud/scripts/drift_audit.py")


# --------------------------------------------------------------------------
# R-114 — retired beta sudoers fragments are notes, not config-drift
# --------------------------------------------------------------------------
class TestRetiredBetaSudoers:
    def test_beta_fragment_is_classified_known_untracked(self):
        assert da.classify_untracked_sudoers("radon-beta") == "known-untracked"

    def test_other_untracked_fragments_are_still_drift(self):
        assert da.classify_untracked_sudoers("radon-mystery") == "drift"

    def test_check_sudoers_notes_the_beta_fragment_instead_of_drifting(
        self, tmp_path, monkeypatch
    ):
        live = tmp_path / "sudoers.d"
        live.mkdir()
        (live / "radon-beta").write_text("radon-beta ALL=(root) NOPASSWD: /bin/true\n")
        (live / "radon-mystery").write_text("radon ALL=(root) NOPASSWD: /bin/false\n")
        monkeypatch.setattr(da, "SUDOERS_DIR", live)
        monkeypatch.setattr(da, "REPO", tmp_path / "empty-repo")

        drifts: list[dict] = []
        known: list[str] = []
        da._check_sudoers(drifts, known)

        assert known == ["radon-beta"]
        assert [d["id"] for d in drifts] == ["untracked-sudoers:radon-mystery"]

    def test_gather_threads_sudoers_notes_into_known_untracked(self, monkeypatch):
        monkeypatch.setattr(da, "FILE_PAIRS", ())
        monkeypatch.setattr(da, "_check_compose", lambda drifts: None)
        monkeypatch.setattr(da, "_check_units", lambda drifts, known: None)
        monkeypatch.setattr(da, "_check_env_invariants", lambda drifts: None)
        monkeypatch.setattr(da, "_check_repo_dirty", lambda drifts: None)
        monkeypatch.setattr(da, "_read_repo", lambda rel: "")

        def fake_sudoers(drifts, known):
            known.append("radon-beta")

        monkeypatch.setattr(da, "_check_sudoers", fake_sudoers)
        drifts, _allowed, known = da.gather()
        assert drifts == []
        assert known == ["radon-beta"]


# --------------------------------------------------------------------------
# R-115 — the oneshot rerun is bounded per unit, not just per deploy
# --------------------------------------------------------------------------
NOW = datetime(2026, 8, 23, 12, 0, tzinfo=timezone.utc)


class _Proc:
    def __init__(self, stdout="", returncode=0):
        self.stdout = stdout
        self.returncode = returncode


def _runner_factory(calls):
    def runner(args):
        calls.append(list(args))
        if args[0] != "show":
            return _Proc()
        target = args[1]
        if target.endswith(".timer"):
            return _Proc("NextElapseUSecRealtime=Mon 2026-08-24 14:00:00 UTC\n")
        return _Proc(
            "ActiveState=failed\nResult=exit-code\n"
            "InactiveEnterTimestamp=Sun 2026-08-23 09:00:00 UTC\n"
        )

    return runner


@pytest.fixture
def responder(monkeypatch):
    mod = _load("grok_page_responder_relc", "scripts/grok_page_responder.py")
    monkeypatch.setattr(mod, "autoship_enabled", lambda: True)
    monkeypatch.setattr(mod, "DEPLOY_TRANSITION_JOURNAL", Path("/nonexistent/journal"))
    monkeypatch.setattr(
        mod.units_mod,
        "_read_deploy_evidence",
        lambda: {"marker_mtime": NOW - timedelta(hours=1)},
    )
    return mod


class TestRerunPerUnitBound:
    def test_a_unit_already_rerun_today_is_not_rerun_again(self, responder, monkeypatch):
        monkeypatch.setattr(
            responder.pages_mod,
            "reruns_since",
            lambda service, since: responder.MAX_RERUNS_PER_UNIT_PER_DAY,
        )
        calls: list[list[str]] = []
        page = {"kind": "unit", "service": "radon-ivrank.service"}
        assert (
            responder.attempt_oneshot_rerun(
                page, now=NOW, systemctl_runner=_runner_factory(calls)
            )
            is None
        )
        assert not any(c[0] == "start" for c in calls)

    def test_first_rerun_of_the_day_still_runs(self, responder, monkeypatch):
        monkeypatch.setattr(responder.pages_mod, "reruns_since", lambda service, since: 0)
        calls: list[list[str]] = []
        page = {"kind": "unit", "service": "radon-ivrank.service"}
        outcome = responder.attempt_oneshot_rerun(
            page, now=NOW, systemctl_runner=_runner_factory(calls)
        )
        assert outcome is not None
        assert outcome[0] == "restarted_unit"

    def test_the_per_unit_bound_counts_only_this_units_reruns(self, responder, monkeypatch):
        seen: list[tuple[str, datetime]] = []

        def spy(service, since):
            seen.append((service, since))
            return 0

        monkeypatch.setattr(responder.pages_mod, "reruns_since", spy)
        responder.attempt_oneshot_rerun(
            {"kind": "unit", "service": "radon-skew.service"},
            now=NOW,
            systemctl_runner=_runner_factory([]),
        )
        assert seen and seen[0][0] == "radon-skew.service"
        assert seen[0][1] == responder._utc_day_start(NOW)

    def test_an_unavailable_count_stands_the_rerun_down(self, responder, monkeypatch):
        monkeypatch.setattr(
            responder.pages_mod,
            "reruns_since",
            lambda service, since: (_ for _ in ()).throw(RuntimeError("turso down")),
        )
        calls: list[list[str]] = []
        assert (
            responder.attempt_oneshot_rerun(
                {"kind": "unit", "service": "radon-skew.service"},
                now=NOW,
                systemctl_runner=_runner_factory(calls),
            )
            is None
        )
        assert not any(c[0] == "start" for c in calls)


class TestRerunsSinceFailsClosed:
    def test_a_transport_failure_returns_a_blocking_count(self, monkeypatch):
        from watchdog import pages as pages_mod

        def boom(*_a, **_k):
            raise RuntimeError("hrana down")

        monkeypatch.setitem(
            sys.modules,
            "db.hrana_http",
            type(sys)("db.hrana_http"),
        )
        sys.modules["db.hrana_http"].hrana_query = boom
        count = pages_mod.reruns_since("radon-skew.service", since=NOW)
        assert count >= pages_mod._UNKNOWN_ACTION_COUNT

    def test_it_counts_only_rerun_completions_for_that_service(self, monkeypatch):
        from watchdog import pages as pages_mod

        captured: dict = {}

        def fake_query(sql, params):
            captured["sql"] = sql
            captured["params"] = params
            return [[3]]

        stub = type(sys)("db.hrana_http")
        stub.hrana_query = fake_query
        monkeypatch.setitem(sys.modules, "db.hrana_http", stub)
        assert pages_mod.reruns_since("radon-skew.service", since=NOW) == 3
        assert "service = ?" in captured["sql"]
        assert "radon-skew.service" in captured["params"]
        assert "restarted_unit" in " ".join(str(p) for p in captured["params"])


# --------------------------------------------------------------------------
# R-116 — the weekend runner clone is single-writer
# --------------------------------------------------------------------------
WRAPPER = REPO / "scripts" / "reliability_weekend.sh"


class TestWeekendRunnerMutualExclusion:
    def test_the_wrapper_takes_a_lock_before_it_resets_the_tree(self):
        text = WRAPPER.read_text()
        lock_at = text.index("acquire_runner_lock")
        reset_at = text.index("git reset --hard --quiet origin/main")
        assert lock_at < reset_at, "the tree is reset before the lock is taken"

    def test_the_lock_is_a_portable_mkdir_not_flock(self):
        text = WRAPPER.read_text()
        assert "flock -" not in text, "flock(1) does not exist on the macOS runner"
        assert "mkdir" in text

    def test_a_held_lock_refuses_the_second_runner(self, tmp_path):
        lock = tmp_path / "weekend.lock"
        lock.mkdir()
        import os

        (lock / "pid").write_text(f"{os.getpid()}\n")  # this test process is alive
        rc = subprocess.run(
            ["bash", "-c", f'source "{WRAPPER}" --lock-lib-only; acquire_runner_lock "{lock}"'],
            capture_output=True,
            text=True,
        )
        assert rc.returncode != 0

    def test_a_stale_lock_from_a_dead_pid_is_reclaimed(self, tmp_path):
        lock = tmp_path / "weekend.lock"
        lock.mkdir()
        (lock / "pid").write_text("999999\n")  # not a live pid
        rc = subprocess.run(
            ["bash", "-c", f'source "{WRAPPER}" --lock-lib-only; acquire_runner_lock "{lock}"'],
            capture_output=True,
            text=True,
        )
        assert rc.returncode == 0, rc.stderr

    def test_an_unheld_lock_is_acquired(self, tmp_path):
        lock = tmp_path / "weekend.lock"
        rc = subprocess.run(
            ["bash", "-c", f'source "{WRAPPER}" --lock-lib-only; acquire_runner_lock "{lock}"'],
            capture_output=True,
            text=True,
        )
        assert rc.returncode == 0, rc.stderr
        assert (lock / "pid").exists()
