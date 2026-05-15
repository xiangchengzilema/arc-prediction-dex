#!/usr/bin/env python3
"""Tests for AMM (Automated Market Maker) Engine.

Tests cover:
- Pool initialization and LP token minting
- Buy/sell outcome shares
- Price calculation and bonding curve
- Slippage calculation
- Fee collection
- Liquidity add/remove
- Edge cases and error handling

Run: pytest tests/test_amm.py -v
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from amm import AMMPool


class TestAMMPoolInit(unittest.TestCase):
    """Test AMM pool initialization."""

    def test_initialize_pool(self):
        """Pool should initialize with equal YES/NO shares."""
        pool = AMMPool("test_init", db_path=":memory:", fee_bps=100)
        result = pool.initialize(10000)
        self.assertEqual(result["market_id"], "test_init")
        self.assertEqual(result["initial_usdc"], 10000)
        self.assertEqual(result["lp_tokens_minted"], 10000)

    def test_initial_price_is_50_50(self):
        """Fresh pool should have 50/50 prices."""
        pool = AMMPool("test_price", db_path=":memory:")
        pool.initialize(10000)
        self.assertAlmostEqual(pool.get_price("YES"), 0.5, places=4)
        self.assertAlmostEqual(pool.get_price("NO"), 0.5, places=4)

    def test_cannot_initialize_twice(self):
        """Should raise error if pool already exists."""
        pool = AMMPool("test_double", db_path=":memory:")
        pool.initialize(10000)
        with self.assertRaises(ValueError):
            pool.initialize(5000)

    def test_custom_fee_bps(self):
        """Pool should accept custom fee."""
        pool = AMMPool("test_fee", db_path=":memory:", fee_bps=200)
        pool.initialize(10000)
        info = pool.get_pool_info()
        self.assertEqual(info["fee_bps"], 200)


class TestAMMBuySell(unittest.TestCase):
    """Test AMM buy and sell operations."""

    def setUp(self):
        self.pool = AMMPool("test_trade", db_path=":memory:", fee_bps=100)
        self.pool.initialize(10000)

    def test_buy_yes_increases_yes_price(self):
        """Buying YES should increase YES price."""
        price_before = self.pool.get_price("YES")
        self.pool.buy_outcome("YES", 500)
        price_after = self.pool.get_price("YES")
        self.assertGreater(price_after, price_before)

    def test_buy_no_increases_no_price(self):
        """Buying NO should increase NO price."""
        price_before = self.pool.get_price("NO")
        self.pool.buy_outcome("NO", 500)
        price_after = self.pool.get_price("NO")
        self.assertGreater(price_after, price_before)

    def test_buy_shares_received(self):
        """Buying should return shares."""
        result = self.pool.buy_outcome("YES", 1000)
        self.assertGreater(result["shares_received"], 0)
        self.assertGreater(result["avg_price"], 0)

    def test_sell_shares_for_usdc(self):
        """Selling shares should return USDC."""
        # First buy some shares
        buy = self.pool.buy_outcome("YES", 1000)
        shares = buy["shares_received"]
        # Then sell them back
        sell = self.pool.sell_outcome("YES", shares * 0.5)
        self.assertGreater(sell["usdc_received"], 0)

    def test_fees_charged_on_buy(self):
        """Fee should be deducted from buy order."""
        result = self.pool.buy_outcome("YES", 1000)
        self.assertGreater(result["fee_charged"], 0)
        expected_fee = 1000 * 0.01  # 1% fee
        self.assertAlmostEqual(result["fee_charged"], expected_fee, places=2)

    def test_fees_charged_on_sell(self):
        """Fee should be deducted from sell proceeds."""
        buy = self.pool.buy_outcome("YES", 1000)
        sell = self.pool.sell_outcome("YES", buy["shares_received"] * 0.5)
        self.assertGreater(sell["fee_charged"], 0)

    def test_invalid_outcome_raises(self):
        """Invalid outcome should raise ValueError."""
        with self.assertRaises(ValueError):
            self.pool.buy_outcome("MAYBE", 100)
        with self.assertRaises(ValueError):
            self.pool.sell_outcome("MAYBE", 100)

    def test_zero_amount_raises(self):
        """Zero or negative amount should raise ValueError."""
        with self.assertRaises(ValueError):
            self.pool.buy_outcome("YES", 0)
        with self.assertRaises(ValueError):
            self.pool.buy_outcome("YES", -100)

    def test_price_impact_reported(self):
        """Buy should report price impact."""
        result = self.pool.buy_outcome("YES", 2000)
        self.assertIn("price_impact", result)

    def test_slippage_protection(self):
        """Trades exceeding slippage tolerance should be rejected."""
        with self.assertRaises(ValueError):
            self.pool.buy_outcome("YES", 50000, max_slippage=0.001)  # 0.1% max

    def test_multiple_trades(self):
        """Multiple sequential trades should work correctly."""
        for i in range(5):
            result = self.pool.buy_outcome("YES", 100)
            self.assertGreater(result["shares_received"], 0)
        # Price should have moved significantly
        self.assertGreater(self.pool.get_price("YES"), 0.54)


class TestAMMLiquidity(unittest.TestCase):
    """Test liquidity management."""

    def setUp(self):
        self.pool = AMMPool("test_liq", db_path=":memory:", fee_bps=100)
        self.pool.initialize(10000, creator_id="alice")

    def test_add_liquidity(self):
        """Adding liquidity should mint LP tokens."""
        result = self.pool.add_liquidity(5000, "bob")
        self.assertGreater(result["lp_tokens_minted"], 0)
        self.assertGreater(result["pool_share_pct"], 0)

    def test_remove_liquidity(self):
        """Removing liquidity should return USDC."""
        add_result = self.pool.add_liquidity(5000, "bob")
        lp_tokens = add_result["lp_tokens_minted"]
        remove_result = self.pool.remove_liquidity(lp_tokens, "bob")
        self.assertGreater(remove_result["usdc_returned"], 0)

    def test_pool_info(self):
        """Pool info should return comprehensive data."""
        info = self.pool.get_pool_info()
        self.assertEqual(info["market_id"], "test_liq")
        self.assertEqual(info["total_liquidity"], 10000)
        self.assertEqual(info["fee_bps"], 100)

    def test_recent_trades(self):
        """Should track recent trades."""
        self.pool.buy_outcome("YES", 500)
        self.pool.buy_outcome("NO", 300)
        trades = self.pool.get_recent_trades()
        self.assertEqual(len(trades), 2)

    def test_slippage_preview(self):
        """Should preview slippage without trading."""
        slip = self.pool.calculate_slippage("YES", 1000)
        self.assertIn("slippage_pct", slip)
        self.assertGreater(slip["slippage_pct"], 0)


if __name__ == "__main__":
    unittest.main()
