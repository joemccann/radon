"""T-531: observe the actual snapshot CLI at a fake broker boundary."""

import json
from types import SimpleNamespace

import pytest
from ib_insync import Option

from scripts import ib_option_quotes as quotes
from scripts.clients import contract_resolver


class FakeBroker:
    def __init__(self, symbol, failure=None):
        self.symbol = symbol
        self.failure = failure
        self.RequestTimeout = 0
        self.calls = []
        self.option_contracts = []

    def _call(self, phase, *args):
        self.calls.append((phase, args, self.RequestTimeout))
        if self.failure == phase:
            raise TimeoutError(f"fake {phase} timeout")

    def reqMarketDataType(self, kind):
        self._call("data_type", kind)

    def qualifyContracts(self, *contracts):
        options = isinstance(contracts[0], Option) if contracts else True
        self._call("qualify_options" if options else "qualify_underlying", *contracts)
        if not contracts:
            return []
        for index, contract in enumerate(contracts):
            contract.conId = 1000 + index
        if not options and self.failure == "unqualified_underlying":
            contracts[0].conId = 0
        if options:
            self.option_contracts = list(contracts)
            # A rejected option must never reach reqTickers.
            contracts[-1].conId = 0
            return [*contracts, None]
        return list(contracts)

    def reqSecDefOptParams(self, *args):
        self._call("secdef", *args)
        return [
            SimpleNamespace(tradingClass=self.symbol, exchange="SMART",
                            expirations={"20261030", "20261120"},
                            strikes=list(range(90, 111))),
            SimpleNamespace(tradingClass=self.symbol + "1", exchange="SMART",
                            expirations={"20261030"}, strikes=[999]),
        ]

    def reqTickers(self, *contracts):
        options = isinstance(contracts[0], Option) if contracts else True
        self._call("snapshot_options" if options else "snapshot_underlying", *contracts)
        if not options:
            return [SimpleNamespace(last=100.0)]
        assert all(c.conId for c in contracts), "unqualified option requested"
        greeks = SimpleNamespace(impliedVol=0.4, delta=-0.3, gamma=0.02, theta=-0.1, vega=0.2)
        # Broker completion order need not match request order.
        return [SimpleNamespace(contract=c, bid=1.0, ask=1.4, last=1.2,
                                volume=12, modelGreeks=greeks) for c in reversed(contracts)]


@pytest.fixture
def broker_cli(monkeypatch):
    def configure(symbol="XYZ", failure=None, right=None, expiries="2026-10-30,20261120,20261218"):
        broker = FakeBroker(symbol, failure)
        connections = []
        disconnects = []

        def connect(**kwargs):
            connections.append(kwargs)
            if failure == "connect":
                raise ConnectionError("fake not authenticated")

        client = SimpleNamespace(_ib=broker, connect=connect,
                                 disconnect=lambda: disconnects.append(True))
        monkeypatch.setattr(quotes, "IBClient", lambda: client)
        # Real contract construction, no resolver or IB network operation.
        underlying = contract_resolver.resolve_quote_contract(symbol)
        monkeypatch.setattr(contract_resolver, "resolve_quote_contract", lambda _: underlying)
        argv = ["ib_option_quotes.py", "--symbol", symbol.lower(),
                "--expiries", expiries, "--wings", "10", "--port", "4999"]
        if right:
            argv += ["--right", right]
        monkeypatch.setattr("sys.argv", argv)
        return broker, connections, disconnects
    return configure


@pytest.mark.parametrize("symbol,sec_type", [("XYZ", "STK"), ("SPX", "IND")])
@pytest.mark.parametrize("right", [None, "P"])
def test_cli_prices_requested_contracts_in_bounded_batches(broker_cli, capsys, symbol, sec_type, right):
    broker, connections, disconnects = broker_cli(symbol, right=right)
    quotes.main()
    payload = json.loads(capsys.readouterr().out)

    assert len(connections) == 1
    assert connections[0]["port"] == 4999
    assert connections[0]["client_id"] == "auto"
    assert 0 < connections[0]["timeout"] <= 15
    assert disconnects == [True]
    assert broker.calls[0][:2] == ("data_type", (4,))
    secdef = [c for c in broker.calls if c[0] == "secdef"]
    assert [c[1] for c in secdef] == [(symbol, "", sec_type, 1000)]
    requested = {(c.symbol, c.lastTradeDateOrContractMonth, c.strike, c.right,
                  c.exchange, c.tradingClass) for c in broker.option_contracts}
    assert requested == {(symbol, expiry, float(strike), side, "SMART", symbol)
                         for expiry in ("20261030", "20261120")
                         for strike in range(90, 111) for side in ([right] if right else ["C", "P"])}
    snapshots = [c[1] for c in broker.calls if c[0] == "snapshot_options"]
    assert [len(batch) for batch in snapshots] == ([40, 1] if right else [40, 40, 3])
    assert [id(c) for batch in snapshots for c in batch] == [id(c) for c in broker.option_contracts[:-1]]
    assert all(timeout == 15 for phase, _, timeout in broker.calls if phase != "data_type")
    assert payload["ticker"] == symbol and payload["source"] == "ib" and payload["spot"] == 100
    assert payload["missing_expiries"] == ["2026-12-18"]
    assert set(payload["expirations"]) == {"2026-10-30", "2026-11-20"}
    for expiry, rows in payload["expirations"].items():
        assert rows and all(row["expiry"] == expiry for row in rows)
        assert [(row["strike"], row["right"]) for row in rows] == sorted(
            (row["strike"], row["right"]) for row in rows)
        assert all(row["mid"] == 1.2 and row["delta"] == -0.3 for row in rows)
    assert sum(map(len, payload["expirations"].values())) == len(broker.option_contracts) - 1


@pytest.mark.parametrize("failure", ["connect", "data_type", "qualify_underlying",
                                     "snapshot_underlying", "secdef", "qualify_options", "snapshot_options"])
def test_cli_errors_are_json_and_always_disconnect(broker_cli, capsys, failure):
    _, _, disconnects = broker_cli(failure=failure)
    with pytest.raises(SystemExit) as exc:
        quotes.main()
    assert exc.value.code == 1
    payload = json.loads(capsys.readouterr().out)
    expected = {
        "connect": "fake not authenticated",
        "data_type": "fake data_type timeout",
        "qualify_underlying": "ib_insync qualifyContracts timed out after 15s",
        "snapshot_underlying": "ib_insync reqTickers timed out after 15s",
        "secdef": "ib_insync reqSecDefOptParams timed out after 15s",
        "qualify_options": "fake qualify_options timeout",
        "snapshot_options": "ib_insync reqTickers timed out after 15s",
    }
    assert payload == {"error": expected[failure]}
    assert "expirations" not in payload
    assert disconnects == [True]


def test_unqualified_underlying_refuses_before_secdef(broker_cli, capsys):
    broker, _, disconnects = broker_cli(failure="unqualified_underlying")
    quotes.main()
    assert json.loads(capsys.readouterr().out) == {"error": "Could not qualify XYZ"}
    assert [c[0] for c in broker.calls] == ["data_type", "qualify_underlying"]
    assert disconnects == [True]


def test_missing_book_reports_absence_without_option_snapshots(broker_cli, capsys):
    broker, _, disconnects = broker_cli(expiries="20261218")
    quotes.main()
    assert json.loads(capsys.readouterr().out) == {
        "ticker": "XYZ", "spot": 100.0, "source": "ib",
        "expirations": {}, "missing_expiries": ["2026-12-18"],
    }
    assert not any(call[0] == "snapshot_options" for call in broker.calls)
    assert disconnects == [True]
