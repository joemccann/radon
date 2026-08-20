"""MenthorQ dashboard re-auth (bootstrap) — the browser interaction itself.

The bootstrap re-mints the dashboard cookie jar from WordPress credentials.
It had NO coverage (existing tests stub `_bootstrap_dashboard_session`
wholesale), and in production it silently failed for 11 days while
`/options/net-gex` was dark.

Live probe on the VPS 2026-08-07 showed the flow completes headlessly when
two things hold, both pinned here:

  1. The context advertises a REAL browser user agent. MenthorQ sits behind
     AWS WAF (the jar carries an `aws-waf-token`); Playwright's default UA
     announces `HeadlessChrome` and the login never completes.
  2. The code waits for the OAuth chain to LAND on dashboard.menthorq.io
     (dashboard/en/login -> menthorq.com/wp-login.php -> dashboard) before
     reading /api/auth/session. Reading too early returns null.

On success the storage state must be persisted — that jar is what lets
every later call mint a fresh token without logging in again.
"""
from __future__ import annotations

import json
import sys
import time
import types
from pathlib import Path

import pytest


class _FakeLocator:
    def __init__(self, page, selector):
        self._page = page
        self._selector = selector

    def wait_for(self, **_kwargs):
        return None

    def count(self):
        return 1

    def fill(self, value):
        self._page.filled[self._selector] = value

    def click(self):
        self._page.clicked.append(self._selector)
        self._page.url = self._page.post_submit_url


class _FakePage:
    def __init__(self, post_submit_url, waited_urls):
        self.url = "https://dashboard.menthorq.io/en/login"
        self.post_submit_url = post_submit_url
        self.filled: dict[str, str] = {}
        self.clicked: list[str] = []
        self.waited_urls = waited_urls
        self.goto_calls: list[str] = []

    def goto(self, url, **_kwargs):
        self.goto_calls.append(url)

    def locator(self, selector):
        return _FakeLocator(self, selector)

    def wait_for_url(self, pattern, **_kwargs):
        self.waited_urls.append(pattern)
        if "dashboard.menthorq.io" not in self.url:
            raise TimeoutError("never reached the dashboard")

    def wait_for_timeout(self, _ms):
        return None

    def expect_navigation(self, **_kwargs):
        page = self

        class _Ctx:
            def __enter__(self_inner):
                return None

            def __exit__(self_inner, *_a):
                return False

        return _Ctx()


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload
        self.ok = payload is not None

    def json(self):
        return self._payload

    def text(self):
        return json.dumps(self._payload)


class _FakeRequest:
    def __init__(self, page, payload):
        self._page = page
        self._payload = payload

    def get(self, _url, **_kwargs):
        # The session only exists once the OAuth chain reached the dashboard.
        if "dashboard.menthorq.io" not in self._page.url:
            return _FakeResponse(None)
        return _FakeResponse(self._payload)


class _FakeContext:
    def __init__(self, kwargs, page, payload, recorder):
        self.kwargs = kwargs
        self._page = page
        self.request = _FakeRequest(page, payload)
        self._recorder = recorder

    def new_page(self):
        return self._page

    def storage_state(self, **kwargs):
        self._recorder["storage_state_calls"] += 1
        state = {"cookies": [], "origins": []}
        path = kwargs.get("path")
        if path:  # real Playwright writes the file; the client then chmods it
            Path(path).write_text(json.dumps(state))
        return state

    def close(self):
        return None


class _FakeBrowser:
    def __init__(self, page, payload, recorder):
        self._page = page
        self._payload = payload
        self._recorder = recorder

    def new_context(self, **kwargs):
        ctx = _FakeContext(kwargs, self._page, self._payload, self._recorder)
        self._recorder["contexts"].append(ctx)
        return ctx

    def close(self):
        return None


class _FakePlaywright:
    def __init__(self, page, payload, recorder):
        self.chromium = types.SimpleNamespace(
            launch=lambda **kwargs: _FakeBrowser(page, payload, recorder)
        )


def _install_fake_playwright(monkeypatch, *, post_submit_url, payload):
    recorder = {"contexts": [], "storage_state_calls": 0, "waited": []}
    page = _FakePage(post_submit_url, recorder["waited"])
    fake_pw = _FakePlaywright(page, payload, recorder)

    class _CtxMgr:
        def __enter__(self):
            return fake_pw

        def __exit__(self, *_a):
            return False

    module = types.ModuleType("playwright.sync_api")
    module.sync_playwright = lambda: _CtxMgr()
    parent = types.ModuleType("playwright")
    parent.sync_api = module
    monkeypatch.setitem(sys.modules, "playwright", parent)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", module)
    return recorder, page


def _client(tmp_path):
    from clients.menthorq_dashboard_client import MenthorQDashboardClient

    return MenthorQDashboardClient(
        storage_state_path=tmp_path / "jar.json",
        username="user@example.com",
        password="hunter2",
    )


LIVE_SESSION = {
    "accessToken": "header.payload.signature",
    "expiresAt": int(time.time()) + 12 * 3600,
}


class TestBootstrapReachesTheDashboard:
    def test_successful_login_returns_token_and_persists_the_jar(self, tmp_path, monkeypatch):
        recorder, _page = _install_fake_playwright(
            monkeypatch,
            post_submit_url="https://dashboard.menthorq.io/en/options/exposure?symbol=MU",
            payload=LIVE_SESSION,
        )
        token = _client(tmp_path)._bootstrap_dashboard_session()
        assert token == "header.payload.signature"
        assert recorder["storage_state_calls"] == 1, "the re-minted jar must be persisted"

    def test_context_advertises_a_real_browser_user_agent(self, tmp_path, monkeypatch):
        """AWS WAF blocks the default `HeadlessChrome` UA — the live probe
        only completed once a real UA was supplied."""
        recorder, _page = _install_fake_playwright(
            monkeypatch,
            post_submit_url="https://dashboard.menthorq.io/en/options/exposure?symbol=MU",
            payload=LIVE_SESSION,
        )
        _client(tmp_path)._bootstrap_dashboard_session()
        ua = recorder["contexts"][0].kwargs.get("user_agent", "")
        assert ua, "no user_agent supplied — WAF will serve the bot challenge"
        assert "Headless" not in ua
        assert "Mozilla/5.0" in ua

    def test_clicks_oidc_authorize_consent_before_waiting_for_the_dashboard(
        self, tmp_path, monkeypatch
    ):
        """2026-08-20: after WP login the page stays on wp-login.php with
        an Authorize submit. Waiting for dashboard.menthorq.io times out
        unless that button is clicked."""
        recorder, page = _install_fake_playwright(
            monkeypatch,
            post_submit_url=(
                "https://menthorq.com/wp-login.php?client_id=aws_cognito_client_id"
                "&action=openid-authenticate"
            ),
            payload=LIVE_SESSION,
        )
        orig_click = _FakeLocator.click

        def _click(self):
            if self._selector == 'input[name="authorize"]':
                self._page.clicked.append(self._selector)
                self._page.url = (
                    "https://dashboard.menthorq.io/en/options/exposure?symbol=MU"
                )
                return
            return orig_click(self)

        monkeypatch.setattr(_FakeLocator, "click", _click)
        token = _client(tmp_path)._bootstrap_dashboard_session()
        assert token == "header.payload.signature"
        assert 'input[name="authorize"]' in page.clicked

    def test_waits_for_the_oauth_chain_to_land_on_the_dashboard(self, tmp_path, monkeypatch):
        recorder, _page = _install_fake_playwright(
            monkeypatch,
            post_submit_url="https://dashboard.menthorq.io/en/options/exposure?symbol=MU",
            payload=LIVE_SESSION,
        )
        _client(tmp_path)._bootstrap_dashboard_session()
        assert any("dashboard.menthorq.io" in pattern for pattern in recorder["waited"]), (
            "must wait for the redirect chain to land before reading the session"
        )

    def test_login_that_never_leaves_wordpress_raises_auth_error(self, tmp_path, monkeypatch):
        from clients.menthorq_dashboard_client import MenthorQDashboardAuthError

        recorder, _page = _install_fake_playwright(
            monkeypatch,
            post_submit_url="https://menthorq.com/wp-login.php?reauth=1",
            payload=LIVE_SESSION,
        )
        with pytest.raises(MenthorQDashboardAuthError):
            _client(tmp_path)._bootstrap_dashboard_session()
        assert recorder["storage_state_calls"] == 0, "never persist a jar for a failed login"

    def test_expired_session_payload_is_rejected(self, tmp_path, monkeypatch):
        from clients.menthorq_dashboard_client import MenthorQDashboardAuthError

        recorder, _page = _install_fake_playwright(
            monkeypatch,
            post_submit_url="https://dashboard.menthorq.io/en/options/exposure?symbol=MU",
            payload={"accessToken": "a.b.c", "expiresAt": int(time.time()) - 60},
        )
        with pytest.raises(MenthorQDashboardAuthError):
            _client(tmp_path)._bootstrap_dashboard_session()
        assert recorder["storage_state_calls"] == 0


class TestStorageStateExchangeAlsoNeedsARealUserAgent:
    """The jar->token exchange is the DURABLE path — it runs on every call.

    It was left on Playwright's default UA, so WAF returned an HTML
    challenge and `response.json()` raised JSONDecodeError (the exact
    signature seen on the VPS 2026-08-07). Fixing only the bootstrap would
    have re-minted a jar that could never be spent.
    """

    def test_context_uses_a_real_user_agent(self, tmp_path, monkeypatch):
        recorder, _page = _install_fake_playwright(
            monkeypatch,
            post_submit_url="https://dashboard.menthorq.io/en/options/exposure?symbol=MU",
            payload=LIVE_SESSION,
        )
        jar = tmp_path / "jar.json"
        jar.write_text(json.dumps({"cookies": [], "origins": []}))
        jar.chmod(0o600)
        from clients.menthorq_dashboard_client import MenthorQDashboardClient

        client = MenthorQDashboardClient(storage_state_path=jar, username="u", password="p")
        client._token_from_storage_state()
        ua = recorder["contexts"][0].kwargs.get("user_agent", "")
        assert ua and "Headless" not in ua, (
            "the jar->token exchange must not advertise HeadlessChrome to WAF"
        )
        assert recorder["contexts"][0].kwargs.get("storage_state"), "jar must be loaded"
