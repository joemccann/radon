"""R-399 / R-400 / R-401 / REL-140: a verdict is only as good as its evidence's age.

R-399: `degraded` was made sufficient to clear a validated off-box
`aggregate_down`, justified by "sidecar/broker only; ping already proved the
edge is serving" -- which describes the STALE row being cleared, not current
host state. `_read_local_aggregate` never inspects `generated_at`, so an
aggregate produced before the off-box sample can clear it, and nothing checks
that the degradation really is dependency-only.

R-400: `flex_sftp_pull.run()` has no outer try/finally, so a Turso failure inside
`ingest_xml` exits the unit non-zero with NO health row written -- the previous
`ok` row stays newest and the 26h/4d windows keep the service green (NF-9).

R-401: `aggregate_state` refuses unit evidence older than
`UNIT_STATE_MAX_AGE_SECS` but folds probe results in with no age at all, and
`ProbeCache.refresh_once` swallows every exception -- so a dead cache thread
serves an hours-old probe dict as current.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
# Sibling-module imports (test_flex_sftp_pull) resolve from the test directory,
# not from the repo root where pytest actually runs.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from health_service import probes, serve  # noqa: E402
from watchdog import external_probe  # noqa: E402
from health_probe import reader  # noqa: E402

T0 = datetime(2026, 8, 29, 12, 0, 0, tzinfo=timezone.utc)


def _iso(moment: datetime) -> str:
    return moment.isoformat().replace("+00:00", "Z")


class TestRecoveryEvidenceMustBeFresh:
    def _drive(self, monkeypatch, payload, *, age_seconds=600.0):
        monkeypatch.setattr(
            external_probe.turso_http, "fetch_external_probe", lambda **_k: {"row": 1}
        )
        monkeypatch.setattr(
            external_probe.reader,
            "classify_external_probe",
            lambda _row, now: {
                "verdict": reader.VERDICT_DOWN,
                "reason": "aggregate_down",
                "age_seconds": age_seconds,
            },
        )
        monkeypatch.setattr(external_probe, "_read_local_aggregate", lambda *a, **k: payload)
        monkeypatch.setattr(external_probe, "_transition_journal_age_seconds", lambda _n: None)
        return external_probe.check_external_probe(now=T0)

    def test_a_stale_local_aggregate_cannot_clear_an_offbox_down(self, monkeypatch):
        """Produced BEFORE the off-box sample: it cannot describe the recovery."""
        stale = {
            "schema_version": 2,
            "ok": False,
            "overall_state": "degraded",
            "generated_at": _iso(T0 - timedelta(minutes=30)),
            "probes": {"api": {"state": "up"}},
            "units": {"radon-monitor.service": {"state": "down"}},
        }
        outcome = self._drive(monkeypatch, stale, age_seconds=600.0)
        assert outcome.fired is True, outcome.message
        assert outcome.severity == "P1", outcome.severity

    def test_a_fresh_dependency_only_degraded_still_clears(self, monkeypatch):
        fresh = {
            "schema_version": 2,
            "ok": False,
            "overall_state": "degraded",
            "generated_at": _iso(T0 - timedelta(seconds=5)),
            "probes": {"api": {"state": "up"}, "ib-gateway": {"state": "down"}},
            "units": {
                "radon-api.service": {"state": "up"},
                "radon-monitor.service": {"state": "down"},
            },
        }
        outcome = self._drive(monkeypatch, fresh, age_seconds=600.0)
        assert outcome.fired is False, outcome.message
        assert outcome.status == "healthy"

    def test_a_degraded_hiding_a_serving_path_failure_cannot_clear(self, monkeypatch):
        """`degraded` is only recovery evidence when the serving path is green."""
        mixed = {
            "schema_version": 2,
            "ok": False,
            "overall_state": "degraded",
            "generated_at": _iso(T0 - timedelta(seconds=5)),
            "probes": {"api": {"state": "down"}},
            "units": {"radon-monitor.service": {"state": "down"}},
        }
        outcome = self._drive(monkeypatch, mixed, age_seconds=600.0)
        assert outcome.fired is True, outcome.message

    def test_a_fresh_up_aggregate_still_clears(self, monkeypatch):
        up = {
            "schema_version": 2,
            "ok": True,
            "overall_state": "up",
            "generated_at": _iso(T0 - timedelta(seconds=5)),
            "probes": {"api": {"state": "up"}},
            "units": {"radon-api.service": {"state": "up"}},
        }
        outcome = self._drive(monkeypatch, up, age_seconds=600.0)
        assert outcome.fired is False, outcome.message


class TestProbeEvidenceHasAnAge:
    def test_snapshot_reports_its_age(self):
        cache = serve.ProbeCache(fetch_fn=lambda: {"api": {"state": "up"}})
        value, age = cache.snapshot()
        assert value == {} and age is None, (value, age)
        cache.refresh_once()
        value, age = cache.snapshot()
        assert value == {"api": {"state": "up"}}
        assert age is not None and age < 5

    def test_a_frozen_probe_cache_reports_unknown_not_up(self, monkeypatch):
        """A dead `health-probe-cache` thread must not serve stale probes as current."""
        cache = serve.ProbeCache(fetch_fn=lambda: {"api": {"state": "up"}})
        cache.refresh_once()
        monkeypatch.setattr(
            serve.time, "time", lambda: cache._updated + probes.PROBE_STATE_MAX_AGE_SECS + 10
        )
        value, age = cache.snapshot()
        assert age > probes.PROBE_STATE_MAX_AGE_SECS

        state = probes.aggregate_state(
            value,
            {"radon-api.service": {"state": "up"}},
            "ok",
            2.0,
            probes_age_secs=age,
        )
        assert state == "unknown", state

    def test_fresh_probe_evidence_is_still_trusted(self):
        state = probes.aggregate_state(
            {"api": {"state": "up"}},
            {"radon-api.service": {"state": "up"}},
            "ok",
            2.0,
            probes_age_secs=1.0,
        )
        assert state == "up", state

    def test_status_response_reports_unknown_on_a_frozen_cache(self, monkeypatch):
        cache = serve.ProbeCache(fetch_fn=lambda: {"api": {"state": "up"}})
        cache.refresh_once()
        monkeypatch.setattr(
            serve.time, "time", lambda: cache._updated + probes.PROBE_STATE_MAX_AGE_SECS + 10
        )

        class _Units:
            @staticmethod
            def snapshot():
                return {"radon-api.service": {"state": "up"}}, 2.0

        _code, body = serve.status_response(cache.snapshot, _Units())
        assert body["overall_state"] == "unknown", body["overall_state"]


class TestFlexPullAlwaysHeartbeats:
    def test_an_unexpected_exception_writes_an_error_row(self, monkeypatch, tmp_path):
        import flex_sftp_pull as pull
        from test_flex_sftp_pull import AFTER_FIRST_DELIVERY, FakeSftp, _ssh_config

        beats: list[tuple] = []
        monkeypatch.setattr(pull, "_heartbeat", lambda state, error=None: beats.append((state, error)))
        monkeypatch.setattr(pull, "nightly_period_ok", lambda _x: True)
        monkeypatch.setattr(pull, "classify_flex_xml", lambda _x: "trades")

        def _boom(*_a, **_k):
            raise RuntimeError("turso down")

        config = _ssh_config(tmp_path / "ssh_config")
        inbox = tmp_path / "inbox"
        inbox.mkdir()

        code = pull.run(
            config=config,
            inbox=inbox,
            runner=FakeSftp({"activity.gpg": b"<FlexQueryResponse/>"}),
            decrypt=lambda data, **k: data.decode(),
            ingest=_boom,
            now=AFTER_FIRST_DELIVERY,
        )
        assert code == 1
        assert beats and beats[-1][0] == "error", beats

    def test_a_raise_outside_every_handler_still_heartbeats(self, monkeypatch, tmp_path):
        """`_ensure_inbox` / `retain_newest_gpg` sit outside every per-file handler."""
        import flex_sftp_pull as pull
        from test_flex_sftp_pull import AFTER_FIRST_DELIVERY, FakeSftp, _ssh_config

        beats: list[tuple] = []
        monkeypatch.setattr(pull, "_heartbeat", lambda state, error=None: beats.append((state, error)))
        monkeypatch.setattr(pull, "nightly_period_ok", lambda _x: True)
        monkeypatch.setattr(pull, "classify_flex_xml", lambda _x: "trades")
        monkeypatch.setattr(pull, "retain_newest_gpg", lambda _inbox: (_ for _ in ()).throw(OSError("disk full")))

        config = _ssh_config(tmp_path / "ssh_config")
        inbox = tmp_path / "inbox"
        inbox.mkdir()

        code = pull.run(
            config=config,
            inbox=inbox,
            runner=FakeSftp({"activity.gpg": b"<FlexQueryResponse/>"}),
            decrypt=lambda data, **k: data.decode(),
            ingest=lambda xml_text, source_path="": {"ok": True, "outcome": "applied"},
            now=AFTER_FIRST_DELIVERY,
        )
        assert code == 1
        assert beats and beats[-1][0] == "error", beats

    def test_a_clean_run_still_heartbeats_ok(self, monkeypatch, tmp_path):
        import flex_sftp_pull as pull
        from test_flex_sftp_pull import AFTER_FIRST_DELIVERY, FakeSftp, _ssh_config

        beats: list[tuple] = []
        monkeypatch.setattr(pull, "_heartbeat", lambda state, error=None: beats.append((state, error)))
        monkeypatch.setattr(pull, "nightly_period_ok", lambda _x: True)
        monkeypatch.setattr(pull, "classify_flex_xml", lambda _x: "trades")

        config = _ssh_config(tmp_path / "ssh_config")
        inbox = tmp_path / "inbox"
        inbox.mkdir()

        code = pull.run(
            config=config,
            inbox=inbox,
            runner=FakeSftp({"activity.gpg": b"<FlexQueryResponse/>"}),
            decrypt=lambda data, **k: data.decode(),
            ingest=lambda xml_text, source_path="": {"ok": True, "outcome": "applied"},
            now=AFTER_FIRST_DELIVERY,
        )
        assert code == 0
        assert [state for state, _ in beats] == ["ok"], beats
