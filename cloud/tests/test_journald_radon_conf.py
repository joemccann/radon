"""cloud/services/journald-radon.conf bounds every copy of the journal on disk."""
from __future__ import annotations

import configparser
from pathlib import Path

CONF = Path(__file__).resolve().parents[1] / "services" / "journald-radon.conf"


def _journal() -> configparser.SectionProxy:
    parser = configparser.ConfigParser(comment_prefixes=("#",), inline_comment_prefixes=None)
    parser.optionxform = str
    parser.read_string(CONF.read_text())
    return parser["Journal"]


def test_journal_disk_use_is_capped():
    assert _journal()["SystemMaxUse"] == "1G"


def test_journal_is_not_duplicated_into_var_log_syslog():
    # rsyslog's /var/log/syslog copy has no size cap and grew ~600M a week
    # (root fs hit 96% on 2026-10-08); journald's capped store is the only copy.
    assert _journal()["ForwardToSyslog"] == "no"


def test_the_dropin_sorts_after_the_vendor_syslog_dropin():
    # journald applies drop-ins in filename order across /etc and /usr/lib;
    # Ubuntu's /usr/lib/systemd/journald.conf.d/syslog.conf sets
    # ForwardToSyslog=yes, so a name sorting before it is silently overridden.
    setup = (CONF.parents[1] / "scripts" / "setup-vps.sh").read_text()
    target = "/etc/systemd/journald.conf.d/zz-radon.conf"
    assert target in setup
    assert Path(target).name > "syslog.conf"
    assert "rm -f /etc/systemd/journald.conf.d/radon.conf" in setup
