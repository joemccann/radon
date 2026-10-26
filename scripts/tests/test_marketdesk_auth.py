"""MarketDesk auth: Firebase refresh-token exchange, password fallback, server login, session files.

No network: Firebase calls go through an injected ``post``; the browser context and page are fakes.
"""
import json
import os
import stat
import urllib.parse
from pathlib import Path

import pytest

from research import marketdesk_auth as auth
from research import marketdesk_bootstrap as bootstrap

FIXTURES = Path(__file__).parent / "fixtures" / "marketdesk"
REFRESH_URL = "https://securetoken.googleapis.com/v1/token?key=AIzaSyCf8fXJkGM9Jgtzl6sdaMLe_ipwtmWdvNc"  # gitleaks:allow
PASSWORD_URL = (
    "https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword"
    "?key=AIzaSyCf8fXJkGM9Jgtzl6sdaMLe_ipwtmWdvNc"  # gitleaks:allow
)
LOGIN_URL = "https://marketdesk.ai/api/user/login"
SECRET_REFRESH = "REFRESH-SECRET-abc123"
SECRET_PASSWORD = "hunter2-SECRET"  # gitleaks:allow


class FakePost:
    """Records Firebase REST calls; answers from a queue of (status, json-able)."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls = []

    def __call__(self, url, body, headers):
        self.calls.append({"url": url, "body": body, "headers": dict(headers)})
        if not self.answers:
            raise AssertionError("unexpected Firebase call")
        status, payload = self.answers.pop(0)
        return status, json.dumps(payload).encode()


class FakeResponse:
    def __init__(self, status=200, payload=None):
        self.status = status
        self._payload = payload if payload is not None else {"success": True}

    @property
    def ok(self):
        return 200 <= self.status < 300

    def json(self):
        return self._payload


class FakeRequest:
    def __init__(self, response=None):
        self.response = response or FakeResponse()
        self.posts = []

    def post(self, url, **kwargs):
        self.posts.append({"url": url, **kwargs})
        return self.response


class FakeContext:
    def __init__(self, response=None, state=None):
        self.request = FakeRequest(response)
        self.state = state or {"cookies": [{"name": "sid", "value": "x"}], "origins": []}
        self.storage_calls = []

    def storage_state(self, **kwargs):
        self.storage_calls.append(kwargs)
        return self.state


class FakePage:
    """``html_sequence`` is what each successive goto lands on."""

    def __init__(self, *html_sequence):
        self.html_sequence = list(html_sequence)
        self.current = ""
        self.gotos = []

    def goto(self, url, **kwargs):
        self.gotos.append(url)
        self.current = self.html_sequence.pop(0) if self.html_sequence else self.current

    def wait_for_selector(self, selector, **kwargs):
        return None

    def content(self):
        return self.current


APP_HTML = '<html><body><div id="app" data-v-app><nav class="nav"><a href="/library">Library</a></nav></div></body></html>'
LANDING_HTML = (FIXTURES / "landing.html").read_text()


@pytest.fixture
def env(tmp_path):
    return {"RADON_RESEARCH_DIR": str(tmp_path / "research")}


def _form(call):
    return dict(urllib.parse.parse_qsl(call["body"].decode()))


# ---- constants and drift guards ------------------------------------------------------------

def test_firebase_api_key_matches_captured_config():
    config = json.loads((FIXTURES / "firebase_config.json").read_text())
    assert config["apiKey"] == auth.FIREBASE_API_KEY
    assert f'apiKey: "{auth.FIREBASE_API_KEY}"' in (FIXTURES / "app-landing.js").read_text()


def test_login_endpoint_matches_captured_landing_script():
    script = (FIXTURES / "app-landing.js").read_text()
    assert "API.post('/api/user/login', {F: token})" in script
    assert auth.BASE_URL + "/api/user/login" == LOGIN_URL


def test_landing_capture_is_classified_unauthenticated():
    assert auth.page_state(LANDING_HTML) == "landing"
    assert auth.page_state(APP_HTML) == "app"
    assert auth.page_state("<html><body><p>loading</p></body></html>") == "unknown"


# ---- session dir ---------------------------------------------------------------------------

def test_session_dir_is_private_under_research_dir(env, tmp_path):
    path = auth.session_dir(env)
    assert path == tmp_path / "research" / "marketdesk"
    assert stat.S_IMODE(path.stat().st_mode) == 0o700


def test_session_dir_rejects_symlink(env, tmp_path):
    (tmp_path / "research").mkdir()
    (tmp_path / "elsewhere").mkdir()
    os.symlink(tmp_path / "elsewhere", tmp_path / "research" / "marketdesk")
    with pytest.raises(auth.MarketDeskAuthError):
        auth.session_dir(env)


# ---- refresh token exchange ----------------------------------------------------------------

def test_exchange_posts_exact_url_and_form_and_returns_rotated_token():
    post = FakePost((200, {"id_token": "ID-1", "refresh_token": "ROTATED-1", "expires_in": "3600"}))
    id_token, rotated = auth.exchange_refresh_token(SECRET_REFRESH, post=post)
    assert (id_token, rotated) == ("ID-1", "ROTATED-1")
    assert len(post.calls) == 1
    call = post.calls[0]
    assert call["url"] == REFRESH_URL
    assert _form(call) == {"grant_type": "refresh_token", "refresh_token": SECRET_REFRESH}
    assert call["headers"]["Content-Type"] == "application/x-www-form-urlencoded"
    assert call["headers"]["Referer"] == "https://marketdesk.ai/"


def test_exchange_keeps_token_when_response_omits_rotation():
    post = FakePost((200, {"id_token": "ID-1"}))
    assert auth.exchange_refresh_token(SECRET_REFRESH, post=post) == ("ID-1", SECRET_REFRESH)


def test_exchange_rejection_names_code_without_secret():
    post = FakePost((400, {"error": {"message": "TOKEN_EXPIRED"}}))
    with pytest.raises(auth.MarketDeskAuthError) as caught:
        auth.exchange_refresh_token(SECRET_REFRESH, post=post)
    assert "TOKEN_EXPIRED" in str(caught.value)
    assert SECRET_REFRESH not in str(caught.value)


def test_exchange_drops_untrusted_error_text():
    post = FakePost((400, {"error": {"message": "bad token " + SECRET_REFRESH}}))
    with pytest.raises(auth.MarketDeskAuthError) as caught:
        auth.exchange_refresh_token(SECRET_REFRESH, post=post)
    assert SECRET_REFRESH not in str(caught.value)


def test_exchange_rejects_empty_token_without_network():
    post = FakePost()
    with pytest.raises(auth.MarketDeskAuthError):
        auth.exchange_refresh_token("", post=post)
    assert post.calls == []


# ---- password fallback ---------------------------------------------------------------------

def test_password_sign_in_posts_exact_url_and_json():
    post = FakePost((200, {"idToken": "ID-PW", "refreshToken": "R"}))
    assert auth.password_id_token("joe@example.com", SECRET_PASSWORD, post=post) == "ID-PW"
    call = post.calls[0]
    assert call["url"] == PASSWORD_URL
    assert json.loads(call["body"]) == {
        "email": "joe@example.com", "password": SECRET_PASSWORD, "returnSecureToken": True,
    }
    assert call["headers"]["Content-Type"] == "application/json"


@pytest.mark.parametrize("code", ["INVALID_PASSWORD", "INVALID_LOGIN_CREDENTIALS"])
def test_password_rejection_maps_to_disabled_without_secret(code):
    post = FakePost((400, {"error": {"message": code}}))
    with pytest.raises(auth.MarketDeskAuthError) as caught:
        auth.password_id_token("joe@example.com", SECRET_PASSWORD, post=post)
    message = str(caught.value)
    assert "password sign-in disabled" in message
    assert SECRET_PASSWORD not in message and "joe@example.com" not in message


# ---- token persistence ---------------------------------------------------------------------

def test_env_seed_used_when_no_rotated_file(env):
    env["MARKETDESK_FIREBASE_REFRESH_TOKEN"] = " SEED "
    assert auth.current_refresh_token(env) == "SEED"


def test_no_token_anywhere_returns_none(env):
    assert auth.current_refresh_token(env) is None


def test_rotated_token_persisted_0600_and_preferred_over_env(env):
    env["MARKETDESK_FIREBASE_REFRESH_TOKEN"] = "SEED"
    auth.save_refresh_token("ROTATED", env)
    path = auth.session_dir(env) / "firebase.json"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert json.loads(path.read_text())["refresh_token"] == "ROTATED"
    assert auth.current_refresh_token(env) == "ROTATED"
    assert [p.name for p in path.parent.iterdir()] == ["firebase.json"]  # no temp leftovers


def test_corrupt_token_file_falls_back_to_env(env):
    env["MARKETDESK_FIREBASE_REFRESH_TOKEN"] = "SEED"
    (auth.session_dir(env) / "firebase.json").write_text("{not json")
    assert auth.current_refresh_token(env) == "SEED"


def test_storage_state_persisted_atomically_0600_with_indexed_db(env):
    context = FakeContext()
    path = auth.persist_storage_state(context, env)
    assert path == auth.storage_state_path(env) == auth.session_dir(env) / "storage.json"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert json.loads(path.read_text()) == context.state
    assert context.storage_calls == [{"indexed_db": True}]


def test_existing_storage_state_returned_only_when_valid(env):
    assert auth.existing_storage_state(env) is None
    auth.storage_state_path(env).write_text("{broken")
    assert auth.existing_storage_state(env) is None
    assert not auth.storage_state_path(env).exists()  # quarantined
    auth.persist_storage_state(FakeContext(), env)
    assert auth.existing_storage_state(env) == str(auth.storage_state_path(env))


# ---- open_session --------------------------------------------------------------------------

def test_warm_session_makes_no_network_call(env):
    env["MARKETDESK_FIREBASE_REFRESH_TOKEN"] = "SEED"
    context, page, post = FakeContext(), FakePage(APP_HTML), FakePost()
    auth.open_session(context, page, env=env, post=post)
    assert post.calls == []
    assert context.request.posts == []
    assert page.gotos == ["https://marketdesk.ai/"]


def test_cold_session_exchanges_refresh_token_and_posts_id_token_to_login(env):
    env["MARKETDESK_FIREBASE_REFRESH_TOKEN"] = SECRET_REFRESH
    context = FakeContext()
    page = FakePage(LANDING_HTML, APP_HTML)
    post = FakePost((200, {"id_token": "ID-1", "refresh_token": "ROTATED-1"}))
    auth.open_session(context, page, env=env, post=post)
    assert _form(post.calls[0])["refresh_token"] == SECRET_REFRESH
    assert len(context.request.posts) == 1
    login = context.request.posts[0]
    assert login["url"] == LOGIN_URL
    assert login["data"] == {"F": "ID-1"}
    assert auth.current_refresh_token(env) == "ROTATED-1"
    assert page.gotos == ["https://marketdesk.ai/", "https://marketdesk.ai/"]


def test_stale_rotated_file_falls_back_to_new_env_seed(env):
    auth.save_refresh_token("STALE", env)
    env["MARKETDESK_FIREBASE_REFRESH_TOKEN"] = "FRESH"
    context = FakeContext()
    page = FakePage(LANDING_HTML, APP_HTML)
    post = FakePost((400, {"error": {"message": "TOKEN_EXPIRED"}}), (200, {"id_token": "ID-2", "refresh_token": "FRESH"}))
    auth.open_session(context, page, env=env, post=post)
    assert [_form(c)["refresh_token"] for c in post.calls] == ["STALE", "FRESH"]
    assert context.request.posts[0]["data"] == {"F": "ID-2"}
    assert auth.current_refresh_token(env) == "FRESH"


def test_password_fallback_when_refresh_token_fails(env):
    env.update({"MARKETDESK_FIREBASE_REFRESH_TOKEN": "SEED", "THEMARKETEAR_EMAIL": " joe@example.com ",
                "THEMARKETEAR_PASSWORD": SECRET_PASSWORD})
    context = FakeContext()
    page = FakePage(LANDING_HTML, APP_HTML)
    post = FakePost((400, {"error": {"message": "INVALID_REFRESH_TOKEN"}}), (200, {"idToken": "ID-PW"}))
    auth.open_session(context, page, env=env, post=post)
    assert post.calls[1]["url"] == PASSWORD_URL
    assert json.loads(post.calls[1]["body"])["email"] == "joe@example.com"
    assert context.request.posts == [{"url": LOGIN_URL, "data": {"F": "ID-PW"}, "timeout": 30000}]


def test_no_credentials_raises_without_network(env):
    context, page, post = FakeContext(), FakePage(LANDING_HTML), FakePost()
    with pytest.raises(auth.MarketDeskAuthError, match="no MarketDesk credentials"):
        auth.open_session(context, page, env=env, post=post)
    assert post.calls == [] and context.request.posts == []


def test_all_paths_failing_raises_without_secrets(env):
    env.update({"MARKETDESK_FIREBASE_REFRESH_TOKEN": SECRET_REFRESH, "THEMARKETEAR_EMAIL": "joe@example.com",
                "THEMARKETEAR_PASSWORD": SECRET_PASSWORD})
    post = FakePost((400, {"error": {"message": "TOKEN_EXPIRED"}}), (400, {"error": {"message": "INVALID_PASSWORD"}}))
    with pytest.raises(auth.MarketDeskAuthError) as caught:
        auth.open_session(FakeContext(), FakePage(LANDING_HTML), env=env, post=post)
    message = str(caught.value)
    assert "TOKEN_EXPIRED" in message and "password sign-in disabled" in message
    for secret in (SECRET_REFRESH, SECRET_PASSWORD, "joe@example.com"):
        assert secret not in message


def test_login_rejected_by_server_raises(env):
    env["MARKETDESK_FIREBASE_REFRESH_TOKEN"] = "SEED"
    context = FakeContext(FakeResponse(200, {"success": False}))
    post = FakePost((200, {"id_token": "ID-1", "refresh_token": "SEED"}))
    with pytest.raises(auth.MarketDeskAuthError, match="login rejected"):
        auth.open_session(context, FakePage(LANDING_HTML, LANDING_HTML), env=env, post=post)


def test_still_landing_after_successful_login_raises(env):
    env["MARKETDESK_FIREBASE_REFRESH_TOKEN"] = "SEED"
    post = FakePost((200, {"id_token": "ID-1", "refresh_token": "SEED"}))
    with pytest.raises(auth.MarketDeskAuthError, match="not authenticated after login"):
        auth.open_session(FakeContext(), FakePage(LANDING_HTML, LANDING_HTML), env=env, post=post)


# ---- bootstrap -----------------------------------------------------------------------------

def _firebase_state(refresh="BOOT-REFRESH"):
    return {
        "cookies": [],
        "origins": [{
            "origin": "https://marketdesk.ai",
            "localStorage": [],
            "indexedDB": [{
                "name": "firebaseLocalStorageDb",
                "version": 1,
                "stores": [{
                    "name": "firebaseLocalStorage",
                    "keyPath": "fbase_key",
                    "records": [{"value": {
                        "fbase_key": f"firebase:authUser:{auth.FIREBASE_API_KEY}:[DEFAULT]",
                        "value": {"uid": "u1", "stsTokenManager": {
                            "refreshToken": refresh, "accessToken": "A", "expirationTime": 1}},
                    }}],
                }],
            }],
        }],
    }


def test_bootstrap_extracts_refresh_token_from_indexed_db_state():
    assert bootstrap.extract_refresh_token(_firebase_state()) == "BOOT-REFRESH"


def test_bootstrap_ignores_other_databases_and_origins():
    state = _firebase_state()
    state["origins"][0]["indexedDB"][0]["name"] = "otherDb"
    assert bootstrap.extract_refresh_token(state) is None
    state = _firebase_state()
    state["origins"][0]["origin"] = "https://evil.example"
    assert bootstrap.extract_refresh_token(state) is None


def test_bootstrap_reads_encoded_record_values():
    state = _firebase_state()
    record = state["origins"][0]["indexedDB"][0]["stores"][0]["records"][0]
    record["valueEncoded"] = json.dumps(record.pop("value"))
    assert bootstrap.extract_refresh_token(state) == "BOOT-REFRESH"


def test_bootstrap_writes_token_file_0600(tmp_path):
    out = tmp_path / "nested" / "token"
    bootstrap.write_token_file(out, "BOOT-REFRESH")
    assert out.read_text() == "BOOT-REFRESH\n"
    assert stat.S_IMODE(out.stat().st_mode) == 0o600


def test_bootstrap_check_reports_ok_and_persists_rotation(tmp_path, capsys):
    out = tmp_path / "token"
    bootstrap.write_token_file(out, "BOOT-REFRESH")
    post = FakePost((200, {"id_token": "ID", "refresh_token": "ROTATED"}))
    assert bootstrap.check(out, post=post) == 0
    assert out.read_text() == "ROTATED\n"
    printed = capsys.readouterr().out
    assert "ok" in printed and "BOOT-REFRESH" not in printed and "ROTATED" not in printed


def test_bootstrap_check_reports_failed(tmp_path, capsys):
    out = tmp_path / "token"
    bootstrap.write_token_file(out, "BOOT-REFRESH")
    post = FakePost((400, {"error": {"message": "TOKEN_EXPIRED"}}))
    assert bootstrap.check(out, post=post) == 1
    printed = capsys.readouterr().out
    assert "failed" in printed and "BOOT-REFRESH" not in printed


def test_login_transport_error_never_carries_the_playwright_call_log():
    class Exploding:
        def post(self, url, **kwargs):
            raise TimeoutError("APIRequestContext.post: Timeout 2000ms exceeded.\nCall log:\n"
                               "  - cookie: __session=OLDSECRET\n    - set-cookie: session=NEWSECRET")

    context = FakeContext()
    context.request = Exploding()
    with pytest.raises(auth.MarketDeskAuthError) as info:
        auth._server_login(context, "id-token")
    assert str(info.value) == "login_transport"
    assert info.value.__cause__ is None and info.value.__suppress_context__ is True
