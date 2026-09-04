"""R-389/416/417/418/419 / REL-146: the Flex sFTP puller stops lying about what it did.

R-389: `_sftp` issues no `rm` or `rename`, so delivered files are never removed
remotely. Once IBKR delivers once, `names` is never empty again, the only
missed-delivery detector can never fire, and every run re-pulls the same
statement -- each returning `outcome: "duplicate"`, which passes
`result.get("ok", True)`, increments `ingested` and heartbeats `ok`.

R-416: the cutover date is a hardcoded constant with no env override, and
2026-08-31 is a MONDAY against a `Tue..Sat` timer, so the grace window buys zero
scheduled runs.

R-417: `_sftp` passes no `timeout=`, so a hung session reaches systemd's
`TimeoutStartSec=120` SIGKILL and no heartbeat runs at all.

R-418: ssh_config validation is a raw substring scan. `StrictHostKeyChecking
off` is not in the reject list, and ssh_config is FIRST-MATCH-WINS, so an `off`
above the required literal wins at connect time while validation passes.

R-419: `_default_ingest` discards the real inbox filename and records a
`NamedTemporaryFile` path that `finally` unlinks, so `flex_deliveries.
source_path` is a permanently dead `/tmp/...` inside the unit's PrivateTmp.
"""

from __future__ import annotations

import subprocess
import sys
from datetime import date, datetime
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import flex_sftp_pull as pull  # noqa: E402
from test_flex_sftp_pull import AFTER_FIRST_DELIVERY, FakeSftp, _ssh_config  # noqa: E402

ZONE = "America/New_York"


def _config_lines(**overrides) -> list[str]:
    base = {
        "IdentitiesOnly": "yes",
        "AddressFamily": "inet",
        "StrictHostKeyChecking": "yes",
        "UserKnownHostsFile": "/tmp/known_hosts",
        "ConnectTimeout": "15",
        "ServerAliveInterval": "15",
    }
    base.update(overrides)
    return ["Host ibkr-flex", "  HostName sftp.interactivebrokers.com"] + [
        f"  {k} {v}" for k, v in base.items()
    ]


def _write(tmp_path: Path, lines: list[str]) -> Path:
    path = tmp_path / "ssh_config"
    path.write_text("\n".join(lines) + "\n")
    return path


class TestCutoverDateIsConfigurable:
    def test_the_env_override_moves_the_grace_window(self, monkeypatch):
        monkeypatch.setenv("RADON_FLEX_FIRST_DELIVERY", "2026-09-30")
        moment = datetime(2026, 9, 2, 8, 0, tzinfo=pull.ZoneInfo(ZONE))
        assert pull.empty_remote_is_expected(moment) is True

    def test_the_default_is_still_the_shipped_constant(self, monkeypatch):
        monkeypatch.delenv("RADON_FLEX_FIRST_DELIVERY", raising=False)
        moment = datetime(2026, 9, 2, 8, 0, tzinfo=pull.ZoneInfo(ZONE))
        assert pull.empty_remote_is_expected(moment) is False

    def test_an_unparseable_override_falls_back_rather_than_crashing(self, monkeypatch):
        monkeypatch.setenv("RADON_FLEX_FIRST_DELIVERY", "not-a-date")
        moment = datetime(2026, 9, 2, 8, 0, tzinfo=pull.ZoneInfo(ZONE))
        assert pull.empty_remote_is_expected(moment) is False


class TestSshConfigIsParsedNotGrepped:
    def test_an_off_above_the_required_literal_is_refused(self, tmp_path):
        # ssh_config is FIRST-MATCH-WINS, so this `off` is what connects.
        lines = _config_lines()
        lines.insert(2, "  StrictHostKeyChecking off")
        with pytest.raises(pull.FlexSftpError):
            pull.validate_ssh_config(_write(tmp_path, lines))

    def test_a_commented_requirement_does_not_satisfy_the_check(self, tmp_path):
        lines = _config_lines()
        lines = [ln.replace("  StrictHostKeyChecking yes", "  # StrictHostKeyChecking yes")
                 for ln in lines]
        with pytest.raises(pull.FlexSftpError):
            pull.validate_ssh_config(_write(tmp_path, lines))

    def test_a_directive_in_an_unrelated_host_block_does_not_count(self, tmp_path):
        lines = _config_lines()
        lines = [ln for ln in lines if "StrictHostKeyChecking" not in ln]
        lines += ["", "Host somewhere-else", "  StrictHostKeyChecking yes"]
        with pytest.raises(pull.FlexSftpError):
            pull.validate_ssh_config(_write(tmp_path, lines))

    def test_a_pinned_known_hosts_file_is_required(self, tmp_path):
        lines = [ln for ln in _config_lines() if "UserKnownHostsFile" not in ln]
        with pytest.raises(pull.FlexSftpError, match="UserKnownHostsFile"):
            pull.validate_ssh_config(_write(tmp_path, lines))

    def test_keepalives_are_required(self, tmp_path):
        lines = [ln for ln in _config_lines() if "ConnectTimeout" not in ln]
        with pytest.raises(pull.FlexSftpError):
            pull.validate_ssh_config(_write(tmp_path, lines))

    def test_the_shipped_shape_validates(self, tmp_path):
        pull.validate_ssh_config(_write(tmp_path, _config_lines()))

    def test_the_repo_test_fixture_matches_the_contract(self, tmp_path):
        """The shared fixture must be a config the validator accepts."""
        pull.validate_ssh_config(_ssh_config(tmp_path / "fixture"))


class TestAHungSessionStillHeartbeats:
    def test_the_sftp_call_is_bounded(self, tmp_path, monkeypatch):
        seen: list[dict] = []

        def _runner(args, **kwargs):
            seen.append(kwargs)
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

        pull.list_remote_gpg(config=_write(tmp_path, _config_lines()), runner=_runner)
        assert seen and seen[0].get("timeout"), seen
        assert seen[0]["timeout"] < 120, "must be well under TimeoutStartSec=120"

    def test_a_timeout_exits_one_with_an_error_row(self, tmp_path, monkeypatch):
        beats: list[tuple] = []
        monkeypatch.setattr(pull, "_heartbeat", lambda state, error=None: beats.append((state, error)))

        def _runner(args, **kwargs):
            raise subprocess.TimeoutExpired(cmd=args, timeout=kwargs.get("timeout", 1))

        code = pull.run(
            config=_write(tmp_path, _config_lines()),
            inbox=tmp_path / "inbox",
            runner=_runner,
            now=AFTER_FIRST_DELIVERY,
        )
        assert code == 1
        assert beats and beats[-1][0] == "error", beats


class TestOnlyNewStatementsCountAsProgress:
    def _drive(self, tmp_path, monkeypatch, outcome, *, now=None):
        beats: list[tuple] = []
        monkeypatch.setattr(pull, "_heartbeat", lambda state, error=None: beats.append((state, error)))
        monkeypatch.setattr(pull, "nightly_period_ok", lambda _x: True)
        monkeypatch.setattr(pull, "classify_flex_xml", lambda _x: "trades")
        inbox = tmp_path / "inbox"
        inbox.mkdir()
        code = pull.run(
            config=_write(tmp_path, _config_lines()),
            inbox=inbox,
            runner=FakeSftp({"activity.gpg": b"<FlexQueryResponse/>"}),
            decrypt=lambda data, **k: data.decode(),
            ingest=lambda xml_text, source_path="", **k: {"ok": True, "outcome": outcome},
            now=now,
        )
        return code, beats

    def test_a_run_that_applied_nothing_new_is_an_error(self, tmp_path, monkeypatch):
        """The stale `outgoing` directory case: same statement re-pulled forever."""
        code, beats = self._drive(
            tmp_path, monkeypatch, "duplicate",
            now=datetime(2026, 9, 2, 8, 0, tzinfo=pull.ZoneInfo(ZONE)),
        )
        assert code == 1
        assert beats and beats[-1][0] == "error", beats

    def test_a_run_that_applied_something_new_is_ok(self, tmp_path, monkeypatch):
        code, beats = self._drive(
            tmp_path, monkeypatch, "applied",
            now=datetime(2026, 9, 2, 8, 0, tzinfo=pull.ZoneInfo(ZONE)),
        )
        assert code == 0
        assert [state for state, _ in beats] == ["ok"], beats

    def test_before_the_cutover_a_duplicate_only_run_is_tolerated(self, tmp_path, monkeypatch):
        code, beats = self._drive(
            tmp_path, monkeypatch, "duplicate",
            now=datetime(2026, 8, 20, 8, 0, tzinfo=pull.ZoneInfo(ZONE)),
        )
        assert code == 0
        assert [state for state, _ in beats] == ["ok"], beats


class TestTheClaimNamesTheDeliveredFile:
    def test_the_recorded_source_path_is_the_remote_basename(self, tmp_path, monkeypatch):
        recorded: list[str] = []
        import flex_delivery_ingest as ingest

        monkeypatch.setattr(
            ingest, "claim_flex_delivery",
            lambda _d, **kw: recorded.append(kw.get("source_path")) or True,
        )
        # R-436: the applied mark after the writers is the same seam; grant it.
        monkeypatch.setattr(ingest, "mark_flex_delivery_applied", lambda _d: True)
        monkeypatch.setattr(ingest, "classify_flex_xml", lambda _x: ingest.TRADES)
        monkeypatch.setattr(
            ingest, "statement_metadata", lambda _x: {"period_from": None, "period_to": None}
        )
        import journal_rehydrate

        monkeypatch.setattr(journal_rehydrate, "rehydrate", lambda **_k: {"ok": True})

        # An inbox-SHAPED path under tmp_path, not the literal production one:
        # `_default_ingest` writes the plaintext to `source_path` itself (R-419),
        # so naming /var/lib/radon/flex-inbox made the test fail with
        # FileNotFoundError on every machine that is not the VPS. What is under
        # test is the RECORDED name, and that is a basename either way.
        inbox = tmp_path / "flex-inbox"
        inbox.mkdir()
        pull._default_ingest("<FlexQueryResponse/>", source_path=str(inbox / "activity.xml"))

        assert recorded and recorded[0] is not None
        assert "activity" in recorded[0], recorded
        assert "/tmp/" not in recorded[0] and "/var/folders/" not in recorded[0], recorded


class TestDeliveryStalenessGate:
    """R-448 / T-413: a duplicate-only run is only an ERROR when the newest
    delivered statement has fallen behind the last completed session.

    Clock is pinned to Wed 2026-09-02 08:00 ET (pre-close), so the last
    completed session is Tue 2026-09-01. Nothing here reads the real date.
    """

    NOW = datetime(2026, 9, 2, 8, 0, tzinfo=pull.ZoneInfo(ZONE))
    LAST_SESSION = date(2026, 9, 1)
    FIVE_SESSIONS_BACK = date(2026, 8, 25)  # 9/1, 8/31, 8/28, 8/27, 8/26 -> 8/25

    @staticmethod
    def _xml(to_date: date) -> bytes:
        stamp = to_date.strftime("%Y%m%d")
        return (
            '<FlexQueryResponse><FlexStatements><FlexStatement '
            f'accountId="U1" fromDate="{stamp}" toDate="{stamp}" period="LastBusinessDay"/>'
            "</FlexStatements></FlexQueryResponse>"
        ).encode()

    def _drive(self, tmp_path, monkeypatch, to_date: date):
        beats: list[tuple] = []
        monkeypatch.setattr(pull, "_heartbeat", lambda state, error=None: beats.append((state, error)))
        monkeypatch.setattr(pull, "classify_flex_xml", lambda _x: "trades")
        inbox = tmp_path / "inbox"
        inbox.mkdir()
        code = pull.run(
            config=_write(tmp_path, _config_lines()),
            inbox=inbox,
            runner=FakeSftp({"activity.gpg": self._xml(to_date)}),
            decrypt=lambda data, **k: data.decode(),
            ingest=lambda xml_text, source_path="", **k: {"ok": True, "outcome": "duplicate"},
            now=self.NOW,
        )
        return code, beats

    def test_the_pinned_session_is_the_one_the_gate_compares_against(self):
        from utils.market_calendar import last_completed_session_date

        assert last_completed_session_date(self.NOW) == self.LAST_SESSION.isoformat()

    def test_a_current_statement_is_not_stale(self):
        assert pull.delivery_is_stale(self.LAST_SESSION, self.NOW) is False

    def test_a_statement_five_sessions_back_is_stale(self):
        assert pull.delivery_is_stale(self.FIVE_SESSIONS_BACK, self.NOW) is True

    def test_a_missing_statement_is_stale(self):
        assert pull.delivery_is_stale(None, self.NOW) is True

    def test_the_period_end_is_read_off_the_statement(self):
        assert pull.statement_period_end(
            self._xml(self.LAST_SESSION).decode()
        ) == self.LAST_SESSION

    def test_a_duplicate_of_todays_statement_still_exits_zero(self, tmp_path, monkeypatch):
        """IBKR delivered; we already have it. Not a delivery stoppage."""
        code, beats = self._drive(tmp_path, monkeypatch, self.LAST_SESSION)
        assert code == 0, beats
        assert [state for state, _ in beats] == ["ok"], beats

    def test_the_same_duplicate_five_sessions_stale_pages(self, tmp_path, monkeypatch):
        code, beats = self._drive(tmp_path, monkeypatch, self.FIVE_SESSIONS_BACK)
        assert code == 1, beats
        assert beats and beats[-1][0] == "error", beats
        assert "stale" in (beats[-1][1] or ""), beats
