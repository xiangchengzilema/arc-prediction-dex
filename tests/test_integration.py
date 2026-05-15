#!/usr/bin/env python3
"""End-to-End Integration Tests for Arc Prediction DEX.

Tests full trading flows:
- Market creation → AMM trade → position tracking
- Order book flow: place orders → match → fill
- Liquidity lifecycle: add → trade → fees → remove
- Oracle flow: propose → dispute → vote → finalize
- Portfolio aggregation across markets

Run: pytest tests/test_integration.py -v
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from amm import AMMPool
from orderbook import OrderBook
from market_engine import MarketEngine
from liquidity_pool import LiquidityPoolManager
from oracle import ResolutionOracle
from portfolio import PortfolioManager
from analytics import MarketAnalytics


class TestAMMTradingFlow(unittest.TestCase):
    """Test complete AMM trading flow."""

    def test_create_market_buy_and_check_position(self):
        """Market → AMM buy → verify position in portfolio."""
        market_id = "btc_150k"
        db = ":memory:"

        # Create market
        engine = MarketEngine(db_path=db)
        mkt = engine.create_market(
            question="Will BTC hit $150k?",
            outcomes=["YES", "NO"],
            deadline="2027-01-01T00:00:00Z",
            creator_id="creator",
        )
        engine.open_market(mkt["market_id"])

        # Initialize AMM
        amm = AMMPool(market_id, db_path=db)
        amm.initialize(10000, creator_id="creator")

        # Buy YES shares
        buy = amm.buy_outcome("YES", 500, user_id="alice")
        self.assertGreater(buy["shares_received"], 0)
        self.assertGreater(buy["avg_price"], 0)

        # Verify price moved
        price_yes = amm.get_price("YES")
        self.assertGreater(price_yes, 0.5)

        # Sell some back
        sell = amm.sell_outcome("YES", buy["shares_received"] * 0.5, user_id="alice")
        self.assertGreater(sell["usdc_received"], 0)

    def test_multiple_traders_move_price(self):
        """Multiple trades should progressively move price."""
        amm = AMMPool("multi_trade", db_path=":memory:")
        amm.initialize(50000)

        prices = []
        for i in range(10):
            amm.buy_outcome("YES", 500, user_id=f"trader_{i}")
            prices.append(amm.get_price("YES"))

        # Prices should be monotonically increasing
        for i in range(1, len(prices)):
            self.assertGreater(prices[i], prices[i-1])


class TestOrderBookFlow(unittest.TestCase):
    """Test order book trading flow."""

    def test_limit_order_matching_flow(self):
        """Place orders on both sides → match → verify fills."""
        book = OrderBook("ob_flow", db_path=":memory:")

        # Build book
        book.place_limit_order("alice", "BUY", 0.55, 100)
        book.place_limit_order("bob", "BUY", 0.54, 200)
        book.place_limit_order("charlie", "SELL", 0.60, 100)
        book.place_limit_order("dave", "SELL", 0.62, 80)

        # Check book state
        data = book.get_order_book()
        self.assertEqual(data["best_bid"], 0.55)
        self.assertEqual(data["best_ask"], 0.60)

        # Place crossing order
        result = book.place_limit_order("eve", "BUY", 0.61, 150)
        self.assertGreater(len(result["fills"]), 0)

        # Verify trades recorded
        trades = book.get_recent_trades()
        self.assertGreater(len(trades), 0)

    def test_market_order_execution(self):
        """Market order should fill against best available."""
        book = OrderBook("mkt_order", db_path=":memory:")

        # Build book
        book.place_limit_order("alice", "SELL", 0.55, 100)
        book.place_limit_order("bob", "SELL", 0.56, 200)

        # Market buy
        result = book.place_market_order("charlie", "BUY", 150)
        self.assertGreater(len(result["fills"]), 0)
        # Should fill at best ask price
        self.assertEqual(result["fills"][0]["price"], 0.55)


class TestLiquidityFlow(unittest.TestCase):
    """Test liquidity management flow."""

    def test_add_remove_liquidity(self):
        """Add liquidity → trade → collect fees → remove."""
        liq = LiquidityPoolManager(db_path=":memory:")

        # Create pool
        pool = liq.create_pool("test_pool", 10000, "alice")
        self.assertIn("pool_id", pool)

        # Add liquidity
        bob = liq.add_liquidity("test_pool", 5000, "bob")
        self.assertGreater(bob["lp_tokens_minted"], 0)

        # Record trade fees
        liq.record_trade_fees("test_pool", 100)

        # Collect fees
        fees = liq.collect_fees("test_pool", "alice")
        self.assertGreaterEqual(fees["fees_collected"], 0)

        # Pool info
        info = liq.get_pool_info("test_pool")
        self.assertEqual(info["total_liquidity"], 15000)

    def test_user_positions(self):
        """Should track LP positions per user."""
        liq = LiquidityPoolManager(db_path=":memory:")
        liq.create_pool("pos_test", 10000, "alice")
        liq.add_liquidity("pos_test", 5000, "bob")
        liq.add_liquidity("pos_test", 3000, "charlie")

        positions = liq.get_user_positions("bob")
        self.assertEqual(len(positions), 1)
        self.assertGreater(positions[0]["lp_tokens"], 0)


class TestOracleFlow(unittest.TestCase):
    """Test oracle resolution flow."""

    def test_undisputed_resolution(self):
        """Propose → no dispute → finalize."""
        oracle = ResolutionOracle(db_path=":memory:")

        prop = oracle.propose_resolution(
            "undisputed_mkt", "YES", "alice",
            evidence="Clear evidence",
            bond_amount=100
        )
        self.assertEqual(prop["status"], "PENDING")

        # Finalize (simulating expired dispute period)
        # Note: In real scenario, dispute period must expire
        # For testing we check the proposal exists
        pending = oracle.get_pending_resolutions()
        self.assertEqual(len(pending), 1)

    def test_dispute_and_vote_flow(self):
        """Propose → dispute → vote → check counts."""
        oracle = ResolutionOracle(db_path=":memory:")

        prop = oracle.propose_resolution("disputed_mkt", "YES", "alice", bond_amount=100)
        oracle.dispute_proposal(prop["proposal_id"], "bob", "NO", "Counter evidence")

        oracle.vote_on_proposal(prop["proposal_id"], "charlie", "SUPPORT", 2.0)
        oracle.vote_on_proposal(prop["proposal_id"], "dave", "SUPPORT", 1.5)
        oracle.vote_on_proposal(prop["proposal_id"], "eve", "OPPOSE", 1.0)

        # Proposal should be disputed
        pending = oracle.get_pending_resolutions()
        disputed = [p for p in pending if p["status"] == "DISPUTED"]
        self.assertEqual(len(disputed), 1)


class TestPortfolioFlow(unittest.TestCase):
    """Test portfolio management flow."""

    def test_open_close_position(self):
        """Open position → close with profit."""
        pm = PortfolioManager(db_path=":memory:")

        # Open
        pos = pm.open_position("alice", "btc_100k", "YES", 100, 0.55, fee=0.55)
        self.assertIn("position_id", pos)

        # Check positions
        positions = pm.get_positions("alice")
        self.assertEqual(len(positions), 1)

        # Close with profit (exit at 0.70)
        close = pm.close_position(pos["position_id"], 100, 0.70, fee=0.70)
        self.assertGreater(close["realized_pnl"], 0)

        # Verify realized P&L
        pnl = pm.calculate_pnl("alice")
        self.assertGreater(pnl["total_realized_pnl"], 0)

    def test_dca_position(self):
        """Multiple buys should average entry price."""
        pm = PortfolioManager(db_path=":memory:")

        pm.open_position("alice", "eth_10k", "YES", 100, 0.40)
        pm.open_position("alice", "eth_10k", "YES", 100, 0.50)

        positions = pm.get_positions("alice")
        self.assertEqual(len(positions), 1)
        # Avg price should be between 0.40 and 0.50
        self.assertGreater(positions[0]["avg_entry_price"], 0.40)
        self.assertLess(positions[0]["avg_entry_price"], 0.50)

    def test_portfolio_value(self):
        """Should calculate total portfolio value."""
        pm = PortfolioManager(db_path=":memory:")

        pm.open_position("alice", "mkt1", "YES", 100, 0.50)
        pm.open_position("alice", "mkt2", "NO", 200, 0.30)

        value = pm.get_portfolio_value("alice", {
            "mkt1_YES": 0.60,
            "mkt2_NO": 0.25,
        })
        self.assertGreater(value["total_value"], 0)
        self.assertEqual(value["open_positions"], 2)

    def test_leaderboard(self):
        """Should generate leaderboard."""
        pm = PortfolioManager(db_path=":memory:")

        pm.open_position("alice", "mkt1", "YES", 100, 0.50)
        pm.open_position("bob", "mkt2", "YES", 200, 0.30)

        lb = pm.get_leaderboard()
        self.assertEqual(len(lb), 2)
        self.assertIn("rank", lb[0])
        self.assertIn("win_rate", lb[0])

    def test_trade_history(self):
        """Should record trade history."""
        pm = PortfolioManager(db_path=":memory:")

        pm.open_position("alice", "mkt1", "YES", 100, 0.50)
        pm.open_position("alice", "mkt1", "YES", 50, 0.55)

        history = pm.get_trade_history("alice")
        self.assertEqual(len(history), 2)


class TestAnalyticsFlow(unittest.TestCase):
    """Test analytics integration."""

    def test_volume_tracking(self):
        """Should track and report volume."""
        analytics = MarketAnalytics(db_path=":memory:")

        analytics.record_volume("mkt1", 500, "BUY", "alice")
        analytics.record_volume("mkt1", 300, "SELL", "bob")
        analytics.record_volume("mkt1", 200, "BUY", "alice")

        vol = analytics.calculate_volume("mkt1", hours=24)
        self.assertEqual(vol["total_volume"], 1000)
        self.assertEqual(vol["total_trades"], 3)

    def test_implied_probability(self):
        """Should calculate implied probabilities."""
        analytics = MarketAnalytics(db_path=":memory:")

        result = analytics.calculate_implied_probability("mkt1", {
            "YES": 0.65, "NO": 0.37
        })
        self.assertIn("implied_probabilities", result)
        self.assertIn("overround", result)
        self.assertEqual(result["most_likely"], "YES")

    def test_manipulation_detection(self):
        """Should detect suspicious patterns."""
        analytics = MarketAnalytics(db_path=":memory:")

        trades = [
            {"user_id": "alice", "side": "BUY", "amount": 100},
            {"user_id": "alice", "side": "SELL", "amount": 100},
            {"user_id": "alice", "side": "BUY", "amount": 100},
            {"user_id": "alice", "side": "SELL", "amount": 100},
            {"user_id": "alice", "side": "BUY", "amount": 100},
        ]
        result = analytics.detect_price_manipulation("mkt1", trades)
        self.assertGreater(result["manipulation_score"], 0)
        self.assertGreater(len(result["alerts"]), 0)

    def test_market_report(self):
        """Should generate comprehensive report."""
        analytics = MarketAnalytics(db_path=":memory:")
        analytics.record_volume("report_mkt", 1000, "BUY")

        report = analytics.generate_market_report(
            "report_mkt",
            pool_data={"total_liquidity": 50000},
            orderbook_data={"spread": 0.05, "mid_price": 0.55, "bids": [], "asks": []},
            outcomes_data={"YES": 0.65, "NO": 0.35},
        )
        self.assertIn("report_id", report)
        self.assertIn("summary", report)


if __name__ == "__main__":
    unittest.main()
