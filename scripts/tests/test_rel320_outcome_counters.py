"""REL-320 / R-027: real owners report outcomes under fake external faults."""
from __future__ import annotations

import json
import logging
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest


@pytest.fixture(autouse=True)
def isolated_metrics(caplog, monkeypatch):
    caplog.set_level(logging.INFO)
    # Baseline has no collector: execute owners and fail on missing evidence,
    # rather than treating a missing import as the fault reproduction.
    module = sys.modules.get("utils.outcome_metrics")
    if module:
        now = [0.0]
        collector = module.OutcomeCounters(clock=lambda: now[0])
        now[0] = 10.0
        monkeypatch.setattr(module, "METRICS", collector)


def sample(caplog, operation):
    module = sys.modules.get("utils.outcome_metrics")
    if module:
        module.METRICS.emit(force=True)
    records = [json.loads(r.message.removeprefix("operation_metrics "))
               for r in caplog.records if r.message.startswith("operation_metrics ")]
    matching = [r for r in records if r["operation"] == operation]
    assert matching, f"no outcome counters for {operation}"
    return matching[-1]


def test_journal_success_and_swallowed_caller_failure_remain_counted(monkeypatch, caplog):
    from db import writer
    write = MagicMock(side_effect=[None, TimeoutError("synthetic receipt loss"), None])
    monkeypatch.setattr(writer, "_hrana_execute", write)
    writer.upsert_journal_entry("fake-one", {})
    with pytest.raises(TimeoutError, match="synthetic receipt loss"):
        writer.upsert_journal_entry("fake-two", {})
    writer.upsert_journal_entry("fake-three", {})
    metrics = sample(caplog, "journal_upsert")
    assert (metrics["success"], metrics["error"], metrics["skipped"]) == (2, 1, 0)
    assert metrics["error_ratio"] == pytest.approx(1 / 3)
    assert metrics["success_per_second"] > 0
    assert write.call_count == 3


def test_database_read_write_error_ratio_and_recovery(monkeypatch, caplog):
    from db import hrana_http as transport
    monkeypatch.setattr(transport, "read_env", lambda: ("libsql://fake.invalid", "synthetic"))
    monkeypatch.setattr(transport, "_refuse_pytest_pollution", lambda: None)
    monkeypatch.setattr(transport, "_check_statement_ok", lambda body: None)
    monkeypatch.setattr(transport, "_execute_result", lambda body: body)
    monkeypatch.setattr(transport, "_rows_as_tuples", lambda body: [])
    post = MagicMock(side_effect=[{}, TimeoutError("synthetic stall"), {}])
    monkeypatch.setattr(transport, "_post_pipeline", post)
    transport.hrana_execute("SELECT 1")
    with pytest.raises(transport.HranaHttpError, match="synthetic stall"):
        transport.hrana_query("SELECT 2")
    assert transport.hrana_query("SELECT 3") == []
    metrics = sample(caplog, "database")
    assert (metrics["success"], metrics["error"]) == (2, 1)
    assert metrics["error_ratio"] == pytest.approx(1 / 3)
    assert post.call_count == 3  # no telemetry retry


def test_order_wire_success_failure_and_guard_refusal(monkeypatch, caplog):
    from clients.ib_client import IBClient, IBOrderError
    import trading_halt
    monkeypatch.setattr(trading_halt, "is_trading_halted", lambda: False)
    broker = MagicMock()
    client = IBClient.__new__(IBClient)
    client._ib, client.logger = broker, logging.getLogger("test-broker")
    client._require_connection = lambda: None
    contract = SimpleNamespace(secType="STK", symbol="FAKE")
    order = SimpleNamespace(action="BUY", totalQuantity=1, lmtPrice=2, orderType="LMT")
    # A realistic returned Trade, rather than a broker acknowledgement claim.
    broker.placeOrder.side_effect = [SimpleNamespace(order=SimpleNamespace(orderId=42)),
                                    RuntimeError("synthetic socket refusal")]
    client.place_order(contract, order)
    with pytest.raises(IBOrderError, match="synthetic socket refusal"):
        client.place_order(contract, order)
    monkeypatch.setattr(trading_halt, "is_trading_halted", lambda: True)
    monkeypatch.setattr(trading_halt, "get_halt_state", lambda: {"reason": "synthetic halt"})
    with pytest.raises(IBOrderError, match="Trading halted"):
        client.place_order(contract, order)
    metrics = sample(caplog, "order_submit")
    assert (metrics["success"], metrics["error"]) == (1, 1)
    assert broker.placeOrder.call_count == 2


def test_fill_delta_not_unchanged_poll_is_counted(monkeypatch, caplog):
    from monitor_daemon.handlers import fill_monitor as owner
    trade = MagicMock()
    trade.order.orderId, trade.order.action, trade.order.totalQuantity = 5, "BUY", 25
    trade.order.lmtPrice = 1.0
    trade.orderStatus.status, trade.orderStatus.filled = "Submitted", 10
    trade.orderStatus.remaining, trade.orderStatus.avgFillPrice = 15, .98
    trade.contract.symbol, trade.contract.localSymbol = "FAKE", "FAKE-STK"
    broker = MagicMock()
    broker.get_open_orders.return_value = [trade]
    monkeypatch.setattr(owner, "IBClient", lambda: broker)
    handler = owner.FillMonitorHandler(send_notifications=False)
    monkeypatch.setattr(handler, "_mirror_ib_orders_snapshot", lambda client: None)
    monkeypatch.setattr(handler, "_persist_fill_to_journal", lambda *args: None)
    handler.known_orders = {5: {"filled": 0}}
    assert handler.execute()["partial_fills"] == 1
    assert handler.execute()["partial_fills"] == 0
    metrics = sample(caplog, "fill_detected")
    assert (metrics["success"], metrics["error"]) == (1, 0)


def test_notification_transport_outcomes_and_unconfigured_skip(monkeypatch, caplog):
    from watchdog import notify
    post = MagicMock(side_effect=[(200, b'{}'), (500, b'fake failure'), TimeoutError('fake stall')])
    monkeypatch.setattr(notify, "_http_post", post)
    assert notify._post_pushover({}) is None
    assert notify._post_pushover({}).startswith("pushover 500")
    assert "fake stall" in notify._post_pushover({})
    monkeypatch.setattr(notify, "_pushover_creds", lambda: None)
    result = notify._emit_pushover(SimpleNamespace(severity="P1"))
    assert not result.attempted
    metrics = sample(caplog, "notification")
    assert (metrics["success"], metrics["error"], metrics["skipped"]) == (1, 2, 1)
    assert metrics["error_ratio"] == pytest.approx(2 / 3)
    assert post.call_count == 3


def test_modify_wire_is_measured_separately(monkeypatch, caplog):
    from clients.ib_client import IBClient, IBOrderError
    import trading_halt
    monkeypatch.setattr(trading_halt, "is_trading_halted", lambda: False)
    broker = MagicMock()
    broker.placeOrder.side_effect = ["trade", RuntimeError("fake modify outage")]
    client = IBClient.__new__(IBClient)
    client._ib, client.logger = broker, logging.getLogger("test-broker")
    client._require_connection = lambda: None
    contract = SimpleNamespace(secType="STK", symbol="FAKE")
    order = SimpleNamespace(totalQuantity=1, lmtPrice=2, orderType="LMT", orderId=42)
    assert client.modify_order(contract, order, lmt_price=3) == "trade"
    with pytest.raises(IBOrderError, match="fake modify outage"):
        client.modify_order(contract, order, lmt_price=4)
    metrics = sample(caplog, "order_modify")
    assert (metrics["success"], metrics["error"]) == (1, 1)


def test_counters_bound_logging_and_do_not_lose_concurrent_outcomes(caplog):
    from concurrent.futures import ThreadPoolExecutor
    from utils.outcome_metrics import OutcomeCounters
    now = [0.0]
    metrics = OutcomeCounters(clock=lambda: now[0])
    def observations(_):
        for _ in range(500):
            metrics.record("database", "error")
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(observations, range(4)))
    assert not [r for r in caplog.records if r.message.startswith("operation_metrics ")]
    now[0] = 60.0
    metrics.emit()
    logged = [json.loads(r.message.removeprefix("operation_metrics "))
              for r in caplog.records if r.message.startswith("operation_metrics ")]
    assert len(logged) == 1
    assert logged[0]["error"] == 2000
    assert logged[0]["error_ratio"] == 1.0
    # Terminal flush emits pending changes even below the log interval.
    metrics.record("journal_upsert", "success")
    metrics.emit(force=True)
    final = json.loads(caplog.records[-1].message.removeprefix("operation_metrics "))
    assert final["operation"] == "journal_upsert"
    assert final["success_per_second"] == pytest.approx(1 / 60)
    assert final["observed_seconds"] == 60


def test_skip_has_no_fabricated_delivery_rate_or_error_ratio(caplog):
    from utils.outcome_metrics import OutcomeCounters
    metrics = OutcomeCounters(clock=lambda: 0.0)
    metrics.record("notification", "skipped")
    metrics.emit(force=True)
    final = json.loads(caplog.records[-1].message.removeprefix("operation_metrics "))
    assert final["success"] == final["error"] == 0
    assert final["skipped"] == 1
    assert final["error_ratio"] is None
    assert final["success_per_second"] is None


def test_daemon_flushes_quiet_pending_metrics_without_database_or_state(monkeypatch, caplog):
    from monitor_daemon.daemon import MonitorDaemon
    from utils import outcome_metrics
    now = [0.0]
    metrics = outcome_metrics.OutcomeCounters(clock=lambda: now[0])
    monkeypatch.setattr(outcome_metrics, "METRICS", metrics)
    metrics.record("journal_upsert", "error")
    now[0] = 60.0
    daemon = MonitorDaemon.__new__(MonitorDaemon)
    daemon.state_file = None
    daemon.save_state()
    final = sample(caplog, "journal_upsert")
    assert final["error"] == 1
    assert final["observed_seconds"] == 60


def test_flat_and_package_owners_share_one_process_counter(monkeypatch, caplog):
    import importlib
    flat = importlib.import_module("db.writer")
    packaged = importlib.import_module("scripts.db.writer")
    for owner in (flat, packaged):
        monkeypatch.setattr(owner, "_hrana_execute", lambda *args, **kwargs: None)
        owner.upsert_journal_entry("fake", {})
    metrics = sample(caplog, "journal_upsert")
    assert metrics["success"] == 2
    assert metrics["error"] == 0


@pytest.mark.parametrize("atomic", [False, True])
def test_monitor_http_connection_counts_failed_receipt_and_recovery(monkeypatch, caplog, atomic):
    from knowledge import http_db
    handle = http_db.Connection.__new__(http_db.Connection)
    handle._poisoned, handle._transaction, handle._baton = False, False, None
    handle._url = handle._origin = "https://fake.invalid"
    monkeypatch.setattr(handle, "_discard", lambda: None)
    close = {"type": "ok", "response": {"type": "close"}}
    statement_result = {"cols": [], "rows": [], "affected_row_count": 0}
    if atomic:
        result = {"step_results": [statement_result] * 3 + [None], "step_errors": [None] * 4}
        reply = [{"type": "ok", "response": {"type": "batch", "result": result}}, close]
        operation = lambda: handle.execute_transaction([("SELECT 1", ())])
    else:
        reply = [{"type": "ok", "response": {"type": "execute", "result": statement_result}}, close]
        operation = lambda: handle.execute("SELECT 1")
    request = MagicMock(side_effect=[reply, http_db.TransportError("fake receipt loss"), reply])
    monkeypatch.setattr(handle, "_request", request)
    operation()
    with pytest.raises(http_db.TransportError, match="fake receipt loss"):
        operation()
    operation()
    metrics = sample(caplog, "database")
    assert (metrics["success"], metrics["error"]) == (2, 1)
    assert metrics["error_ratio"] == pytest.approx(1 / 3)
    assert request.call_count == 3


@pytest.mark.parametrize("atomic", [False, True])
def test_api_database_fault_preserves_error_and_counts_once(monkeypatch, caplog, atomic):
    from api import db_http
    monkeypatch.setattr(db_http, "read_env", lambda: ("libsql://fake.invalid", "synthetic"))
    if atomic:
        body = {"results": [{"type": "ok", "response": {"type": "batch", "result": {
            "step_errors": [], "step_results": [{}, {}, {}, None],
        }}}]}
        response = MagicMock()
        response.__enter__.return_value = response
        response.read.return_value = json.dumps(body).encode()
        transport = MagicMock(side_effect=[response, TimeoutError("fake API stall"), response])
        monkeypatch.setattr(db_http.urllib.request, "urlopen", transport)
        operation = lambda: db_http.hrana_transaction([("SELECT 1", ())])
    else:
        transport = MagicMock(side_effect=[{}, TimeoutError("fake API stall"), {}])
        monkeypatch.setattr(db_http, "_post_pipeline", transport)
        monkeypatch.setattr(db_http, "_execute_result", lambda body: body)
        monkeypatch.setattr(db_http, "_rows_as_tuples", lambda body: [])
        operation = lambda: db_http.hrana_execute("SELECT 1")
    operation()
    with pytest.raises(db_http.DbHttpError):
        operation()
    operation()
    metrics = sample(caplog, "database")
    assert (metrics["success"], metrics["error"]) == (2, 1)
    assert metrics["error_ratio"] == pytest.approx(1 / 3)
    assert transport.call_count == 3


def test_removed_order_with_execution_is_distinct_from_a_new_fill_delta(monkeypatch, caplog):
    from monitor_daemon.handlers import fill_monitor as owner
    broker = MagicMock()
    broker.get_open_orders.return_value = []
    broker.get_managed_accounts.return_value = ["synthetic-account"]
    broker.get_fills.return_value = [SimpleNamespace(execution=SimpleNamespace(orderId=5))]
    monkeypatch.setattr(owner, "IBClient", lambda: broker)
    handler = owner.FillMonitorHandler(send_notifications=False)
    monkeypatch.setattr(handler, "_mirror_ib_orders_snapshot", lambda client: None)
    handler.known_orders = {5: {"filled": 25, "quantity": 25, "symbol": "FAKE"}}
    assert handler.execute()["complete_fills"] == 1
    assert handler.execute()["complete_fills"] == 0
    metrics = sample(caplog, "filled_order_removed")
    assert metrics["success"] == 1
    assert not any('"operation": "fill_detected"' in r.message for r in caplog.records)


def _raise_metrics_fault(*_args, **_kwargs):
    raise RuntimeError("synthetic metrics fault")


def test_metrics_fault_cannot_rewrite_a_completed_broker_call(monkeypatch):
    """A counter failure after placeOrder must not look like a rejected order.

    The transmitted-order guard in ib_place_order only arms after place_order
    returns. Reporting the live submit as IBOrderError lets a later cycle
    submit again, and it also hides the broker's own refusal.
    """
    from clients.ib_client import IBClient, IBOrderError
    from utils import outcome_metrics
    import trading_halt

    monkeypatch.setattr(trading_halt, "is_trading_halted", lambda: False)
    monkeypatch.setattr(outcome_metrics.METRICS, "record", _raise_metrics_fault)
    broker = MagicMock()
    trade = SimpleNamespace(order=SimpleNamespace(orderId=7))
    broker.placeOrder.return_value = trade
    client = IBClient.__new__(IBClient)
    client._ib = broker
    client.logger = logging.getLogger("metrics-fault")
    client._require_connection = lambda: None
    contract = SimpleNamespace(secType="STK", symbol="FAKE")
    order = SimpleNamespace(action="BUY", totalQuantity=1, lmtPrice=2, orderType="LMT")

    assert client.place_order(contract, order) is trade

    broker.placeOrder.side_effect = RuntimeError("synthetic socket refusal")
    with pytest.raises(IBOrderError, match="synthetic socket refusal"):
        client.place_order(contract, order)
    assert broker.placeOrder.call_count == 2


def test_metrics_fault_cannot_skip_fill_persistence_or_journal_upsert(monkeypatch):
    from db import writer
    from monitor_daemon.handlers import fill_monitor as owner
    from utils import outcome_metrics

    monkeypatch.setattr(outcome_metrics.METRICS, "record", _raise_metrics_fault)
    writes = []
    monkeypatch.setattr(writer, "_hrana_execute", lambda *args, **kwargs: writes.append(args))
    writer.upsert_journal_entry("fake-trade", {"symbol": "FAKE"})
    assert writes, "a committed journal upsert was reported as a failure"

    trade = MagicMock()
    trade.order.orderId, trade.order.action, trade.order.totalQuantity = 5, "BUY", 25
    trade.order.lmtPrice = 1.0
    trade.orderStatus.status, trade.orderStatus.filled = "Submitted", 10
    trade.orderStatus.remaining, trade.orderStatus.avgFillPrice = 15, 0.98
    trade.contract.symbol, trade.contract.localSymbol = "FAKE", "FAKE-STK"
    broker = MagicMock()
    broker.get_open_orders.return_value = [trade]
    monkeypatch.setattr(owner, "IBClient", lambda: broker)
    handler = owner.FillMonitorHandler(send_notifications=False)
    monkeypatch.setattr(handler, "_mirror_ib_orders_snapshot", lambda client: None)
    persisted = []
    monkeypatch.setattr(handler, "_persist_fill_to_journal", lambda *args: persisted.append(args))
    handler.known_orders = {5: {"filled": 0}}
    assert handler.execute()["partial_fills"] == 1
    assert persisted, "fill journal write was skipped after a metrics fault"
