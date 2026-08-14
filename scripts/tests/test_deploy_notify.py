"""Live-deploy Pushover is a normal page, never a P1 emergency."""

from __future__ import annotations

from unittest.mock import patch

from deploy_notify import (
    build_live_payload,
    notify_deploy_live,
)


class TestLivePayload:
    def test_is_normal_priority(self):
        payload = build_live_payload(
            user="u",
            token="t",
            sha="f63c5a69e06b82d4507b64c660bc861ec12de2d0",
            subject="fix(skew): embargo UW daily quota",
        )
        assert payload["priority"] == 0
        assert "retry" not in payload
        assert "expire" not in payload
        assert payload["title"] == "radon deploy live"
        assert payload["message"] == "f63c5a6 is live: fix(skew): embargo UW daily quota"
        assert payload["user"] == "u"
        assert payload["token"] == "t"

    def test_truncates_long_subject(self):
        payload = build_live_payload(
            user="u", token="t", sha="abc1234", subject="x" * 300
        )
        assert len(payload["message"]) < 200
        assert payload["message"].startswith("abc1234 is live:")


class TestSend:
    def test_missing_creds_is_skip_not_error(self, monkeypatch):
        monkeypatch.delenv("PUSHOVER_USER", raising=False)
        monkeypatch.delenv("PUSHOVER_TOKEN", raising=False)
        with patch("deploy_notify._http_post") as post:
            err = notify_deploy_live(sha="abc", subject="fix")
        assert err is None
        post.assert_not_called()

    def test_posts_once_when_creds_present(self, monkeypatch):
        monkeypatch.setenv("PUSHOVER_USER", "u")
        monkeypatch.setenv("PUSHOVER_TOKEN", "t")
        with patch("deploy_notify._http_post", return_value=(200, b"")) as post:
            err = notify_deploy_live(
                sha="f63c5a69deadbeef",
                subject="fix(skew): embargo UW daily quota",
            )
        assert err is None
        post.assert_called_once()
        url, payload = post.call_args[0][:2]
        assert "pushover.net" in url
        assert payload["priority"] == 0
        assert "f63c5a6 is live" in payload["message"]

    def test_transport_error_does_not_raise(self, monkeypatch):
        monkeypatch.setenv("PUSHOVER_USER", "u")
        monkeypatch.setenv("PUSHOVER_TOKEN", "t")
        with patch("deploy_notify._http_post", side_effect=OSError("down")):
            err = notify_deploy_live(sha="abc", subject="fix")
        assert err is not None
        assert "down" in err
