"""2026-09-17: radon-flex-pull Result=exit-code after leftover-history sFTP RST.

Page e8da0c53… 12:35Z. 07:30 ET applied today's Equity_Summary + Trade_History
(flex_deliveries status=applied 11:30Z). 08:30 ET is the empty-dir retry, but
IBKR never removes outgoing, so the oneshot re-GETs the full history. After
the current statements (cash-flow-sync ok 12:30:39Z) OpenSSH RST'd the tail
(`sftp_get_failed` / `kex_exchange_identification: read: Connection reset by
peer` on 20260903..20260828). `failed=True` then heartbeated
`one or more files rejected` and exited 1 (NRestarts=0). Next timer 24h out.
Edge and :8321/health/lite stayed up; Python Turso canary 52 ms.
"""

from __future__ import annotations

import sys
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

SCRIPTS = Path(__file__).resolve().parent.parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import flex_sftp_pull as pull  # noqa: E402
from test_flex_sftp_pull import FakeSftp, _ssh_config  # noqa: E402

ET = ZoneInfo("America/New_York")
# Thursday 08:30 ET: last completed session is Wednesday 2026-09-16.
RETRY_NOW = datetime(2026, 9, 17, 8, 30, tzinfo=ET)
CURRENT = date(2026, 9, 16)
STALE = date(2026, 8, 28)

CURRENT_TRADE = "U4698258.Trade_History.20260916.20260916.xml.pgp"
CURRENT_EQ = "U4698258.Equity_Summary_in_Base.20260916.20260916.xml.pgp"
OLD_TRADE = "U4698258.Trade_History.20260903.20260903.xml.pgp"
OLD_EQ = "U4698258.Equity_Summary_in_Base.20260828.20260828.xml.pgp"

# Verbatim OpenSSH stderr from the 12:30Z unit journal.
KEX_RST = (
    "kex_exchange_identification: read: Connection reset by peer\n"
    "Connection reset by 64.190.196.110 port 22\n"
    "Connection closed\n"
)


def _xml(period_end: date) -> bytes:
    stamp = period_end.strftime("%Y%m%d")
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<FlexQueryResponse queryName="Trade Confirmation" type="TC">'
        '<FlexStatements count="1">'
        f'<FlexStatement accountId="U4698258" fromDate="{stamp}" toDate="{stamp}"'
        ' period="LastBusinessDay">'
        "<Trades></Trades>"
        "</FlexStatement></FlexStatements></FlexQueryResponse>"
    ).encode()


class ResetOnNamedSftp(FakeSftp):
    """Succeed ls + GET for known files; RST GET for `reset_names`."""

    def __init__(self, files: dict[str, bytes], reset_names: set[str]):
        super().__init__(files)
        self.reset_names = set(reset_names)

    def __call__(self, args, **kwargs):
        stdin = kwargs.get("input") or ""
        if isinstance(stdin, bytes):
            stdin = stdin.decode()
        for line in stdin.splitlines():
            line = line.strip()
            if line.startswith("get "):
                key = line.split()[1].split("/")[-1]
                if key in self.reset_names:
                    self.calls.append(list(args))
                    self.inputs.append(stdin)
                    return SimpleNamespace(
                        args=args,
                        returncode=255,
                        stdout="",
                        stderr=KEX_RST,
                    )
        return super().__call__(args, **kwargs)


def _drive(tmp_path, monkeypatch, files, reset_names, now=RETRY_NOW):
    beats: list[tuple] = []
    monkeypatch.setattr(pull, "_heartbeat", lambda state, error=None: beats.append((state, error)))
    monkeypatch.setattr(pull, "nightly_period_ok", lambda _x: True)
    monkeypatch.setattr(pull, "classify_flex_xml", lambda _x: "activity")
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    code = pull.run(
        config=_ssh_config(tmp_path / "ssh_config"),
        inbox=inbox,
        runner=ResetOnNamedSftp(files, reset_names),
        decrypt=lambda data, **k: data.decode(),
        ingest=lambda xml, **k: {
            "ok": True,
            "outcome": "duplicate",
            "persistence_confirmed": True,
        },
        now=now,
    )
    return code, beats


def test_current_duplicate_then_kex_reset_on_history_exits_zero(tmp_path, monkeypatch):
    """Reproduce 2026-09-17 12:30Z: leftover outgoing RST must not fail the retry."""
    files = {
        CURRENT_TRADE: _xml(CURRENT),
        CURRENT_EQ: _xml(CURRENT),
        OLD_TRADE: _xml(date(2026, 9, 3)),
        OLD_EQ: _xml(STALE),
    }
    code, beats = _drive(
        tmp_path,
        monkeypatch,
        files,
        reset_names={OLD_TRADE, OLD_EQ},
    )
    assert code == 0, f"08:30 retry paged P1 after current duplicates; beats={beats}"
    assert beats, "must heartbeat"
    assert beats[-1][0] == "ok"
    assert "one or more files rejected" not in str(beats[-1][1] or "")


def test_kex_reset_on_every_get_without_a_current_statement_still_errors(
    tmp_path, monkeypatch
):
    files = {OLD_EQ: _xml(STALE), OLD_TRADE: _xml(date(2026, 9, 3))}
    code, beats = _drive(tmp_path, monkeypatch, files, reset_names={OLD_EQ, OLD_TRADE})
    assert code == 1
    assert beats[-1][0] == "error"


def test_host_key_failure_on_get_still_fails_closed(tmp_path, monkeypatch):
    beats: list[tuple] = []
    monkeypatch.setattr(pull, "_heartbeat", lambda state, error=None: beats.append((state, error)))
    monkeypatch.setattr(pull, "nightly_period_ok", lambda _x: True)
    monkeypatch.setattr(pull, "classify_flex_xml", lambda _x: "activity")

    class HostKeyGet(FakeSftp):
        def __call__(self, args, **kwargs):
            stdin = kwargs.get("input") or ""
            if isinstance(stdin, bytes):
                stdin = stdin.decode()
            if any(ln.strip().startswith("get ") for ln in stdin.splitlines()):
                self.calls.append(list(args))
                self.inputs.append(stdin)
                return SimpleNamespace(
                    args=args,
                    returncode=255,
                    stdout="",
                    stderr="Host key verification failed.\n",
                )
            return super().__call__(args, **kwargs)

    inbox = tmp_path / "inbox"
    inbox.mkdir()
    code = pull.run(
        config=_ssh_config(tmp_path / "ssh_config"),
        inbox=inbox,
        runner=HostKeyGet({CURRENT_TRADE: _xml(CURRENT)}),
        decrypt=lambda data, **k: data.decode(),
        ingest=lambda xml, **k: {"ok": True, "outcome": "applied"},
        now=RETRY_NOW,
    )
    assert code == 1
    assert beats[-1][0] == "error"


def test_kex_reset_classifier_matches_the_unit_journal():
    assert hasattr(pull, "is_transient_sftp_error")
    err = pull.FlexSftpError(f"sftp_get_failed:{OLD_EQ}:{KEX_RST.strip()}")
    assert pull.is_transient_sftp_error(err) is True
    host = pull.FlexSftpError("host key verification failed: Host key verification failed.")
    assert pull.is_transient_sftp_error(host) is False
    ingest = pull.FlexSftpError("ingest_failed:{'ok': False}")
    assert pull.is_transient_sftp_error(ingest) is False
    assert pull.is_transient_sftp_error(RuntimeError("Connection reset by peer")) is False
