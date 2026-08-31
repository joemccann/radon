"""Weekend-loop phase Pushover is a normal page, never a P1 emergency."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from weekend_notify import (
    build_weekend_payload,
    main,
    notify_weekend_phase,
)


class TestWeekendPayload:
    def test_is_normal_priority(self):
        payload = build_weekend_payload(
            user="u",
            token="t",
            loop="testing",
            phase="audit",
            status="OK",
            pr_url="",
            detail="",
        )
        assert payload["priority"] == 0
        assert "retry" not in payload
        assert "expire" not in payload
        assert payload["title"] == "radon testing audit"
        assert payload["message"] == "OK"
        assert payload["user"] == "u"
        assert payload["token"] == "t"

    def test_detail_is_second_line(self):
        payload = build_weekend_payload(
            user="u",
            token="t",
            loop="reliability",
            phase="remediate",
            status="TIMEOUT after 21600s",
            pr_url="",
            detail="log: remediate-20260823T000000.log",
        )
        assert payload["title"] == "radon reliability remediate"
        assert payload["message"] == (
            "TIMEOUT after 21600s\nlog: remediate-20260823T000000.log"
        )

    def test_pr_url_is_last_line(self):
        payload = build_weekend_payload(
            user="u",
            token="t",
            loop="testing",
            phase="remediate",
            status="FAILED (exit 3)",
            pr_url="https://github.com/joemccann/radon/pull/91",
            detail="log: remediate-20260823T000000.log",
        )
        assert payload["message"] == (
            "FAILED (exit 3)\n"
            "log: remediate-20260823T000000.log\n"
            "https://github.com/joemccann/radon/pull/91"
        )

    def test_pr_url_without_detail(self):
        payload = build_weekend_payload(
            user="u",
            token="t",
            loop="testing",
            phase="audit",
            status="OK",
            pr_url="https://github.com/joemccann/radon/pull/91",
            detail="",
        )
        assert payload["message"] == (
            "OK\nhttps://github.com/joemccann/radon/pull/91"
        )


class TestSend:
    def test_missing_creds_is_skip_not_error(self, monkeypatch):
        monkeypatch.delenv("PUSHOVER_USER", raising=False)
        monkeypatch.delenv("PUSHOVER_TOKEN", raising=False)
        with patch("weekend_notify._http_post") as post:
            err = notify_weekend_phase(
                loop="testing", phase="audit", status="OK", pr_url="", detail=""
            )
        assert err is None
        post.assert_not_called()

    def test_posts_once_when_creds_present(self, monkeypatch):
        monkeypatch.setenv("PUSHOVER_USER", "u")
        monkeypatch.setenv("PUSHOVER_TOKEN", "t")
        with patch("weekend_notify._http_post", return_value=(200, b"")) as post:
            err = notify_weekend_phase(
                loop="reliability",
                phase="audit",
                status="OK",
                pr_url="",
                detail="",
            )
        assert err is None
        post.assert_called_once()
        url, payload = post.call_args[0][:2]
        assert "pushover.net" in url
        assert payload["priority"] == 0
        assert payload["title"] == "radon reliability audit"

    def test_transport_error_does_not_raise(self, monkeypatch):
        monkeypatch.setenv("PUSHOVER_USER", "u")
        monkeypatch.setenv("PUSHOVER_TOKEN", "t")
        with patch("weekend_notify._http_post", side_effect=OSError("down")):
            err = notify_weekend_phase(
                loop="testing", phase="audit", status="OK", pr_url="", detail=""
            )
        assert err is not None
        assert "down" in err


class TestMainAlwaysExitsZero:
    ARGV = [
        "--loop",
        "testing",
        "--phase",
        "audit",
        "--status",
        "OK",
        "--pr-url",
        "",
        "--detail",
        "",
    ]

    def test_http_error_still_exits_zero(self, monkeypatch):
        monkeypatch.setenv("PUSHOVER_USER", "u")
        monkeypatch.setenv("PUSHOVER_TOKEN", "t")
        with patch("weekend_notify._http_post", return_value=(500, b"boom")):
            assert main(list(self.ARGV)) == 0

    def test_transport_error_still_exits_zero(self, monkeypatch):
        monkeypatch.setenv("PUSHOVER_USER", "u")
        monkeypatch.setenv("PUSHOVER_TOKEN", "t")
        with patch("weekend_notify._http_post", side_effect=OSError("down")):
            assert main(list(self.ARGV)) == 0

    def test_missing_creds_still_exits_zero(self, monkeypatch):
        monkeypatch.delenv("PUSHOVER_USER", raising=False)
        monkeypatch.delenv("PUSHOVER_TOKEN", raising=False)
        with patch("weekend_notify._http_post") as post:
            assert main(list(self.ARGV)) == 0
        post.assert_not_called()


class TestEnvFile:
    def test_env_file_does_not_override_existing_env(self, monkeypatch, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text("PUSHOVER_USER=from-file\nPUSHOVER_TOKEN=from-file\n")
        monkeypatch.setenv("PUSHOVER_USER", "from-env")
        monkeypatch.delenv("PUSHOVER_TOKEN", raising=False)
        with patch("weekend_notify._http_post", return_value=(200, b"")) as post:
            assert main(list(self.argv(env_file))) == 0
        payload = post.call_args[0][1]
        assert payload["user"] == "from-env"
        assert payload["token"] == "from-file"

    @staticmethod
    def argv(env_file):
        return TestMainAlwaysExitsZero.ARGV + ["--env-file", str(env_file)]


class TestProloguePhaseStillPages:
    """A prologue death must page, not just post an issue comment.

    Every wrapper's `report()` is reachable with `PHASE="prologue"`: the
    marker refusal, the held-lock refusal and the ERR trap armed over the
    whole prologue all fire before `begin_phase` ever runs. `--phase`
    accepted only audit/remediate, so argparse exited 2 BEFORE the Pushover
    call, and the wrapper sends the page with `|| true` — the loudest
    failures (a moved clone, a full disk, a reused pid that makes every
    subsequent daily fire exit 3 in under a second) posted a comment and
    never paged. Observed on the 2026-08-30 CI-performance install smoke
    test, which reproduced the held-lock refusal end to end.
    """

    LOOPS = ("reliability", "testing", "ci-performance", "documentation")

    @pytest.mark.parametrize("loop", LOOPS)
    def test_the_prologue_page_is_sent(self, loop, monkeypatch):
        monkeypatch.setenv("PUSHOVER_USER", "u")
        monkeypatch.setenv("PUSHOVER_TOKEN", "t")
        argv = [
            "--loop",
            loop,
            "--phase",
            "prologue",
            "--status",
            "REFUSED (lock held)",
        ]
        with patch("weekend_notify._http_post", return_value=(200, b"")) as post:
            assert main(argv) == 0
        post.assert_called_once()
        payload = post.call_args[0][1]
        assert payload["title"] == f"radon {loop} prologue"
        assert "REFUSED (lock held)" in payload["message"]
        assert payload["priority"] == 0
