"""REL-055 / R-141: `exit-orders` is a control that does not exist.

`ExitOrdersHandler` requires `exit_orders[order_type]["status"] == "PENDING"`
plus `price` and `contract_spec` keys. Nothing in the repository writes an
`exit_orders` section at all -- the legacy `exit_order_service.py` only
*updates* one that a producer would have had to create, and it uses
`PENDING_MANUAL` / `target_price` and no `contract_spec`. So every candidate
falls to the `no_contract_spec` skip while `exit-orders` sits in both watchdog
catalogs reporting healthy.

Decision (audit option b): the handler and its hardening (REL-002 ack
discipline, REL-003 durable guard, the REL-004 halt check, REL-040's OCA
group) stay in the tree and stay drilled, but nothing schedules them and no
catalog claims the control exists. Wiring a real producer is a deliberate
money-path feature, not a reliability fix -- and the moment one lands,
`test_no_producer_writes_an_exit_orders_section` fails and forces the
registration back.
"""

from __future__ import annotations

import ast
import pathlib
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPTS = REPO_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))


def _producer_sites() -> list[str]:
    """Assignments that would CREATE an `exit_orders` section the handler can
    act on: a dict literal carrying the three keys it reads."""
    hits: list[str] = []
    for path in SCRIPTS.rglob("*.py"):
        rel = path.relative_to(REPO_ROOT).as_posix()
        if "/tests/" in rel or rel.endswith("/exit_orders.py"):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Dict):
                continue
            keys = {k.value for k in node.keys if isinstance(k, ast.Constant)}
            if {"status", "price", "contract_spec"} <= keys:
                hits.append(f"{rel}:{node.lineno}")
    return hits


def test_no_producer_writes_an_exit_orders_section():
    assert _producer_sites() == [], (
        "an exit_orders producer now exists -- re-register ExitOrdersHandler in "
        "create_daemon() and restore `exit-orders` to BOTH watchdog catalogs"
    )


def test_exit_orders_handler_is_not_scheduled():
    from monitor_daemon.run import create_daemon

    daemon = create_daemon()
    names = {
        getattr(handler, "service_name", None) for handler in daemon.handlers
    }
    assert "exit-orders" not in names, (
        "a handler that cannot place reports an ok heartbeat every cycle"
    )
    assert "fill-monitor" in names, "control test: real handlers stay registered"


def test_exit_orders_absent_from_both_watchdog_catalogs():
    from watchdog import services as watchdog_services

    assert "exit-orders" not in watchdog_services.SCHEDULED_SERVICES
    for bucket, names in watchdog_services.BUCKETS.items():
        assert "exit-orders" not in names, bucket

    windows = (REPO_ROOT / "web" / "lib" / "serviceHealthWindows.ts").read_text(
        encoding="utf-8"
    )
    assert '"exit-orders"' not in windows, (
        "the web catalog still expects a heartbeat from an unscheduled handler"
    )
