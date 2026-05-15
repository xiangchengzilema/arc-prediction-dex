#!/usr/bin/env python3
"""High-Level Trading SDK for Arc Prediction DEX.

Provides a clean, intuitive interface for all trading operations.
Designed for zero external dependencies - uses only Python stdlib.

Key features:
- One-line trading: buy/sell in a single function call
- Auto-routing between AMM and Order Book
- Portfolio management in one object
- Market discovery and search
- WebSocket-like polling for updates

Usage:
    sdk = PredictionDexSDK("http://localhost:5003")
    market = sdk.create_market("Will BTC hit $200k?", ["YES", "NO"])
    result = sdk.buy(market["market_id"], "YES", 100)
    portfolio = sdk.get_portfolio()
"""
import json
import os
import urllib.request
import urllib.error
import urllib.parse
from typing import Any, Dict, List, Optional


class PredictionDexSDK:
    """Zero-dependency Python SDK for Arc Prediction DEX."""

    def __init__(self, base_url: str = None, api_key: str = None):
        """Initialize SDK.

        Args:
            base_url: API server URL (default: from env PREDICT_DEX_URL or localhost:5003).
            api_key: Optional API key for authenticated endpoints.
        """
        self.base_url = (base_url or
                         os.environ.get("PREDICT_DEX_URL",
                                        "http://localhost:5003")).rstrip("/")
        self.api_key = api_key or os.environ.get("PREDICT_DEX_API_KEY", "")
        self._timeout = 30

    # ─── Market Operations ────────────────────────────────────────

    def create_market(self, question: str, outcomes: List[str],
                      deadline: str = None, category: str = "general",
                      description: str = "", initial_liquidity: float = 0) -> Dict:
        """Create a new prediction market.

        Args:
            question: The prediction question.
            outcomes: Possible outcomes (e.g., ["YES", "NO"]).
            deadline: ISO timestamp when market closes.
            category: Market category.
            description: Detailed description.
            initial_liquidity: Seed liquidity in USDC.

        Returns:
            Market details including market_id.
        """
        data = {
            "question": question,
            "outcomes": outcomes,
            "category": category,
            "description": description,
            "initial_liquidity": initial_liquidity,
        }
        if deadline:
            data["deadline"] = deadline
        return self._post("/api/markets", data)

    def get_market(self, market_id: str) -> Dict:
        """Get market details."""
        return self._get(f"/api/markets/{market_id}")

    def list_markets(self, status: str = "OPEN", category: str = None,
                     page: int = 1) -> Dict:
        """List markets with optional filters."""
        params = {"status": status, "page": page}
        if category:
            params["category"] = category
        return self._get("/api/markets", params)

    def search_markets(self, query: str) -> List[Dict]:
        """Search markets by keyword."""
        return self._get("/api/markets/search", {"q": query})

    def get_trending(self, limit: int = 10) -> List[Dict]:
        """Get trending markets."""
        return self._get("/api/markets/trending", {"limit": limit})

    # ─── Trading Operations ───────────────────────────────────────

    def buy(self, market_id: str, outcome: str, amount_usdc: float,
            order_type: str = "amm", price_limit: float = None,
            max_slippage: float = 0.05) -> Dict:
        """Buy outcome shares.

        Auto-routes between AMM and Order Book based on order_type.

        Args:
            market_id: Market to trade on.
            outcome: "YES" or "NO".
            amount_usdc: USDC to spend.
            order_type: "amm" (instant) or "limit" (order book).
            price_limit: For limit orders, max price to pay.
            max_slippage: Max acceptable slippage for AMM orders.

        Returns:
            Trade result with shares received and price details.
        """
        if order_type == "amm":
            return self._post("/api/trade/amm/buy", {
                "market_id": market_id,
                "outcome": outcome,
                "amount_usdc": amount_usdc,
                "max_slippage": max_slippage,
            })
        else:
            return self._post("/api/trade/orderbook/limit", {
                "market_id": market_id,
                "side": "BUY",
                "outcome": outcome,
                "price": price_limit,
                "quantity": amount_usdc,
            })

    def sell(self, market_id: str, outcome: str, shares: float,
             order_type: str = "amm", price_limit: float = None) -> Dict:
        """Sell outcome shares.

        Args:
            market_id: Market to trade on.
            outcome: "YES" or "NO".
            shares: Number of shares to sell.
            order_type: "amm" or "limit".
            price_limit: For limit orders, min price to accept.

        Returns:
            Trade result with USDC received.
        """
        if order_type == "amm":
            return self._post("/api/trade/amm/sell", {
                "market_id": market_id,
                "outcome": outcome,
                "shares": shares,
            })
        else:
            return self._post("/api/trade/orderbook/limit", {
                "market_id": market_id,
                "side": "SELL",
                "outcome": outcome,
                "price": price_limit,
                "quantity": shares,
            })

    def quick_trade(self, market_id: str, outcome: str, usdc: float) -> Dict:
        """One-line trade: buy via AMM with default settings.

        This is the simplest way to trade - just specify what you want.
        """
        return self.buy(market_id, outcome, usdc, order_type="amm")

    def cancel_order(self, order_id: str, user_id: str) -> Dict:
        """Cancel a limit order."""
        return self._post(f"/api/trade/orderbook/cancel", {
            "order_id": order_id,
            "user_id": user_id,
        })

    # ─── Liquidity Operations ─────────────────────────────────────

    def add_liquidity(self, market_id: str, amount_usdc: float,
                      user_id: str = None) -> Dict:
        """Add liquidity to a market's AMM pool."""
        return self._post("/api/liquidity/add", {
            "market_id": market_id,
            "amount_usdc": amount_usdc,
            "user_id": user_id,
        })

    def remove_liquidity(self, market_id: str, lp_tokens: float,
                         user_id: str = None) -> Dict:
        """Remove liquidity from a pool."""
        return self._post("/api/liquidity/remove", {
            "market_id": market_id,
            "lp_tokens": lp_tokens,
            "user_id": user_id,
        })

    def get_pool_info(self, market_id: str) -> Dict:
        """Get pool information for a market."""
        return self._get(f"/api/liquidity/pool/{market_id}")

    # ─── Portfolio & Analytics ─────────────────────────────────────

    def get_portfolio(self, user_id: str = None) -> Dict:
        """Get user portfolio with positions and P&L."""
        uid = user_id or "default"
        return self._get(f"/api/portfolio/{uid}")

    def get_positions(self, user_id: str = None) -> List[Dict]:
        """Get open positions."""
        uid = user_id or "default"
        return self._get(f"/api/portfolio/{uid}/positions")

    def get_trade_history(self, user_id: str = None, limit: int = 50) -> List[Dict]:
        """Get trade history."""
        uid = user_id or "default"
        return self._get(f"/api/portfolio/{uid}/trades", {"limit": limit})

    def get_pnl(self, user_id: str = None) -> Dict:
        """Get P&L breakdown."""
        uid = user_id or "default"
        return self._get(f"/api/portfolio/{uid}/pnl")

    def get_leaderboard(self, sort_by: str = "total_pnl", limit: int = 20) -> List[Dict]:
        """Get trader leaderboard."""
        return self._get("/api/portfolio/leaderboard", {
            "sort_by": sort_by, "limit": limit
        })

    # ─── Order Book ───────────────────────────────────────────────

    def get_order_book(self, market_id: str, depth: int = 20) -> Dict:
        """Get Level 2 order book data."""
        return self._get(f"/api/orderbook/{market_id}", {"depth": depth})

    def get_spread(self, market_id: str) -> Dict:
        """Get current bid/ask spread."""
        return self._get(f"/api/orderbook/{market_id}/spread")

    # ─── Oracle ───────────────────────────────────────────────────

    def propose_resolution(self, market_id: str, outcome: str,
                           evidence: str = "", bond: float = 0) -> Dict:
        """Propose a market resolution."""
        return self._post("/api/oracle/propose", {
            "market_id": market_id,
            "outcome": outcome,
            "evidence": evidence,
            "bond_amount": bond,
        })

    def get_pending_resolutions(self) -> List[Dict]:
        """Get pending oracle resolutions."""
        return self._get("/api/oracle/pending")

    # ─── Analytics ────────────────────────────────────────────────

    def get_market_stats(self, market_id: str) -> Dict:
        """Get market statistics."""
        return self._get(f"/api/analytics/market/{market_id}")

    def get_system_stats(self) -> Dict:
        """Get system-wide statistics."""
        return self._get("/api/analytics/system")

    # ─── Internal HTTP Methods ────────────────────────────────────

    def _get(self, path: str, params: Dict = None) -> Any:
        """Make a GET request."""
        url = f"{self.base_url}{path}"
        if params:
            url += "?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url)
        if self.api_key:
            req.add_header("Authorization", f"Bearer {self.api_key}")
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            body = e.read().decode() if e.fp else ""
            raise Exception(f"HTTP {e.code}: {body}")
        except urllib.error.URLError as e:
            raise Exception(f"Connection error: {e.reason}")

    def _post(self, path: str, data: Dict = None) -> Any:
        """Make a POST request."""
        url = f"{self.base_url}{path}"
        body = json.dumps(data or {}).encode()
        req = urllib.request.Request(url, data=body, method="POST")
        req.add_header("Content-Type", "application/json")
        if self.api_key:
            req.add_header("Authorization", f"Bearer {self.api_key}")
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            body = e.read().decode() if e.fp else ""
            raise Exception(f"HTTP {e.code}: {body}")
        except urllib.error.URLError as e:
            raise Exception(f"Connection error: {e.reason}")

    def health_check(self) -> Dict:
        """Check API server health."""
        try:
            return self._get("/api/health")
        except Exception as e:
            return {"status": "unhealthy", "error": str(e)}


if __name__ == "__main__":
    sdk = PredictionDexSDK("http://localhost:5003")

    print("Checking API health...")
    health = sdk.health_check()
    print(f"  Status: {health.get('status', 'unknown')}")

    print("\nSearching for BTC markets...")
    try:
        markets = sdk.search_markets("BTC")
        print(f"  Found {len(markets)} markets")
    except Exception as e:
        print(f"  API not running: {e}")
        print("  Start the server with: python app.py")
