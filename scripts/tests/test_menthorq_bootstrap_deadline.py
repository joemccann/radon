"""REL-050 / R-096 (P1), R-119 (P2) — the MenthorQ bootstrap has no total
deadline, and its embargo is indistinguishable from a real login failure.

R-096: d2d595e7 added a 20s `wait_for` on the OPTIONAL OIDC consent form.
When WordPress remembers the consent — the steady state after the first
grant — that locator never appears, so `_bootstrap_dashboard_session` sits a
flat 20s, then the `wait_for_url` chain (~15s on the VPS), then the
`/api/auth/session` poll (up to 20s). The per-step caps sum to ~150s against
a 50s Next.js proxy, so `/options/net-gex` returns the 504 MEASUREMENT FAULT
that 287e330c — shipped seven hours earlier — was written to fix.
`REQUEST_PATH_AUTH_BUDGET_SECONDS = 40` was applied only per step.

R-119: the 300s embargo raises `MenthorQDashboardAuthError("dashboard
authentication is unavailable")`, byte-identical to a genuine login failure,
and `_trip_auth_embargo` fires on ANY such error including the local `OSError`
path in `_persist_dashboard_storage_state`. A chromium OOM at 06:00Z trips the
embargo; the 06:02Z login probe matches `503 + "authentication"`, takes the
PERSISTENT branch and latches a full-day false "re-login chain is broken"
alarm on a chain that was never exercised.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

from clients import menthorq_dashboard_client as mq


@pytest.fixture(autouse=True)
def _clear_embargo():
    mq._reset_auth_embargo_for_tests()
    yield
    mq._reset_auth_embargo_for_tests()


class TestConsentProbeIsShort:
    def test_the_optional_consent_form_gets_a_probe_not_a_full_timeout(self):
        """The Authorize input is OPTIONAL — absent whenever WordPress
        remembers the grant — so waiting the full login timeout for it burns
        half the request budget on the steady-state path."""
        assert hasattr(mq, "CONSENT_PROBE_SECONDS")
        assert 0 < mq.CONSENT_PROBE_SECONDS <= 2.0


class TestTotalDeadline:
    def test_a_deadline_is_threaded_through_every_step(self):
        import inspect

        source = inspect.getsource(mq.MenthorQDashboardClient._bootstrap_dashboard_session)
        assert "deadline" in source
        assert "_remaining_ms" in source, (
            "each step still gets its own full cap; nothing bounds total elapsed time"
        )

    def test_remaining_never_exceeds_the_request_budget(self):
        client = mq.MenthorQDashboardClient(username="u", password="p")
        deadline = time.monotonic() + mq.REQUEST_PATH_AUTH_BUDGET_SECONDS
        assert client._remaining_ms(deadline, cap_seconds=1000) <= int(
            mq.REQUEST_PATH_AUTH_BUDGET_SECONDS * 1000
        )

    def test_an_exhausted_deadline_refuses_rather_than_starting_a_step(self):
        client = mq.MenthorQDashboardClient(username="u", password="p")
        with pytest.raises(mq.MenthorQDashboardAuthError):
            client._remaining_ms(time.monotonic() - 1, cap_seconds=10)

    def test_the_production_request_budget_is_forty_seconds(self):
        """test_menthorq_dashboard_bootstrap overrides the budget per-test so
        the expired-session poll runs out in 0.5s instead of 40s (CIP-001).
        Pin the module default so that test-only override can never become
        the production value."""
        assert mq.REQUEST_PATH_AUTH_BUDGET_SECONDS == 40.0


class TestEmbargoIsDistinguishable:
    def test_the_embargo_raises_its_own_subclass(self):
        mq._trip_auth_embargo()
        client = mq.MenthorQDashboardClient(username="u", password="p")
        with pytest.raises(mq.MenthorQDashboardAuthEmbargoed) as exc:
            client._resolve_access_token()
        assert isinstance(exc.value, mq.MenthorQDashboardAuthError)
        assert "embargo" in str(exc.value).lower()

    def test_a_local_disk_failure_does_not_trip_the_embargo(self, tmp_path):
        """R-119: `_persist_dashboard_storage_state`'s OSError path is a
        local problem — a full disk — and tripping a 300s token embargo on it
        latches a false full-day alarm on a chain that never ran."""
        client = mq.MenthorQDashboardClient(username="u", password="p")

        class _Context:
            def storage_state(self, path):
                raise OSError("No space left on device")

        with pytest.raises(mq.MenthorQDashboardStorageError):
            client._persist_dashboard_storage_state(_Context())
        assert mq._auth_embargo_active() is False

    def test_a_genuine_auth_failure_still_trips_it(self, monkeypatch):
        client = mq.MenthorQDashboardClient(username="u", password="p")
        monkeypatch.setattr(
            client,
            "_resolve_access_token_uncached",
            lambda: (_ for _ in ()).throw(mq.MenthorQDashboardAuthError("bad creds")),
        )
        with pytest.raises(mq.MenthorQDashboardAuthError):
            client._resolve_access_token()
        assert mq._auth_embargo_active() is True


class TestLoginProbeClassifiesTheEmbargo:
    def test_an_embargoed_response_is_transient_not_persistent(self):
        from monitor_daemon.handlers import menthorq_login_probe as probe

        assert probe._is_auth_failure(503, "dashboard authentication is unavailable")
        assert not probe._is_auth_failure(
            503, "dashboard authentication embargoed after a recent failure"
        ), "the embargo still latches a full-day 're-login chain broken' alarm"


class TestBootstrapWallTime:
    def test_a_hanging_consent_locator_does_not_blow_the_request_budget(
        self, monkeypatch
    ):
        """R-096's injection: the `authorize` locator sleeps its full timeout
        then raises; every other step is instant. Total wall time must stay
        under the 40s request budget (it was 20 + ~15 + up to 20)."""
        client = mq.MenthorQDashboardClient(username="u", password="p")
        clock = {"now": 0.0}
        monkeypatch.setattr(mq.time, "monotonic", lambda: clock["now"])

        class _Locator:
            def __init__(self, hangs: bool):
                self.hangs = hangs

            def wait_for(self, state=None, timeout=None):
                if self.hangs:
                    clock["now"] += (timeout or 0) / 1000
                    raise RuntimeError("locator never appeared")

            def count(self):
                return 1

            def fill(self, _value):
                return None

            def click(self):
                return None

        class _Response:
            ok = True

            @staticmethod
            def json():
                return {"accessToken": "tok", "expiresAt": None}

        class _Request:
            @staticmethod
            def get(_url, timeout=None):
                return _Response()

        class _Page:
            request = _Request()

            def goto(self, *_a, **_k):
                return None

            def locator(self, selector):
                return _Locator(hangs='name="authorize"' in selector)

            def wait_for_url(self, *_a, **_k):
                return None

            def wait_for_timeout(self, ms):
                clock["now"] += ms / 1000

        class _Context:
            request = _Request()

            def new_page(self):
                return _Page()

            def storage_state(self, path=None):
                return None

            def close(self):
                return None

        class _Browser:
            def new_context(self, **_k):
                return _Context()

            def close(self):
                return None

        class _Chromium:
            @staticmethod
            def launch(**_k):
                return _Browser()

        class _Playwright:
            chromium = _Chromium()

            def __enter__(self):
                return self

            def __exit__(self, *_a):
                return False

        monkeypatch.setattr(
            mq, "_session_expired", lambda _expires: False, raising=False
        )
        monkeypatch.setattr(
            client, "_persist_dashboard_storage_state", lambda _ctx: None
        )
        import playwright.sync_api as pw

        monkeypatch.setattr(pw, "sync_playwright", lambda: _Playwright())

        client._bootstrap_dashboard_session()

        assert clock["now"] < mq.REQUEST_PATH_AUTH_BUDGET_SECONDS, (
            f"bootstrap burned {clock['now']:.0f}s of a "
            f"{mq.REQUEST_PATH_AUTH_BUDGET_SECONDS:.0f}s budget waiting for an "
            "optional consent form"
        )
        assert clock["now"] <= mq.CONSENT_PROBE_SECONDS + 0.01
