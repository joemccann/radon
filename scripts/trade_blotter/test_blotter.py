"""
Test suite for Trade Blotter / Reconciliation Service.
Run with: python -m pytest scripts/trade_blotter/test_blotter.py -v
"""
import pytest
from datetime import datetime
from decimal import Decimal
from unittest.mock import Mock, patch, MagicMock

from models import Execution, Trade, TradeBlotter, Spread, Side, SecurityType


class TestExecution:
    """Tests for Execution model."""
    
    def test_stock_buy_cash_flow(self):
        """Buying stock = negative cash flow (pay money)."""
        exec = Execution(
            exec_id="001",
            time=datetime.now(),
            symbol="AAPL",
            sec_type=SecurityType.STOCK,
            side=Side.BUY,
            quantity=Decimal("100"),
            price=Decimal("150.00"),
            commission=Decimal("1.00"),
        )
        # Pay 100 * 150 + 1 commission = -15001
        assert exec.net_cash_flow == Decimal("-15001.00")
    
    def test_stock_sell_cash_flow(self):
        """Selling stock = positive cash flow (receive money)."""
        exec = Execution(
            exec_id="002",
            time=datetime.now(),
            symbol="AAPL",
            sec_type=SecurityType.STOCK,
            side=Side.SELL,
            quantity=Decimal("100"),
            price=Decimal("155.00"),
            commission=Decimal("1.00"),
        )
        # Receive 100 * 155 - 1 commission = 15499
        assert exec.net_cash_flow == Decimal("15499.00")
    
    def test_option_buy_cash_flow(self):
        """Buying options uses multiplier (default 100)."""
        exec = Execution(
            exec_id="003",
            time=datetime.now(),
            symbol="AAPL",
            sec_type=SecurityType.OPTION,
            side=Side.BUY,
            quantity=Decimal("10"),  # 10 contracts
            price=Decimal("5.50"),   # $5.50 per share
            commission=Decimal("6.50"),
            strike=Decimal("150"),
            right="C",
            expiry="20260320",
        )
        # Pay 10 * 5.50 * 100 + 6.50 = -5506.50
        assert exec.net_cash_flow == Decimal("-5506.50")
    
    def test_option_sell_cash_flow(self):
        """Selling options credits account."""
        exec = Execution(
            exec_id="004",
            time=datetime.now(),
            symbol="AAPL",
            sec_type=SecurityType.OPTION,
            side=Side.SELL,
            quantity=Decimal("10"),
            price=Decimal("3.20"),
            commission=Decimal("6.50"),
            strike=Decimal("145"),
            right="P",
            expiry="20260320",
        )
        # Receive 10 * 3.20 * 100 - 6.50 = 3193.50
        assert exec.net_cash_flow == Decimal("3193.50")
    
    def test_contract_description_stock(self):
        exec = Execution(
            exec_id="005",
            time=datetime.now(),
            symbol="MSFT",
            sec_type=SecurityType.STOCK,
            side=Side.BUY,
            quantity=Decimal("50"),
            price=Decimal("400.00"),
            commission=Decimal("1.00"),
        )
        assert exec.contract_desc == "MSFT (STK)"
    
    def test_contract_description_option(self):
        exec = Execution(
            exec_id="006",
            time=datetime.now(),
            symbol="TSLA",
            sec_type=SecurityType.OPTION,
            side=Side.BUY,
            quantity=Decimal("5"),
            price=Decimal("12.30"),
            commission=Decimal("3.25"),
            strike=Decimal("250"),
            right="C",
            expiry="20260417",
        )
        assert exec.contract_desc == "TSLA 20260417 250C"


class TestTrade:
    """Tests for Trade model (round-trip calculations)."""
    
    def test_closed_stock_trade_profitable(self):
        """Complete stock round-trip with profit."""
        trade = Trade(
            symbol="AAPL",
            contract_desc="AAPL (STK)",
            sec_type=SecurityType.STOCK,
            executions=[
                Execution(
                    exec_id="001",
                    time=datetime(2026, 3, 1, 10, 0),
                    symbol="AAPL",
                    sec_type=SecurityType.STOCK,
                    side=Side.BUY,
                    quantity=Decimal("100"),
                    price=Decimal("150.00"),
                    commission=Decimal("1.00"),
                ),
                Execution(
                    exec_id="002",
                    time=datetime(2026, 3, 3, 14, 0),
                    symbol="AAPL",
                    sec_type=SecurityType.STOCK,
                    side=Side.SELL,
                    quantity=Decimal("100"),
                    price=Decimal("160.00"),
                    commission=Decimal("1.00"),
                ),
            ]
        )
        
        assert trade.is_closed == True
        assert trade.net_quantity == Decimal("0")
        assert trade.total_commission == Decimal("2.00")
        # Bought at 15000, sold at 16000, paid 2 in commissions
        # P&L = 16000 - 15000 - 2 = 998
        assert trade.realized_pnl == Decimal("998.00")
    
    def test_closed_stock_trade_loss(self):
        """Complete stock round-trip with loss."""
        trade = Trade(
            symbol="MSFT",
            contract_desc="MSFT (STK)",
            sec_type=SecurityType.STOCK,
            executions=[
                Execution(
                    exec_id="003",
                    time=datetime(2026, 3, 1, 10, 0),
                    symbol="MSFT",
                    sec_type=SecurityType.STOCK,
                    side=Side.BUY,
                    quantity=Decimal("50"),
                    price=Decimal("420.00"),
                    commission=Decimal("1.00"),
                ),
                Execution(
                    exec_id="004",
                    time=datetime(2026, 3, 3, 14, 0),
                    symbol="MSFT",
                    sec_type=SecurityType.STOCK,
                    side=Side.SELL,
                    quantity=Decimal("50"),
                    price=Decimal("400.00"),
                    commission=Decimal("1.00"),
                ),
            ]
        )
        
        assert trade.is_closed == True
        # Bought at 21000, sold at 20000, paid 2 in commissions
        # P&L = 20000 - 21000 - 2 = -1002
        assert trade.realized_pnl == Decimal("-1002.00")
    
    def test_closed_option_trade(self):
        """Complete option round-trip."""
        trade = Trade(
            symbol="EWY",
            contract_desc="EWY 20260313 148P",
            sec_type=SecurityType.OPTION,
            executions=[
                # Sell to open (credit)
                Execution(
                    exec_id="005",
                    time=datetime(2026, 3, 3, 11, 0),
                    symbol="EWY",
                    sec_type=SecurityType.OPTION,
                    side=Side.SELL,
                    quantity=Decimal("60"),
                    price=Decimal("15.62"),
                    commission=Decimal("39.00"),
                    strike=Decimal("148"),
                    right="P",
                    expiry="20260313",
                ),
                # Buy to close (debit)
                Execution(
                    exec_id="006",
                    time=datetime(2026, 3, 3, 15, 0),
                    symbol="EWY",
                    sec_type=SecurityType.OPTION,
                    side=Side.BUY,
                    quantity=Decimal("60"),
                    price=Decimal("13.50"),
                    commission=Decimal("39.00"),
                ),
            ]
        )
        
        assert trade.is_closed == True
        # Sold 60 * 15.62 * 100 = 93720, commission 39
        # Bought 60 * 13.50 * 100 = 81000, commission 39
        # P&L = (93720 - 39) - (81000 + 39) = 93681 - 81039 = 12642
        assert trade.realized_pnl == Decimal("12642.00")
    
    def test_open_position_no_realized_pnl(self):
        """Open position should return None for realized P&L."""
        trade = Trade(
            symbol="NVDA",
            contract_desc="NVDA (STK)",
            sec_type=SecurityType.STOCK,
            executions=[
                Execution(
                    exec_id="007",
                    time=datetime(2026, 3, 1, 10, 0),
                    symbol="NVDA",
                    sec_type=SecurityType.STOCK,
                    side=Side.BUY,
                    quantity=Decimal("25"),
                    price=Decimal("800.00"),
                    commission=Decimal("1.00"),
                ),
            ]
        )
        
        assert trade.is_closed == False
        assert trade.net_quantity == Decimal("25")
        assert trade.realized_pnl is None
    
    def test_unrealized_pnl_stock(self):
        """Calculate unrealized P&L for open stock position."""
        trade = Trade(
            symbol="NVDA",
            contract_desc="NVDA (STK)",
            sec_type=SecurityType.STOCK,
            executions=[
                Execution(
                    exec_id="008",
                    time=datetime(2026, 3, 1, 10, 0),
                    symbol="NVDA",
                    sec_type=SecurityType.STOCK,
                    side=Side.BUY,
                    quantity=Decimal("100"),
                    price=Decimal("800.00"),
                    commission=Decimal("1.00"),
                ),
            ]
        )
        
        # Current price 850
        # Market value = 100 * 850 = 85000
        # Cash flow = -80001 (paid 80000 + 1 commission)
        # Unrealized = 85000 + (-80001) = 4999
        assert trade.unrealized_pnl(Decimal("850.00")) == Decimal("4999.00")
    
    def test_unrealized_pnl_option(self):
        """Calculate unrealized P&L for open option position."""
        trade = Trade(
            symbol="AAPL",
            contract_desc="AAPL 20260417 150C",
            sec_type=SecurityType.OPTION,
            executions=[
                Execution(
                    exec_id="009",
                    time=datetime(2026, 3, 1, 10, 0),
                    symbol="AAPL",
                    sec_type=SecurityType.OPTION,
                    side=Side.BUY,
                    quantity=Decimal("10"),
                    price=Decimal("5.00"),
                    commission=Decimal("6.50"),
                    strike=Decimal("150"),
                    right="C",
                    expiry="20260417",
                ),
            ]
        )
        
        # Current option price 7.50
        # Market value = 10 * 7.50 * 100 = 7500
        # Cash flow = -(10 * 5.00 * 100 + 6.50) = -5006.50
        # Unrealized = 7500 + (-5006.50) = 2493.50
        assert trade.unrealized_pnl(Decimal("7.50")) == Decimal("2493.50")
    
    def test_partial_close(self):
        """Partially closed position shows realized P&L on closed quantity and remaining basis on open quantity."""
        trade = Trade(
            symbol="TSLA",
            contract_desc="TSLA (STK)",
            sec_type=SecurityType.STOCK,
            executions=[
                Execution(
                    exec_id="010",
                    time=datetime(2026, 3, 1, 10, 0),
                    symbol="TSLA",
                    sec_type=SecurityType.STOCK,
                    side=Side.BUY,
                    quantity=Decimal("100"),
                    price=Decimal("250.00"),
                    commission=Decimal("1.00"),
                ),
                Execution(
                    exec_id="011",
                    time=datetime(2026, 3, 2, 14, 0),
                    symbol="TSLA",
                    sec_type=SecurityType.STOCK,
                    side=Side.SELL,
                    quantity=Decimal("50"),
                    price=Decimal("260.00"),
                    commission=Decimal("1.00"),
                ),
            ]
        )

        assert trade.is_closed == False
        assert trade.net_quantity == Decimal("50")
        assert trade.realized_quantity == Decimal("50")
        assert trade.realized_cost_basis == Decimal("12500.50")
        assert trade.realized_pnl == Decimal("498.50")
        assert trade.cost_basis == Decimal("12500.50")

        # At current price 270:
        # Cash flow so far = -25001 (buy) + 12999 (sell) = -12002
        # Market value = 50 * 270 = 13500
        # Unrealized = 13500 + (-12002) = 1498
        assert trade.unrealized_pnl(Decimal("270.00")) == Decimal("1498.00")


class TestSpreadTrade:
    """Tests for multi-leg spread trades."""
    
    def test_bull_put_spread_opened_and_closed(self):
        """
        Bull put spread (credit spread):
        - Sell higher strike put
        - Buy lower strike put
        """
        # Opening leg 1: Sell $148 put
        sell_high = Execution(
            exec_id="100",
            time=datetime(2026, 3, 3, 11, 9),
            symbol="EWY",
            sec_type=SecurityType.OPTION,
            side=Side.SELL,
            quantity=Decimal("60"),
            price=Decimal("15.62"),
            commission=Decimal("39.00"),
            strike=Decimal("148"),
            right="P",
            expiry="20260313",
        )
        
        # Opening leg 2: Buy $140 put
        buy_low = Execution(
            exec_id="101",
            time=datetime(2026, 3, 3, 11, 9),
            symbol="EWY",
            sec_type=SecurityType.OPTION,
            side=Side.BUY,
            quantity=Decimal("60"),
            price=Decimal("9.91"),
            commission=Decimal("39.00"),
            strike=Decimal("140"),
            right="P",
            expiry="20260313",
        )
        
        # Create separate trades for each leg
        short_put = Trade(
            symbol="EWY",
            contract_desc="EWY 20260313 148P",
            sec_type=SecurityType.OPTION,
            executions=[sell_high],
        )
        
        long_put = Trade(
            symbol="EWY",
            contract_desc="EWY 20260313 140P",
            sec_type=SecurityType.OPTION,
            executions=[buy_low],
        )
        
        # Verify opening cash flows
        # Short $148P: 60 * 15.62 * 100 - 39 = 93681
        assert sell_high.net_cash_flow == Decimal("93681.00")
        # Long $140P: -(60 * 9.91 * 100 + 39) = -59499
        assert buy_low.net_cash_flow == Decimal("-59499.00")
        
        # Net credit received = 93681 - 59499 = 34182
        net_credit = sell_high.net_cash_flow + buy_low.net_cash_flow
        assert net_credit == Decimal("34182.00")
        
        # Now close the spread
        buy_to_close_high = Execution(
            exec_id="102",
            time=datetime(2026, 3, 3, 15, 30),
            symbol="EWY",
            sec_type=SecurityType.OPTION,
            side=Side.BUY,
            quantity=Decimal("60"),
            price=Decimal("13.28"),
            commission=Decimal("39.00"),
            strike=Decimal("148"),
            right="P",
            expiry="20260313",
        )
        
        sell_to_close_low = Execution(
            exec_id="103",
            time=datetime(2026, 3, 3, 15, 30),
            symbol="EWY",
            sec_type=SecurityType.OPTION,
            side=Side.SELL,
            quantity=Decimal("60"),
            price=Decimal("5.28"),
            commission=Decimal("39.00"),
            strike=Decimal("140"),
            right="P",
            expiry="20260313",
        )
        
        short_put.executions.append(buy_to_close_high)
        long_put.executions.append(sell_to_close_low)
        
        # Both legs should be closed now
        assert short_put.is_closed == True
        assert long_put.is_closed == True
        
        # Short $148P P&L:
        # Received: 93681, Paid: 60 * 13.28 * 100 + 39 = 79719
        # P&L = 93681 - 79719 = 13962
        assert short_put.realized_pnl == Decimal("13962.00")
        
        # Long $140P P&L:
        # Paid: 59499, Received: 60 * 5.28 * 100 - 39 = 31641
        # P&L = 31641 - 59499 = -27858
        assert long_put.realized_pnl == Decimal("-27858.00")
        
        # Combined spread P&L = 13962 - 27858 = -13896
        # This is a LOSS because EWY dropped below both strikes
        combined_pnl = short_put.realized_pnl + long_put.realized_pnl
        assert combined_pnl == Decimal("-13896.00")


class TestTradeBlotter:
    """Tests for TradeBlotter aggregation."""
    
    def test_total_realized_pnl(self):
        """Sum realized P&L across closed and partially closed trades."""
        blotter = TradeBlotter(trades=[
            Trade(
                symbol="AAPL",
                contract_desc="AAPL (STK)",
                sec_type=SecurityType.STOCK,
                executions=[
                    Execution("001", datetime.now(), "AAPL", SecurityType.STOCK,
                              Side.BUY, Decimal("100"), Decimal("150"), Decimal("1")),
                    Execution("002", datetime.now(), "AAPL", SecurityType.STOCK,
                              Side.SELL, Decimal("100"), Decimal("160"), Decimal("1")),
                ]
            ),
            Trade(
                symbol="MSFT",
                contract_desc="MSFT (STK)",
                sec_type=SecurityType.STOCK,
                executions=[
                    Execution("003", datetime.now(), "MSFT", SecurityType.STOCK,
                              Side.BUY, Decimal("50"), Decimal("400"), Decimal("1")),
                    Execution("004", datetime.now(), "MSFT", SecurityType.STOCK,
                              Side.SELL, Decimal("50"), Decimal("390"), Decimal("1")),
                ]
            ),
            Trade(
                symbol="TSLA",
                contract_desc="TSLA (STK)",
                sec_type=SecurityType.STOCK,
                executions=[
                    Execution("005", datetime.now(), "TSLA", SecurityType.STOCK,
                              Side.BUY, Decimal("100"), Decimal("250"), Decimal("1")),
                    Execution("006", datetime.now(), "TSLA", SecurityType.STOCK,
                              Side.SELL, Decimal("50"), Decimal("260"), Decimal("1")),
                ]
            ),
        ])

        # AAPL: (16000 - 1) - (15000 + 1) = 998
        # MSFT: (19500 - 1) - (20000 + 1) = -502
        # TSLA partial: 50 * (260 - 250.01) - 1 pro-rata already included = 498.50
        # Total: 998 - 502 + 498.50 = 994.50
        assert blotter.total_realized_pnl == Decimal("994.50")
    
    def test_total_commissions(self):
        """Sum of all commissions across all trades."""
        blotter = TradeBlotter(trades=[
            Trade(
                symbol="AAPL",
                contract_desc="AAPL (STK)",
                sec_type=SecurityType.STOCK,
                executions=[
                    Execution("001", datetime.now(), "AAPL", SecurityType.STOCK,
                              Side.BUY, Decimal("100"), Decimal("150"), Decimal("1.50")),
                    Execution("002", datetime.now(), "AAPL", SecurityType.STOCK,
                              Side.SELL, Decimal("100"), Decimal("160"), Decimal("1.50")),
                ]
            ),
            Trade(
                symbol="TSLA",
                contract_desc="TSLA 20260320 250C",
                sec_type=SecurityType.OPTION,
                executions=[
                    Execution("003", datetime.now(), "TSLA", SecurityType.OPTION,
                              Side.BUY, Decimal("10"), Decimal("5.00"), Decimal("6.50"),
                              strike=Decimal("250"), right="C", expiry="20260320"),
                ]
            ),
        ])
        
        assert blotter.total_commissions == Decimal("9.50")
    
    def test_open_vs_closed_trades(self):
        """Correctly categorize open and closed trades."""
        closed_trade = Trade(
            symbol="AAPL",
            contract_desc="AAPL (STK)",
            sec_type=SecurityType.STOCK,
            executions=[
                Execution("001", datetime.now(), "AAPL", SecurityType.STOCK,
                          Side.BUY, Decimal("100"), Decimal("150"), Decimal("1")),
                Execution("002", datetime.now(), "AAPL", SecurityType.STOCK,
                          Side.SELL, Decimal("100"), Decimal("160"), Decimal("1")),
            ]
        )
        
        open_trade = Trade(
            symbol="MSFT",
            contract_desc="MSFT (STK)",
            sec_type=SecurityType.STOCK,
            executions=[
                Execution("003", datetime.now(), "MSFT", SecurityType.STOCK,
                          Side.BUY, Decimal("50"), Decimal("400"), Decimal("1")),
            ]
        )
        
        blotter = TradeBlotter(trades=[closed_trade, open_trade])
        
        assert len(blotter.open_trades) == 1
        assert len(blotter.closed_trades) == 1
        assert blotter.open_trades[0].symbol == "MSFT"
        assert blotter.closed_trades[0].symbol == "AAPL"


class TestIBFetcher:
    """Tests for IB data fetching (will be mocked)."""
    
    def test_fetch_executions_parses_fills(self):
        """IBFetcher correctly parses IB fill objects."""
        from blotter_service import IBFetcher
        
        # Create mock IB fill
        mock_fill = Mock()
        mock_fill.contract.symbol = "AAPL"
        mock_fill.contract.secType = "STK"
        mock_fill.contract.strike = 0
        mock_fill.contract.right = ""
        mock_fill.contract.lastTradeDateOrContractMonth = ""
        mock_fill.execution.execId = "0001"
        mock_fill.execution.time = datetime(2026, 3, 3, 10, 30)
        mock_fill.execution.side = "BOT"
        mock_fill.execution.shares = 100.0
        mock_fill.execution.price = 150.50
        mock_fill.commissionReport.commission = 1.00
        
        fetcher = IBFetcher(host="127.0.0.1", port=4001)
        
        with patch.object(fetcher, '_connect'):
            with patch.object(fetcher, '_disconnect'):
                with patch.object(fetcher, 'ib') as mock_ib:
                    mock_ib.fills.return_value = [mock_fill]
                    
                    executions = fetcher.fetch_today_executions()
        
        assert len(executions) == 1
        assert executions[0].symbol == "AAPL"
        assert executions[0].side == Side.BUY
        assert executions[0].quantity == Decimal("100")
        assert executions[0].price == Decimal("150.50")
        assert executions[0].commission == Decimal("1.00")
    
    def test_fetch_executions_handles_options(self):
        """IBFetcher correctly parses option fills."""
        from blotter_service import IBFetcher
        
        mock_fill = Mock()
        mock_fill.contract.symbol = "EWY"
        mock_fill.contract.secType = "OPT"
        mock_fill.contract.strike = 148.0
        mock_fill.contract.right = "P"
        mock_fill.contract.lastTradeDateOrContractMonth = "20260313"
        mock_fill.execution.execId = "0002"
        mock_fill.execution.time = datetime(2026, 3, 3, 11, 9)
        mock_fill.execution.side = "SLD"
        mock_fill.execution.shares = 60.0
        mock_fill.execution.price = 15.62
        mock_fill.commissionReport.commission = 39.00
        
        fetcher = IBFetcher(host="127.0.0.1", port=4001)
        
        with patch.object(fetcher, '_connect'):
            with patch.object(fetcher, '_disconnect'):
                with patch.object(fetcher, 'ib') as mock_ib:
                    mock_ib.fills.return_value = [mock_fill]
                    
                    executions = fetcher.fetch_today_executions()
        
        assert len(executions) == 1
        assert executions[0].symbol == "EWY"
        assert executions[0].sec_type == SecurityType.OPTION
        assert executions[0].side == Side.SELL
        assert executions[0].strike == Decimal("148")
        assert executions[0].right == "P"
        assert executions[0].expiry == "20260313"


class TestBlotterService:
    """Tests for the main BlotterService."""
    
    def test_group_executions_by_contract(self):
        """Executions are grouped into trades by contract."""
        from blotter_service import BlotterService
        
        executions = [
            Execution("001", datetime(2026, 3, 3, 10, 0), "AAPL", SecurityType.STOCK,
                      Side.BUY, Decimal("100"), Decimal("150"), Decimal("1")),
            Execution("002", datetime(2026, 3, 3, 11, 0), "MSFT", SecurityType.STOCK,
                      Side.BUY, Decimal("50"), Decimal("400"), Decimal("1")),
            Execution("003", datetime(2026, 3, 3, 14, 0), "AAPL", SecurityType.STOCK,
                      Side.SELL, Decimal("100"), Decimal("160"), Decimal("1")),
        ]
        
        service = BlotterService(fetcher=Mock())
        trades = service._group_executions(executions)
        
        assert len(trades) == 2
        
        aapl_trade = next(t for t in trades if t.symbol == "AAPL")
        msft_trade = next(t for t in trades if t.symbol == "MSFT")
        
        assert len(aapl_trade.executions) == 2
        assert len(msft_trade.executions) == 1
        assert aapl_trade.is_closed == True
        assert msft_trade.is_closed == False
    
    def test_build_blotter_from_executions(self):
        """Full blotter build from raw executions."""
        from blotter_service import BlotterService
        
        mock_fetcher = Mock()
        mock_fetcher.fetch_executions.return_value = [
            Execution("001", datetime(2026, 3, 3, 10, 0), "AAPL", SecurityType.STOCK,
                      Side.BUY, Decimal("100"), Decimal("150"), Decimal("1")),
            Execution("002", datetime(2026, 3, 3, 14, 0), "AAPL", SecurityType.STOCK,
                      Side.SELL, Decimal("100"), Decimal("160"), Decimal("1")),
        ]
        
        service = BlotterService(fetcher=mock_fetcher)
        blotter = service.build_blotter()
        
        assert len(blotter.trades) == 1
        assert blotter.total_realized_pnl == Decimal("998.00")
        assert blotter.total_commissions == Decimal("2.00")


class TestSpreadGrouping:
    """Tests for spread identification and grouping."""
    
    def test_put_spread_identification(self):
        """Identify put spread from two legs."""
        blotter = TradeBlotter(trades=[
            Trade(
                symbol="EWY",
                contract_desc="EWY 20260313 148P",
                sec_type=SecurityType.OPTION,
                executions=[
                    Execution("001", datetime.now(), "EWY", SecurityType.OPTION,
                              Side.SELL, Decimal("60"), Decimal("15.62"), Decimal("39"),
                              strike=Decimal("148"), right="P", expiry="20260313"),
                ]
            ),
            Trade(
                symbol="EWY",
                contract_desc="EWY 20260313 140P",
                sec_type=SecurityType.OPTION,
                executions=[
                    Execution("002", datetime.now(), "EWY", SecurityType.OPTION,
                              Side.BUY, Decimal("60"), Decimal("9.91"), Decimal("39"),
                              strike=Decimal("140"), right="P", expiry="20260313"),
                ]
            ),
        ])
        
        spreads = blotter.get_spreads()
        assert len(spreads) == 1
        assert "Put Spread" in spreads[0].name
        assert len(spreads[0].legs) == 2
    
    def test_risk_reversal_identification(self):
        """Identify risk reversal (short put, long call)."""
        blotter = TradeBlotter(trades=[
            Trade(
                symbol="AAOI",
                contract_desc="AAOI 20260306 90P",
                sec_type=SecurityType.OPTION,
                executions=[
                    Execution("001", datetime.now(), "AAOI", SecurityType.OPTION,
                              Side.SELL, Decimal("25"), Decimal("3.65"), Decimal("1"),
                              strike=Decimal("90"), right="P", expiry="20260306"),
                ]
            ),
            Trade(
                symbol="AAOI",
                contract_desc="AAOI 20260306 105C",
                sec_type=SecurityType.OPTION,
                executions=[
                    Execution("002", datetime.now(), "AAOI", SecurityType.OPTION,
                              Side.BUY, Decimal("25"), Decimal("2.65"), Decimal("1"),
                              strike=Decimal("105"), right="C", expiry="20260306"),
                ]
            ),
        ])
        
        spreads = blotter.get_spreads()
        assert len(spreads) == 1
        assert "Risk Reversal" in spreads[0].name
    
    def test_spread_combined_cash_flow(self):
        """Verify combined cash flow calculation for spreads."""
        blotter = TradeBlotter(trades=[
            Trade(
                symbol="EWY",
                contract_desc="EWY 20260313 148P",
                sec_type=SecurityType.OPTION,
                executions=[
                    Execution("001", datetime.now(), "EWY", SecurityType.OPTION,
                              Side.SELL, Decimal("60"), Decimal("15.62"), Decimal("40"),
                              strike=Decimal("148"), right="P", expiry="20260313"),
                ]
            ),
            Trade(
                symbol="EWY",
                contract_desc="EWY 20260313 140P",
                sec_type=SecurityType.OPTION,
                executions=[
                    Execution("002", datetime.now(), "EWY", SecurityType.OPTION,
                              Side.BUY, Decimal("60"), Decimal("9.91"), Decimal("40"),
                              strike=Decimal("140"), right="P", expiry="20260313"),
                ]
            ),
        ])
        
        spreads = blotter.get_spreads()
        spread = spreads[0]
        
        # Short $148P: 60 * 15.62 * 100 - 40 = 93680
        # Long $140P: -(60 * 9.91 * 100 + 40) = -59500
        # Net: 93680 - 59500 = 34180 - but we need to account for commissions in cash flow
        # Actual: (60 * 15.62 * 100 - 40) + (-(60 * 9.91 * 100 + 40))
        # = 93680 + (-59500) = 34180
        
        # Verify it's positive (credit spread)
        assert spread.total_cash_flow > 0
        assert spread.total_commission == Decimal("80")


class TestFlexQueryFetcher:
    """Tests for historical data via Flex Query."""
    
    def test_parse_flex_query_response(self):
        """Parse XML response from IB Flex Query."""
        from blotter_service import FlexQueryFetcher
        
        sample_xml = """<?xml version="1.0" encoding="UTF-8"?>
        <FlexQueryResponse>
            <FlexStatements>
                <FlexStatement>
                    <Trades>
                        <Trade symbol="AAPL" securityType="STK" 
                               dateTime="2026-03-01;10:30:00"
                               buySell="BUY" quantity="100" tradePrice="150.00"
                               ibCommission="1.00" tradeID="001"/>
                        <Trade symbol="AAPL" securityType="STK"
                               dateTime="2026-03-03;14:00:00"  
                               buySell="SELL" quantity="100" tradePrice="160.00"
                               ibCommission="1.00" tradeID="002"/>
                    </Trades>
                </FlexStatement>
            </FlexStatements>
        </FlexQueryResponse>"""
        
        fetcher = FlexQueryFetcher(token="test", query_id="test")
        executions = fetcher._parse_xml(sample_xml)
        
        assert len(executions) == 2
        assert executions[0].symbol == "AAPL"
        assert executions[0].side == Side.BUY
        assert executions[1].side == Side.SELL


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
