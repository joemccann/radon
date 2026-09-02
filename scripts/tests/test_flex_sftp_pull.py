"""OpenSSH Flex sFTP puller. No Flex Web Service. Stub SSH only."""

from __future__ import annotations

import subprocess
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS))

FIXTURES = Path(__file__).resolve().parent / "fixtures"
ACTIVITY_YTD = FIXTURES / "cash_transactions_flex_ytd_detail_sample.xml"
ACTIVITY_365 = FIXTURES / "cash_transactions_flex_sample.xml"
TRADES = FIXTURES / "flex_trade_confirm_sample.xml"
# T-318: every `run()` pins the clock. `empty_remote_is_expected()` reads the
# live ET clock when `now` is None, so an unpinned call flips on the R-416
# `FIRST_DELIVERY_DATE` cutover (2026-08-31). Tests that are not about the
# cutover run on the production side of it.
AFTER_FIRST_DELIVERY = datetime(2026, 9, 1, 8, 0, tzinfo=ZoneInfo("America/New_York"))


def _ssh_config(path: Path) -> Path:
    path.write_text(
        "\n".join(
            [
                "Host ibkr-flex",
                "  HostName sftp.interactivebrokers.com",
                "  User flexuser",
                "  Port 22",
                "  IdentityFile /tmp/ibkr_sftp",
                "  IdentitiesOnly yes",
                "  AddressFamily inet",
                "  UserKnownHostsFile /tmp/known_hosts",
                "  StrictHostKeyChecking yes",
                # R-417: `_sftp`'s own `timeout=` bounds the PROCESS; these
                # bound the SESSION, and REQUIRED_CONFIG now demands both.
                "  ConnectTimeout 15",
                "  ServerAliveInterval 15",
            ]
        )
        + "\n"
    )
    return path


class FakeSftp:
    def __init__(
        self,
        files: dict[str, bytes],
        *,
        returncode: int = 0,
        stderr: str = "",
        ls_stdout: str | None = None,
    ):
        self.files = files
        self.returncode = returncode
        self.stderr = stderr
        self.ls_stdout = ls_stdout
        self.calls: list[list[str]] = []
        self.inputs: list[str] = []

    def __call__(self, args, **kwargs):
        self.calls.append(list(args))
        stdin = kwargs.get("input") or ""
        if isinstance(stdin, bytes):
            stdin = stdin.decode()
        self.inputs.append(stdin)
        completed = SimpleNamespace(
            args=args,
            returncode=self.returncode,
            stdout="",
            stderr=self.stderr,
        )
        if self.returncode != 0:
            return completed
        lines = [ln.strip() for ln in stdin.splitlines() if ln.strip()]
        cwd = ""
        for line in lines:
            if line.startswith("cd "):
                cwd = line.split(maxsplit=1)[1]
                continue
            if line.split()[0] == "ls":
                completed.stdout = (
                    self.ls_stdout
                    if self.ls_stdout is not None
                    else "\n".join(self.files) + ("\n" if self.files else "")
                )
                return completed
            if line.startswith("get "):
                _, remote, local = line.split(maxsplit=2)
                key = remote.split("/")[-1]
                Path(local).write_bytes(self.files[key])
                self.cwd = cwd
                return completed
        raise AssertionError(f"unexpected sftp batch: {stdin!r}")


def test_source_does_not_import_gdcdyn_or_sendrequest():
    source = (SCRIPTS / "flex_sftp_pull.py").read_text()
    for banned in ("gdcdyn", "SendRequest", "FlexStatementService", "FlexReport("):
        assert banned not in source
    assert "-o StrictHostKeyChecking=accept-new" not in source


def test_rejects_ssh_config_without_ipv4_and_strict_host_key(tmp_path):
    import flex_sftp_pull as pull

    bad = tmp_path / "ssh_config"
    bad.write_text("Host ibkr-flex\n  StrictHostKeyChecking accept-new\n")
    with pytest.raises(pull.FlexSftpError, match="ssh_config"):
        pull.validate_ssh_config(bad)


def test_list_dir_uses_sftp_dash4_and_batch_stdin(tmp_path):
    import flex_sftp_pull as pull

    config = _ssh_config(tmp_path / "ssh_config")
    fake = FakeSftp({"a.gpg": b"x", "b.xml": b"nope"})
    names = pull.list_remote_gpg(config=config, runner=fake)
    assert names == ["a.gpg"]
    assert fake.calls[0][0].endswith("sftp") or fake.calls[0][0] == "sftp"
    assert "-4" in fake.calls[0]
    assert "-b" in fake.calls[0]
    assert "-F" in fake.calls[0]
    assert "accept-new" not in " ".join(fake.calls[0])
    assert fake.inputs[0].strip().splitlines()[0] == "cd outgoing"
    assert fake.inputs[0].strip().splitlines()[1] == "ls -1"


def test_empty_remote_before_first_delivery_is_ok_skip(tmp_path, monkeypatch):
    import flex_sftp_pull as pull
    from datetime import datetime
    from zoneinfo import ZoneInfo

    heartbeats = []
    monkeypatch.setattr(pull, "_heartbeat", lambda state, error=None: heartbeats.append((state, error)))
    config = _ssh_config(tmp_path / "ssh_config")
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    code = pull.run(
        config=config,
        inbox=inbox,
        runner=FakeSftp({}),
        decrypt=lambda data, **k: data,
        ingest=lambda xml, **k: {"ok": True},
        now=datetime(2026, 8, 28, 12, 0, tzinfo=ZoneInfo("America/New_York")),
    )
    assert code == 0
    assert heartbeats[0][0] == "ok"
    assert "empty" in str(heartbeats[0][1]).lower()


def test_empty_remote_after_first_delivery_is_error(tmp_path, monkeypatch):
    import flex_sftp_pull as pull
    from datetime import datetime
    from zoneinfo import ZoneInfo

    heartbeats = []
    monkeypatch.setattr(pull, "_heartbeat", lambda state, error=None: heartbeats.append((state, error)))
    config = _ssh_config(tmp_path / "ssh_config")
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    code = pull.run(
        config=config,
        inbox=inbox,
        runner=FakeSftp({}),
        decrypt=lambda data, **k: data,
        ingest=lambda xml, **k: {"ok": True},
        now=datetime(2026, 9, 1, 7, 30, tzinfo=ZoneInfo("America/New_York")),
    )
    assert code == 1
    assert heartbeats[0][0] == "error"
    assert "empty" in str(heartbeats[0][1]).lower()
    assert "2026-08-31" in str(heartbeats[0][1])


def test_host_key_failure_is_fail_closed(tmp_path, monkeypatch):
    import flex_sftp_pull as pull

    heartbeats = []
    monkeypatch.setattr(pull, "_heartbeat", lambda state, error=None: heartbeats.append((state, error)))
    config = _ssh_config(tmp_path / "ssh_config")
    fake = FakeSftp({}, returncode=255, stderr="Host key verification failed.\n")
    code = pull.run(config=config, inbox=tmp_path / "inbox", runner=fake, now=AFTER_FIRST_DELIVERY)
    assert code == 1
    assert heartbeats[0][0] == "error"
    assert "host key" in str(heartbeats[0][1]).lower()


def test_rejects_365_day_and_ytd_period(tmp_path, monkeypatch):
    import flex_sftp_pull as pull

    monkeypatch.setattr(pull, "_heartbeat", lambda *a, **k: None)
    config = _ssh_config(tmp_path / "ssh_config")
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    fake = FakeSftp({
        "activity365.gpg": ACTIVITY_365.read_bytes(),
        "activityytd.gpg": ACTIVITY_YTD.read_bytes(),
    })
    code = pull.run(
        config=config,
        inbox=inbox,
        runner=fake,
        decrypt=lambda data, **k: data.decode(),
        ingest=lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not ingest")),
        now=AFTER_FIRST_DELIVERY,
    )
    assert code == 1
    assert (inbox / "activity365.gpg").exists()
    assert (inbox / "activityytd.gpg").exists()


def test_pulls_last_business_day_trades_and_ingests(tmp_path, monkeypatch):
    import flex_sftp_pull as pull

    monkeypatch.setattr(pull, "_heartbeat", lambda *a, **k: None)
    config = _ssh_config(tmp_path / "ssh_config")
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    ingested = []
    fake = FakeSftp({"trades.gpg": TRADES.read_bytes()})
    code = pull.run(
        config=config,
        inbox=inbox,
        runner=fake,
        decrypt=lambda data, **k: data.decode(),
        ingest=lambda xml_text, source_path="": ingested.append((xml_text, source_path)) or {"ok": True},
        now=AFTER_FIRST_DELIVERY,
    )
    assert code == 0
    assert ingested
    assert "NAK" in ingested[0][0]
    assert (inbox / "trades.gpg").exists()
    get_batch = fake.inputs[-1]
    assert "get outgoing/trades.gpg" in get_batch


def test_decrypt_failure_keeps_gpg(tmp_path, monkeypatch):
    import flex_sftp_pull as pull

    monkeypatch.setattr(pull, "_heartbeat", lambda *a, **k: None)
    config = _ssh_config(tmp_path / "ssh_config")
    inbox = tmp_path / "inbox"
    inbox.mkdir()

    def boom(_data, **k):
        raise pull.FlexSftpError("pgp_decrypt_failed")

    fake = FakeSftp({"trades.gpg": b"not-really-gpg"})
    code = pull.run(config=config, inbox=inbox, runner=fake, decrypt=boom, now=AFTER_FIRST_DELIVERY)
    assert code == 1
    assert (inbox / "trades.gpg").exists()


def test_ambiguous_xml_keeps_gpg_and_does_not_ingest(tmp_path, monkeypatch):
    import flex_sftp_pull as pull

    monkeypatch.setattr(pull, "_heartbeat", lambda *a, **k: None)
    mixed = (
        '<?xml version="1.0"?><FlexQueryResponse>'
        "<FlexStatement fromDate='20260820' toDate='20260820' period='LastBusinessDay'>"
        "<EquitySummaryByReportDateInBase />"
        "<Trade />"
        "</FlexStatement></FlexQueryResponse>"
    )
    config = _ssh_config(tmp_path / "ssh_config")
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    fake = FakeSftp({"mixed.gpg": mixed.encode()})
    code = pull.run(
        config=config,
        inbox=inbox,
        runner=fake,
        decrypt=lambda data, **k: data.decode(),
        ingest=lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not ingest")),
        now=AFTER_FIRST_DELIVERY,
    )
    assert code == 1
    assert (inbox / "mixed.gpg").exists()


def test_retains_at_most_three_newest_gpg(tmp_path):
    import flex_sftp_pull as pull
    import time

    inbox = tmp_path / "inbox"
    inbox.mkdir()
    paths = []
    for i in range(5):
        p = inbox / f"{i}.gpg"
        p.write_bytes(b"x")
        paths.append(p)
        time.sleep(0.01)
    pull.retain_newest_gpg(inbox, keep=3)
    remaining = sorted(p.name for p in inbox.glob("*.gpg"))
    assert remaining == ["2.gpg", "3.gpg", "4.gpg"]


def test_nightly_period_ok_on_last_business_day():
    import flex_sftp_pull as pull

    assert pull.nightly_period_ok(TRADES.read_text()) is True
    assert pull.nightly_period_ok(ACTIVITY_365.read_text()) is False
    assert pull.nightly_period_ok(ACTIVITY_YTD.read_text()) is False


def test_list_remote_gpg_parses_columnised_ls(tmp_path):
    """`sftp ls` is multi-column by default; every name must survive. T-255."""
    import flex_sftp_pull as pull

    config = _ssh_config(tmp_path / "ssh_config")
    fake = FakeSftp({}, ls_stdout="a.gpg  b.gpg  c.gpg\n")
    assert pull.list_remote_gpg(config=config, runner=fake) == ["a.gpg", "b.gpg", "c.gpg"]
    assert fake.inputs[0].strip().splitlines()[1] == "ls -1"

    mixed = FakeSftp({}, ls_stdout="a.gpg  b.gpg\nc.gpg\nnotes.txt\n")
    assert pull.list_remote_gpg(config=config, runner=mixed) == ["a.gpg", "b.gpg", "c.gpg"]


def test_multi_file_delivery_pulls_every_file(tmp_path, monkeypatch):
    """Three files on one `ls` line must all be fetched, not just the last. T-255."""
    import flex_sftp_pull as pull

    monkeypatch.setattr(pull, "_heartbeat", lambda *a, **k: None)
    config = _ssh_config(tmp_path / "ssh_config")
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    trades = TRADES.read_bytes()
    fake = FakeSftp(
        {"one.gpg": trades, "two.gpg": trades, "three.gpg": trades},
        ls_stdout="one.gpg  two.gpg  three.gpg\n",
    )
    ingested = []
    code = pull.run(
        config=config,
        inbox=inbox,
        runner=fake,
        decrypt=lambda data, **k: data.decode(),
        ingest=lambda xml_text, source_path="": ingested.append(source_path) or {"ok": True},
        now=AFTER_FIRST_DELIVERY,
    )
    assert code == 0
    assert len(ingested) == 3
    assert sorted(p.name for p in inbox.glob("*.gpg")) == ["one.gpg", "three.gpg", "two.gpg"]


def test_no_trade_session_trade_confirm_is_ok(tmp_path, monkeypatch):
    """A quiet session's Trade Confirmation must not page. T-256."""
    import flex_sftp_pull as pull

    heartbeats = []
    monkeypatch.setattr(pull, "_heartbeat", lambda state, error=None: heartbeats.append((state, error)))
    quiet = (
        '<?xml version="1.0"?><FlexQueryResponse queryName="Trade Confirmation" type="TC">'
        "<FlexStatements count='1'>"
        "<FlexStatement accountId='U0000000' fromDate='20260804' toDate='20260804' "
        "period='LastBusinessDay'><Trades></Trades></FlexStatement>"
        "</FlexStatements></FlexQueryResponse>"
    )
    config = _ssh_config(tmp_path / "ssh_config")
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    code = pull.run(
        config=config,
        inbox=inbox,
        runner=FakeSftp({"quiet.gpg": quiet.encode()}),
        decrypt=lambda data, **k: data.decode(),
        ingest=lambda xml_text, source_path="": {"ok": True},
        now=AFTER_FIRST_DELIVERY,
    )
    assert code == 0
    assert heartbeats[-1][0] == "ok"


def test_run_without_ingest_drives_default_ingest(tmp_path, monkeypatch):
    """The production wiring: `run()` with no `ingest=` must reach the writers. T-259.

    The re-pull of the same bytes is the R-389 stale-remote case: after the
    cutover a duplicate-only run is an error, not progress. T-318.
    """
    import flex_delivery_ingest
    import flex_sftp_pull as pull
    import journal_rehydrate

    heartbeats = []
    monkeypatch.setattr(pull, "_heartbeat", lambda state, error=None: heartbeats.append((state, error)))
    # T-250 (`db.writer.claim_flex_delivery` reads `rows_affected`) is a
    # different change's lane; stub the claim at flex_delivery_ingest's own
    # indirection with a stateful fake so the rest of the wiring runs for real.
    claims: dict[str, dict] = {}

    def fake_claim(digest, **kwargs):
        if digest in claims:
            return False
        claims[digest] = kwargs
        return True

    monkeypatch.setattr(flex_delivery_ingest, "claim_flex_delivery", fake_claim)
    # R-436: the applied mark after the writers and the status lookup behind a
    # lost claim are the same seam; the fake above only ever holds applied rows.
    monkeypatch.setattr(flex_delivery_ingest, "mark_flex_delivery_applied", lambda _d: True)
    monkeypatch.setattr(flex_delivery_ingest, "flex_delivery_status", lambda _d: "applied")
    rehydrated = []
    monkeypatch.setattr(
        journal_rehydrate,
        "rehydrate",
        lambda **kwargs: rehydrated.append(kwargs) or {"ok": True, "imported": 1, "skipped": 0},
    )

    config = _ssh_config(tmp_path / "ssh_config")
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    fake = FakeSftp({"trades.gpg": TRADES.read_bytes()})
    code = pull.run(
        config=config,
        inbox=inbox,
        runner=fake,
        decrypt=lambda data, **k: data.decode(),
        now=AFTER_FIRST_DELIVERY,
    )

    assert code == 0
    assert heartbeats[-1][0] == "ok"
    assert len(rehydrated) == 1
    assert "NAK" in rehydrated[0]["xml_text"]
    # Provenance is the delivered filename the operator can map back to sFTP,
    # not a random /tmp name from PrivateTmp.
    assert list(claims.values())[0]["source_path"] == "trades.xml"
    # Decrypted plaintext must not linger beside the .gpg.
    assert list(inbox.glob("*.xml")) == []

    code_again = pull.run(
        config=config,
        inbox=inbox,
        runner=fake,
        decrypt=lambda data, **k: data.decode(),
        now=AFTER_FIRST_DELIVERY,
    )
    assert code_again == 1
    assert heartbeats[-1][0] == "error"
    assert "no NEW statement" in str(heartbeats[-1][1])
    assert len(rehydrated) == 1


def test_retain_newest_gpg_keep_zero_removes_all(tmp_path):
    """`files[:-0]` is `files[:0]` — keep=0 must delete, not retain. T-259."""
    import flex_sftp_pull as pull

    inbox = tmp_path / "inbox"
    inbox.mkdir()
    for i in range(3):
        (inbox / f"{i}.gpg").write_bytes(b"x")
    pull.retain_newest_gpg(inbox, keep=0)
    assert list(inbox.glob("*.gpg")) == []


# --- IBKR's real delivery names end in .xml.pgp, not .gpg (2026-09-02) ------

IBKR_LS = (
    "U4698258.Equity_Summary_in_Base.20260901.20260901.xml.pgp\n"
    "U4698258.Trade_History.20260901.20260901.xml.pgp\n"
)


def test_list_remote_gpg_accepts_ibkr_pgp_names(tmp_path):
    """Three days of deliveries sat in `outgoing` while every run reported
    `empty remote directory after 2026-08-31 delivery start`: IBKR names its
    PGP files `*.xml.pgp`, and the filter only kept `*.gpg`."""
    import flex_sftp_pull as pull

    config = _ssh_config(tmp_path / "ssh_config")
    fake = FakeSftp({}, ls_stdout=IBKR_LS + "notes.txt\n")
    assert pull.list_remote_gpg(config=config, runner=fake) == [
        "U4698258.Equity_Summary_in_Base.20260901.20260901.xml.pgp",
        "U4698258.Trade_History.20260901.20260901.xml.pgp",
    ]


def test_run_ingests_ibkr_pgp_delivery(tmp_path):
    from datetime import datetime
    from zoneinfo import ZoneInfo

    import flex_sftp_pull as pull

    config = _ssh_config(tmp_path / "ssh_config")
    inbox = tmp_path / "inbox"
    name = "U4698258.Trade_History.20260901.20260901.xml.pgp"
    fake = FakeSftp({name: TRADES.read_bytes()}, ls_stdout=f"{name}\n")
    seen: list[str] = []

    def ingest(xml, **k):
        seen.append(k.get("source_path"))
        return {"ok": True, "outcome": "applied"}

    rc = pull.run(
        config=config,
        inbox=inbox,
        runner=fake,
        decrypt=lambda data, **k: data.decode(),
        ingest=ingest,
        now=datetime(2026, 9, 2, 7, 30, tzinfo=ZoneInfo("America/New_York")),
    )
    assert rc == 0
    assert (inbox / name).exists()
    assert seen == [str(inbox / "U4698258.Trade_History.20260901.20260901.xml")]


def test_retain_newest_gpg_prunes_pgp_names(tmp_path):
    import time

    import flex_sftp_pull as pull

    inbox = tmp_path / "inbox"
    inbox.mkdir()
    for i in range(5):
        (inbox / f"U4698258.Trade_History.2026090{i}.2026090{i}.xml.pgp").write_bytes(b"x")
        time.sleep(0.01)
    pull.retain_newest_gpg(inbox, keep=3)
    remaining = sorted(p.name for p in inbox.iterdir())
    assert remaining == [
        "U4698258.Trade_History.20260902.20260902.xml.pgp",
        "U4698258.Trade_History.20260903.20260903.xml.pgp",
        "U4698258.Trade_History.20260904.20260904.xml.pgp",
    ]
