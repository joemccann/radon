"""Root-cause-aware alert grouping in the watchdog.

When FastAPI ``/health`` reports the IB Gateway session is
``awaiting_2fa`` or ``unreachable``, the watchdog must collapse the N
individual stale/error alerts on IB-dependent services into ONE
grouped Pushover message. The individual ``service_health`` rows still
write through normally — only the external push channel is gated.

Services with ``requires_ib=False`` (newsfeed, replica-watchdog,
cash-flow-sync, etc.) continue to alert per-service even when IB is
the suspected upstream root cause; their failure is independent.

Threshold: at least 2 IB-dependent services must be degraded in the
same cycle before grouping kicks in. A single IB-dependent failure
might be a one-off network blip — collapsing it into a noisy "IB down"
message would over-report. Two simultaneously degraded IB services is
a real signal.

Cooldown still applies to the grouped key (``ib-gateway-grouped``,
severity ``P1``) so we don't paste the same message every 5 minutes
during a multi-hour 2FA window.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest


def _seed(db_conn, service: str, state: str, updated_at: datetime, error: dict | None = None):
    db_conn.execute(
        """
        INSERT OR REPLACE INTO service_health
          (service, state, last_attempt_started_at, last_attempt_finished_at,
           last_error, updated_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            service,
            state,
            None,
            updated_at.isoformat().replace("+00:00", "Z"),
            json.dumps(error) if error else None,
            updated_at.isoformat().replace("+00:00", "Z"),
        ),
    )
    db_conn.commit()


def _push_calls(http_calls: list) -> list:
    """Filter the fake-http-post log to only Pushover calls.

    ``http_calls`` is a list of ``(url, payload)`` tuples appended by
    the test's ``fake_http_post``. Each entry's ``[0]`` is the URL
    string, ``[1]`` is the JSON payload dict.
    """
    return [c for c in http_calls if "pushover" in c[0]]


def _ungroup_messages(push_calls: list) -> list[str]:
    return [c[1].get("message", "") for c in push_calls]


@pytest.fixture
def fresh_now() -> datetime:
    # 11:00 ET = 15:00 UTC (EDT). Inside market hours AND past the 35m open-bell
    # grace, so the intraday scanners (cri-scan/vcg-scan) are genuinely stale
    # here when seeded old — the grouping logic under test needs real staleness.
    return datetime(2026, 5, 13, 15, 0, tzinfo=timezone.utc)


class TestGroupedAlertOnIbAwaiting2fa:
    """Three IB services + one non-IB service stale; IB Gateway is
    awaiting_2fa → one grouped Pushover for the IB cohort + one
    per-service Pushover for the non-IB service.
    """

    def test_groups_ib_services_into_single_alert(self, db_conn, monkeypatch, fresh_now):
        from watchdog import grouping  # NEW module: implements the IB-aware dispatch path

        monkeypatch.setenv("PUSHOVER_USER", "u")
        monkeypatch.setenv("PUSHOVER_TOKEN", "t")

        # Seed all four intraday IB services as stale. Use 60m so cri-scan
        # (35m window) is past-window along with the 10-15m services.
        for svc in ("vcg-scan", "cri-scan", "orders-sync", "portfolio-sync"):
            _seed(db_conn, svc, "ok", fresh_now - timedelta(minutes=60))

        # Pre-trip hysteresis by running the bucket twice.
        from watchdog import check
        for tick in (fresh_now, fresh_now + timedelta(minutes=5)):
            check.check_bucket(bucket="intraday", now=tick)

        # Now the third call: fired=True on all four IB services.
        report = check.check_bucket(bucket="intraday", now=fresh_now + timedelta(minutes=10))
        fired = [o for o in report.outcomes if o.fired]
        assert len(fired) == 4, "all four IB intraday services should be ready to fire"

        http_calls = []

        def fake_http_post(url, payload, headers=None):
            http_calls.append((url, payload))
            return (200, b"")

        def fake_get_health():
            return {"auth_state": "awaiting_2fa"}

        with patch("watchdog.notify._http_post", side_effect=fake_http_post), \
             patch("watchdog.grouping.fetch_health", side_effect=fake_get_health):
            grouping.dispatch_with_grouping(outcomes=fired, now=fresh_now + timedelta(minutes=10))

        push_calls = _push_calls(http_calls)
        # Exactly ONE Pushover call — the grouped one.
        assert len(push_calls) == 1, f"expected 1 grouped push, got {len(push_calls)}: {push_calls}"
        msg = push_calls[0][1]["message"]
        assert "IB Gateway" in msg
        assert "awaiting_2fa" in msg
        # Mentions all four IB services in the comma-separated list.
        for svc in ("vcg-scan", "cri-scan", "orders-sync", "portfolio-sync"):
            assert svc in msg, f"grouped message missing {svc}: {msg}"
        # Mentions the count.
        assert "4" in msg
        # Operator action hint.
        assert "reset-backoff" in msg or "Approve on phone" in msg

    def test_api_warmup_suppresses_grouped_2fa_push(self, db_conn, monkeypatch, fresh_now):
        """A radon-api restart (a deploy) briefly reports awaiting_2fa during
        pool warmup. The grouped 2FA page must be SUPPRESSED (no push) — there's
        nothing to approve — while the IB failures are still absorbed (no
        per-service spam)."""
        from watchdog import check, grouping

        monkeypatch.setenv("PUSHOVER_USER", "u")
        monkeypatch.setenv("PUSHOVER_TOKEN", "t")

        for svc in ("vcg-scan", "cri-scan", "orders-sync", "portfolio-sync"):
            _seed(db_conn, svc, "ok", fresh_now - timedelta(minutes=60))
        for tick in (fresh_now, fresh_now + timedelta(minutes=5)):
            check.check_bucket(bucket="intraday", now=tick)
        report = check.check_bucket(bucket="intraday", now=fresh_now + timedelta(minutes=10))
        fired = [o for o in report.outcomes if o.fired]

        http_calls = []

        def fake_http_post(url, payload, headers=None):
            http_calls.append((url, payload))
            return (200, b"")

        with patch("watchdog.notify._http_post", side_effect=fake_http_post), \
             patch("watchdog.grouping.fetch_health", return_value={"auth_state": "awaiting_2fa"}), \
             patch("watchdog.grouping._api_recently_restarted", return_value=True):
            grouping.dispatch_with_grouping(outcomes=fired, now=fresh_now + timedelta(minutes=10))

        # Zero pushes — the warmup transient is suppressed, IB services absorbed.
        assert _push_calls(http_calls) == [], (
            f"expected NO push during api warmup, got {_push_calls(http_calls)}"
        )

    def test_non_ib_services_still_alert_per_service(self, db_conn, monkeypatch, fresh_now):
        from watchdog import check, grouping

        monkeypatch.setenv("PUSHOVER_USER", "u")
        monkeypatch.setenv("PUSHOVER_TOKEN", "t")

        # 2 IB-dependent intraday + 1 non-IB error (cash-flow-sync, daily bucket).
        for svc in ("vcg-scan", "cri-scan"):
            _seed(db_conn, svc, "ok", fresh_now - timedelta(minutes=60))
        for svc in ("orders-sync", "portfolio-sync"):
            _seed(db_conn, svc, "ok", fresh_now - timedelta(minutes=2))
        _seed(
            db_conn,
            "cash-flow-sync",
            "error",
            fresh_now,
            error={"message": "Flex 1019 throttle"},
        )

        # Trip hysteresis on intraday (stale).
        for tick in (fresh_now, fresh_now + timedelta(minutes=5)):
            check.check_bucket(bucket="intraday", now=tick)
        # Trip hysteresis on error bucket.
        for tick in (fresh_now, fresh_now + timedelta(minutes=5)):
            check.check_bucket(bucket="error", now=tick)

        intraday_report = check.check_bucket(bucket="intraday", now=fresh_now + timedelta(minutes=10))
        error_report = check.check_bucket(bucket="error", now=fresh_now + timedelta(minutes=10))
        all_fired = [o for o in intraday_report.outcomes if o.fired] + [
            o for o in error_report.outcomes if o.fired and o.service == "cash-flow-sync"
        ]

        http_calls = []

        def fake_http_post(url, payload, headers=None):
            http_calls.append((url, payload))
            return (200, b"")

        with patch("watchdog.notify._http_post", side_effect=fake_http_post), \
             patch("watchdog.grouping.fetch_health", return_value={"auth_state": "awaiting_2fa"}):
            grouping.dispatch_with_grouping(outcomes=all_fired, now=fresh_now + timedelta(minutes=10))

        push_calls = _push_calls(http_calls)
        # One grouped push for IB cohort. cash-flow-sync is P2 (error) — Pushover
        # only fires for P1, so it should NOT appear in push_calls regardless.
        # But its Discord/service_health row still writes through. Verify the
        # grouped push covers vcg+cri, not cash-flow.
        msgs = _ungroup_messages(push_calls)
        ib_grouped = [m for m in msgs if "IB Gateway" in m]
        assert len(ib_grouped) == 1
        assert "vcg-scan" in ib_grouped[0]
        assert "cri-scan" in ib_grouped[0]
        assert "cash-flow-sync" not in ib_grouped[0]


class TestGroupingThreshold:
    """At least 2 IB services must be degraded for grouping to kick in.
    A single IB-dependent stale is more likely a transient blip — fire
    the per-service P1 alert as usual.
    """

    def test_single_ib_service_does_not_group(self, db_conn, monkeypatch, fresh_now):
        from watchdog import check, grouping

        monkeypatch.setenv("PUSHOVER_USER", "u")
        monkeypatch.setenv("PUSHOVER_TOKEN", "t")

        _seed(db_conn, "vcg-scan", "ok", fresh_now - timedelta(minutes=30))
        # Other intraday services are fresh.
        for other in ("cri-scan", "orders-sync", "portfolio-sync"):
            _seed(db_conn, other, "ok", fresh_now - timedelta(minutes=2))

        for tick in (fresh_now, fresh_now + timedelta(minutes=5)):
            check.check_bucket(bucket="intraday", now=tick)
        report = check.check_bucket(bucket="intraday", now=fresh_now + timedelta(minutes=10))
        fired = [o for o in report.outcomes if o.fired]
        assert len(fired) == 1 and fired[0].service == "vcg-scan"

        http_calls = []

        def fake_http_post(url, payload, headers=None):
            http_calls.append((url, payload))
            return (200, b"")

        with patch("watchdog.notify._http_post", side_effect=fake_http_post), \
             patch("watchdog.grouping.fetch_health", return_value={"auth_state": "awaiting_2fa"}):
            grouping.dispatch_with_grouping(outcomes=fired, now=fresh_now + timedelta(minutes=10))

        push_calls = _push_calls(http_calls)
        # 1 per-service alert; no grouping (only one IB service degraded).
        assert len(push_calls) == 1
        msg = push_calls[0][1].get("message", "")
        assert "IB Gateway" not in msg, "single-service alert should be the regular per-service message"
        assert "vcg-scan" in push_calls[0][1].get("title", "")


class TestGroupingSuppressedWhenIbAuthenticated:
    """When auth_state=authenticated, the grouping branch is NOT taken —
    IB is healthy, so each stale service is a real independent failure
    and we want per-service alerts.
    """

    def test_authenticated_ib_keeps_per_service_alerts(self, db_conn, monkeypatch, fresh_now):
        from watchdog import check, grouping

        monkeypatch.setenv("PUSHOVER_USER", "u")
        monkeypatch.setenv("PUSHOVER_TOKEN", "t")

        # 60m past every IB-service window. portfolio-sync stays fresh.
        for svc in ("vcg-scan", "cri-scan", "orders-sync"):
            _seed(db_conn, svc, "ok", fresh_now - timedelta(minutes=60))
        _seed(db_conn, "portfolio-sync", "ok", fresh_now - timedelta(minutes=2))  # fresh

        for tick in (fresh_now, fresh_now + timedelta(minutes=5)):
            check.check_bucket(bucket="intraday", now=tick)
        report = check.check_bucket(bucket="intraday", now=fresh_now + timedelta(minutes=10))
        fired = [o for o in report.outcomes if o.fired]
        assert len(fired) == 3

        http_calls = []

        def fake_http_post(url, payload, headers=None):
            http_calls.append((url, payload))
            return (200, b"")

        with patch("watchdog.notify._http_post", side_effect=fake_http_post), \
             patch(
                 "watchdog.grouping.fetch_health",
                 return_value={"auth_state": "authenticated"},
             ):
            grouping.dispatch_with_grouping(outcomes=fired, now=fresh_now + timedelta(minutes=10))

        push_calls = _push_calls(http_calls)
        # Three per-service alerts; no grouping.
        assert len(push_calls) == 3
        for c in push_calls:
            assert "IB Gateway" not in c[1].get("message", "")


class TestHealthFetchFailure:
    """If FastAPI /health is unreachable we cannot prove IB is the root
    cause. Fall through to per-service alerts so we don't silently
    suppress everything. ``unreachable`` (the env var case) is treated
    distinctly from ``unknown`` (transport failure).
    """

    def test_health_unreachable_falls_back_to_per_service(self, db_conn, monkeypatch, fresh_now):
        from watchdog import check, grouping

        monkeypatch.setenv("PUSHOVER_USER", "u")
        monkeypatch.setenv("PUSHOVER_TOKEN", "t")

        # vcg-scan + cri-scan stale (60m past their respective windows);
        # orders-sync + portfolio-sync fresh so we get exactly 2 fired IB services.
        for svc in ("vcg-scan", "cri-scan"):
            _seed(db_conn, svc, "ok", fresh_now - timedelta(minutes=60))
        for svc in ("orders-sync", "portfolio-sync"):
            _seed(db_conn, svc, "ok", fresh_now - timedelta(minutes=2))

        for tick in (fresh_now, fresh_now + timedelta(minutes=5)):
            check.check_bucket(bucket="intraday", now=tick)
        report = check.check_bucket(bucket="intraday", now=fresh_now + timedelta(minutes=10))
        fired = [o for o in report.outcomes if o.fired]
        assert len(fired) == 2

        http_calls = []

        def fake_http_post(url, payload, headers=None):
            http_calls.append((url, payload))
            return (200, b"")

        # /health raises → grouping helper returns auth_state=unknown.
        def fake_get_health():
            raise RuntimeError("connection refused")

        with patch("watchdog.notify._http_post", side_effect=fake_http_post), \
             patch("watchdog.grouping.fetch_health", side_effect=fake_get_health):
            grouping.dispatch_with_grouping(outcomes=fired, now=fresh_now + timedelta(minutes=10))

        push_calls = _push_calls(http_calls)
        # 2 per-service alerts; no grouping because we can't confirm IB is degraded.
        assert len(push_calls) == 2

    def test_health_unreachable_state_groups(self, db_conn, monkeypatch, fresh_now):
        """When /health DOES respond but reports auth_state=unreachable
        (gateway TCP dead), grouping still applies — the cohort failure
        is provably IB-caused.
        """
        from watchdog import check, grouping

        monkeypatch.setenv("PUSHOVER_USER", "u")
        monkeypatch.setenv("PUSHOVER_TOKEN", "t")

        for svc in ("vcg-scan", "cri-scan"):
            _seed(db_conn, svc, "ok", fresh_now - timedelta(minutes=60))
        for svc in ("orders-sync", "portfolio-sync"):
            _seed(db_conn, svc, "ok", fresh_now - timedelta(minutes=2))

        for tick in (fresh_now, fresh_now + timedelta(minutes=5)):
            check.check_bucket(bucket="intraday", now=tick)
        report = check.check_bucket(bucket="intraday", now=fresh_now + timedelta(minutes=10))
        fired = [o for o in report.outcomes if o.fired]
        assert len(fired) == 2

        http_calls = []

        def fake_http_post(url, payload, headers=None):
            http_calls.append((url, payload))
            return (200, b"")

        with patch("watchdog.notify._http_post", side_effect=fake_http_post), \
             patch(
                 "watchdog.grouping.fetch_health",
                 return_value={"auth_state": "unreachable"},
             ):
            grouping.dispatch_with_grouping(outcomes=fired, now=fresh_now + timedelta(minutes=10))

        push_calls = _push_calls(http_calls)
        assert len(push_calls) == 1
        msg = push_calls[0][1]["message"]
        assert "unreachable" in msg


class TestServiceHealthRowsAlwaysWrite:
    """Even when the grouped Pushover alert subsumes per-service push
    alerts, the meta-row ``watchdog-alerts`` must reflect dispatcher
    health and the underlying per-service rows must remain intact.

    Contract change 2026-05-19: ``watchdog-alerts.last_error`` no
    longer carries downstream alert content — it's reserved for real
    dispatcher failures. Downstream truth lives in each service's own
    row (vcg-scan, cri-scan, etc.) which is written by that service's
    writer, not by the dispatcher. See
    ``test_dispatcher_writer_semantics.py``.
    """

    def test_grouped_dispatch_does_not_leak_into_meta_row(self, db_conn, monkeypatch, fresh_now):
        from watchdog import check, grouping

        # All four intraday IB services seeded as stale ok rows so the
        # grouped-dispatch path trips (threshold >= 2 IB services). Use
        # 60m so cri-scan (35m window) is already past-window at tick 0.
        # Previously this seeded only vcg+cri and relied on orders-sync/
        # portfolio-sync having no row (treated as stale). Services with
        # no row are now dormant (suppressed) — explicit rows are required.
        for svc in ("vcg-scan", "cri-scan", "orders-sync", "portfolio-sync"):
            _seed(db_conn, svc, "ok", fresh_now - timedelta(minutes=60))

        for tick in (fresh_now, fresh_now + timedelta(minutes=5)):
            check.check_bucket(bucket="intraday", now=tick)
        report = check.check_bucket(bucket="intraday", now=fresh_now + timedelta(minutes=10))
        fired = [o for o in report.outcomes if o.fired]
        assert len(fired) >= 2

        with patch("watchdog.notify._http_post", return_value=(200, b"")), \
             patch("watchdog.grouping.fetch_health", return_value={"auth_state": "awaiting_2fa"}):
            grouping.dispatch_with_grouping(outcomes=fired, now=fresh_now + timedelta(minutes=10))

        # ``watchdog-alerts`` row reflects dispatcher health only —
        # state=ok because the (mocked) Pushover succeeded.
        watchdog_rows = db_conn.execute(
            "SELECT state, last_error FROM service_health WHERE service='watchdog-alerts'"
        ).fetchall()
        assert len(watchdog_rows) >= 1
        state, last_error = watchdog_rows[0][0], watchdog_rows[0][1] or ""
        assert state == "ok", (
            f"successful grouped dispatch must leave row state=ok; got {state!r}"
        )
        ib_services_in_payload = sum(
            1 for svc in ("vcg-scan", "cri-scan", "orders-sync", "portfolio-sync")
            if svc in last_error
        )
        assert ib_services_in_payload == 0, (
            f"downstream service detail must NOT live in watchdog-alerts.last_error; "
            f"got {last_error!r}"
        )

        # Original ok rows for vcg + cri still exist (we never delete
        # them; grouping only affects the alert channel, not the table).
        underlying = db_conn.execute(
            "SELECT service FROM service_health WHERE service IN ('vcg-scan', 'cri-scan')"
        ).fetchall()
        assert {r[0] for r in underlying} == {"vcg-scan", "cri-scan"}


class TestCooldownAppliesToGroupedKey:
    """The grouped alert key (``ib-gateway-grouped`` / ``P1``) participates
    in the same 1h cooldown machinery. Two consecutive grouped fires
    inside the cooldown window must only push once.
    """

    def test_grouped_alert_respects_cooldown(self, db_conn, monkeypatch, fresh_now):
        from watchdog import check, cooldown as cooldown_mod, grouping

        monkeypatch.setenv("PUSHOVER_USER", "u")
        monkeypatch.setenv("PUSHOVER_TOKEN", "t")

        # All four intraday IB services seeded with 60m-old rows (cri-scan
        # has a 35m window, so 60m is safely stale at tick 0 for all four).
        # Previously this relied on orders-sync/portfolio-sync having no row
        # (treated as stale). Services with no row are now dormant — explicit
        # stale rows are required to trip the grouping threshold.
        for svc in ("vcg-scan", "cri-scan", "orders-sync", "portfolio-sync"):
            _seed(db_conn, svc, "ok", fresh_now - timedelta(minutes=60))

        for tick in (fresh_now, fresh_now + timedelta(minutes=5)):
            check.check_bucket(bucket="intraday", now=tick)
        fired_first = [
            o for o in check.check_bucket(
                bucket="intraday", now=fresh_now + timedelta(minutes=10)
            ).outcomes if o.fired
        ]

        http_calls = []

        def fake_http_post(url, payload, headers=None):
            http_calls.append((url, payload))
            return (200, b"")

        with patch("watchdog.notify._http_post", side_effect=fake_http_post), \
             patch("watchdog.grouping.fetch_health", return_value={"auth_state": "awaiting_2fa"}):
            grouping.dispatch_with_grouping(
                outcomes=fired_first, now=fresh_now + timedelta(minutes=10)
            )

        first_pushes = len(_push_calls(http_calls))
        assert first_pushes == 1

        # 30 minutes later — still inside cooldown window.
        later = fresh_now + timedelta(minutes=40)
        fired_second = [
            o for o in check.check_bucket(bucket="intraday", now=later).outcomes if o.fired
        ]
        assert len(fired_second) >= 2  # both still stale

        with patch("watchdog.notify._http_post", side_effect=fake_http_post), \
             patch("watchdog.grouping.fetch_health", return_value={"auth_state": "awaiting_2fa"}):
            grouping.dispatch_with_grouping(outcomes=fired_second, now=later)

        # Still only 1 push (the grouped key is in cooldown).
        assert len(_push_calls(http_calls)) == 1


class TestFetchHealthHelper:
    """The ``fetch_health`` helper hits FastAPI ``/health`` with a tight
    timeout. Network failures and HTTP errors degrade to
    ``{auth_state: unknown}`` so the caller can fall back.
    """

    def test_returns_auth_state_from_payload(self, monkeypatch):
        from watchdog import grouping

        class FakeResp:
            status_code = 200

            def json(self):
                return {"ib_gateway": {"auth_state": "awaiting_2fa"}}

        with patch("urllib.request.urlopen", return_value=FakeFp(
            status=200,
            body=json.dumps({"ib_gateway": {"auth_state": "awaiting_2fa"}}).encode(),
        )):
            health = grouping.fetch_health()
        assert health.get("auth_state") == "awaiting_2fa"

    def test_returns_unknown_on_transport_failure(self, monkeypatch):
        from watchdog import grouping
        from urllib import error as urllib_error

        with patch("urllib.request.urlopen", side_effect=urllib_error.URLError("refused")):
            health = grouping.fetch_health()
        assert health.get("auth_state") == "unknown"

    def test_returns_unknown_on_5xx(self):
        from watchdog import grouping
        from urllib import error as urllib_error

        # urlopen raises HTTPError on 5xx.
        with patch(
            "urllib.request.urlopen",
            side_effect=urllib_error.HTTPError(
                url="http://127.0.0.1:8321/health",
                code=500,
                msg="boom",
                hdrs=None,  # type: ignore[arg-type]
                fp=None,
            ),
        ):
            health = grouping.fetch_health()
        assert health.get("auth_state") == "unknown"


class FakeFp:
    """urlopen-style context-managed response stub."""

    def __init__(self, status: int, body: bytes):
        self.status = status
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return self._body
