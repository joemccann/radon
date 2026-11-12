"""D13: radon-market-calendar.{service,timer} refresh the IBKR trading calendar.

`POST /market-calendar/refresh` (scripts/api/server.py) cites a scheduled
radon-market-calendar timer, and the relay plus `market_state()` read the
`data/market_calendar.json` cache it keeps. No unit existed, so the forward
calendar only grew when someone hit the route by hand.
"""

import configparser
import hashlib
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
REPO = ROOT.parent
SERVICES = ROOT / "services"
UNITS = ("radon-market-calendar.service", "radon-market-calendar.timer")


def _unit(name: str) -> configparser.ConfigParser:
    cfg = configparser.ConfigParser(strict=False, interpolation=None)
    cfg.read_string((SERVICES / name).read_text())
    return cfg


def test_service_runs_the_calendar_fetch_as_radon_oneshot():
    svc = _unit("radon-market-calendar.service")["Service"]
    assert svc["Type"] == "oneshot"
    assert svc["User"] == "radon"
    assert svc["EnvironmentFile"] == "/etc/radon/env"
    assert svc["ExecStart"] == (
        "/home/radon/radon/.venv/bin/python /home/radon/radon/scripts/fetch_market_calendar.py"
    )


def test_timer_fires_weekly_on_sunday_evening_utc_before_the_trading_week():
    timer = (SERVICES / "radon-market-calendar.timer").read_text()
    assert "OnCalendar=Sun *-*-* 22:00:00 UTC" in timer
    assert "Persistent=true" in timer


def test_units_are_installed_by_the_deploy_manifest_with_matching_hashes():
    manifest = (ROOT / "config" / "installed-units.sha256").read_text().splitlines()
    pinned = {line.split()[1]: line.split()[0] for line in manifest if line.strip() and not line.startswith("#")}
    for name in UNITS:
        digest = hashlib.sha256((SERVICES / name).read_bytes()).hexdigest()
        assert pinned.get(name) == digest, name


def test_units_are_in_the_bootstrap_inventory_and_auto_sync_allowlist():
    setup = (ROOT / "scripts" / "setup-vps.sh").read_text()
    allow = (ROOT / "config" / "auto-sync-units.txt").read_text().split()
    for name in UNITS:
        assert f"  {name}\n" in setup, name
        assert name in allow, name


def test_route_docstring_names_the_weekly_timer():
    server = (REPO / "scripts" / "api" / "server.py").read_text()
    route = server.split('@app.post("/market-calendar/refresh")', 1)[1].split("@app.", 1)[0]
    assert "weekly radon-market-calendar timer" in route
