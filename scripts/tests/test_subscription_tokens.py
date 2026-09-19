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
        probe=lambda _provider, _binary, _env: st.PROBE_FAILED,
        login=lambda _provider, _binary, _env, _on_prompt, _wait: False,
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
    # Antigravity CLI (`agy`) 1.2.x: ~/.gemini/antigravity-cli/antigravity-oauth-token.
    # Google retired the Gemini CLI OAuth client for individuals on 2026-09-18.
    return {
        "token": {
            "access_token": FAKE_ACCESS,
            "token_type": "Bearer",
            "refresh_token": FAKE_REFRESH,
            "expiry": expires.isoformat().replace("+00:00", "Z"),
        },
        "auth_method": "consumer",
        "id_token": "id-fake",
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
        st.PROVIDERS["gemini"].path(env)
        == tmp_path / ".gemini" / "antigravity-cli" / "antigravity-oauth-token"
    )


def test_gemini_reads_the_antigravity_token_shape():
    expires = NOW + timedelta(hours=1)
    doc = gemini_doc(expires)
    assert st.PROVIDERS["gemini"].read_expiry(doc) == expires
    assert st.PROVIDERS["gemini"].read_refresh(doc) == FAKE_REFRESH
    # agy writes nanosecond precision; the parser must not choke on it.
    doc["token"]["expiry"] = "2026-09-18T23:05:47.355949945Z"
    assert st.PROVIDERS["gemini"].read_expiry(doc) == datetime(
        2026, 9, 18, 23, 5, 47, 355949, tzinfo=timezone.utc
    )


def test_gemini_refresh_is_agy_native_when_installed(tmp_path):
    http = FakeHttp()  # any HTTP call raises
    rt = make_runtime(tmp_path, http=http, which=lambda binary: "/home/radon/.local/bin/" + binary)
    path = write_doc(rt, "gemini", gemini_doc(NOW + timedelta(seconds=60)))
    seen: list[tuple] = []

    def fake_cli(provider, binary, _env):
        seen.append((binary, provider.probe_args))
        path.write_text(json.dumps(gemini_doc(NOW + timedelta(hours=1))), encoding="utf-8")
        return st.PROBE_OK

    rt.probe = fake_cli
    report = st.run("once", ["gemini"], rt)
    assert report["providers"][0]["state"] == st.REFRESHED
    assert seen == [("/home/radon/.local/bin/agy", ("models",))]
    assert http.calls == []


def test_gemini_token_endpoint_refresh_names_the_antigravity_client(tmp_path):
    http = FakeHttp({"https://oauth2.googleapis.com/token": [ok_token_response()]})
    rt = make_runtime(tmp_path, http=http)
    path = write_doc(rt, "gemini", gemini_doc(NOW + timedelta(seconds=60)))
    report = st.run("once", ["gemini"], rt)
    assert report["providers"][0]["state"] == st.REFRESHED
    method, url, form = http.calls[-1]
    assert url == "https://oauth2.googleapis.com/token"
    assert form["grant_type"] == "refresh_token"
    assert form["client_id"] == st.ANTIGRAVITY_CLIENT_ID
    after = json.loads(path.read_text())
    assert after["token"]["access_token"] == FAKE_NEW_ACCESS
    assert after["auth_method"] == "consumer"
    assert st.PROVIDERS["gemini"].read_expiry(after) > NOW + timedelta(minutes=30)


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

    def fake_cli(provider, _binary, _env):
        doc = grok_doc(NOW + timedelta(hours=2))
        path.write_text(json.dumps(doc), encoding="utf-8")
        return st.PROBE_OK

    rt.probe = fake_cli
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

    def fake_cli(_provider, _binary, _env):
        path.write_text(json.dumps(cli_doc), encoding="utf-8")
        return st.PROBE_OK

    rt.probe = fake_cli
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


# --------------------------------------------------------------------------
# refresh grant shape (2026-09-18 audit: only grok had ever refreshed)
# --------------------------------------------------------------------------

ANTHROPIC_TOKEN_URL = "https://console.anthropic.com/v1/oauth/token"
CODEX_TOKEN_URL = "https://auth.openai.com/oauth/token"


def fake_jwt(expires: datetime) -> str:
    import base64

    claims = json.dumps({"exp": int(expires.timestamp())}).encode("utf-8")
    body = base64.urlsafe_b64encode(claims).decode("ascii").rstrip("=")
    return f"FAKEHEADER.{body}.FAKESIGNATURE"


def real_shape_codex_doc(*, access_expires: datetime, last_refresh: datetime) -> dict:
    """The shape codex 0.155 writes: no ``tokens.expires_at`` at all."""
    return {
        "auth_mode": "chatgpt",
        "OPENAI_API_KEY": None,
        "tokens": {
            "id_token": "FAKE-id-token-0005",
            "access_token": fake_jwt(access_expires),
            "refresh_token": FAKE_REFRESH,
            "account_id": "account-fake",
        },
        "last_refresh": last_refresh.isoformat().replace("+00:00", "Z"),
    }


@pytest.mark.parametrize(
    "name, url",
    [("anthropic", ANTHROPIC_TOKEN_URL), ("codex", CODEX_TOKEN_URL)],
)
def test_refresh_grant_carries_the_providers_public_client_id(tmp_path, name, url):
    # Verified against the live endpoints with a bogus token: without client_id
    # both answer 400 invalid_request, so no refresh could ever succeed.
    http = FakeHttp({url: [ok_token_response()]})
    rt = make_runtime(tmp_path, http=http)
    write_doc(rt, name, DOCS[name](NOW + timedelta(seconds=60)))

    report = st.run("once", [name], rt)

    assert report["providers"][0]["state"] == st.REFRESHED
    sent = http.calls[-1][2]
    assert sent["client_id"] == st.PROVIDERS[name].client_id
    assert sent["client_id"]


@pytest.mark.parametrize(
    "body",
    [
        {"type": "error", "error": {"type": "invalid_request_error", "message": "Invalid request format"}},
        {"error": {"code": "missing_required_parameter", "param": "client_id"}},
        {"error": "invalid_request", "error_description": "Could not determine client ID from request."},
    ],
)
def test_a_malformed_refresh_request_is_our_bug_not_a_dead_login(tmp_path, body):
    # These are the three real 400 bodies the daemon used to page a browser
    # login for. A request the provider could not parse says nothing about
    # whether the refresh token is alive.
    http = FakeHttp({ANTHROPIC_TOKEN_URL: [st.HttpResponse(400, body)]})
    rt = make_runtime(tmp_path, http=http)
    write_doc(rt, "anthropic", anthropic_doc(NOW + timedelta(seconds=60)))

    report = st.run("once", ["anthropic"], rt)

    assert report["providers"][0]["state"] == st.ERROR
    assert rt.sent == []


@pytest.mark.parametrize(
    "response",
    [
        st.HttpResponse(401, {"error": {"code": "token_expired"}}),
        st.HttpResponse(400, {"error": {"code": "refresh_token_reused"}}),
        st.HttpResponse(400, {"error": "invalid_grant"}),
    ],
)
def test_a_dead_grant_is_needs_reauth(tmp_path, response):
    http = FakeHttp({CODEX_TOKEN_URL: [response]})
    rt = make_runtime(tmp_path, http=http)
    write_doc(rt, "codex", codex_doc(NOW + timedelta(seconds=60)))

    report = st.run("once", ["codex"], rt)

    assert report["providers"][0]["state"] == st.NEEDS_REAUTH


def test_codex_expiry_is_the_access_token_jwt_not_a_guessed_hour(tmp_path):
    # Real codex access tokens live ten days. Guessing last_refresh + 1h fired a
    # refresh fifty minutes after every login.
    http = FakeHttp()  # any request raises
    rt = make_runtime(tmp_path, http=http)
    doc = real_shape_codex_doc(
        access_expires=NOW + timedelta(days=8), last_refresh=NOW - timedelta(days=2)
    )
    write_doc(rt, "codex", doc)

    report = st.run("once", ["codex"], rt)

    assert report["providers"][0]["state"] == st.LIVE
    assert http.calls == []


def test_codex_refresh_never_adds_a_key_codex_did_not_write(tmp_path):
    http = FakeHttp(
        {
            CODEX_TOKEN_URL: [
                st.HttpResponse(
                    200,
                    {
                        "access_token": fake_jwt(NOW + timedelta(days=10)),
                        "refresh_token": FAKE_NEW_REFRESH,
                        "id_token": "FAKE-new-id-token-0006",
                    },
                )
            ]
        }
    )
    rt = make_runtime(tmp_path, http=http)
    doc = real_shape_codex_doc(
        access_expires=NOW + timedelta(seconds=60), last_refresh=NOW - timedelta(days=10)
    )
    path = write_doc(rt, "codex", doc)

    report = st.run("once", ["codex"], rt)

    assert report["providers"][0]["state"] == st.REFRESHED
    after = json.loads(path.read_text())
    assert set(after["tokens"]) == set(doc["tokens"])
    assert after["tokens"]["refresh_token"] == FAKE_NEW_REFRESH
    expires = datetime.fromisoformat(report["providers"][0]["expires_at"])
    assert expires > NOW + timedelta(days=9)


# --------------------------------------------------------------------------
# the scheduled set: claude, codex, grok, antigravity (the `gemini` row)
# --------------------------------------------------------------------------


def test_the_scheduled_set_covers_the_four_required_subscriptions(tmp_path):
    assert set(st.PROVIDERS) == {"anthropic", "codex", "grok", "gemini"}
    antigravity = st.PROVIDERS["gemini"]
    assert antigravity.cli_binary == "agy"
    assert antigravity.path({"HOME": str(tmp_path)}) == (
        tmp_path / ".gemini" / "antigravity-cli" / "antigravity-oauth-token"
    )


def test_agy_is_proven_with_an_authenticated_call_that_is_not_a_model_call():
    assert st.PROVIDERS["gemini"].probe_args == ("models",)
    # agy wants its code pasted back, so no push login can finish it.
    assert st.PROVIDERS["gemini"].login_args is None
    assert "agy" in st.PROVIDERS["gemini"].reauth_command


def test_the_claude_reauth_command_is_one_that_writes_the_credential_file():
    command = st.PROVIDERS["anthropic"].reauth_command
    assert "claude auth login" in command
    assert "setup-token" not in command


# --------------------------------------------------------------------------
# keepalive probe: a real model call proves the login and warms the grant
# --------------------------------------------------------------------------


class FakeProbe:
    def __init__(self, outcome=None, on_call=None) -> None:
        self.outcome = outcome or st.PROBE_OK
        self.on_call = on_call
        self.calls: list[str] = []

    def __call__(self, provider, binary, env):
        self.calls.append(provider.name)
        if self.on_call:
            self.on_call()
        return self.outcome


def installed(binary: str) -> str:
    return "/home/fake/.local/bin/" + binary


def test_a_live_credential_is_probed_once_per_keepalive_interval(tmp_path):
    probe = FakeProbe()
    rt = make_runtime(tmp_path, which=installed, probe=probe)
    write_doc(rt, "grok", grok_doc(NOW + timedelta(days=30)))

    first = st.run("once", ["grok"], rt)
    assert first["providers"][0]["state"] == st.LIVE
    assert first["providers"][0]["last_probe_at"] == NOW.isoformat()

    rt.now = lambda: NOW + timedelta(hours=23)
    st.run("once", ["grok"], rt)
    assert probe.calls == ["grok"]

    rt.now = lambda: NOW + st.KEEPALIVE_INTERVAL + timedelta(minutes=1)
    st.run("once", ["grok"], rt)
    assert probe.calls == ["grok", "grok"]


def test_a_credential_the_provider_rejects_is_not_live_whatever_its_expiry_says(tmp_path):
    rt = make_runtime(tmp_path, which=installed, probe=FakeProbe(st.PROBE_AUTH_FAILED))
    write_doc(rt, "gemini", gemini_doc(NOW + timedelta(hours=1)))

    report = st.run("once", ["gemini"], rt)

    assert report["providers"][0]["state"] == st.NEEDS_REAUTH
    assert report["exit_code"] == 1
    assert len(rt.sent) == 1
    assert "url" not in rt.sent[0]  # paste-code provider: the SSH command


def test_an_inconclusive_probe_neither_pages_nor_counts_as_a_keepalive(tmp_path):
    # A usage cap or a vendor outage says nothing about the login.
    probe = FakeProbe(st.PROBE_FAILED)
    rt = make_runtime(tmp_path, which=installed, probe=probe)
    write_doc(rt, "grok", grok_doc(NOW + timedelta(days=30)))

    for _ in range(4):
        report = st.run("once", ["grok"], rt)

    assert report["providers"][0]["state"] == st.LIVE
    assert report["providers"][0]["last_probe_at"] is None
    assert "inconclusive" in report["providers"][0]["last_error"]
    assert rt.sent == []
    assert len(probe.calls) == 4  # retried every run until it answers


def test_check_mode_never_probes(tmp_path):
    probe = FakeProbe()
    rt = make_runtime(tmp_path, which=installed, probe=probe)
    write_doc(rt, "grok", grok_doc(NOW + timedelta(days=30)))

    st.run("check", ["grok"], rt)

    assert probe.calls == []


def test_a_credential_the_cli_rotated_during_the_probe_is_resealed(tmp_path):
    vault = FakeVault()
    rt = make_runtime(tmp_path, vault=vault, which=installed)
    path = write_doc(rt, "grok", grok_doc(NOW + timedelta(days=30)))
    rotated = grok_doc(NOW + timedelta(days=31))
    rotated[GROK_KEY]["refresh_token"] = FAKE_NEW_REFRESH
    rt.probe = FakeProbe(on_call=lambda: path.write_text(json.dumps(rotated)))

    st.run("once", ["grok"], rt)

    assert json.loads(vault.get("grok"))[GROK_KEY]["refresh_token"] == FAKE_NEW_REFRESH


def test_the_cli_environment_is_an_allowlist(tmp_path):
    # /etc/radon/env carries ANTHROPIC_API_KEY (which OUTRANKS the subscription,
    # so the probe would bill the metered key and prove nothing) plus every
    # other Radon secret. A third-party agent CLI gets none of it.
    env = {
        "HOME": str(tmp_path),
        "PATH": "/usr/bin",
        "CLAUDE_CONFIG_DIR": str(tmp_path / "cfg"),
        "ANTHROPIC_API_KEY": "FAKE-metered",
        "CLAUDE_CODE_OAUTH_TOKEN": "FAKE-env-token",
        "TURSO_AUTH_TOKEN": "FAKE-turso",
        "UW_TOKEN": "FAKE-uw",
        "PUSHOVER_TOKEN": "FAKE-push",
    }

    child = st.cli_env(st.PROVIDERS["anthropic"], env)

    assert child["HOME"] == str(tmp_path)
    assert child["CLAUDE_CONFIG_DIR"] == str(tmp_path / "cfg")
    assert child["PATH"].split(":")[0] == str(tmp_path / ".local" / "bin")
    assert not [key for key in child if "KEY" in key or "TOKEN" in key]


def test_binaries_in_the_radon_local_bin_are_found_off_the_unit_path(tmp_path):
    # grok and agy install to ~/.local/bin, which systemd's PATH does not carry.
    local_bin = tmp_path / ".local" / "bin"
    local_bin.mkdir(parents=True)
    binary = local_bin / "grok"
    binary.write_text("#!/bin/sh\nexit 0\n")
    binary.chmod(0o755)

    rt = st.Runtime(env={"HOME": str(tmp_path), "PATH": "/nonexistent"})

    assert rt.which("grok") == str(binary)


def fake_cli(tmp_path: Path, name: str, script: str) -> str:
    path = tmp_path / name
    path.write_text("#!/bin/sh\n" + script)
    path.chmod(0o755)
    return str(path)


@pytest.mark.parametrize(
    "provider, script, expected",
    [
        ("anthropic", "echo ok\n", "PROBE_OK"),
        ("anthropic", "echo 'Not logged in · Please run /login'\nexit 1\n", "PROBE_AUTH_FAILED"),
        ("grok", "echo 'Error: Not signed in. To authenticate without a browser' >&2\nexit 1\n", "PROBE_AUTH_FAILED"),
        ("codex", "echo 'ERROR: unexpected status 401 Unauthorized: Missing bearer' >&2\nexit 1\n", "PROBE_AUTH_FAILED"),
        ("gemini", "echo 'Error: Please sign in to view available models. Launch the CLI without arguments to sign in.'\nexit 1\n", "PROBE_AUTH_FAILED"),
        ("anthropic", "echo 'usage limit reached'\nexit 1\n", "PROBE_FAILED"),
        ("anthropic", "sleep 30\n", "PROBE_FAILED"),
    ],
)
def test_the_real_probe_classifies_the_output_each_cli_actually_prints(
    tmp_path, provider, script, expected
):
    # Every failure string here was captured from the CLI on the VPS, run
    # against a throwaway home.
    binary = fake_cli(tmp_path, "cli", script)
    env = {"HOME": str(tmp_path), "PATH": "/usr/bin:/bin"}

    outcome = st.default_probe(st.PROVIDERS[provider], binary, env, timeout=2)

    assert outcome == getattr(st, expected)


# --------------------------------------------------------------------------
# push-driven login: the page IS the login link
# --------------------------------------------------------------------------

CODEX_DEVICE_OUTPUT = (
    "\x1b[1mWelcome to Codex\x1b[0m [v0.155.1]\r\n"
    "Follow these steps to sign in with ChatGPT using device code authorization:\n\n"
    "1. Open this link in your browser and sign in to your account\n"
    "   \x1b[94mhttps://auth.openai.com/codex/device\x1b[0m\n\n"
    "2. Enter this one-time code (expires in 15 minutes)\n"
    "   \x1b[94mKZE1-CPFLP\x1b[0m\n"
)
GROK_DEVICE_OUTPUT = (
    "To sign in, open this URL in your browser:\n\n"
    "  https://accounts.x.ai/oauth2/device?user_code=ZTZA-TPED\n\n"
    "Confirm this code in your browser:\n\n  ZTZA-TPED\n"
)
AGY_LOGIN_URL = (
    "https://accounts.google.com/o/oauth2/auth?access_type=offline"
    "&client_id=fake.apps.googleusercontent.com&code_challenge=" + "c" * 43 +
    "&redirect_uri=https%3A%2F%2Fantigravity.google%2Foauth-callback&scope=" + "s" * 420
)


def test_login_prompts_are_parsed_from_the_real_cli_output():
    codex = st.parse_login_prompt(st.PROVIDERS["codex"], CODEX_DEVICE_OUTPUT)
    assert codex == st.LoginPrompt("https://auth.openai.com/codex/device", "KZE1-CPFLP")

    grok = st.parse_login_prompt(st.PROVIDERS["grok"], GROK_DEVICE_OUTPUT)
    assert grok.url == "https://accounts.x.ai/oauth2/device?user_code=ZTZA-TPED"
    assert grok.code == "ZTZA-TPED"


def test_codex_prompt_waits_for_the_code_the_url_does_not_carry():
    partial = CODEX_DEVICE_OUTPUT.split("2. Enter")[0]
    assert st.parse_login_prompt(st.PROVIDERS["codex"], partial) is None


def test_a_url_on_any_other_host_is_never_pushed_to_the_operators_phone():
    hostile = (
        "Open https://auth.openai.com.evil.example/codex/device and enter ABCD-EFGH\n"
        "or https://evil.example/?next=https://auth.openai.com/codex/device\n"
    )
    assert st.parse_login_prompt(st.PROVIDERS["codex"], hostile) is None


def healing_login(path: Path, doc: dict, prompt):
    def login(provider, binary, env, on_prompt, wait_seconds):
        on_prompt(prompt)
        path.write_text(json.dumps(doc), encoding="utf-8")
        return True

    return login


def test_a_dead_grok_login_is_healed_by_one_tap_on_the_page(tmp_path):
    vault = FakeVault()
    rt = invalid_grant_runtime(tmp_path, vault=vault)
    rt.which = installed
    # Rejected before the login, accepted when the healed file is re-evaluated.
    outcomes = iter([st.PROBE_AUTH_FAILED, st.PROBE_OK])
    rt.probe = lambda *_a: next(outcomes)
    path = st.PROVIDERS["grok"].path(rt.env)
    fresh = grok_doc(NOW + timedelta(days=30))
    fresh[GROK_KEY]["refresh_token"] = FAKE_NEW_REFRESH
    prompt = st.LoginPrompt("https://accounts.x.ai/oauth2/device?user_code=ZTZA-TPED", "ZTZA-TPED")
    rt.login = healing_login(path, fresh, prompt)

    report = st.run("once", ["grok"], rt)

    assert report["exit_code"] == 0
    assert report["providers"][0]["state"] in st.HEALTHY_STATES
    assert len(rt.sent) == 1
    page = rt.sent[0]
    assert page["url"] == prompt.url
    assert page["url_title"]
    assert "ZTZA-TPED" in page["message"]
    assert json.loads(vault.get("grok"))[GROK_KEY]["refresh_token"] == FAKE_NEW_REFRESH
    for needle in (FAKE_ACCESS, FAKE_REFRESH, FAKE_NEW_REFRESH):
        assert needle not in json.dumps(rt.sent)


def test_an_ignored_login_link_is_not_resent_inside_the_cooldown(tmp_path):
    rt = invalid_grant_runtime(tmp_path)
    rt.which = installed
    rt.probe = FakeProbe(st.PROBE_AUTH_FAILED)
    attempts: list[int] = []

    def ignored(provider, binary, env, on_prompt, wait_seconds):
        attempts.append(wait_seconds)
        on_prompt(st.LoginPrompt("https://accounts.x.ai/oauth2/device?user_code=AAAA-BBBB", "AAAA-BBBB"))
        return False

    rt.login = ignored

    first = st.run("once", ["grok"], rt)
    st.run("once", ["grok"], rt)

    assert first["providers"][0]["state"] == st.NEEDS_REAUTH
    assert len(attempts) == 1
    assert 0 < attempts[0] <= st.LOGIN_WAIT_SECONDS
    assert len(rt.sent) == 1

    rt.now = lambda: NOW + timedelta(hours=13)
    st.run("once", ["grok"], rt)
    assert len(attempts) == 2


def test_a_login_that_never_prints_a_link_falls_back_to_the_plain_page(tmp_path):
    rt = invalid_grant_runtime(tmp_path)
    rt.which = installed
    rt.probe = FakeProbe(st.PROBE_AUTH_FAILED)
    rt.login = lambda provider, binary, env, on_prompt, wait_seconds: False

    st.run("once", ["grok"], rt)

    assert len(rt.sent) == 1
    assert "url" not in rt.sent[0]
    assert st.PROVIDERS["grok"].reauth_command in rt.sent[0]["message"]


def test_only_one_login_runs_per_timer_fire_and_the_next_provider_keeps_its_turn(tmp_path):
    # Each login can hold the oneshot for LOGIN_WAIT_SECONDS. The provider that
    # lost the slot must NOT be plain-paged: that stamps its 12h cooldown, and
    # with codex always evaluated first grok would never get a link.
    http = FakeHttp(
        {
            DISCOVERY_URL: [st.HttpResponse(200, {"token_endpoint": GROK_TOKEN_URL})],
            GROK_TOKEN_URL: [st.HttpResponse(400, {"error": "invalid_grant"})],
            CODEX_TOKEN_URL: [st.HttpResponse(400, {"error": "invalid_grant"})],
        }
    )
    rt = make_runtime(tmp_path, http=http, which=installed, probe=FakeProbe(st.PROBE_AUTH_FAILED))
    write_doc(rt, "grok", grok_doc(NOW + timedelta(seconds=60)))
    write_doc(rt, "codex", codex_doc(NOW + timedelta(seconds=60)))
    started: list[str] = []
    rt.login = lambda provider, *_a: started.append(provider.name) or False

    st.run("once", ["codex", "grok"], rt)

    assert started == ["codex"]
    assert ["codex" in page["message"] for page in rt.sent] == [True]

    st.run("once", ["codex", "grok"], rt)  # codex is now inside its cooldown

    assert started == ["codex", "grok"]


# --------------------------------------------------------------------------
# second-pass adversarial review (2026-09-18)
# --------------------------------------------------------------------------


def test_a_failed_probe_after_the_cli_rotated_the_token_never_replays_the_old_one(tmp_path):
    # The CLI refreshed (rotating the refresh token) and THEN its model call hit
    # a usage cap. Presenting the pre-probe token is refresh-token reuse, which
    # pages a healthy login and can revoke the whole token family.
    http = grok_http(ok_token_response())
    rt = make_runtime(tmp_path, http=http, which=installed)
    path = write_doc(rt, "grok", grok_doc(NOW + timedelta(seconds=60)))
    rotated = grok_doc(NOW + timedelta(seconds=90))
    rotated[GROK_KEY]["refresh_token"] = FAKE_NEW_REFRESH
    rt.probe = FakeProbe(st.PROBE_FAILED, on_call=lambda: path.write_text(json.dumps(rotated)))

    st.run("once", ["grok"], rt)

    assert http.calls[-1][2]["refresh_token"] == FAKE_NEW_REFRESH


def test_an_inconclusive_keepalive_still_reseals_what_the_cli_rotated(tmp_path):
    vault = FakeVault()
    rt = make_runtime(tmp_path, vault=vault, which=installed)
    path = write_doc(rt, "grok", grok_doc(NOW + timedelta(days=30)))
    rotated = grok_doc(NOW + timedelta(days=31))
    rotated[GROK_KEY]["refresh_token"] = FAKE_NEW_REFRESH
    rt.probe = FakeProbe(st.PROBE_FAILED, on_call=lambda: path.write_text(json.dumps(rotated)))

    st.run("once", ["grok"], rt)

    assert json.loads(vault.get("grok"))[GROK_KEY]["refresh_token"] == FAKE_NEW_REFRESH


def test_a_discovery_outage_is_an_error_not_a_login_request(tmp_path):
    http = FakeHttp({DISCOVERY_URL: [st.HttpResponse(503, {})]})
    rt = make_runtime(tmp_path, http=http, which=installed)
    write_doc(rt, "grok", grok_doc(NOW + timedelta(seconds=60)))
    started: list[str] = []
    rt.login = lambda provider, *_a: started.append(provider.name) or False

    report = st.run("once", ["grok"], rt)

    assert report["providers"][0]["state"] == st.ERROR
    assert started == []
    assert rt.sent == []


def test_a_login_nothing_has_proven_for_days_stops_claiming_to_be_live(tmp_path):
    # A vendor rewording its auth error leaves every probe "inconclusive".
    rt = make_runtime(tmp_path, which=installed, probe=FakeProbe(st.PROBE_FAILED))
    write_doc(rt, "grok", grok_doc(NOW + timedelta(days=30)))

    first = st.run("once", ["grok"], rt)
    assert first["providers"][0]["state"] == st.LIVE

    rt.now = lambda: NOW + st.KEEPALIVE_UNPROVEN_LIMIT + timedelta(minutes=1)
    late = st.run("once", ["grok"], rt)
    assert late["providers"][0]["state"] == st.ERROR
    assert "unproven" in late["providers"][0]["last_error"]

    rt.probe = FakeProbe(st.PROBE_OK)
    healed = st.run("once", ["grok"], rt)
    assert healed["providers"][0]["state"] == st.LIVE


@pytest.mark.parametrize(
    "response",
    [
        st.HttpResponse(401, {"error": "invalid_client"}),
        st.HttpResponse(400, {"error": "unauthorized_client"}),
        st.HttpResponse(400, {"error": {"code": ["not", "a", "string"]}}),
    ],
)
def test_a_rejected_client_is_our_bug_not_a_dead_login(tmp_path, response):
    http = FakeHttp({CODEX_TOKEN_URL: [response]})
    rt = make_runtime(tmp_path, http=http)
    write_doc(rt, "codex", codex_doc(NOW + timedelta(seconds=60)))

    report = st.run("once", ["codex"], rt)

    assert report["providers"][0]["state"] == st.ERROR


def test_a_dead_grant_is_never_presented_a_second_time(tmp_path):
    http = FakeHttp(
        {
            CODEX_TOKEN_URL: [
                st.HttpResponse(400, {"error": {"code": "refresh_token_reused"}}),
                st.HttpResponse(415, {}),
            ]
        }
    )
    rt = make_runtime(tmp_path, http=http)
    write_doc(rt, "codex", codex_doc(NOW + timedelta(seconds=60)))

    report = st.run("once", ["codex"], rt)

    assert report["providers"][0]["state"] == st.NEEDS_REAUTH
    assert len([c for c in http.calls if c[0] == "POST"]) == 1


def test_an_absurd_expiry_claim_is_no_expiry_not_a_crash():
    import base64

    for exp in (10**30, -(10**30), float("inf")):
        claims = base64.urlsafe_b64encode(json.dumps({"exp": exp}).encode()).decode().rstrip("=")
        assert st._jwt_expiry(f"FAKEHEADER.{claims}.FAKESIG") is None
    assert st.PROVIDERS["anthropic"].read_expiry({"claudeAiOauth": {"expiresAt": 10**30}}) is None


def test_a_refresh_is_not_started_once_the_run_budget_is_spent(tmp_path):
    http = FakeHttp()  # any request raises
    rt = make_runtime(tmp_path, http=http)
    write_doc(rt, "grok", grok_doc(NOW + timedelta(seconds=60)))
    clock = iter([0.0] + [st.RUN_BUDGET_SECONDS - 30.0] * 50)
    rt.monotonic = lambda: next(clock)

    report = st.run("once", ["grok"], rt)

    assert report["providers"][0]["state"] == st.ERROR
    assert "budget" in report["providers"][0]["last_error"]
    assert http.calls == []


def test_check_mode_never_pages(tmp_path, monkeypatch):
    monkeypatch.setattr(
        st, "open_vault", lambda _env: (_ for _ in ()).throw(st.VaultUnavailable("boom"))
    )
    rt = make_runtime(tmp_path)
    rt.vault = None

    report = st.run("check", ["grok"], rt)

    assert report["exit_code"] == 78
    assert rt.sent == []


def test_reauth_json_output_is_one_clean_document(tmp_path, capsys):
    rt = make_runtime(tmp_path, which=installed)
    rt.vault = None
    path = write_doc(rt, "grok", grok_doc(NOW - timedelta(days=1)))
    prompt = st.LoginPrompt("https://accounts.x.ai/oauth2/device?user_code=ZTZA-TPED", "ZTZA-TPED")
    rt.login = healing_login(path, grok_doc(NOW + timedelta(days=30)), prompt)

    st.run("reauth", ["grok"], rt, json_output=True)

    captured = capsys.readouterr()
    assert json.loads(captured.out)["providers"][0]["state"] == st.LIVE
    assert "ZTZA-TPED" in captured.err


def test_login_links_survive_terminal_decoration():
    osc = (
        "Open \x1b]8;;https://accounts.x.ai/oauth2/device?user_code=ZTZA-TPED\x1b\\"
        "(https://accounts.x.ai/oauth2/device?user_code=ZTZA-TPED).\x1b]8;;\x1b\\ \n"
        "Build ABCD-EFGH \n"
    )
    prompt = st.parse_login_prompt(st.PROVIDERS["grok"], osc)
    assert prompt.url == "https://accounts.x.ai/oauth2/device?user_code=ZTZA-TPED"
    # The code the URL itself carries outranks any look-alike in the banner.
    assert prompt.code == "ZTZA-TPED"


def test_a_helper_that_outlives_the_cli_does_not_turn_success_into_a_timeout(tmp_path):
    import time as _time

    # The CLI exits 0 while a child still holds its stdout open.
    binary = fake_cli(tmp_path, "cli", "sleep 30 &\necho ok\nexit 0\n")

    started = _time.monotonic()
    outcome = st.default_probe(
        st.PROVIDERS["anthropic"], binary, {"HOME": str(tmp_path), "PATH": "/usr/bin:/bin"}, timeout=5
    )

    assert outcome == st.PROBE_OK
    assert _time.monotonic() - started < 4


def test_a_run_short_on_time_pages_the_command_instead_of_starting_a_login(tmp_path):
    # The unit SIGKILLs the oneshot at TimeoutStartSec; a login or probe started
    # late would die with it and take the sidecar and heartbeat along.
    probe = FakeProbe(st.PROBE_AUTH_FAILED)
    rt = make_runtime(tmp_path, which=installed, probe=probe)
    dead = grok_doc(NOW + timedelta(seconds=60))
    dead[GROK_KEY].pop("refresh_token")  # needs_reauth with no request at all
    write_doc(rt, "grok", dead)
    clock = iter([0.0] + [st.RUN_BUDGET_SECONDS - 30.0] * 50)
    rt.monotonic = lambda: next(clock)
    started: list[str] = []
    rt.login = lambda provider, *_a: started.append(provider.name) or False

    st.run("once", ["grok"], rt)

    assert started == []
    assert probe.calls == []
    assert len(rt.sent) == 1
    assert "url" not in rt.sent[0]


def test_no_login_is_started_without_a_way_to_deliver_the_link(tmp_path):
    rt = invalid_grant_runtime(tmp_path)
    rt.which = installed
    rt.probe = FakeProbe(st.PROBE_AUTH_FAILED)
    rt.env = {k: v for k, v in rt.env.items() if not k.startswith("PUSHOVER_")}
    started: list[str] = []
    rt.login = lambda provider, *_a: started.append(provider.name) or False

    report = st.run("once", ["grok"], rt)

    assert started == []
    assert report["page_failures"] == ["pushover credentials missing"]


def test_a_link_longer_than_pushovers_url_field_rides_in_the_message():
    # A Google consent URL is ~700 chars; Pushover's url field takes 512.
    assert len(AGY_LOGIN_URL) > st.PUSHOVER_URL_FIELD_MAX
    result = st.ProviderResult("grok", st.NEEDS_REAUTH, last_error="x" * 600)

    page = st._page_body(result, st.LoginPrompt(AGY_LOGIN_URL, None))

    assert "url" not in page
    assert AGY_LOGIN_URL in page["message"]
    assert len(page["message"]) <= st.PUSHOVER_MESSAGE_MAX


def test_a_link_cut_off_mid_read_is_never_the_one_that_gets_pushed():
    # Output arrives in arbitrary chunks; this prefix is a valid URL on an
    # allowed host that signs the operator in to nothing.
    cut = "To sign in, open this URL in your browser:\n\n  https://accounts.x.ai/oauth2/dev"
    assert st.parse_login_prompt(st.PROVIDERS["grok"], cut) is None
    cut_code = CODEX_DEVICE_OUTPUT.split("CPFLP")[0]
    assert st.parse_login_prompt(st.PROVIDERS["codex"], cut_code) is None


def printing(output: str) -> str:
    return "printf '" + output.replace("\n", "\\n") + "'\n"


def test_the_real_login_runner_pushes_the_prompt_and_reports_the_exit(tmp_path):
    script = printing(GROK_DEVICE_OUTPUT) + "sleep 0.2\nexit 0\n"
    binary = fake_cli(tmp_path, "grok", script)
    prompts: list = []

    ok = st.default_login(
        st.PROVIDERS["grok"], binary, {"HOME": str(tmp_path), "PATH": "/usr/bin:/bin"},
        prompts.append, 5,
    )

    assert ok is True
    assert [p.code for p in prompts] == ["ZTZA-TPED"]


def test_the_real_login_runner_kills_a_login_nobody_approved(tmp_path):
    import time as _time

    # No trailing newline after the spinner, as the real CLIs print it.
    script = printing(GROK_DEVICE_OUTPUT + "Waiting for authorization...") + "sleep 60\n"
    binary = fake_cli(tmp_path, "grok", script)
    prompts: list = []

    started = _time.monotonic()
    ok = st.default_login(
        st.PROVIDERS["grok"], binary, {"HOME": str(tmp_path), "PATH": "/usr/bin:/bin"},
        prompts.append, 1,
    )

    assert ok is False
    assert len(prompts) == 1
    assert _time.monotonic() - started < 10


def test_reauth_mode_ignores_the_cooldown_and_needs_no_vault(tmp_path, monkeypatch):
    # The operator retry after a missed window, run from a bare shell where the
    # store key is unavailable by design.
    monkeypatch.setattr(st, "open_vault", lambda _env: (_ for _ in ()).throw(AssertionError("vault opened")))
    rt = make_runtime(tmp_path, which=installed)
    rt.vault = None
    path = write_doc(rt, "grok", grok_doc(NOW - timedelta(days=1)))
    prompt = st.LoginPrompt("https://accounts.x.ai/oauth2/device?user_code=ZTZA-TPED", "ZTZA-TPED")
    rt.login = healing_login(path, grok_doc(NOW + timedelta(days=30)), prompt)

    first = st.run("reauth", ["grok"], rt)
    second = st.run("reauth", ["grok"], rt)

    assert first["providers"][0]["state"] == st.LIVE
    assert second["providers"][0]["state"] == st.LIVE
    assert len(rt.sent) == 2


def test_reauth_mode_names_the_command_for_a_paste_code_provider(tmp_path):
    rt = make_runtime(tmp_path, which=installed)
    rt.vault = None

    report = st.run("reauth", ["anthropic"], rt)

    assert report["providers"][0]["state"] == st.ERROR
    assert "claude auth login" in report["providers"][0]["last_error"]


def test_lock_lives_in_the_radon_owned_state_dir_not_world_writable_run_lock():
    # /run/lock is 1777: any local user could pre-hold LOCK_EX and turn every
    # timer fire into a silent exit-0 skip. The lock belongs beside the
    # sidecar, inside the unit's own StateDirectory.
    assert st.LOCK_PATH.parent == st.SIDECAR_PATH.parent
