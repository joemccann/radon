"""Subscription-token vault + autonomous refresh engine.

No network, no real secret store. Every credential value here is obviously
synthetic (``FAKE-...``) so gitleaks stays quiet and so the "no token material
anywhere" test has an unambiguous needle.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from scripts import subscription_tokens as st

NOW = datetime(2026, 9, 17, 12, 0, 0, tzinfo=timezone.utc)

FAKE_REFRESH = "FAKE-refresh-token-value-0001"
FAKE_ACCESS = "FAKE-access-token-value-0002"
FAKE_NEW_ACCESS = "FAKE-rotated-access-token-0003"
FAKE_NEW_REFRESH = "FAKE-rotated-refresh-token-0004"

GROK_KEY = "https://auth.x.ai::11111111-2222-3333-4444-555555555555"


# --------------------------------------------------------------------------
# fixtures / fakes
# --------------------------------------------------------------------------


class FakeVault:
    def __init__(self, seeded: dict[str, str] | None = None) -> None:
        self.data = dict(seeded or {})
        self.seals: list[str] = []

    def get(self, provider: str) -> str | None:
        return self.data.get(provider)

    def seal(self, provider: str, value: str) -> None:
        self.data[provider] = value
        self.seals.append(provider)


class FakeHttp:
    """Records requests; replies from a queued script keyed by url."""

    def __init__(self, responses: dict[str, list[st.HttpResponse]] | None = None):
        self.responses = {k: list(v) for k, v in (responses or {}).items()}
        self.calls: list[tuple[str, str, dict | None]] = []

    def __call__(self, method, url, *, form=None, json_body=None, timeout=20):
        self.calls.append((method, url, form or json_body))
        queue = self.responses.get(url)
        if not queue:
            raise AssertionError(f"unexpected request to {url}")
        return queue.pop(0) if len(queue) > 1 else queue[0]


def make_runtime(tmp_path, *, home=None, vault=None, http=None, **kw) -> st.Runtime:
    home = home or tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    notified: list[dict] = []
    rt = st.Runtime(
        env={"HOME": str(home), "PUSHOVER_USER": "u-fake", "PUSHOVER_TOKEN": "t-fake"},
        vault=vault if vault is not None else FakeVault(),
        http=http or FakeHttp(),
        now=lambda: NOW,
        which=lambda _binary: None,
        run_cli=lambda _provider: False,
        sidecar_path=tmp_path / "state.json",
        lock_path=tmp_path / "run.lock",
        notify=notified.append if "notify" not in kw else kw.pop("notify"),
        heartbeat=lambda *a, **k: None,
        sleep=lambda _seconds: None,
    )
    for key, value in kw.items():
        setattr(rt, key, value)
    rt.sent = notified  # type: ignore[attr-defined]
    return rt


def grok_doc(expires: datetime, *, extra=None) -> dict:
    entry = {
        "key": FAKE_ACCESS,
        "auth_mode": "oauth",
        "user_id": "user-fake",
        "email": "fake@example.invalid",
        "refresh_token": FAKE_REFRESH,
        "expires_at": expires.isoformat().replace("+00:00", "Z"),
        "oidc_issuer": "https://auth.x.ai",
        "oidc_client_id": "11111111-2222-3333-4444-555555555555",
    }
    entry.update(extra or {})
    return {GROK_KEY: entry}


def anthropic_doc(expires: datetime) -> dict:
    return {
        "claudeAiOauth": {
            "accessToken": FAKE_ACCESS,
            "refreshToken": FAKE_REFRESH,
            "expiresAt": int(expires.timestamp() * 1000),
            "scopes": ["user:inference"],
        }
    }


def codex_doc(expires: datetime) -> dict:
    return {
        "tokens": {
            "access_token": FAKE_ACCESS,
            "refresh_token": FAKE_REFRESH,
            "expires_at": expires.isoformat().replace("+00:00", "Z"),
        },
        "last_refresh": NOW.isoformat().replace("+00:00", "Z"),
    }


def gemini_doc(expires: datetime) -> dict:
    return {
        "access_token": FAKE_ACCESS,
        "refresh_token": FAKE_REFRESH,
        "expiry_date": int(expires.timestamp() * 1000),
        "token_type": "Bearer",
    }


DOCS = {
    "grok": grok_doc,
    "anthropic": anthropic_doc,
    "codex": codex_doc,
    "gemini": gemini_doc,
}


def write_doc(rt: st.Runtime, provider: str, doc: dict) -> Path:
    path = st.PROVIDERS[provider].path(rt.env)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=2), encoding="utf-8")
    return path


DISCOVERY_URL = "https://auth.x.ai/.well-known/openid-configuration"
GROK_TOKEN_URL = "https://auth.x.ai/oauth2/token"


def grok_http(token_response: st.HttpResponse) -> FakeHttp:
    return FakeHttp(
        {
            DISCOVERY_URL: [
                st.HttpResponse(200, {"token_endpoint": GROK_TOKEN_URL})
            ],
            GROK_TOKEN_URL: [token_response],
        }
    )


def ok_token_response(expires_in=3600) -> st.HttpResponse:
    return st.HttpResponse(
        200,
        {
            "access_token": FAKE_NEW_ACCESS,
            "refresh_token": FAKE_NEW_REFRESH,
            "expires_in": expires_in,
        },
    )


# --------------------------------------------------------------------------
# provider parsing
# --------------------------------------------------------------------------


@pytest.mark.parametrize("name", sorted(DOCS))
def test_provider_parses_expiry_and_refresh_token(name):
    expires = NOW + timedelta(hours=1)
    doc = DOCS[name](expires)
    provider = st.PROVIDERS[name]
    parsed = provider.read_expiry(doc)
    assert parsed is not None
    assert parsed.tzinfo is not None
    assert abs((parsed - expires).total_seconds()) < 1.5
    assert provider.read_refresh(doc) == FAKE_REFRESH


def test_provider_paths_honour_env_overrides(tmp_path):
    env = {
        "HOME": str(tmp_path),
        "CLAUDE_CONFIG_DIR": str(tmp_path / "cfg"),
        "CODEX_HOME": str(tmp_path / "cx"),
    }
    assert st.PROVIDERS["anthropic"].path(env) == tmp_path / "cfg" / ".credentials.json"
    assert st.PROVIDERS["codex"].path(env) == tmp_path / "cx" / "auth.json"
    assert st.PROVIDERS["grok"].path(env) == tmp_path / ".grok" / "auth.json"
    assert (
        st.PROVIDERS["gemini"].path(env) == tmp_path / ".gemini" / "oauth_creds.json"
    )


def test_grok_token_endpoint_comes_from_oidc_discovery(tmp_path):
    http = grok_http(ok_token_response())
    rt = make_runtime(tmp_path, http=http)
    write_doc(rt, "grok", grok_doc(NOW + timedelta(seconds=60)))

    report = st.run("once", ["grok"], rt)

    assert report["providers"][0]["state"] == st.REFRESHED
    assert http.calls[0][:2] == ("GET", DISCOVERY_URL)
    assert http.calls[1][:2] == ("POST", GROK_TOKEN_URL)
    # never a guessed constant
    assert all(url != "https://auth.x.ai/oauth/token" for _m, url, _b in http.calls)


# --------------------------------------------------------------------------
# refresh
# --------------------------------------------------------------------------


def test_expiring_token_is_refreshed_and_unmodelled_keys_survive(tmp_path):
    rt = make_runtime(tmp_path, http=grok_http(ok_token_response()))
    doc = grok_doc(NOW + timedelta(seconds=60), extra={"vendor_future_field": "keep-me"})
    path = write_doc(rt, "grok", doc)

    report = st.run("once", ["grok"], rt)

    assert report["providers"][0]["state"] == st.REFRESHED
    after = json.loads(path.read_text())[GROK_KEY]
    assert after["vendor_future_field"] == "keep-me"
    assert after["email"] == "fake@example.invalid"
    assert after["key"] == FAKE_NEW_ACCESS
    assert after["refresh_token"] == FAKE_NEW_REFRESH
    assert path.stat().st_mode & 0o777 == 0o600
    assert not list(path.parent.glob("*.radon-tmp"))
    # seal-known-good happened before the write, and the fresh doc re-sealed
    assert rt.vault.seals == ["grok", "grok"]
    assert json.loads(rt.vault.get("grok"))[GROK_KEY]["key"] == FAKE_NEW_ACCESS


def test_cli_native_refresh_is_preferred_over_token_endpoint(tmp_path):
    http = FakeHttp()  # any HTTP call raises
    rt = make_runtime(tmp_path, http=http, which=lambda binary: "/usr/bin/" + binary)
    path = write_doc(rt, "grok", grok_doc(NOW + timedelta(seconds=60)))

    def fake_cli(provider):
        doc = grok_doc(NOW + timedelta(hours=2))
        path.write_text(json.dumps(doc), encoding="utf-8")
        return True

    rt.run_cli = fake_cli
    report = st.run("once", ["grok"], rt)

    assert report["providers"][0]["state"] == st.REFRESHED
    assert http.calls == []


def test_live_token_is_left_alone(tmp_path):
    rt = make_runtime(tmp_path, http=FakeHttp())
    path = write_doc(rt, "grok", grok_doc(NOW + timedelta(hours=5)))
    before = path.read_bytes()

    report = st.run("once", ["grok"], rt)

    assert report["providers"][0]["state"] == st.LIVE
    assert path.read_bytes() == before
    assert report["exit_code"] == 0


def test_live_token_is_sealed_so_a_deleted_file_is_recoverable(tmp_path):
    # A freshly logged-in credential is live for its whole first hour. If the
    # vault only ever gets a copy at refresh time, a wipe inside that window
    # loses it outright and the provider comes back unbootstrapped.
    vault = FakeVault()
    rt = make_runtime(tmp_path, vault=vault, http=FakeHttp())
    path = write_doc(rt, "grok", grok_doc(NOW + timedelta(hours=5)))

    st.run("once", ["grok"], rt)

    assert vault.get("grok") == path.read_text()

    path.unlink()
    report = st.run("once", ["grok"], rt)

    assert report["providers"][0]["state"] == st.RESTORED
    # Idempotent: an unchanged file is not re-sealed on every run.
    assert vault.seals == ["grok"]


# --------------------------------------------------------------------------
# restore / bootstrap
# --------------------------------------------------------------------------


def test_missing_file_with_vault_copy_is_restored(tmp_path):
    doc = grok_doc(NOW + timedelta(hours=5))
    vault = FakeVault({"grok": json.dumps(doc)})
    rt = make_runtime(tmp_path, vault=vault, http=FakeHttp())

    report = st.run("once", ["grok"], rt)

    path = st.PROVIDERS["grok"].path(rt.env)
    assert report["providers"][0]["state"] == st.RESTORED
    assert json.loads(path.read_text()) == doc
    assert path.stat().st_mode & 0o777 == 0o600
    assert path.parent.stat().st_mode & 0o777 == 0o700
    assert report["exit_code"] == 0


def test_missing_file_and_empty_vault_is_unbootstrapped_and_never_pages(tmp_path):
    rt = make_runtime(tmp_path)

    report = st.run("once", ["anthropic"], rt)

    assert report["providers"][0]["state"] == st.UNBOOTSTRAPPED
    assert report["exit_code"] == 0
    assert rt.sent == []


# --------------------------------------------------------------------------
# failure states + paging
# --------------------------------------------------------------------------


def invalid_grant_runtime(tmp_path, vault=None):
    http = grok_http(st.HttpResponse(400, {"error": "invalid_grant"}))
    rt = make_runtime(tmp_path, http=http, vault=vault)
    write_doc(rt, "grok", grok_doc(NOW + timedelta(seconds=60)))
    return rt


def test_invalid_grant_pages_once_then_respects_cooldown(tmp_path):
    rt = invalid_grant_runtime(tmp_path)

    first = st.run("once", ["grok"], rt)
    assert first["providers"][0]["state"] == st.NEEDS_REAUTH
    assert first["exit_code"] == 1
    assert len(rt.sent) == 1
    payload = rt.sent[0]
    assert payload["title"] == "radon subscription token"
    assert "grok" in payload["message"]
    assert st.PROVIDERS["grok"].reauth_command in payload["message"]

    second = st.run("once", ["grok"], rt)
    assert second["providers"][0]["state"] == st.NEEDS_REAUTH
    assert len(rt.sent) == 1  # inside the 12h cooldown

    rt.now = lambda: NOW + timedelta(hours=13)
    st.run("once", ["grok"], rt)
    assert len(rt.sent) == 2


def test_no_refresh_token_is_needs_reauth_without_any_request(tmp_path):
    http = FakeHttp()
    rt = make_runtime(tmp_path, http=http)
    doc = grok_doc(NOW + timedelta(seconds=60))
    doc[GROK_KEY].pop("refresh_token")
    write_doc(rt, "grok", doc)

    report = st.run("once", ["grok"], rt)

    assert report["providers"][0]["state"] == st.NEEDS_REAUTH
    assert http.calls == []


def test_server_error_is_error_and_pages_only_on_third_consecutive_run(tmp_path):
    http = grok_http(st.HttpResponse(503, {}))
    rt = make_runtime(tmp_path, http=http)
    write_doc(rt, "grok", grok_doc(NOW + timedelta(seconds=60)))

    for _ in range(2):
        report = st.run("once", ["grok"], rt)
        assert report["providers"][0]["state"] == st.ERROR
    assert rt.sent == []

    report = st.run("once", ["grok"], rt)
    assert report["providers"][0]["state"] == st.ERROR
    assert report["exit_code"] == 1
    assert len(rt.sent) == 1


def test_store_open_failure_is_store_unavailable_never_empty(tmp_path, monkeypatch):
    def boom(_env):
        raise st.VaultUnavailable("secret store I/O failed")

    monkeypatch.setattr(st, "open_vault", boom)
    rt = make_runtime(tmp_path)
    rt.vault = None

    report = st.run("once", ["grok"], rt)

    assert report["providers"][0]["state"] == st.STORE_UNAVAILABLE
    assert report["exit_code"] == 78
    assert len(rt.sent) == 1


def test_refreshed_doc_missing_an_original_key_aborts_and_leaves_file(tmp_path):
    http = grok_http(st.HttpResponse(200, {"access_token": FAKE_NEW_ACCESS}))
    rt = make_runtime(tmp_path, http=http)
    path = write_doc(rt, "grok", grok_doc(NOW + timedelta(seconds=60)))
    before = path.read_bytes()

    monkey = st.PROVIDERS["grok"]
    original_apply = st._grok_apply

    def lossy_apply(doc, resp, now):
        new = original_apply(doc, resp, now)
        new[GROK_KEY].pop("email")
        return new

    object.__setattr__(monkey, "apply", lossy_apply)
    try:
        report = st.run("once", ["grok"], rt)
    finally:
        object.__setattr__(monkey, "apply", st._grok_apply)

    assert report["providers"][0]["state"] == st.ERROR
    assert path.read_bytes() == before


def test_crash_between_refresh_and_write_leaves_a_refreshable_copy_in_vault(
    tmp_path, monkeypatch
):
    # Vault ahead of disk, never behind: the refresh token we presented may
    # already have been invalidated by the POST that succeeded, so the vault
    # must hold the ROTATED document, not the pre-refresh corpse.
    rt = make_runtime(tmp_path, http=grok_http(ok_token_response()))
    doc = grok_doc(NOW + timedelta(seconds=60))
    path = write_doc(rt, "grok", doc)

    def exploding_write(*_args, **_kwargs):
        raise OSError("disk went away")

    monkeypatch.setattr(st, "atomic_write_credential", exploding_write)
    report = st.run("once", ["grok"], rt)

    assert report["providers"][0]["state"] == st.ERROR
    sealed = json.loads(rt.vault.get("grok"))[GROK_KEY]
    assert sealed["refresh_token"] == FAKE_NEW_REFRESH
    assert sealed["email"] == "fake@example.invalid"
    assert json.loads(path.read_text()) == doc


def test_a_seal_failure_does_not_turn_a_successful_refresh_into_an_error(tmp_path):
    class BrokenVault(FakeVault):
        def seal(self, provider, value):
            raise RuntimeError("database is locked")

    rt = make_runtime(
        tmp_path, vault=BrokenVault(), http=grok_http(ok_token_response())
    )
    path = write_doc(rt, "grok", grok_doc(NOW + timedelta(seconds=60)))

    report = st.run("once", ["grok"], rt)

    assert report["providers"][0]["state"] == st.REFRESHED
    assert report["providers"][0]["last_refresh_at"] == NOW.isoformat()
    assert "database is locked" not in json.dumps(report)
    assert json.loads(path.read_text())[GROK_KEY]["key"] == FAKE_NEW_ACCESS


# --------------------------------------------------------------------------
# secret hygiene
# --------------------------------------------------------------------------


def test_no_token_material_in_logs_sidecar_heartbeat_or_pushover(
    tmp_path, capsys, caplog
):
    caplog.set_level(logging.DEBUG)
    heartbeats: list[tuple] = []
    http = grok_http(st.HttpResponse(400, {"error": "invalid_grant"}))
    rt = make_runtime(tmp_path, http=http, heartbeat=lambda *a, **k: heartbeats.append((a, k)))
    write_doc(rt, "grok", grok_doc(NOW + timedelta(seconds=60)))

    st.run("once", ["grok"], rt, json_output=True)

    captured = capsys.readouterr()
    haystacks = [
        captured.out,
        captured.err,
        "\n".join(r.getMessage() for r in caplog.records),
        rt.sidecar_path.read_text(),
        json.dumps(heartbeats, default=str),
        json.dumps(rt.sent),
    ]
    for needle in (FAKE_ACCESS, FAKE_REFRESH):
        for haystack in haystacks:
            assert needle not in haystack


def test_redaction_helper_reports_length_only():
    assert st.redact(FAKE_ACCESS) == f"<redacted len={len(FAKE_ACCESS)}>"
    assert st.redact(None) == "<redacted len=0>"


# --------------------------------------------------------------------------
# CLI surface
# --------------------------------------------------------------------------


def test_check_mode_exit_codes_for_every_state(tmp_path):
    cases = {
        st.LIVE: 0,
        st.UNBOOTSTRAPPED: 0,
        st.NEEDS_REAUTH: 1,
        st.ERROR: 1,
        st.STORE_UNAVAILABLE: 78,
    }
    for state, expected in cases.items():
        assert st.exit_code_for([state]) == expected
    assert st.exit_code_for([st.LIVE, st.NEEDS_REAUTH]) == 1
    assert st.exit_code_for([st.NEEDS_REAUTH, st.STORE_UNAVAILABLE]) == 78
    assert st.exit_code_for([st.REFRESHED, st.RESTORED]) == 0


def test_check_mode_never_writes_or_refreshes(tmp_path):
    http = FakeHttp()
    rt = make_runtime(tmp_path, http=http)
    path = write_doc(rt, "grok", grok_doc(NOW + timedelta(seconds=60)))
    before = path.read_bytes()

    report = st.run("check", ["grok"], rt)

    assert report["exit_code"] == 1  # expiring is not live
    assert path.read_bytes() == before
    assert http.calls == []
    assert rt.vault.seals == []


def test_seal_and_restore_round_trip_byte_for_byte(tmp_path):
    rt = make_runtime(tmp_path)
    path = st.PROVIDERS["grok"].path(rt.env)
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = '{\n  "odd":   "spacing",\n  "%s": {"refresh_token": "%s"}\n}\n' % (
        GROK_KEY,
        FAKE_REFRESH,
    )
    path.write_text(raw, encoding="utf-8")

    assert st.run("seal", ["grok"], rt)["exit_code"] == 0
    path.unlink()
    assert st.run("restore", ["grok"], rt)["exit_code"] == 0
    assert path.read_text(encoding="utf-8") == raw


def test_json_report_is_machine_readable_and_redacted(tmp_path, capsys):
    rt = make_runtime(tmp_path)
    write_doc(rt, "grok", grok_doc(NOW + timedelta(hours=5)))

    st.run("check", ["grok"], rt, json_output=True)

    payload = json.loads(capsys.readouterr().out)
    assert payload["providers"][0]["provider"] == "grok"
    assert payload["providers"][0]["state"] == st.LIVE
    assert FAKE_ACCESS not in json.dumps(payload)


def test_sidecar_records_per_provider_detail(tmp_path):
    rt = make_runtime(tmp_path, http=grok_http(ok_token_response()))
    write_doc(rt, "grok", grok_doc(NOW + timedelta(seconds=60)))

    st.run("once", ["grok"], rt)

    from scripts.utils.atomic_io import verified_load

    state = verified_load(str(rt.sidecar_path))
    entry = state["providers"]["grok"]
    assert entry["state"] == st.REFRESHED
    assert entry["consecutive_error_count"] == 0
    assert entry["last_refresh_at"] == NOW.isoformat()
    assert entry["expires_at"].startswith("2026-09-17T13:00")
    assert entry["last_error"] is None


def test_heartbeat_reports_worst_state_and_never_fails_the_run(tmp_path):
    calls: list[tuple] = []

    def flaky(service, state, **kwargs):
        calls.append((service, state, kwargs))
        raise RuntimeError("turso down")

    http = grok_http(st.HttpResponse(400, {"error": "invalid_grant"}))
    rt = make_runtime(tmp_path, http=http, heartbeat=flaky)
    write_doc(rt, "grok", grok_doc(NOW + timedelta(seconds=60)))

    report = st.run("once", ["grok"], rt)

    assert report["providers"][0]["state"] == st.NEEDS_REAUTH
    assert calls[0][0] == "subscription-tokens"
    assert calls[0][1] == "error"


def test_missing_pushover_credentials_are_reported_not_skipped(tmp_path):
    http = grok_http(st.HttpResponse(400, {"error": "invalid_grant"}))
    rt = make_runtime(tmp_path, http=http)
    rt.env.pop("PUSHOVER_TOKEN")
    write_doc(rt, "grok", grok_doc(NOW + timedelta(seconds=60)))

    report = st.run("once", ["grok"], rt)

    assert report["page_failures"] == ["pushover credentials missing"]
    assert rt.sent == []


# --------------------------------------------------------------------------
# transport trust boundary
# --------------------------------------------------------------------------


def test_plaintext_issuer_never_puts_a_refresh_token_on_the_wire(tmp_path):
    http = FakeHttp()  # any request at all raises
    rt = make_runtime(tmp_path, http=http)
    doc = grok_doc(NOW + timedelta(seconds=60))
    doc[GROK_KEY]["oidc_issuer"] = "http://auth.x.ai"
    write_doc(rt, "grok", doc)

    report = st.run("once", ["grok"], rt)

    assert report["providers"][0]["state"] == st.NEEDS_REAUTH
    assert http.calls == []


def test_discovery_may_not_redirect_the_refresh_token_to_another_host(tmp_path):
    evil = "http://evil.invalid/oauth/token"
    http = FakeHttp({DISCOVERY_URL: [st.HttpResponse(200, {"token_endpoint": evil})]})
    rt = make_runtime(tmp_path, http=http)
    write_doc(rt, "grok", grok_doc(NOW + timedelta(seconds=60)))

    report = st.run("once", ["grok"], rt)

    assert report["providers"][0]["state"] == st.NEEDS_REAUTH
    assert [m for m, _u, _b in http.calls] == ["GET"]


def test_cross_host_https_endpoint_is_refused_too(tmp_path):
    http = FakeHttp(
        {
            DISCOVERY_URL: [
                st.HttpResponse(200, {"token_endpoint": "https://evil.invalid/t"})
            ]
        }
    )
    rt = make_runtime(tmp_path, http=http)
    write_doc(rt, "grok", grok_doc(NOW + timedelta(seconds=60)))

    assert st.run("once", ["grok"], rt)["providers"][0]["state"] == st.NEEDS_REAUTH
    assert [m for m, _u, _b in http.calls] == ["GET"]


def test_http_request_refuses_a_plaintext_url():
    with pytest.raises(ValueError):
        st.http_request("POST", "http://evil.invalid/oauth/token", form={"a": "b"})


# --------------------------------------------------------------------------
# credential file safety
# --------------------------------------------------------------------------


def test_a_pre_placed_symlink_cannot_redirect_the_credential(tmp_path):
    rt = make_runtime(tmp_path, http=grok_http(ok_token_response()))
    path = write_doc(rt, "grok", grok_doc(NOW + timedelta(seconds=60)))
    outside = tmp_path / "published.json"
    outside.write_text("", encoding="utf-8")
    (path.parent / (path.name + ".radon-tmp")).symlink_to(outside)

    st.run("once", ["grok"], rt)

    assert outside.read_text() == ""
    assert not path.is_symlink()


def test_a_symlinked_credential_path_is_refused(tmp_path):
    rt = make_runtime(tmp_path, http=grok_http(ok_token_response()))
    outside = tmp_path / "published.json"
    outside.write_text(
        json.dumps(grok_doc(NOW + timedelta(seconds=60))), encoding="utf-8"
    )
    path = st.PROVIDERS["grok"].path(rt.env)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.symlink_to(outside)

    report = st.run("once", ["grok"], rt)

    assert report["providers"][0]["state"] == st.ERROR
    assert FAKE_NEW_ACCESS not in outside.read_text()


def test_a_credential_rewritten_during_the_refresh_is_not_clobbered(tmp_path):
    other = grok_doc(NOW + timedelta(hours=9), extra={"written_by": "the cli"})

    class RacingHttp(FakeHttp):
        def __call__(self, method, url, **kw):
            if method == "POST":
                path.write_text(json.dumps(other), encoding="utf-8")
            return super().__call__(method, url, **kw)

    http = RacingHttp(
        {
            DISCOVERY_URL: [st.HttpResponse(200, {"token_endpoint": GROK_TOKEN_URL})],
            GROK_TOKEN_URL: [ok_token_response()],
        }
    )
    rt = make_runtime(tmp_path, http=http)
    path = write_doc(rt, "grok", grok_doc(NOW + timedelta(seconds=60)))

    report = st.run("once", ["grok"], rt)

    assert report["providers"][0]["state"] == st.ERROR
    assert json.loads(path.read_text()) == other


def test_a_cli_refresh_that_stays_expiring_is_not_rolled_back(tmp_path):
    # The CLI wrote a short-lived token and rotated the refresh token. Falling
    # through to the HTTP path with the pre-CLI document would replace both.
    cli_doc = grok_doc(NOW + timedelta(seconds=120), extra={"written_by": "the cli"})
    cli_doc[GROK_KEY]["refresh_token"] = FAKE_NEW_REFRESH
    http = grok_http(ok_token_response())
    rt = make_runtime(tmp_path, http=http, which=lambda binary: "/usr/bin/" + binary)
    path = write_doc(rt, "grok", grok_doc(NOW + timedelta(seconds=60)))

    def fake_cli(_provider):
        path.write_text(json.dumps(cli_doc), encoding="utf-8")
        return True

    rt.run_cli = fake_cli
    st.run("once", ["grok"], rt)

    after = json.loads(path.read_text())[GROK_KEY]
    assert after["written_by"] == "the cli"
    assert http.calls[-1][2]["refresh_token"] == FAKE_NEW_REFRESH


# --------------------------------------------------------------------------
# vault protection
# --------------------------------------------------------------------------


def test_an_emptied_credential_never_overwrites_a_usable_vault_copy(tmp_path):
    good = json.dumps(anthropic_doc(NOW + timedelta(hours=1)))
    vault = FakeVault({"anthropic": good})
    rt = make_runtime(tmp_path, vault=vault, http=FakeHttp())
    path = st.PROVIDERS["anthropic"].path(rt.env)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"claudeAiOauth": {}}', encoding="utf-8")

    report = st.run("once", ["anthropic"], rt)

    assert report["providers"][0]["state"] == st.NEEDS_REAUTH
    assert vault.get("anthropic") == good


def test_restore_refuses_to_destroy_a_newer_on_disk_credential(tmp_path):
    stale = json.dumps(grok_doc(NOW - timedelta(hours=4)))
    vault = FakeVault({"grok": stale})
    rt = make_runtime(tmp_path, vault=vault)
    fresh = grok_doc(NOW + timedelta(hours=5), extra={"written_by": "the operator"})
    path = write_doc(rt, "grok", fresh)

    report = st.run("restore", ["grok"], rt)

    assert report["providers"][0]["state"] == st.ERROR
    assert report["exit_code"] == 1
    assert json.loads(path.read_text()) == fresh

    forced = st.run("restore", ["grok"], rt, force=True)
    assert forced["providers"][0]["state"] == st.RESTORED
    assert json.loads(path.read_text()) == json.loads(stale)
    # --force means the operator chose the vault copy; the slot still holds it
    assert json.loads(vault.get("grok")) == json.loads(stale)


def test_an_unparsable_file_is_reported_before_the_vault_copy_replaces_it(tmp_path):
    # A provider CLI mid-write looks exactly like this; racing it loses the
    # credential it is in the middle of minting.
    good = grok_doc(NOW + timedelta(hours=5))
    vault = FakeVault({"grok": json.dumps(good)})
    rt = make_runtime(tmp_path, vault=vault, http=FakeHttp())
    path = st.PROVIDERS["grok"].path(rt.env)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"half-writ', encoding="utf-8")

    first = st.run("once", ["grok"], rt)
    assert first["providers"][0]["state"] == st.ERROR
    assert path.read_text() == '{"half-writ'

    second = st.run("once", ["grok"], rt)
    assert second["providers"][0]["state"] == st.RESTORED
    assert json.loads(path.read_text()) == good


# --------------------------------------------------------------------------
# concurrency
# --------------------------------------------------------------------------


def test_a_second_run_skips_instead_of_replaying_the_same_refresh_token(tmp_path):
    rt = make_runtime(tmp_path, http=grok_http(ok_token_response()))
    write_doc(rt, "grok", grok_doc(NOW + timedelta(seconds=60)))

    with st.run_lock(rt.lock_path) as acquired:
        assert acquired
        report = st.run("once", ["grok"], rt)

    assert report["skipped"] == "another run holds the lock"
    assert report["providers"] == []
    assert report["exit_code"] == 0
    assert rt.sent == []


# --------------------------------------------------------------------------
# multi-account grok
# --------------------------------------------------------------------------


def test_a_second_grok_account_is_never_silently_left_to_expire(tmp_path):
    http = FakeHttp()
    rt = make_runtime(tmp_path, http=http)
    doc = grok_doc(NOW + timedelta(hours=5))
    doc["https://auth.x.ai::66666666-7777-8888-9999-000000000000"] = grok_doc(
        NOW + timedelta(seconds=60)
    )[GROK_KEY]
    write_doc(rt, "grok", doc)

    report = st.run("once", ["grok"], rt)

    assert report["providers"][0]["state"] == st.NEEDS_REAUTH
    assert "multiple grok accounts" in report["providers"][0]["last_error"]
    assert http.calls == []


# --------------------------------------------------------------------------
# sidecar integrity
# --------------------------------------------------------------------------


def test_check_mode_does_not_reset_the_consecutive_error_streak(tmp_path):
    http = grok_http(st.HttpResponse(503, {}))
    rt = make_runtime(tmp_path, http=http)
    write_doc(rt, "grok", grok_doc(NOW + timedelta(seconds=60)))

    st.run("once", ["grok"], rt)
    st.run("once", ["grok"], rt)
    st.run("check", ["grok"], rt)  # the documented read-only monitoring poll

    report = st.run("once", ["grok"], rt)
    assert report["providers"][0]["state"] == st.ERROR
    assert len(rt.sent) == 1


# --------------------------------------------------------------------------
# store failures
# --------------------------------------------------------------------------


def test_a_secret_store_import_failure_is_store_unavailable(monkeypatch):
    import builtins

    real_import = builtins.__import__

    def boom(name, *args, **kwargs):
        if name.endswith("secret_store"):
            raise ImportError("No module named 'cryptography'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", boom)
    with pytest.raises(st.VaultUnavailable):
        st.open_vault({})
