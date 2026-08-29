#!/usr/bin/env python3
"""Pull Flex Query files from IB-hosted sFTP. No Flex Web Service.

OpenSSH client, IPv4 only, pinned known_hosts, PGP decrypt in memory.
Empty outgoing is ok skip through 2026-08-31 (IBKR first delivery).
After that date an empty remote is an error heartbeat. Never a token fetch.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
from xml.etree import ElementTree as ET

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from lib.flex_classify import FlexClassifyError, classify_flex_xml

SERVICE = "flex-pull"
DEFAULT_CONFIG = Path("/var/lib/radon/flex-secrets/ssh_config")
DEFAULT_INBOX = Path("/var/lib/radon/flex-inbox")
DEFAULT_GNUPG = Path("/var/lib/radon/flex-secrets/gnupg")
SFTP_HOST_ALIAS = "ibkr-flex"
DEFAULT_REMOTE_DIR = "outgoing"
FIRST_DELIVERY_DATE = date(2026, 8, 31)
MAX_NIGHTLY_SPAN_DAYS = 5
KEEP_GPG = 3

REQUIRED_CONFIG = (
    "IdentitiesOnly yes",
    "AddressFamily inet",
    "StrictHostKeyChecking yes",
)


class FlexSftpError(RuntimeError):
    """Fail-closed sFTP / PGP / period error. Never a token fetch."""


def validate_ssh_config(path: Path) -> None:
    if not path.is_file():
        raise FlexSftpError(f"ssh_config_missing:{path}")
    text = path.read_text()
    lowered = text.lower()
    if "accept-new" in lowered or "stricthostkeychecking no" in lowered:
        raise FlexSftpError("ssh_config: accept-new/insecure host key checking forbidden")
    missing = [line for line in REQUIRED_CONFIG if line not in text]
    if missing:
        raise FlexSftpError(f"ssh_config missing {missing}")


def _sftp(
    batch: str,
    *,
    config: Path,
    runner,
) -> subprocess.CompletedProcess:
    args = ["sftp", "-4", "-b", "-", "-F", str(config), SFTP_HOST_ALIAS]
    return runner(
        args,
        input=batch if batch.endswith("\n") else batch + "\n",
        capture_output=True,
        text=True,
        check=False,
    )


def list_remote_gpg(*, config: Path, runner, remote_dir: str = DEFAULT_REMOTE_DIR) -> List[str]:
    validate_ssh_config(config)
    result = _sftp(f"cd {remote_dir}\nls -1", config=config, runner=runner)
    if result.returncode != 0:
        stderr = result.stderr or ""
        if "host key" in stderr.lower() or "identification has changed" in stderr.lower():
            raise FlexSftpError(f"host key verification failed: {stderr.strip()}")
        raise FlexSftpError(f"sftp_ls_failed:{result.returncode}:{stderr.strip()}")
    names = []
    for line in (result.stdout or "").splitlines():
        # `ls` is columnised by default, so a three-file delivery can arrive on
        # one line. `-1` above asks for one per line; this reads every token
        # regardless, so a server that ignores it still yields every file.
        for token in line.split():
            if token.lower().endswith(".gpg"):
                names.append(token.split("/")[-1])
    return names


def _ensure_inbox(inbox: Path) -> None:
    inbox.mkdir(parents=True, exist_ok=True)
    os.chmod(inbox, 0o700)


def _write_gpg(dest: Path, data: bytes) -> None:
    tmp = dest.with_suffix(dest.suffix + ".tmp")
    tmp.write_bytes(data)
    os.chmod(tmp, 0o600)
    tmp.replace(dest)
    os.chmod(dest, 0o600)


def pull_gpg(
    name: str,
    dest: Path,
    *,
    config: Path,
    runner,
    remote_dir: str = DEFAULT_REMOTE_DIR,
) -> None:
    remote = f"{remote_dir}/{name}" if remote_dir else name
    result = _sftp(f"get {remote} {dest}", config=config, runner=runner)
    if result.returncode != 0:
        stderr = result.stderr or ""
        if "host key" in stderr.lower():
            raise FlexSftpError(f"host key verification failed: {stderr.strip()}")
        raise FlexSftpError(f"sftp_get_failed:{name}:{stderr.strip()}")
    if dest.is_file():
        os.chmod(dest, 0o600)


def _gpg_decrypt(data: bytes, *, gnupg_home: Path) -> str:
    result = subprocess.run(
        [
            "gpg",
            "--homedir",
            str(gnupg_home),
            "--batch",
            "--yes",
            "--decrypt",
        ],
        input=data,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise FlexSftpError("pgp_decrypt_failed")
    return result.stdout.decode("utf-8")


def nightly_period_ok(xml_text: str) -> bool:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return False
    statement = root.find(".//FlexStatement")
    if statement is None:
        return False
    period = (statement.get("period") or "").replace(" ", "")
    if "365" in period.lower() or period.lower() in {"yeartodate", "ytd"}:
        return False
    start = _flex_day(statement.get("fromDate"))
    end = _flex_day(statement.get("toDate"))
    if start is None or end is None:
        return period.lower() == "lastbusinessday"
    return (end - start).days <= MAX_NIGHTLY_SPAN_DAYS


def _flex_day(value: Optional[str]) -> Optional[date]:
    if not value or len(value) < 8:
        return None
    try:
        return date(int(value[0:4]), int(value[4:6]), int(value[6:8]))
    except ValueError:
        return None


def retain_newest_gpg(inbox: Path, keep: int = KEEP_GPG) -> None:
    files = sorted(inbox.glob("*.gpg"), key=lambda p: p.stat().st_mtime)
    for stale in files[:-keep] if keep > 0 else files:
        stale.unlink(missing_ok=True)


def _heartbeat(state: str, error: Optional[Any] = None) -> None:
    try:
        from db import writer

        payload = error if isinstance(error, dict) else ({"message": str(error)} if error else None)
        writer.record_service_health(
            SERVICE,
            state,
            finished_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            error=payload,
        )
    except Exception as exc:  # noqa: BLE001 — best-effort
        print(f"[flex-pull] heartbeat non-fatal: {exc}", file=sys.stderr)


def _default_ingest(xml_text: str, *, source_path: str = "") -> Dict[str, Any]:
    import flex_delivery_ingest

    # `source_path` is the delivery's provenance — it is what lands in
    # `flex_deliveries.source_path` — and `ingest_xml` also reads it back off
    # disk for the activity writers. Write the plaintext THERE rather than to a
    # random /tmp name, and remove it once the writers are done.
    if source_path:
        tmp = Path(source_path)
        # Create 0600 BEFORE the first byte. `write_text` would create at the
        # umask default and leave decrypted statement plaintext world-readable
        # until the chmod below.
        fd = os.open(tmp, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(xml_text)
    else:
        with tempfile.NamedTemporaryFile("w", suffix=".xml", delete=False) as handle:
            handle.write(xml_text)
            tmp = Path(handle.name)
    try:
        os.chmod(tmp, 0o600)
        return flex_delivery_ingest.ingest_xml(xml_text, source_path=str(tmp))
    finally:
        tmp.unlink(missing_ok=True)


def empty_remote_is_expected(now: Optional[datetime] = None) -> bool:
    """IBKR first drop is 2026-08-31. After that date, empty is a miss."""
    moment = now or datetime.now(ZoneInfo("America/New_York"))
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=ZoneInfo("America/New_York"))
    return moment.astimezone(ZoneInfo("America/New_York")).date() <= FIRST_DELIVERY_DATE


def run(
    *,
    config: Path,
    inbox: Path,
    runner=subprocess.run,
    decrypt: Optional[Callable[..., str]] = None,
    ingest: Optional[Callable[..., Dict[str, Any]]] = None,
    gnupg_home: Path = DEFAULT_GNUPG,
    remote_dir: str = DEFAULT_REMOTE_DIR,
    now: Optional[datetime] = None,
) -> int:
    decrypt_fn = decrypt or (lambda data, **k: _gpg_decrypt(data, gnupg_home=gnupg_home))
    ingest_fn = ingest or _default_ingest
    try:
        validate_ssh_config(config)
        names = list_remote_gpg(config=config, runner=runner, remote_dir=remote_dir)
    except FlexSftpError as exc:
        _heartbeat("error", exc)
        return 1

    if not names:
        if empty_remote_is_expected(now):
            _heartbeat("ok", "empty remote directory; waiting for IBKR delivery")
            return 0
        _heartbeat("error", "empty remote directory after 2026-08-31 delivery start")
        return 1

    _ensure_inbox(inbox)
    failed = False
    ingested = 0
    for name in names:
        dest = inbox / Path(name).name
        try:
            pull_gpg(name, dest, config=config, runner=runner, remote_dir=remote_dir)
            xml_text = decrypt_fn(dest.read_bytes())
            if not nightly_period_ok(xml_text):
                raise FlexSftpError("period_gate: nightly path rejects 365-day/YTD")
            classify_flex_xml(xml_text)
            result = ingest_fn(xml_text, source_path=str(dest.with_suffix(".xml")))
            if not result.get("ok", True):
                raise FlexSftpError(f"ingest_failed:{result}")
            ingested += 1
        except (FlexSftpError, FlexClassifyError, OSError) as exc:
            print(f"[flex-pull] {name}: {exc}", file=sys.stderr)
            failed = True
            continue

    retain_newest_gpg(inbox)
    if failed:
        _heartbeat("error", "one or more files rejected")
        return 1
    _heartbeat("ok")
    return 0 if ingested else 1


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--inbox", type=Path, default=DEFAULT_INBOX)
    parser.add_argument("--gnupg-home", type=Path, default=DEFAULT_GNUPG)
    parser.add_argument("--list-only", action="store_true")
    parser.add_argument("--remote-dir", default=DEFAULT_REMOTE_DIR)
    args = parser.parse_args(argv)
    if args.list_only:
        try:
            names = list_remote_gpg(
                config=args.config, runner=subprocess.run, remote_dir=args.remote_dir
            )
        except FlexSftpError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        print("\n".join(names))
        return 0 if names else 1
    return run(
        config=args.config,
        inbox=args.inbox,
        gnupg_home=args.gnupg_home,
        remote_dir=args.remote_dir,
    )


if __name__ == "__main__":
    sys.exit(main())
