#!/usr/bin/env python3
"""Automated Market Maker (AMM) Engine for Prediction Markets.

Implements a Constant Product AMM (x*y=k) adapted for binary prediction markets.
Supports YES/NO outcome token trading with configurable fees and slippage protection.

Key features:
- Constant product bonding curve (x * y = k)
- Configurable trading fees (basis points)
- Price impact calculation and slippage protection
- Liquidity provision and LP token accounting
- Price history tracking for charting
- Multi-market AMM pool management

Usage:
    amm = AMMPool(market_id="btc_100k", fee_bps=100)
    amm.initialize(10000.0)  # Seed with 10000 USDC
    result = amm.buy_outcome("YES", 500.0)
    print(f"Got {result['shares']} YES shares at {result['avg_price']}")
"""
import json
import math
import os
import sqlite3
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple


class AMMPool:
    """Constant Product AMM for binary prediction markets.

    In a binary market, YES + NO always equals 1 USDC.
    The pool holds both YES and NO tokens backed by USDC.

    Bonding curve: shares_yes * shares_no = k (constant)
    Price of YES = shares_no / (shares_yes + shares_no)
    Price of NO  = shares_yes / (shares_yes + shares_no)
    """

    DEFAULT_FEE_BPS = 100  # 1% fee
    SLIPPAGE_TOLERANCE = 0.35  # 35% max slippage

    def __init__(self, market_id: str, db_path: str = "amm.db",
                 fee_bps: int = None):
        self.market_id = market_id
        self.db_path = db_path
        self.fee_bps = fee_bps or self.DEFAULT_FEE_BPS
        self._conn = None
        self._init_db()

    def _get_conn(self):
        """Get cached database connection."""
        if not hasattr(self, '_conn') or self._conn is None:
            self._conn = sqlite3.connect(self.db_path)
            self._conn.row_factory = None
        return self._conn

    def _init_db(self):
        """Initialize AMM database tables."""
        conn = self._get_conn()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS amm_pools (
                market_id TEXT PRIMARY KEY,
                shares_yes REAL NOT NULL DEFAULT 0,
                shares_no REAL NOT NULL DEFAULT 0,
                total_liquidity REAL NOT NULL DEFAULT 0,
                fee_bps INTEGER NOT NULL DEFAULT 100,
                k_constant REAL NOT NULL DEFAULT 0,
                lp_total_supply REAL NOT NULL DEFAULT 0,
                volume_24h REAL NOT NULL DEFAULT 0,
                total_volume REAL NOT NULL DEFAULT 0,
                trade_count INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS amm_trades (
                trade_id INTEGER PRIMARY KEY AUTOINCREMENT,
                market_id TEXT NOT NULL,
                side TEXT NOT NULL,
                outcome TEXT NOT NULL,
                input_amount REAL NOT NULL,
                output_amount REAL NOT NULL,
                avg_price REAL NOT NULL,
                price_impact REAL NOT NULL,
                fee_charged REAL NOT NULL,
                lp_fee REAL NOT NULL,
                user_id TEXT,
                timestamp TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS amm_price_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                market_id TEXT NOT NULL,
                price_yes REAL NOT NULL,
                price_no REAL NOT NULL,
                shares_yes REAL NOT NULL,
                shares_no REAL NOT NULL,
                volume REAL NOT NULL DEFAULT 0,
                timestamp TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS amm_lp_positions (
                lp_id INTEGER PRIMARY KEY AUTOINCREMENT,
                market_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                lp_tokens REAL NOT NULL,
                usdc_deposited REAL NOT NULL,
                fees_earned REAL NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            )
        """)

    def initialize(self, initial_usdc: float, creator_id: str = "system") -> Dict[str, Any]:
        """Initialize a new AMM pool with initial liquidity.

        Args:
            initial_usdc: Amount of USDC to seed the pool.
            creator_id: ID of the liquidity provider.

        Returns:
            Dict with pool state and LP token info.
        """
        conn = self._get_conn()
        existing = conn.execute(
            "SELECT market_id FROM amm_pools WHERE market_id = ?",
            (self.market_id,)
        ).fetchone()
        if existing:
            raise ValueError(f"Pool for {self.market_id} already initialized")

        now = datetime.now(timezone.utc).isoformat()
        # Split initial liquidity equally: half YES, half NO
        shares_yes = initial_usdc / 2.0
        shares_no = initial_usdc / 2.0
        k = shares_yes * shares_no

        conn.execute("""
            INSERT INTO amm_pools
            (market_id, shares_yes, shares_no, total_liquidity, fee_bps,
             k_constant, lp_total_supply, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (self.market_id, shares_yes, shares_no, initial_usdc,
              self.fee_bps, k, initial_usdc, now, now))

        # Give creator LP tokens = initial deposit
        conn.execute("""
            INSERT INTO amm_lp_positions
            (market_id, user_id, lp_tokens, usdc_deposited, created_at)
            VALUES (?, ?, ?, ?, ?)
        """, (self.market_id, creator_id, initial_usdc, initial_usdc, now))

        self._record_price(conn)

        return {
            "market_id": self.market_id,
            "shares_yes": shares_yes,
            "shares_no": shares_no,
            "initial_usdc": initial_usdc,
            "lp_tokens_minted": initial_usdc,
            "price_yes": self.get_price("YES"),
            "price_no": self.get_price("NO"),
        }

    def get_price(self, outcome: str) -> float:
        """Get current price for an outcome (0.0 to 1.0).

        Price of YES = shares_no / (shares_yes + shares_no)
        Price of NO  = shares_yes / (shares_yes + shares_no)
        """
        pool = self._get_pool()
        if not pool:
            return 0.5
        sy, sn = pool["shares_yes"], pool["shares_no"]
        total = sy + sn
        if total == 0:
            return 0.5
        if outcome == "YES":
            return sn / total
        return sy / total

    def get_price_after_trade(self, outcome: str, amount: float) -> float:
        """Calculate what the price would be after a trade of given size."""
        pool = self._get_pool()
        if not pool:
            return 0.5
        sy, sn = pool["shares_yes"], pool["shares_no"]
        k = sy * sn
        if outcome == "YES":
            # Buying YES: add to NO side, take from YES side
            new_sn = sn + amount
            new_sy = k / new_sn
            return new_sn / (new_sy + new_sn)
        else:
            new_sy = sy + amount
            new_sn = k / new_sy
            return new_sy / (new_sy + new_sn)

    def buy_outcome(self, outcome: str, amount_usdc: float,
                    max_slippage: float = None, user_id: str = None) -> Dict[str, Any]:
        """Buy outcome shares from the AMM pool.

        Args:
            outcome: "YES" or "NO"
            amount_usdc: USDC amount to spend
            max_slippage: Maximum acceptable slippage (0.0-1.0)
            user_id: Buyer identifier

        Returns:
            Dict with trade details including shares received and price.
        """
        if amount_usdc <= 0:
            raise ValueError("Amount must be positive")
        if outcome not in ("YES", "NO"):
            raise ValueError("Outcome must be YES or NO")

        max_slip = max_slippage or self.SLIPPAGE_TOLERANCE
        fee_rate = self.fee_bps / 10000.0

        conn = self._get_conn()
        pool = self._get_pool(conn)
        if not pool:
            raise ValueError(f"No pool found for market {self.market_id}")

        sy, sn = pool["shares_yes"], pool["shares_no"]
        k = pool["k_constant"]

        # Calculate fee
        fee = amount_usdc * fee_rate
        net_amount = amount_usdc - fee
        lp_fee = fee * 0.8  # 80% to LPs, 20% to protocol

        # Calculate shares received using constant product formula
        # Buying an outcome means taking shares FROM that side of the pool.
        # To keep x*y=k, we add USDC to the OPPOSITE side.
        if outcome == "YES":
            # Add to NO side, calculate how many YES shares we can take
            new_sn = sn + net_amount
            new_sy = k / new_sn
            shares_received = sy - new_sy
        else:
            # Add to YES side, calculate how many NO shares we can take
            new_sy = sy + net_amount
            new_sn = k / new_sy
            shares_received = sn - new_sn

        if shares_received <= 0:
            raise ValueError("Trade too small to produce shares")

        # Calculate prices
        price_before = sn / (sy + sn) if outcome == "YES" else sy / (sy + sn)
        price_after = new_sn / (new_sy + new_sn) if outcome == "YES" else new_sy / (new_sy + new_sn)
        avg_price = amount_usdc / shares_received
        price_impact = abs(price_after - price_before) / price_before if price_before > 0 else 0

        # Check slippage
        if price_impact > max_slip:
            raise ValueError(
                f"Price impact {price_impact:.2%} exceeds max slippage {max_slip:.2%}"
            )

        # Update pool
        new_k = new_sy * new_sn
        now = datetime.now(timezone.utc).isoformat()
        conn.execute("""
            UPDATE amm_pools SET
                shares_yes = ?, shares_no = ?, k_constant = ?,
                total_volume = total_volume + ?,
                trade_count = trade_count + 1,
                updated_at = ?
            WHERE market_id = ?
        """, (new_sy, new_sn, new_k, amount_usdc, now, self.market_id))

        # Record trade
        conn.execute("""
            INSERT INTO amm_trades
            (market_id, side, outcome, input_amount, output_amount,
             avg_price, price_impact, fee_charged, lp_fee, user_id, timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (self.market_id, "BUY", outcome, amount_usdc, shares_received,
              avg_price, price_impact, fee, lp_fee, user_id, now))

        self._record_price(conn)

        return {
            "market_id": self.market_id,
            "outcome": outcome,
            "shares_received": round(shares_received, 6),
            "usdc_spent": amount_usdc,
            "avg_price": round(avg_price, 6),
            "price_before": round(price_before, 6),
            "price_after": round(price_after, 6),
            "price_impact": f"{price_impact:.2%}",
            "fee_charged": round(fee, 6),
        }

    def sell_outcome(self, outcome: str, shares: float,
                     user_id: str = None) -> Dict[str, Any]:
        """Sell outcome shares back to the AMM pool.

        Args:
            outcome: "YES" or "NO"
            shares: Number of shares to sell
            user_id: Seller identifier

        Returns:
            Dict with trade details including USDC received.
        """
        if shares <= 0:
            raise ValueError("Shares must be positive")
        if outcome not in ("YES", "NO"):
            raise ValueError("Outcome must be YES or NO")

        fee_rate = self.fee_bps / 10000.0

        conn = self._get_conn()
        pool = self._get_pool(conn)
        if not pool:
            raise ValueError(f"No pool found for market {self.market_id}")

        sy, sn = pool["shares_yes"], pool["shares_no"]
        k = pool["k_constant"]

        # Selling shares: return shares TO the pool (increase that side)
        # The opposite side shrinks, and we receive the USDC equivalent.
        if outcome == "YES":
            new_sy = sy + shares
            new_sn = k / new_sy
            usdc_equivalent = sn - new_sn
        else:
            new_sn = sn + shares
            new_sy = k / new_sn
            usdc_equivalent = sy - new_sy

        if usdc_equivalent <= 0:
            raise ValueError("Trade results in zero or negative USDC")

        # Apply fee on sale proceeds
        fee = usdc_equivalent * fee_rate
        usdc_received = usdc_equivalent - fee
        lp_fee = fee * 0.8

        price_before = sn / (sy + sn) if outcome == "YES" else sy / (sy + sn)
        price_after = new_sn / (new_sy + new_sn) if outcome == "YES" else new_sy / (new_sy + new_sn)
        avg_price = usdc_equivalent / shares

        new_k = new_sy * new_sn
        now = datetime.now(timezone.utc).isoformat()
        conn.execute("""
            UPDATE amm_pools SET
                shares_yes = ?, shares_no = ?, k_constant = ?,
                total_volume = total_volume + ?,
                trade_count = trade_count + 1,
                updated_at = ?
            WHERE market_id = ?
        """, (new_sy, new_sn, new_k, usdc_equivalent, now, self.market_id))

        conn.execute("""
            INSERT INTO amm_trades
            (market_id, side, outcome, input_amount, output_amount,
             avg_price, price_impact, fee_charged, lp_fee, user_id, timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (self.market_id, "SELL", outcome, shares, usdc_received,
              avg_price, abs(price_after - price_before) / price_before if price_before > 0 else 0,
              fee, lp_fee, user_id, now))

        self._record_price(conn)

        return {
            "market_id": self.market_id,
            "outcome": outcome,
            "shares_sold": shares,
            "usdc_received": round(usdc_received, 6),
            "avg_price": round(avg_price, 6),
            "fee_charged": round(fee, 6),
        }

    def add_liquidity(self, amount_usdc: float, user_id: str) -> Dict[str, Any]:
        """Add liquidity to the pool and receive LP tokens.

        LP tokens are proportional to the share of the pool.
        Must add liquidity in the current price ratio.
        """
        if amount_usdc <= 0:
            raise ValueError("Amount must be positive")

        conn = self._get_conn()
        pool = self._get_pool(conn)
        if not pool:
            raise ValueError(f"No pool for market {self.market_id}")

        sy, sn = pool["shares_yes"], pool["shares_no"]
        total_liq = pool["total_liquidity"]
        lp_supply = pool["lp_total_supply"]

        # Calculate LP tokens to mint
        if total_liq == 0:
            lp_minted = amount_usdc
        else:
            lp_minted = (amount_usdc / total_liq) * lp_supply

        # Add liquidity proportionally
        ratio = amount_usdc / total_liq if total_liq > 0 else 1.0
        new_sy = sy * (1 + ratio)
        new_sn = sn * (1 + ratio)
        new_k = new_sy * new_sn
        new_total = total_liq + amount_usdc
        new_lp_supply = lp_supply + lp_minted

        now = datetime.now(timezone.utc).isoformat()
        conn.execute("""
            UPDATE amm_pools SET
                shares_yes = ?, shares_no = ?, total_liquidity = ?,
                k_constant = ?, lp_total_supply = ?, updated_at = ?
            WHERE market_id = ?
        """, (new_sy, new_sn, new_total, new_k, new_lp_supply, now, self.market_id))

        conn.execute("""
            INSERT INTO amm_lp_positions
            (market_id, user_id, lp_tokens, usdc_deposited, created_at)
            VALUES (?, ?, ?, ?, ?)
        """, (self.market_id, user_id, lp_minted, amount_usdc, now))

        return {
            "market_id": self.market_id,
            "usdc_deposited": amount_usdc,
            "lp_tokens_minted": round(lp_minted, 6),
            "pool_share_pct": round(lp_minted / new_lp_supply * 100, 2),
            "new_total_liquidity": new_total,
        }

    def remove_liquidity(self, lp_tokens: float, user_id: str) -> Dict[str, Any]:
        """Remove liquidity and receive USDC back."""
        if lp_tokens <= 0:
            raise ValueError("LP tokens must be positive")

        conn = self._get_conn()
        pool = self._get_pool(conn)
        lp_pos = conn.execute("""
            SELECT lp_tokens, usdc_deposited FROM amm_lp_positions
            WHERE market_id = ? AND user_id = ? AND lp_tokens >= ?
            ORDER BY lp_id DESC LIMIT 1
        """, (self.market_id, user_id, lp_tokens)).fetchone()

        if not lp_pos:
            raise ValueError(f"Insufficient LP tokens for user {user_id}")

        lp_supply = pool["lp_total_supply"]
        ratio = lp_tokens / lp_supply

        usdc_out = pool["total_liquidity"] * ratio
        new_total = pool["total_liquidity"] - usdc_out
        new_lp_supply = lp_supply - lp_tokens
        new_sy = pool["shares_yes"] * (1 - ratio)
        new_sn = pool["shares_no"] * (1 - ratio)
        new_k = new_sy * new_sn

        now = datetime.now(timezone.utc).isoformat()
        conn.execute("""
            UPDATE amm_pools SET
                shares_yes = ?, shares_no = ?, total_liquidity = ?,
                k_constant = ?, lp_total_supply = ?, updated_at = ?
            WHERE market_id = ?
        """, (new_sy, new_sn, new_total, new_k, new_lp_supply, now, self.market_id))

        # Update LP position
        conn.execute("""
            UPDATE amm_lp_positions SET lp_tokens = lp_tokens - ?
            WHERE market_id = ? AND user_id = ?
        """, (lp_tokens, self.market_id, user_id))

        return {
            "market_id": self.market_id,
            "lp_tokens_burned": lp_tokens,
            "usdc_received": round(usdc_out, 6),
            "usdc_returned": round(usdc_out, 6),
            "pnl": round(usdc_out - lp_pos[1] * (lp_tokens / pool["lp_total_supply"] + ratio), 6),
        }

    def get_pool_info(self) -> Dict[str, Any]:
        """Get comprehensive pool information."""
        pool = self._get_pool()
        if not pool:
            return {"error": f"No pool for market {self.market_id}"}
        return {
            "market_id": self.market_id,
            "price_yes": round(self.get_price("YES"), 6),
            "price_no": round(self.get_price("NO"), 6),
            "shares_yes": round(pool["shares_yes"], 2),
            "shares_no": round(pool["shares_no"], 2),
            "total_liquidity": round(pool["total_liquidity"], 2),
            "total_volume": round(pool["total_volume"], 2),
            "trade_count": pool["trade_count"],
            "fee_bps": pool["fee_bps"],
            "lp_total_supply": round(pool["lp_total_supply"], 2),
            "k_constant": round(pool["k_constant"], 2),
        }

    def get_price_history(self, hours: int = 24) -> List[Dict[str, Any]]:
        """Get price history for charting."""
        conn = self._get_conn()
        conn.row_factory = sqlite3.Row
        rows = conn.execute("""
            SELECT price_yes, price_no, volume, timestamp
            FROM amm_price_history
            WHERE market_id = ?
            ORDER BY timestamp DESC LIMIT ?
        """, (self.market_id, hours * 12)).fetchall()  # ~5min intervals
        return [dict(r) for r in rows]

    def get_recent_trades(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Get recent trades for this market."""
        conn = self._get_conn()
        conn.row_factory = sqlite3.Row
        rows = conn.execute("""
            SELECT trade_id, side, outcome, input_amount, output_amount,
                   avg_price, price_impact, fee_charged, user_id, timestamp
            FROM amm_trades
            WHERE market_id = ?
            ORDER BY timestamp DESC LIMIT ?
        """, (self.market_id, limit)).fetchall()
        return [dict(r) for r in rows]

    def calculate_slippage(self, outcome: str, amount_usdc: float) -> Dict[str, float]:
        """Preview slippage for a potential trade without executing."""
        price_before = self.get_price(outcome)
        price_after = self.get_price_after_trade(outcome, amount_usdc)
        slippage = abs(price_after - price_before) / price_before if price_before > 0 else 0
        return {
            "price_before": round(price_before, 6),
            "price_after": round(price_after, 6),
            "slippage_pct": round(slippage * 100, 4),
        }

    def _get_pool(self, conn=None) -> Optional[Dict[str, Any]]:
        """Get pool state from database."""
        if conn is None:
            conn = self._get_conn()
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM amm_pools WHERE market_id = ?",
            (self.market_id,)
        ).fetchone()
        return dict(row) if row else None

    def _record_price(self, conn):
        """Record current price to history table."""
        pool = self._get_pool(conn)
        if pool:
            now = datetime.now(timezone.utc).isoformat()
            conn.execute("""
                INSERT INTO amm_price_history
                (market_id, price_yes, price_no, shares_yes, shares_no, timestamp)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (self.market_id,
                  pool["shares_no"] / (pool["shares_yes"] + pool["shares_no"]),
                  pool["shares_yes"] / (pool["shares_yes"] + pool["shares_no"]),
                  pool["shares_yes"], pool["shares_no"], now))


if __name__ == "__main__":
    # Demo
    pool = AMMPool("demo_market", db_path=":memory:", fee_bps=100)
    print("Initializing pool with 10,000 USDC...")
    result = pool.initialize(10000)
    print(f"  YES price: {pool.get_price('YES'):.4f}")
    print(f"  NO price:  {pool.get_price('NO'):.4f}")

    print("\nBuying 500 USDC of YES...")
    buy = pool.buy_outcome("YES", 500)
    print(f"  Shares: {buy['shares_received']:.2f}")
    print(f"  Avg price: {buy['avg_price']:.4f}")
    print(f"  Impact: {buy['price_impact']}")

    print(f"\nNew prices after buy:")
    print(f"  YES: {pool.get_price('YES'):.4f}")
    print(f"  NO:  {pool.get_price('NO'):.4f}")
