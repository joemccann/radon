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
from datetime import date, datetime, timedelta, timezone
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
# 2026-08-31 is a MONDAY and the timer fires Tue..Sat, so the last run inside
# the `<=` grace window is Sat 2026-08-29 and the first scheduled run after it
# is already past the cutover — the grace bought zero scheduled runs, and any
# slip in IBKR onboarding paged twice a day until a code deploy. R-416.
FIRST_DELIVERY_DATE = date(2026, 8, 31)
FIRST_DELIVERY_ENV = "RADON_FLEX_FIRST_DELIVERY"
MAX_NIGHTLY_SPAN_DAYS = 5
# How far behind the last completed session the NEWEST delivered statement may
# sit before the remote counts as stale. Deliberately NOT MAX_NIGHTLY_SPAN_DAYS,
# which bounds a statement's own period span — overloading it would let a real
# IBKR delivery stoppage go unpaged for five days. R-448.
MAX_DELIVERY_LAG_DAYS = 1
KEEP_GPG = 3
# IBKR names a PGP delivery `<acct>.<Query_Name>.<from>.<to>.xml.pgp`. The
# filter kept only `.gpg`, so three days of files sat in `outgoing` while every
# run reported an empty directory (2026-09-01 .. 2026-09-02).
ENCRYPTED_SUFFIXES = (".pgp", ".gpg")

# Directive -> the value it must carry, or None when any value will do.
# ConnectTimeout / ServerAliveInterval are required because `_sftp`'s own
# `timeout=` bounds the PROCESS while these bound the SESSION, and a
# UserKnownHostsFile pin is what the module docstring already claims. R-417/418.
REQUIRED_CONFIG: dict[str, Optional[str]] = {
    "IdentitiesOnly": "yes",
    "AddressFamily": "inet",
    "StrictHostKeyChecking": "yes",
    "UserKnownHostsFile": None,
    "ConnectTimeout": None,
    "ServerAliveInterval": None,
}

# Well under the unit's TimeoutStartSec=120: past that systemd SIGKILLs the
# process, `_heartbeat` never runs, and there is no error row at all — only a
# `failed` unit, surfaced a day later by the 26h window. R-417.
SFTP_TIMEOUT_SECS = 45


class FlexSftpError(RuntimeError):
    """Fail-closed sFTP / PGP / period error. Never a token fetch."""


_INSECURE_HOST_KEY = {"no", "off", "accept-new"}


def _host_block_directives(text: str, alias: str) -> list[tuple[str, str]]:
    """Directives that apply to `alias`, in file order, comments stripped.

    ssh_config is FIRST-MATCH-WINS, so an `off` placed ABOVE the required
    literal is what connects — while a raw `in text` scan saw the required line
    and passed. A commented-out requirement, or one scoped to an unrelated
    `Host` block, satisfied the same scan. R-418.
    """
    directives: list[tuple[str, str]] = []
    in_block = False
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        key, _, value = line.partition(" ")
        if key.lower() == "host":
            patterns = value.replace(",", " ").split()
            in_block = any(p == alias or p == "*" for p in patterns)
            continue
        if in_block:
            directives.append((key, value.strip()))
    return directives


def validate_ssh_config(path: Path, alias: str = SFTP_HOST_ALIAS) -> None:
    if not path.is_file():
        raise FlexSftpError(f"ssh_config_missing:{path}")
    directives = _host_block_directives(path.read_text(), alias)
    effective: dict[str, str] = {}
    for key, value in directives:
        effective.setdefault(key.lower(), value)  # first match wins

    strict = effective.get("stricthostkeychecking", "")
    if strict.lower() in _INSECURE_HOST_KEY:
        raise FlexSftpError(
            f"ssh_config: StrictHostKeyChecking {strict!r} is not fail-closed"
        )
    missing = [
        key for key, want in REQUIRED_CONFIG.items()
        if key.lower() not in effective
        or (want is not None and effective[key.lower()].lower() != want.lower())
    ]
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
        timeout=SFTP_TIMEOUT_SECS,
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
            if token.lower().endswith(ENCRYPTED_SUFFIXES):
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


def statement_period_end(xml_text: str) -> Optional[date]:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return None
    statement = root.find(".//FlexStatement")
    if statement is None:
        return None
    return _flex_day(statement.get("toDate"))


def _sessions_between(start: date, end: date) -> int:
    """Trading sessions strictly after ``start`` up to and including ``end``."""
    from utils.market_calendar import load_holidays

    if end <= start:
        return 0
    count = 0
    day = start + timedelta(days=1)
    while day <= end:
        if day.weekday() < 5 and day.strftime("%Y-%m-%d") not in load_holidays(day.year):
            count += 1
        day += timedelta(days=1)
    return count


def _delivery_key(name: str) -> str:
    """`<acct>.<Query_Name>.<from>.<to>.xml.pgp` -> `<acct>.<Query_Name>`.

    The key a stoppage is judged against. Anything that does not parse falls
    back to the whole basename, which errs toward MORE keys and therefore
    toward paging, never toward suppression.
    """
    from pathlib import Path as _Path

    base = _Path(name).name
    parts = base.split(".")
    if len(parts) >= 4:
        return ".".join(parts[:2])
    return base


def delivery_is_stale(period_end: Optional[date], now: Optional[datetime] = None) -> bool:
    """R-614: the lag is counted in trading SESSIONS, not calendar days.

    `last_completed_session_date` returns a session date, but subtracting
    calendar days from it stretched the one-session tolerance across weekends
    and holidays — a stoppage whose last statement was Thursday's scored a
    diff of 1 on Monday and stayed unpaged for roughly two extra days.
    """
    from utils.market_calendar import last_completed_session_date

    if period_end is None:
        return True
    last_session = date.fromisoformat(last_completed_session_date(now))
    return _sessions_between(period_end, last_session) > MAX_DELIVERY_LAG_DAYS


def _flex_day(value: Optional[str]) -> Optional[date]:
    if not value or len(value) < 8:
        return None
    try:
        return date(int(value[0:4]), int(value[4:6]), int(value[6:8]))
    except ValueError:
        return None


def retain_newest_gpg(inbox: Path, keep: int = KEEP_GPG) -> None:
    files = sorted(
        (p for p in inbox.iterdir() if p.is_file() and p.suffix.lower() in ENCRYPTED_SUFFIXES),
        key=lambda p: p.stat().st_mtime,
    )
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
    """Ingest from a private temp file, but RECORD the delivered filename.

    The temp file is load-bearing — the activity branch runs
    `cash_flow_sync --from-file <path>` — but recording that path made
    `flex_deliveries.source_path`, the only column linking a fingerprint back to
    a delivered statement, a permanently dead `/tmp/...` inside the unit's
    PrivateTmp namespace. R-419.
    """
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
        return flex_delivery_ingest.ingest_xml(
            xml_text,
            source_path=str(tmp),
            record_as=Path(source_path).name if source_path else None,
        )
    finally:
        tmp.unlink(missing_ok=True)


def first_delivery_date() -> date:
    """Cutover date, overridable so a slip needs no code deploy. R-416."""
    raw = (os.environ.get(FIRST_DELIVERY_ENV) or "").strip()
    if not raw:
        return FIRST_DELIVERY_DATE
    try:
        return date.fromisoformat(raw)
    except ValueError:
        print(
            f"[flex-pull] ignoring unparseable {FIRST_DELIVERY_ENV}={raw!r}",
            file=sys.stderr,
        )
        return FIRST_DELIVERY_DATE


def empty_remote_is_expected(now: Optional[datetime] = None) -> bool:
    """Before the first IBKR drop an empty directory is normal; after it, a miss."""
    moment = now or datetime.now(ZoneInfo("America/New_York"))
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=ZoneInfo("America/New_York"))
    return moment.astimezone(ZoneInfo("America/New_York")).date() <= first_delivery_date()


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
    """Outer heartbeat guarantee (NF-9). The body below writes a row on the
    happy path and on the failures it anticipates, but `_ensure_inbox`,
    `retain_newest_gpg`, a `TimeoutExpired` from the sftp runner or the decrypt,
    and anything out of `ingest_xml` (a Turso write failure, `ET.ParseError`)
    all escaped every handler. The unit then exited non-zero with NO row
    written, the previous `ok` row stayed newest, and the 26h/4d windows kept
    `flex-pull` green over a job that had not run. R-400.
    """
    try:
        return _run(
            config=config,
            inbox=inbox,
            runner=runner,
            decrypt=decrypt,
            ingest=ingest,
            gnupg_home=gnupg_home,
            remote_dir=remote_dir,
            now=now,
        )
    except Exception as exc:  # noqa: BLE001 — the row is the point
        print(f"[flex-pull] unhandled: {type(exc).__name__}: {exc}", file=sys.stderr)
        _heartbeat("error", f"{type(exc).__name__}: {exc}")
        return 1


def _run(
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
    newest_period_end: Optional[date] = None
    newest_by_key: Dict[str, date] = {}
    for name in names:
        dest = inbox / Path(name).name
        try:
            pull_gpg(name, dest, config=config, runner=runner, remote_dir=remote_dir)
            xml_text = decrypt_fn(dest.read_bytes())
            if not nightly_period_ok(xml_text):
                raise FlexSftpError("period_gate: nightly path rejects 365-day/YTD")
            classify_flex_xml(xml_text)
            # Every classified file counts here, duplicates included: an
            # idempotent re-pull of the CURRENT statement is not a stoppage.
            period_end = statement_period_end(xml_text)
            # R-601: track the newest period PER QUERY/account key, not one max
            # over the whole directory. IBKR names a delivery
            # `<acct>.<Query_Name>.<from>.<to>.xml.pgp` and never removes it
            # remotely, so a single max let one still-delivering query mask
            # another query's total stoppage forever — a suppression with no
            # dwell bound.
            key = _delivery_key(name)
            if period_end is not None:
                prior = newest_by_key.get(key)
                if prior is None or period_end > prior:
                    newest_by_key[key] = period_end
                if newest_period_end is None or period_end > newest_period_end:
                    newest_period_end = period_end
            # `a.xml.pgp` labels as `a.xml`, `trades.gpg` as `trades.xml`.
            plain = dest.with_suffix("")
            if plain.suffix.lower() != ".xml":
                plain = plain.with_suffix(".xml")
            result = ingest_fn(xml_text, source_path=str(plain))
            if not result.get("ok", True):
                raise FlexSftpError(f"ingest_failed:{result}")
            # `_sftp` issues no `rm` or `rename`, so a delivered file is never
            # removed remotely — once IBKR delivers once, `names` is never empty
            # again and the missed-delivery detector above can never fire. Every
            # run then re-pulls the same statement, each returning
            # `outcome: "duplicate"`, which passed `ok` and counted as progress.
            # Only a NEW statement is progress. R-389.
            if result.get("outcome") != "duplicate":
                ingested += 1
        except Exception as exc:  # noqa: BLE001 — one bad file must not abort the batch
            # Was `(FlexSftpError, FlexClassifyError, OSError)`, which covered
            # neither a `TimeoutExpired` from the decrypt nor anything out of
            # `ingest_xml`. R-400.
            print(f"[flex-pull] {name}: {type(exc).__name__}: {exc}", file=sys.stderr)
            failed = True
            continue

    retain_newest_gpg(inbox)
    if failed:
        _heartbeat("error", "one or more files rejected")
        return 1
    stale_keys = sorted(
        key for key, seen in newest_by_key.items() if delivery_is_stale(seen, now)
    )
    if not newest_by_key and delivery_is_stale(newest_period_end, now):
        stale_keys = ["<unparseable period>"]
    if not ingested and stale_keys and not empty_remote_is_expected(now):
        _heartbeat(
            "error",
            "no NEW statement applied and these deliveries are stale: "
            + ", ".join(stale_keys)
            + " (IBKR has stopped delivering them, or every file is already ingested)",
        )
        return 1
    _heartbeat("ok")
    return 0


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
