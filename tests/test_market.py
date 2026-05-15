#!/usr/bin/env python3
"""Tests for Market Engine and Oracle.

Tests cover:
- Market lifecycle (create → open → close → resolve → settle)
- Market search and filtering
- Oracle proposal and dispute mechanism
- Resolution voting and finalization

Run: pytest tests/test_market.py -v
"""
import os
import sys
import unittest
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from market_engine import MarketEngine
from oracle import ResolutionOracle


class TestMarketLifecycle(unittest.TestCase):
    """Test market creation and lifecycle management."""

    def setUp(self):
        self.engine = MarketEngine(db_path=":memory:")

    def test_create_market(self):
        """Should create a new market."""
        mkt = self.engine.create_market(
            question="Will BTC reach $150k?",
            outcomes=["YES", "NO"],
            deadline="2027-01-01T00:00:00Z",
            creator_id="alice",
        )
        self.assertIn("market_id", mkt)
        self.assertEqual(mkt["status"], "DRAFT")
        self.assertEqual(mkt["outcomes"], ["YES", "NO"])

    def test_open_market(self):
        """Should open a DRAFT market."""
        mkt = self._create_market()
        result = self.engine.open_market(mkt["market_id"], initial_liquidity=5000)
        self.assertEqual(result["status"], "OPEN")

    def test_cannot_open_non_draft(self):
        """Should not open a market that's not DRAFT."""
        mkt = self._create_market()
        self.engine.open_market(mkt["market_id"])
        with self.assertRaises(ValueError):
            self.engine.open_market(mkt["market_id"])

    def test_close_market(self):
        """Should close an OPEN market."""
        mkt = self._create_and_open()
        result = self.engine.close_market(mkt["market_id"])
        self.assertEqual(result["status"], "CLOSED")

    def test_resolve_market(self):
        """Should resolve a CLOSED market."""
        mkt = self._create_and_open()
        self.engine.close_market(mkt["market_id"])
        result = self.engine.resolve_market(mkt["market_id"], "YES", "oracle")
        self.assertEqual(result["status"], "RESOLVED")
        self.assertEqual(result["winning_outcome"], "YES")

    def test_invalid_resolution_outcome(self):
        """Invalid outcome should raise error."""
        mkt = self._create_and_open()
        self.engine.close_market(mkt["market_id"])
        with self.assertRaises(ValueError):
            self.engine.resolve_market(mkt["market_id"], "MAYBE")

    def test_full_lifecycle(self):
        """Complete market lifecycle test."""
        mkt = self._create_market()
        self.assertEqual(mkt["status"], "DRAFT")

        self.engine.open_market(mkt["market_id"])
        market = self.engine.get_market(mkt["market_id"])
        self.assertEqual(market["status"], "OPEN")

        self.engine.close_market(mkt["market_id"])
        market = self.engine.get_market(mkt["market_id"])
        self.assertEqual(market["status"], "CLOSED")

        self.engine.resolve_market(mkt["market_id"], "YES")
        market = self.engine.get_market(mkt["market_id"])
        self.assertEqual(market["status"], "RESOLVED")

    def test_list_markets(self):
        """Should list markets with pagination."""
        for i in range(5):
            self.engine.create_market(
                question=f"Test market {i} question?",
                outcomes=["YES", "NO"],
                deadline="2027-01-01T00:00:00Z",
                creator_id="alice",
                category="crypto" if i % 2 == 0 else "sports",
            )
        result = self.engine.list_markets(status="DRAFT")
        self.assertEqual(result["total"], 5)
        self.assertEqual(len(result["markets"]), 5)

    def test_filter_by_category(self):
        """Should filter markets by category."""
        for i in range(3):
            self.engine.create_market(
                question=f"Crypto market {i} question?",
                outcomes=["YES", "NO"],
                deadline="2027-01-01T00:00:00Z",
                creator_id="alice",
                category="crypto",
            )
        self.engine.create_market(
            question="Sports market question here?",
            outcomes=["YES", "NO"],
            deadline="2027-01-01T00:00:00Z",
            creator_id="alice",
            category="sports",
        )
        crypto = self.engine.list_markets(category="crypto")
        self.assertEqual(crypto["total"], 3)

    def test_search_markets(self):
        """Should search by question text."""
        self.engine.create_market(
            question="Will Bitcoin hit $200k by end of 2026?",
            outcomes=["YES", "NO"],
            deadline="2027-01-01T00:00:00Z",
            creator_id="alice",
        )
        results = self.engine.search_markets("Bitcoin")
        self.assertEqual(len(results), 1)
        results = self.engine.search_markets("Ethereum")
        self.assertEqual(len(results), 0)

    def test_market_stats(self):
        """Should return market statistics."""
        mkt = self._create_and_open()
        stats = self.engine.get_market_stats(mkt["market_id"])
        self.assertIn("outcomes", stats)
        self.assertIn("recent_events", stats)

    def test_short_question_raises(self):
        """Short question should raise error."""
        with self.assertRaises(ValueError):
            self.engine.create_market(
                question="Too short",
                outcomes=["YES", "NO"],
                deadline="2027-01-01T00:00:00Z",
                creator_id="alice",
            )

    def test_multi_outcome_market(self):
        """Should support markets with more than 2 outcomes."""
        mkt = self.engine.create_market(
            question="Who will win the 2028 election?",
            outcomes=["Candidate A", "Candidate B", "Candidate C", "Other"],
            deadline="2028-11-01T00:00:00Z",
            creator_id="alice",
        )
        self.assertEqual(len(mkt["outcomes"]), 4)

    # Helpers
    def _create_market(self):
        return self.engine.create_market(
            question="Will BTC reach $150k by end of 2026?",
            outcomes=["YES", "NO"],
            deadline="2027-01-01T00:00:00Z",
            creator_id="alice",
        )

    def _create_and_open(self):
        mkt = self._create_market()
        self.engine.open_market(mkt["market_id"], initial_liquidity=5000)
        return mkt


class TestOracleResolution(unittest.TestCase):
    """Test oracle resolution system."""

    def setUp(self):
        self.oracle = ResolutionOracle(db_path=":memory:")

    def test_register_source(self):
        """Should register a data source."""
        src = self.oracle.register_source("CoinGecko", "price_feed", 0.9)
        self.assertIn("source_id", src)
        self.assertEqual(src["confidence"], 0.9)

    def test_propose_resolution(self):
        """Should create a resolution proposal."""
        prop = self.oracle.propose_resolution(
            "btc_100k", "YES", "alice",
            evidence="BTC reached $151k on CoinGecko",
            bond_amount=100
        )
        self.assertEqual(prop["status"], "PENDING")
        self.assertEqual(prop["proposed_outcome"], "YES")

    def test_dispute_proposal(self):
        """Should dispute a resolution."""
        prop = self.oracle.propose_resolution("eth_5k", "YES", "alice", bond_amount=100)
        disp = self.oracle.dispute_proposal(
            prop["proposal_id"], "bob", "NO",
            evidence="ETH only reached $4800"
        )
        self.assertEqual(disp["status"], "DISPUTED")

    def test_vote_on_proposal(self):
        """Should record votes."""
        prop = self.oracle.propose_resolution("sol_500", "YES", "alice")
        vote = self.oracle.vote_on_proposal(prop["proposal_id"], "charlie", "SUPPORT", 2.0)
        self.assertEqual(vote["vote"], "SUPPORT")
        self.assertEqual(vote["weight"], 2.0)

    def test_invalid_vote_raises(self):
        """Invalid vote value should raise error."""
        prop = self.oracle.propose_resolution("test_mkt", "YES", "alice")
        with self.assertRaises(ValueError):
            self.oracle.vote_on_proposal(prop["proposal_id"], "bob", "MAYBE")

    def test_list_sources(self):
        """Should list registered sources."""
        self.oracle.register_source("Source A", "price_feed", 0.9)
        self.oracle.register_source("Source B", "api", 0.7)
        sources = self.oracle.get_sources()
        self.assertEqual(len(sources), 2)

    def test_get_pending_resolutions(self):
        """Should list pending proposals."""
        self.oracle.propose_resolution("mkt1", "YES", "alice")
        self.oracle.propose_resolution("mkt2", "NO", "bob")
        pending = self.oracle.get_pending_resolutions()
        self.assertEqual(len(pending), 2)

    def test_resolution_history(self):
        """Should track resolution history."""
        self.oracle.propose_resolution("hist_mkt", "YES", "alice")
        history = self.oracle.get_resolution_history("hist_mkt")
        self.assertIsInstance(history, list)


if __name__ == "__main__":
    unittest.main()
