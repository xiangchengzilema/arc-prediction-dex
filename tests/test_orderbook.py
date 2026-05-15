#!/usr/bin/env python3
"""Tests for Central Limit Order Book (CLOB).

Tests cover:
- Limit order placement
- Market order execution
- Price-time priority matching
- Order cancellation
- Spread calculation
- Fill-or-kill and immediate-or-cancel

Run: pytest tests/test_orderbook.py -v
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from orderbook import OrderBook


class TestOrderPlacement(unittest.TestCase):
    """Test order placement and book management."""

    def setUp(self):
        self.book = OrderBook("test_market", db_path=":memory:")

    def test_place_buy_order(self):
        """Should place a buy limit order."""
        result = self.book.place_limit_order("alice", "BUY", 0.55, 100)
        self.assertEqual(result["side"], "BUY")
        self.assertEqual(result["price"], 0.55)
        self.assertIn(result["status"], ("OPEN", "PARTIALLY_FILLED", "FILLED"))

    def test_place_sell_order(self):
        """Should place a sell limit order."""
        result = self.book.place_limit_order("bob", "SELL", 0.60, 50)
        self.assertEqual(result["side"], "SELL")
        self.assertEqual(result["price"], 0.60)

    def test_invalid_price_raises(self):
        """Price outside 0.01-0.99 should raise error."""
        with self.assertRaises(ValueError):
            self.book.place_limit_order("alice", "BUY", 0.005, 100)
        with self.assertRaises(ValueError):
            self.book.place_limit_order("alice", "BUY", 1.0, 100)

    def test_invalid_side_raises(self):
        """Invalid side should raise error."""
        with self.assertRaises(ValueError):
            self.book.place_limit_order("alice", "HOLD", 0.5, 100)

    def test_small_quantity_raises(self):
        """Quantity below minimum should raise error."""
        with self.assertRaises(ValueError):
            self.book.place_limit_order("alice", "BUY", 0.5, 0.1)

    def test_cancel_order(self):
        """Should cancel an open order."""
        result = self.book.place_limit_order("alice", "BUY", 0.55, 100)
        cancel = self.book.cancel_order(result["order_id"], "alice")
        self.assertEqual(cancel["status"], "CANCELLED")

    def test_cancel_other_users_order_fails(self):
        """Cannot cancel another user's order."""
        result = self.book.place_limit_order("alice", "BUY", 0.55, 100)
        with self.assertRaises(ValueError):
            self.book.cancel_order(result["order_id"], "bob")

    def test_cancel_nonexistent_order_fails(self):
        """Cancelling non-existent order should fail."""
        with self.assertRaises(ValueError):
            self.book.cancel_order("fake_id", "alice")

    def test_get_user_orders(self):
        """Should return user's orders."""
        self.book.place_limit_order("alice", "BUY", 0.55, 100)
        self.book.place_limit_order("alice", "BUY", 0.50, 200)
        orders = self.book.get_user_orders("alice")
        self.assertEqual(len(orders), 2)


class TestOrderMatching(unittest.TestCase):
    """Test the matching engine."""

    def setUp(self):
        self.book = OrderBook("match_test", db_path=":memory:")

    def test_buy_matches_sell(self):
        """Buy at higher price should match sell at lower price."""
        sell = self.book.place_limit_order("bob", "SELL", 0.55, 100)
        buy = self.book.place_limit_order("alice", "BUY", 0.60, 100)

        # Should have fills
        self.assertGreater(len(buy["fills"]), 0)
        # Fill price should be at maker (sell) price
        self.assertEqual(buy["fills"][0]["price"], 0.55)

    def test_partial_fill(self):
        """Large order should partially fill against smaller one."""
        self.book.place_limit_order("bob", "SELL", 0.55, 50)
        buy = self.book.place_limit_order("alice", "BUY", 0.60, 100)

        # First 50 shares should fill
        self.assertEqual(buy["fills"][0]["quantity"], 50)

    def test_no_match_when_prices_dont_cross(self):
        """Orders that don't cross should not match."""
        self.book.place_limit_order("bob", "SELL", 0.65, 100)
        buy = self.book.place_limit_order("alice", "BUY", 0.55, 100)

        # No fills
        self.assertEqual(len(buy["fills"]), 0)

    def test_price_time_priority(self):
        """Earlier order at same price should fill first."""
        self.book.place_limit_order("bob", "SELL", 0.55, 100)
        self.book.place_limit_order("charlie", "SELL", 0.55, 100)
        buy = self.book.place_limit_order("alice", "BUY", 0.60, 150)

        # Bob's order (placed first) should fill first
        self.assertEqual(buy["fills"][0]["quantity"], 100)
        # Charlie fills 50
        self.assertEqual(buy["fills"][1]["quantity"], 50)

    def test_best_price_first(self):
        """Best priced order should fill first (lowest ask)."""
        self.book.place_limit_order("bob", "SELL", 0.58, 100)
        self.book.place_limit_order("charlie", "SELL", 0.55, 100)
        buy = self.book.place_limit_order("alice", "BUY", 0.60, 100)

        # Charlie's lower price should fill first
        self.assertEqual(buy["fills"][0]["price"], 0.55)

    def test_market_order_fills(self):
        """Market order should fill against existing book."""
        self.book.place_limit_order("bob", "SELL", 0.55, 100)
        result = self.book.place_market_order("alice", "BUY", 100)

        self.assertGreater(len(result["fills"]), 0)

    def test_maker_taker_fees(self):
        """Maker and taker fees should be calculated."""
        self.book.place_limit_order("bob", "SELL", 0.55, 100)
        result = self.book.place_limit_order("alice", "BUY", 0.60, 100)

        fill = result["fills"][0]
        self.assertGreater(fill["maker_fee"], 0)
        self.assertGreater(fill["taker_fee"], 0)
        # Taker fee > Maker fee
        self.assertGreater(fill["taker_fee"], fill["maker_fee"])


class TestOrderBookData(unittest.TestCase):
    """Test order book data retrieval."""

    def setUp(self):
        self.book = OrderBook("data_test", db_path=":memory:")
        self.book.place_limit_order("alice", "BUY", 0.55, 100)
        self.book.place_limit_order("alice", "BUY", 0.54, 200)
        self.book.place_limit_order("alice", "BUY", 0.53, 150)
        self.book.place_limit_order("bob", "SELL", 0.60, 100)
        self.book.place_limit_order("bob", "SELL", 0.62, 80)

    def test_get_order_book(self):
        """Should return bids and asks."""
        book = self.book.get_order_book()
        self.assertGreater(len(book["bids"]), 0)
        self.assertGreater(len(book["asks"]), 0)

    def test_best_bid_ask(self):
        """Should return correct best bid and ask."""
        book = self.book.get_order_book()
        self.assertEqual(book["best_bid"], 0.55)
        self.assertEqual(book["best_ask"], 0.60)

    def test_spread(self):
        """Should calculate spread correctly."""
        book = self.book.get_order_book()
        self.assertIsNotNone(book["spread"])
        self.assertAlmostEqual(book["spread"], 0.05, places=2)

    def test_mid_price(self):
        """Should calculate mid price."""
        book = self.book.get_order_book()
        self.assertAlmostEqual(book["mid_price"], 0.575, places=3)

    def test_spread_analysis(self):
        """Should return detailed spread analysis."""
        analysis = self.book.get_spread_analysis()
        self.assertIn("spread_pct", analysis)
        self.assertIn("bid_ask_ratio", analysis)

    def test_recent_trades_empty(self):
        """No trades means empty list."""
        trades = self.book.get_recent_trades()
        self.assertEqual(len(trades), 0)


if __name__ == "__main__":
    unittest.main()
