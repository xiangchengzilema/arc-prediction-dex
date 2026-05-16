#!/usr/bin/env python3
"""Tests for new API endpoints added in the hackathon push.

Covers:
- POST /api/markets full flow (create + auto-seed AMM + auto-open)
- POST /api/portfolio/close (sell back into AMM, lock realized P&L)
- GET  /api/orders/<user> (aggregate resting orders across markets)
- GET  /api/portfolio/<user>/positions (enriched with live price)

Run: pytest tests/test_api_endpoints.py -v
"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class APITestBase(unittest.TestCase):
    """Spin a Flask test client against a fresh per-test SQLite file."""

    def setUp(self):
        # Each test gets its own DB file
        fd, self.db_path = tempfile.mkstemp(suffix=".db", prefix="arc_test_")
        os.close(fd)
        os.environ["PREDICT_DEX_DB"] = self.db_path
        os.environ["PREDICT_DEX_SKIP_SEED"] = "1"  # don't auto-seed demo data
        os.environ["PREDICT_DEX_SKIP_AGENT"] = "1"  # don't start Pythia thread

        # Re-import app fresh so module-level singletons bind to our DB
        for mod in [
            "app", "amm", "orderbook", "market_engine", "liquidity_pool",
            "oracle", "portfolio", "analytics", "agent",
        ]:
            sys.modules.pop(mod, None)

        import app as app_module  # noqa: E402
        self.app_module = app_module
        app_module.app.config["TESTING"] = True
        self.client = app_module.app.test_client()

    def tearDown(self):
        try:
            os.unlink(self.db_path)
        except OSError:
            pass
        os.environ.pop("PREDICT_DEX_DB", None)
        os.environ.pop("PREDICT_DEX_SKIP_SEED", None)
        os.environ.pop("PREDICT_DEX_SKIP_AGENT", None)

    def _create_market(self, question="Test market open?", liquidity=10000,
                       creator="alice"):
        r = self.client.post("/api/markets", json={
            "question": question,
            "outcomes": ["YES", "NO"],
            "deadline": "2027-01-01T00:00:00Z",
            "category": "crypto",
            "initial_liquidity": liquidity,
            "fee_bps": 100,
            "creator_id": creator,
        })
        self.assertEqual(r.status_code, 201, r.get_data(as_text=True))
        return r.get_json()


class TestMarketCreateEndpoint(APITestBase):

    def test_create_seeds_amm_and_opens(self):
        m = self._create_market()
        self.assertIn("market_id", m)
        # Confirm the market is OPEN by re-fetching it
        r = self.client.get(f"/api/markets/{m['market_id']}")
        self.assertEqual(r.status_code, 200)
        full = r.get_json()
        self.assertEqual(full["status"], "OPEN")
        self.assertEqual(full["total_liquidity"], 10000.0)

    def test_create_validates_required_fields(self):
        r = self.client.post("/api/markets", json={"question": "missing outcomes"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("error", r.get_json())

    def test_created_market_appears_in_list(self):
        m = self._create_market(question="Will it rain tomorrow?")
        r = self.client.get("/api/markets?status=OPEN&limit=20")
        self.assertEqual(r.status_code, 200)
        ids = [x["market_id"] for x in r.get_json()["markets"]]
        self.assertIn(m["market_id"], ids)


class TestClosePositionEndpoint(APITestBase):

    def test_full_close_zeros_remaining(self):
        m = self._create_market()
        mid = m["market_id"]

        # Buy YES shares via AMM (returns 200, opens position internally)
        r = self.client.post("/api/trade/amm/buy", json={
            "market_id": mid,
            "outcome": "YES",
            "amount_usdc": 50,
            "user_id": "alice",
        })
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        # The AMM buy endpoint should also create a position
        r2 = self.client.get("/api/portfolio/alice/positions")
        self.assertEqual(r2.status_code, 200)
        positions = r2.get_json()
        if isinstance(positions, dict):
            positions = positions.get("positions", [])
        self.assertTrue(len(positions) >= 1)
        pos = positions[0]
        self.assertEqual(pos["market_id"], mid)
        self.assertGreater(pos["shares"], 0)

        # Close it fully
        r3 = self.client.post("/api/portfolio/close", json={
            "position_id": pos["position_id"],
            "shares": pos["shares"],
        })
        self.assertEqual(r3.status_code, 200, r3.get_data(as_text=True))
        d = r3.get_json()
        self.assertEqual(d["status"], "CLOSED")
        self.assertLess(d["remaining_shares"], 0.001)
        self.assertIn("realized_pnl", d)
        self.assertIn("usdc_received", d)

    def test_partial_close_keeps_remainder(self):
        m = self._create_market()
        mid = m["market_id"]

        self.client.post("/api/trade/amm/buy", json={
            "market_id": mid, "outcome": "YES",
            "amount_usdc": 50, "user_id": "bob",
        })
        positions = self.client.get(
            "/api/portfolio/bob/positions").get_json()
        if isinstance(positions, dict):
            positions = positions.get("positions", [])
        pos = positions[0]
        half = pos["shares"] / 2

        r = self.client.post("/api/portfolio/close", json={
            "position_id": pos["position_id"],
            "shares": half,
        })
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        d = r.get_json()
        self.assertEqual(d["status"], "OPEN")
        self.assertGreater(d["remaining_shares"], 0)
        self.assertAlmostEqual(d["remaining_shares"], pos["shares"] - half,
                                places=2)


class TestOrdersAggregationEndpoint(APITestBase):

    def test_empty_user_returns_empty_list(self):
        r = self.client.get("/api/orders/nobody")
        self.assertEqual(r.status_code, 200)
        d = r.get_json()
        self.assertEqual(d["count"], 0)
        self.assertEqual(d["orders"], [])

    def test_resting_limit_order_appears_then_disappears_after_cancel(self):
        m = self._create_market()
        mid = m["market_id"]

        # Place a deep buy that won't get filled
        r = self.client.post("/api/trade/orderbook/limit", json={
            "market_id": mid,
            "user_id": "alice",
            "side": "BUY",
            "outcome": "YES",
            "price": 0.05,
            "quantity": 10,
        })
        self.assertEqual(r.status_code, 201, r.get_data(as_text=True))
        order_id = r.get_json()["order_id"]

        # Should appear in /api/orders/alice
        r = self.client.get("/api/orders/alice")
        self.assertEqual(r.status_code, 200)
        d = r.get_json()
        self.assertEqual(d["count"], 1)
        row = d["orders"][0]
        self.assertEqual(row["order_id"], order_id)
        self.assertEqual(row["market_id"], mid)
        self.assertEqual(row["side"], "BUY")
        self.assertIn("market_question", row)

        # Cancel it
        r = self.client.post("/api/trade/orderbook/cancel", json={
            "market_id": mid,
            "order_id": order_id,
            "user_id": "alice",
        })
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))

        # Now it should be gone
        r = self.client.get("/api/orders/alice")
        self.assertEqual(r.get_json()["count"], 0)

    def test_orders_aggregated_across_markets(self):
        m1 = self._create_market(question="Market one open?")
        m2 = self._create_market(question="Market two open?")

        for mid in (m1["market_id"], m2["market_id"]):
            r = self.client.post("/api/trade/orderbook/limit", json={
                "market_id": mid, "user_id": "carol",
                "side": "BUY", "outcome": "YES",
                "price": 0.05, "quantity": 5,
            })
            self.assertEqual(r.status_code, 201)

        r = self.client.get("/api/orders/carol")
        self.assertEqual(r.status_code, 200)
        d = r.get_json()
        self.assertEqual(d["count"], 2)
        market_ids = {row["market_id"] for row in d["orders"]}
        self.assertEqual(market_ids,
                          {m1["market_id"], m2["market_id"]})


class TestEnrichedPositionsEndpoint(APITestBase):

    def test_positions_include_live_price_and_unrealized_pnl(self):
        m = self._create_market()
        mid = m["market_id"]

        self.client.post("/api/trade/amm/buy", json={
            "market_id": mid, "outcome": "YES",
            "amount_usdc": 30, "user_id": "dave",
        })
        r = self.client.get("/api/portfolio/dave/positions")
        self.assertEqual(r.status_code, 200)
        positions = r.get_json()
        if isinstance(positions, dict):
            positions = positions.get("positions", [])
        self.assertEqual(len(positions), 1)
        pos = positions[0]

        # Enriched fields
        for key in ("current_price", "unrealized_pnl", "market_value",
                     "market_question"):
            self.assertIn(key, pos, f"missing enriched field: {key}")

        self.assertGreater(pos["current_price"], 0)
        self.assertGreater(pos["market_value"], 0)


if __name__ == "__main__":
    unittest.main()
