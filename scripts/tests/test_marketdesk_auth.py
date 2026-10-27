"""MarketDesk auth: Firebase refresh-token exchange, password fallback, cookie session over plain HTTP.

No network: Firebase calls go through an injected ``post``; marketdesk.ai calls go through a fake opener.
"""
import io
import json
import stat
import urllib.error
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
LATEST_URL = "https://marketdesk.ai/api/mobile/latest/latest"
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


class FakeHTTPResponse(io.BytesIO):
    def __init__(self, status, body, headers=None):
        super().__init__(body)
        self.status = status
        self.headers = headers or {}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeOpener:
    """marketdesk.ai stand-in. ``routes`` maps (method, url) to a queue of (status, body, headers)."""

    def __init__(self, routes=None):
        self.routes = {key: list(value) for key, value in (routes or {}).items()}
        self.requests = []

    def open(self, request, timeout=None):
        method = request.get_method()
        self.requests.append({"method": method, "url": request.full_url, "data": request.data,
                              "headers": {k.lower(): v for k, v in request.header_items()}, "timeout": timeout})
        queue = self.routes.get((method, request.full_url))
        if not queue:
            raise AssertionError(f"unexpected request {method} {request.full_url}")
        status, body, headers = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode()
        if isinstance(body, Exception):
            raise body
        if status >= 300:
            raise urllib.error.HTTPError(request.full_url, status, "x", headers or {}, io.BytesIO(body))
        return FakeHTTPResponse(status, body, headers)


def login_ok():
    return (200, {"success": True}, {})


@pytest.fixture
def env(tmp_path):
    return {"RADON_RESEARCH_DIR": str(tmp_path / "research"), "MARKETDESK_FIREBASE_REFRESH_TOKEN": "SEED"}


def _form(call):
    return dict(urllib.parse.parse_qsl(call["body"].decode()))


# ---- constants and drift guards ------------------------------------------------------------

def test_firebase_api_key_matches_captured_config():
    config = json.loads((FIXTURES / "firebase_config.json").read_text())
    assert config["apiKey"] == auth.FIREBASE_API_KEY
    assert auth.REFRESH_URL == REFRESH_URL and auth.PASSWORD_URL == PASSWORD_URL


def test_login_endpoint_matches_captured_landing_script():
    script = (FIXTURES / "app-landing.js").read_text()
    assert "API.post('/api/user/login', {F: token})" in script
    assert auth.LOGIN_URL == LOGIN_URL


def test_session_dir_is_private_under_research_dir(env, tmp_path):
    path = auth.session_dir(env)
    assert path == tmp_path / "research" / "marketdesk"
    assert stat.S_IMODE(path.stat().st_mode) == 0o700


def test_session_dir_rejects_symlink(env, tmp_path):
    (tmp_path / "research").mkdir()
    (tmp_path / "elsewhere").mkdir()
    (tmp_path / "research" / "marketdesk").symlink_to(tmp_path / "elsewhere")
    with pytest.raises(auth.MarketDeskAuthError):
        auth.session_dir(env)


# ---- Firebase REST -------------------------------------------------------------------------

def test_exchange_posts_exact_url_and_form_and_returns_rotated_token():
    post = FakePost((200, {"id_token": "ID-1", "refresh_token": "ROTATED"}))
    assert auth.exchange_refresh_token(SECRET_REFRESH, post=post) == ("ID-1", "ROTATED")
    call = post.calls[0]
    assert call["url"] == REFRESH_URL
    assert _form(call) == {"grant_type": "refresh_token", "refresh_token": SECRET_REFRESH}
    assert call["headers"]["Referer"] == "https://marketdesk.ai/"
    assert call["headers"]["Origin"] == "https://marketdesk.ai"


def test_exchange_keeps_token_when_response_omits_rotation():
    post = FakePost((200, {"id_token": "ID-1"}))
    assert auth.exchange_refresh_token(SECRET_REFRESH, post=post) == ("ID-1", SECRET_REFRESH)


def test_exchange_rejection_names_code_without_secret():
    post = FakePost((400, {"error": {"message": "TOKEN_EXPIRED"}}))
    with pytest.raises(auth.MarketDeskAuthError) as info:
        auth.exchange_refresh_token(SECRET_REFRESH, post=post)
    assert "TOKEN_EXPIRED" in str(info.value) and SECRET_REFRESH not in str(info.value)


def test_exchange_drops_untrusted_error_text():
    post = FakePost((400, {"error": {"message": "token REFRESH-SECRET-abc123 is bad"}}))
    with pytest.raises(auth.MarketDeskAuthError) as info:
        auth.exchange_refresh_token(SECRET_REFRESH, post=post)
    assert "UNRECOGNIZED" in str(info.value) and SECRET_REFRESH not in str(info.value)


def test_exchange_rejects_empty_token_without_network():
    post = FakePost()
    with pytest.raises(auth.MarketDeskAuthError, match="refresh token missing"):
        auth.exchange_refresh_token("  ", post=post)
    assert post.calls == []


def test_password_sign_in_posts_exact_url_and_json():
    post = FakePost((200, {"idToken": "ID-P"}))
    assert auth.password_id_token("a@b.c", SECRET_PASSWORD, post=post) == "ID-P"
    assert post.calls[0]["url"] == PASSWORD_URL
    assert json.loads(post.calls[0]["body"]) == {"email": "a@b.c", "password": SECRET_PASSWORD,
                                                 "returnSecureToken": True}


@pytest.mark.parametrize("code", ["INVALID_PASSWORD", "INVALID_LOGIN_CREDENTIALS", "PASSWORD_LOGIN_DISABLED"])
def test_password_rejection_maps_to_disabled_without_secret(code):
    post = FakePost((400, {"error": {"message": code}}))
    with pytest.raises(auth.MarketDeskAuthError) as info:
        auth.password_id_token("a@b.c", SECRET_PASSWORD, post=post)
    assert "password sign-in disabled" in str(info.value)
    assert SECRET_PASSWORD not in str(info.value) and "a@b.c" not in str(info.value)


# ---- refresh-token storage -----------------------------------------------------------------

def test_env_seed_used_when_no_rotated_file(env):
    assert auth.current_refresh_token(env) == "SEED"


def test_no_token_anywhere_returns_none(tmp_path):
    assert auth.current_refresh_token({"RADON_RESEARCH_DIR": str(tmp_path / "r")}) is None


def test_rotated_token_persisted_0600_and_preferred_over_env(env):
    auth.save_refresh_token("ROTATED", env)
    path = auth.session_dir(env) / "firebase.json"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert auth.current_refresh_token(env) == "ROTATED"


def test_corrupt_token_file_falls_back_to_env(env):
    (auth.session_dir(env) / "firebase.json").write_text("{not json")
    assert auth.current_refresh_token(env) == "SEED"


# ---- id token selection --------------------------------------------------------------------

def test_stale_rotated_file_falls_back_to_new_env_seed(env):
    auth.save_refresh_token("STALE", env)
    post = FakePost((400, {"error": {"message": "TOKEN_EXPIRED"}}),
                    (200, {"id_token": "ID-2", "refresh_token": "SEED"}))
    assert auth.obtain_id_token(env, post=post) == "ID-2"
    assert [_form(c)["refresh_token"] for c in post.calls] == ["STALE", "SEED"]
    assert auth.current_refresh_token(env) == "SEED"


def test_password_fallback_when_refresh_token_fails(env):
    env.update(THEMARKETEAR_EMAIL="a@b.c", THEMARKETEAR_PASSWORD=SECRET_PASSWORD)
    post = FakePost((400, {"error": {"message": "TOKEN_EXPIRED"}}), (200, {"idToken": "ID-P"}))
    assert auth.obtain_id_token(env, post=post) == "ID-P"


def test_no_credentials_raises_without_network(tmp_path):
    post = FakePost()
    with pytest.raises(auth.MarketDeskAuthError, match="no MarketDesk credentials configured"):
        auth.obtain_id_token({"RADON_RESEARCH_DIR": str(tmp_path / "r")}, post=post)
    assert post.calls == []


def test_all_paths_failing_raises_without_secrets(env):
    env.update(THEMARKETEAR_EMAIL="a@b.c", THEMARKETEAR_PASSWORD=SECRET_PASSWORD)
    post = FakePost((400, {"error": {"message": "TOKEN_EXPIRED"}}), (400, {"error": {"message": "INVALID_PASSWORD"}}))
    with pytest.raises(auth.MarketDeskAuthError) as info:
        auth.obtain_id_token(env, post=post)
    message = str(info.value)
    assert "TOKEN_EXPIRED" in message and "password sign-in disabled" in message
    assert all(secret not in message for secret in ("SEED", SECRET_PASSWORD, "a@b.c"))


# ---- cookie session ------------------------------------------------------------------------

def _session(env, opener, post=None):
    post = post or FakePost((200, {"id_token": "ID-1", "refresh_token": "SEED"}),
                            (200, {"id_token": "ID-2", "refresh_token": "SEED"}))
    return auth.MarketDeskSession(env, opener=opener, post=post), post


def test_open_session_posts_id_token_to_login_with_browser_headers(env):
    opener = FakeOpener({("POST", LOGIN_URL): [login_ok()]})
    session = auth.open_session(env, opener=opener, post=FakePost((200, {"id_token": "ID-1"})))
    assert isinstance(session, auth.MarketDeskSession)
    request = opener.requests[0]
    assert request["method"] == "POST" and request["url"] == LOGIN_URL
    assert json.loads(request["data"]) == {"F": "ID-1"}
    assert request["headers"]["origin"] == "https://marketdesk.ai"
    assert request["headers"]["referer"] == "https://marketdesk.ai/"
    assert request["headers"]["content-type"] == "application/json"


def test_login_rejected_by_server_raises(env):
    opener = FakeOpener({("POST", LOGIN_URL): [(200, {"success": False}, {})]})
    with pytest.raises(auth.MarketDeskAuthError, match="login rejected"):
        auth.open_session(env, opener=opener, post=FakePost((200, {"id_token": "ID-1"})))


def test_api_posts_json_and_returns_payload(env):
    opener = FakeOpener({("POST", LOGIN_URL): [login_ok()], ("POST", LATEST_URL): [(200, ["a", "b"], {})]})
    session, _ = _session(env, opener)
    session.login()
    assert session.api("POST", "/api/mobile/latest/latest", {"institutions": []}) == ["a", "b"]
    request = opener.requests[-1]
    assert request["url"] == LATEST_URL and json.loads(request["data"]) == {"institutions": []}


def test_success_false_relogs_in_once_then_retries(env):
    opener = FakeOpener({("POST", LOGIN_URL): [login_ok()],
                         ("POST", LATEST_URL): [(200, {"success": False}, {}), (200, ["a"], {})]})
    session, post = _session(env, opener)
    session.login()
    assert session.api("POST", "/api/mobile/latest/latest", {"institutions": []}) == ["a"]
    assert [r["url"] for r in opener.requests] == [LOGIN_URL, LATEST_URL, LOGIN_URL, LATEST_URL]
    assert len(post.calls) == 2


def test_success_false_after_relogin_is_unauthenticated(env):
    opener = FakeOpener({("POST", LOGIN_URL): [login_ok()], ("POST", LATEST_URL): [(200, {"success": False}, {})]})
    session, _ = _session(env, opener)
    session.login()
    with pytest.raises(auth.MarketDeskError) as info:
        session.api("POST", "/api/mobile/latest/latest", {"institutions": []})
    assert str(info.value) == "unauthenticated" and info.value.transient


FILE_URL = "https://ts1.marketdesk.ai/files/4AJs5Adl2Yp/preview"


def test_blob_follows_one_hop_to_the_file_server(env):
    url = "https://marketdesk.ai/files/4AJs5Adl2Yp/blob"
    opener = FakeOpener({("POST", LOGIN_URL): [login_ok()],
                         ("GET", url): [(302, b"", {"Location": FILE_URL})],
                         ("GET", FILE_URL): [(200, b"%PDF-1.7", {"Content-Type": "application/pdf"})]})
    session, _ = _session(env, opener)
    session.login()
    assert session.blob("/files/4AJs5Adl2Yp/blob", limit=1024) == (b"%PDF-1.7", "application/pdf")
    assert [r["url"] for r in opener.requests] == [LOGIN_URL, url, FILE_URL]


@pytest.mark.parametrize("location", [
    "https://evil.example/files/4AJs5Adl2Yp/preview",
    "http://ts1.marketdesk.ai/files/4AJs5Adl2Yp/preview",
    "https://ts1.marketdesk.ai.evil.example/files/4AJs5Adl2Yp/preview",
    "https://ts1.marketdesk.ai/files/OTHERITEM01/preview",
    "https://ts1.marketdesk.ai:8443/files/4AJs5Adl2Yp/preview",
])
def test_blob_never_follows_a_redirect_off_the_file_servers(env, location):
    url = "https://marketdesk.ai/files/4AJs5Adl2Yp/blob"
    opener = FakeOpener({("POST", LOGIN_URL): [login_ok()], ("GET", url): [(302, b"", {"Location": location})]})
    session, _ = _session(env, opener)
    session.login()
    with pytest.raises(auth.MarketDeskError, match="unauthenticated"):
        session.blob("/files/4AJs5Adl2Yp/blob", limit=1024)
    assert all(r["url"] in (LOGIN_URL, url) for r in opener.requests)


def test_file_server_redirect_is_not_followed_further(env):
    url = "https://marketdesk.ai/files/4AJs5Adl2Yp/blob"
    opener = FakeOpener({("POST", LOGIN_URL): [login_ok()],
                         ("GET", url): [(302, b"", {"Location": FILE_URL})],
                         ("GET", FILE_URL): [(302, b"", {"Location": "https://ts2.marketdesk.ai/files/4AJs5Adl2Yp/preview"})]})
    session, _ = _session(env, opener)
    session.login()
    with pytest.raises(auth.MarketDeskError, match="unauthenticated"):
        session.blob("/files/4AJs5Adl2Yp/blob", limit=1024)


def test_blob_redirect_means_session_expired(env):
    url = "https://marketdesk.ai/files/4AJs5Adl2Yp/blob"
    opener = FakeOpener({("POST", LOGIN_URL): [login_ok()],
                         ("GET", url): [(302, b"", {"Location": "/"}), (200, b"%PDF-1.7", {"Content-Type": "application/pdf"})]})
    session, _ = _session(env, opener)
    session.login()
    data, content_type = session.blob("/files/4AJs5Adl2Yp/blob", limit=1024)
    assert data == b"%PDF-1.7" and content_type == "application/pdf"
    assert [r["url"] for r in opener.requests] == [LOGIN_URL, url, LOGIN_URL, url]


def test_blob_size_limit(env):
    url = "https://marketdesk.ai/files/4AJs5Adl2Yp/blob"
    opener = FakeOpener({("POST", LOGIN_URL): [login_ok()], ("GET", url): [(200, b"%PDF-" + b"x" * 50, {})]})
    session, _ = _session(env, opener)
    session.login()
    with pytest.raises(auth.MarketDeskError, match="too_large"):
        session.blob("/files/4AJs5Adl2Yp/blob", limit=10)


@pytest.mark.parametrize("status,code,transient", [(500, "http_500", True), (429, "http_429", True),
                                                   (404, "http_404", False)])
def test_http_errors_map_to_codes(env, status, code, transient):
    opener = FakeOpener({("POST", LOGIN_URL): [login_ok()], ("POST", LATEST_URL): [(status, b"boom", {})]})
    session, _ = _session(env, opener)
    session.login()
    with pytest.raises(auth.MarketDeskError) as info:
        session.api("POST", "/api/mobile/latest/latest", {})
    assert str(info.value) == code and info.value.transient is transient


def test_transport_error_is_redacted_and_transient(env):
    opener = FakeOpener({("POST", LOGIN_URL): [login_ok()],
                         ("POST", LATEST_URL): [(200, urllib.error.URLError("cookie A=SECRET"), {})]})
    session, _ = _session(env, opener)
    session.login()
    with pytest.raises(auth.MarketDeskError) as info:
        session.api("POST", "/api/mobile/latest/latest", {})
    assert str(info.value) == "http_transport" and info.value.transient
    assert info.value.__cause__ is None and info.value.__suppress_context__ is True


def test_non_json_api_response_is_bad_response(env):
    opener = FakeOpener({("POST", LOGIN_URL): [login_ok()], ("POST", LATEST_URL): [(200, b"<html>", {})]})
    session, _ = _session(env, opener)
    session.login()
    with pytest.raises(auth.MarketDeskError, match="bad_response"):
        session.api("POST", "/api/mobile/latest/latest", {})


def test_session_refuses_paths_off_the_marketdesk_origin(env):
    session, _ = _session(env, FakeOpener())
    for path in ("https://evil.example/x", "//evil.example/x", "api/x"):
        with pytest.raises(auth.MarketDeskError, match="bad_path"):
            session.api("GET", path)


def test_default_opener_does_not_follow_redirects():
    opener = auth.build_opener()
    assert any(isinstance(h, auth._NoRedirect) for h in opener.handlers)


# ---- bootstrap -----------------------------------------------------------------------------

def _firebase_records(refresh="BOOT-REFRESH"):
    return [{"fbase_key": f"firebase:authUser:{auth.FIREBASE_API_KEY}:[DEFAULT]",
             "value": {"uid": "u1", "stsTokenManager": {"refreshToken": refresh, "accessToken": "A"}}}]


def test_bootstrap_extracts_refresh_token_from_indexed_db_records():
    assert bootstrap.extract_refresh_token(_firebase_records()) == "BOOT-REFRESH"


def test_bootstrap_reads_encoded_record_values():
    records = _firebase_records()
    records[0]["value"] = json.dumps(records[0]["value"])
    assert bootstrap.extract_refresh_token(records) == "BOOT-REFRESH"


def test_bootstrap_returns_none_without_a_signed_in_user():
    assert bootstrap.extract_refresh_token([{"fbase_key": "x", "value": {"uid": "u1"}}]) is None
    assert bootstrap.extract_refresh_token(None) is None


def test_bootstrap_writes_token_file_0600(tmp_path):
    out = tmp_path / "nested" / "token"
    bootstrap.write_token_file(out, "BOOT-REFRESH")
    assert out.read_text() == "BOOT-REFRESH\n"
    assert stat.S_IMODE(out.stat().st_mode) == 0o600


def test_bootstrap_check_signs_in_lists_and_persists_rotation(tmp_path, capsys):
    out = tmp_path / "token"
    bootstrap.write_token_file(out, "BOOT-REFRESH")
    post = FakePost((200, {"id_token": "ID", "refresh_token": "ROTATED"}))
    opener = FakeOpener({("POST", LOGIN_URL): [login_ok()], ("POST", LATEST_URL): [(200, ["a", "b"], {})]})
    assert bootstrap.check(out, post=post, opener=opener) == 0
    assert out.read_text() == "ROTATED\n"
    printed = capsys.readouterr().out
    assert "ok" in printed and "2 latest" in printed
    assert "BOOT-REFRESH" not in printed and "ROTATED" not in printed


def test_bootstrap_check_reports_failed(tmp_path, capsys):
    out = tmp_path / "token"
    bootstrap.write_token_file(out, "BOOT-REFRESH")
    post = FakePost((400, {"error": {"message": "TOKEN_EXPIRED"}}))
    assert bootstrap.check(out, post=post, opener=FakeOpener()) == 1
    printed = capsys.readouterr().out
    assert "failed" in printed and "BOOT-REFRESH" not in printed
