"""OpenSSH Flex sFTP puller. No Flex Web Service. Stub SSH only."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS))

FIXTURES = Path(__file__).resolve().parent / "fixtures"
ACTIVITY_YTD = FIXTURES / "cash_transactions_flex_ytd_detail_sample.xml"
ACTIVITY_365 = FIXTURES / "cash_transactions_flex_sample.xml"
TRADES = FIXTURES / "flex_trade_confirm_sample.xml"


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
            ]
        )
        + "\n"
    )
    return path


class FakeSftp:
    def __init__(self, files: dict[str, bytes], *, returncode: int = 0, stderr: str = ""):
        self.files = files
        self.returncode = returncode
        self.stderr = stderr
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
            if line == "ls":
                completed.stdout = "\n".join(self.files) + ("\n" if self.files else "")
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
    assert fake.inputs[0].strip().splitlines()[1] == "ls"


def test_empty_remote_is_error_heartbeat_not_sendrequest(tmp_path, monkeypatch):
    import flex_sftp_pull as pull

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
    )
    assert code == 0
    assert heartbeats[0][0] == "ok"
    assert "empty" in str(heartbeats[0][1]).lower()


def test_host_key_failure_is_fail_closed(tmp_path, monkeypatch):
    import flex_sftp_pull as pull

    heartbeats = []
    monkeypatch.setattr(pull, "_heartbeat", lambda state, error=None: heartbeats.append((state, error)))
    config = _ssh_config(tmp_path / "ssh_config")
    fake = FakeSftp({}, returncode=255, stderr="Host key verification failed.\n")
    code = pull.run(config=config, inbox=tmp_path / "inbox", runner=fake)
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
    code = pull.run(config=config, inbox=inbox, runner=fake, decrypt=boom)
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
