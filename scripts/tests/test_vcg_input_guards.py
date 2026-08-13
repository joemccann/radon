import argparse

import pytest

from scripts import vcg_scan


@pytest.mark.parametrize("raw, expected", [("HYG", "HYG"), ("jnk", "JNK"), (" lqd ", "LQD")])
def test_credit_proxy_allowlist(raw, expected):
    assert vcg_scan._credit_proxy(raw) == expected


@pytest.mark.parametrize("raw", ["SPY", "HYG;touch /tmp/pwn", "", "VIX"])
def test_credit_proxy_rejects_noncanonical_symbols(raw):
    with pytest.raises(argparse.ArgumentTypeError):
        vcg_scan._credit_proxy(raw)


@pytest.mark.parametrize("raw", ["0", "-1", "1.5", "2521"])
def test_backtest_days_are_bounded_positive_integers(raw):
    with pytest.raises(argparse.ArgumentTypeError):
        vcg_scan._bounded_backtest_days(raw)


def test_exploratory_proxy_does_not_publish_shared_snapshot(monkeypatch):
    monkeypatch.setattr(vcg_scan, "mirror_scan_snapshot", lambda *_: pytest.fail("published"))
    assert vcg_scan._publish_canonical_snapshot("JNK", {"credit_proxy": "JNK"}) is False


def test_hyg_publishes_shared_snapshot(monkeypatch):
    calls = []
    monkeypatch.setattr(vcg_scan, "mirror_scan_snapshot", lambda *args: calls.append(args))
    payload = {"credit_proxy": "HYG"}
    assert vcg_scan._publish_canonical_snapshot("HYG", payload) is True
    assert calls == [("vcg-scan", payload)]
