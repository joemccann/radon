"""Canonical IB order-status vocabulary: helpers and agreement with ib_insync."""
from __future__ import annotations

import pytest

from utils import ib_order_status as s


def test_constants_are_the_exact_ib_strings():
    assert s.PENDING_SUBMIT == "PendingSubmit"
    assert s.PENDING_CANCEL == "PendingCancel"
    assert s.PRE_SUBMITTED == "PreSubmitted"
    assert s.SUBMITTED == "Submitted"
    assert s.API_PENDING == "ApiPending"
    assert s.API_CANCELLED == "ApiCancelled"
    assert s.CANCELLED == "Cancelled"
    assert s.FILLED_STATUS == "Filled"
    assert s.INACTIVE == "Inactive"
    assert s.REJECTED == "Rejected"
    assert s.UNKNOWN == "Unknown"


def test_sets():
    assert s.WORKING == {"PendingSubmit", "ApiPending", "PreSubmitted", "Submitted", "PendingCancel"}
    assert s.TERMINAL_FAILED == {"Cancelled", "ApiCancelled", "Inactive", "Rejected"}
    assert s.FILLED == {"Filled"}
    assert s.TERMINAL == s.TERMINAL_FAILED | s.FILLED
    assert s.UNACKNOWLEDGED == {"PendingSubmit", "ApiPending", "Unknown"}
    assert not (s.WORKING & s.TERMINAL)
    assert s.ALL_STATUSES == s.WORKING | s.TERMINAL | {"Unknown"}


def test_sets_agree_with_ib_insync():
    order = pytest.importorskip("ib_insync.order")
    assert set(order.OrderStatus.ActiveStates) <= s.WORKING
    assert set(order.OrderStatus.DoneStates) <= s.TERMINAL
    assert s.FILLED <= set(order.OrderStatus.DoneStates)
    emitted = {
        v for k, v in vars(order.OrderStatus).items()
        if k[:1].isupper() and isinstance(v, str)
    }
    assert emitted <= s.ALL_STATUSES


@pytest.mark.parametrize("status", ["Submitted", "PreSubmitted", "PendingSubmit", "ApiPending", "PendingCancel"])
def test_is_working_true(status):
    assert s.is_working(status)
    assert not s.is_terminal(status)


@pytest.mark.parametrize("status", ["Filled", "Cancelled", "ApiCancelled", "Inactive", "Rejected"])
def test_is_terminal_true(status):
    assert s.is_terminal(status)
    assert not s.is_working(status)


@pytest.mark.parametrize("status", [None, "", "Unknown", "submitted", "garbage"])
def test_helpers_are_strict_on_non_canonical(status):
    assert not s.is_working(status)
    assert not s.is_terminal(status)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Submitted", "Submitted"),
        ("submitted", "Submitted"),
        ("  PRESUBMITTED ", "PreSubmitted"),
        ("apicancelled", "ApiCancelled"),
        ("unknown", "Unknown"),
        ("rejected", "Rejected"),
        ("", None),
        (None, None),
        ("Working", None),
        (123, None),
    ],
)
def test_normalize(raw, expected):
    assert s.normalize(raw) == expected


def test_cancel_outcome_members():
    assert [m.name for m in s.CancelOutcome] == [
        "CANCELLED", "FILLED", "PARTIALLY_FILLED_THEN_CANCELLED", "STILL_WORKING", "UNKNOWN",
    ]
    assert s.CancelOutcome("cancelled") is s.CancelOutcome.CANCELLED
    assert s.CancelOutcome.PARTIALLY_FILLED_THEN_CANCELLED.value == "partially_filled_then_cancelled"
