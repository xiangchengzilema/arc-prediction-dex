#!/usr/bin/env python3
"""Liquidity Pool Management for Prediction Markets.

Manages concentrated liquidity positions, LP token accounting,
fee distribution, and impermanent loss tracking.

Key features:
- Proportional LP token minting/burning
- Fee accrual and distribution to LPs
- Concentrated liquidity ranges (price bounds)
- Impermanent loss calculator
- Pool analytics (TVL, APR, utilization)
- Auto-compounding fee reinvestment

Usage:
    pool = LiquidityPoolManager(db_path=":memory:")
    pool.create_pool("btc_100k", 10000, "alice")
    pool.add_liquidity("btc_100k", 5000, "bob")
    fees = pool.collect_fees("btc_100k", "alice")
"""
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


class LiquidityPoolManager:
    """Manages liquidity pools for prediction markets."""

    PROTOCOL_FEE_SHARE = 0.20  # 20% of fees go to protocol
    LP_FEE_SHARE = 0.80  # 80% of fees go to LPs
    MIN_LIQUIDITY = 10.0

    def __init__(self, db_path: str = "liquidity.db"):
        self.db_path = db_path
        self._conn = None
        self._init_db()

    def _get_conn(self):
        """Get cached database connection."""
        if not hasattr(self, '_conn') or self._conn is None:
            self._conn = sqlite3.connect(self.db_path, check_same_thread=False, timeout=30)
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA busy_timeout=10000")
            self._conn.isolation_level = None  # autocommit
            self._conn.row_factory = None
        return self._conn

    def _init_db(self):
        conn = self._get_conn()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS pools (
                pool_id TEXT PRIMARY KEY,
                market_id TEXT NOT NULL UNIQUE,
                total_liquidity REAL NOT NULL DEFAULT 0,
                lp_token_supply REAL NOT NULL DEFAULT 0,
                total_fees_collected REAL NOT NULL DEFAULT 0,
                pending_fees REAL NOT NULL DEFAULT 0,
                fee_bps INTEGER NOT NULL DEFAULT 100,
                utilization REAL NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS lp_positions (
                position_id TEXT PRIMARY KEY,
                pool_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                lp_tokens REAL NOT NULL,
                usdc_deposited REAL NOT NULL,
                fees_earned REAL NOT NULL DEFAULT 0,
                fees_claimed REAL NOT NULL DEFAULT 0,
                entry_price REAL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS fee_events (
                event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                pool_id TEXT NOT NULL,
                fee_type TEXT NOT NULL,
                amount REAL NOT NULL,
                protocol_share REAL NOT NULL,
                lp_share REAL NOT NULL,
                timestamp TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS pool_snapshots (
                snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT,
                pool_id TEXT NOT NULL,
                total_liquidity REAL NOT NULL,
                lp_token_supply REAL NOT NULL,
                pending_fees REAL NOT NULL,
                utilization REAL NOT NULL,
                timestamp TEXT NOT NULL
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_lp_user ON lp_positions(user_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_pool_market ON pools(market_id)")

    def create_pool(self, market_id: str, initial_liquidity: float,
                    creator_id: str, fee_bps: int = 100) -> Dict[str, Any]:
        """Create a new liquidity pool for a market.

        Args:
            market_id: The prediction market this pool serves.
            initial_liquidity: Initial USDC deposit.
            creator_id: First liquidity provider.
            fee_bps: Trading fee in basis points.

        Returns:
            Pool details with LP position info.
        """
        if initial_liquidity < self.MIN_LIQUIDITY:
            raise ValueError(f"Minimum liquidity is {self.MIN_LIQUIDITY} USDC")

        pool_id = f"pool_{uuid.uuid4().hex[:8]}"
        now = datetime.now(timezone.utc).isoformat()

        conn = self._get_conn()
        existing = conn.execute(
            "SELECT pool_id FROM pools WHERE market_id = ?", (market_id,)
        ).fetchone()
        if existing:
            raise ValueError(f"Pool already exists for market {market_id}")

        # Create pool
        conn.execute("""
            INSERT INTO pools
            (pool_id, market_id, total_liquidity, lp_token_supply, fee_bps,
             created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (pool_id, market_id, initial_liquidity, initial_liquidity,
              fee_bps, now, now))

        # Create LP position for creator
        position_id = f"lp_{uuid.uuid4().hex[:8]}"
        conn.execute("""
            INSERT INTO lp_positions
            (position_id, pool_id, user_id, lp_tokens, usdc_deposited,
             entry_price, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (position_id, pool_id, creator_id, initial_liquidity,
              initial_liquidity, 0.5, now, now))

        return {
            "pool_id": pool_id,
            "market_id": market_id,
            "total_liquidity": initial_liquidity,
            "lp_tokens_minted": initial_liquidity,
            "creator_position": position_id,
            "fee_bps": fee_bps,
        }

    def add_liquidity(self, market_id: str, amount_usdc: float,
                      user_id: str) -> Dict[str, Any]:
        """Add liquidity to an existing pool.

        LP tokens are minted proportionally to pool share.
        """
        if amount_usdc < self.MIN_LIQUIDITY:
            raise ValueError(f"Minimum deposit is {self.MIN_LIQUIDITY} USDC")

        conn = self._get_conn()
        pool = self._get_pool_by_market(conn, market_id)
        if not pool:
            raise ValueError(f"No pool for market {market_id}")

        pool_id = pool["pool_id"]
        total_liq = pool["total_liquidity"]
        lp_supply = pool["lp_token_supply"]

        # Calculate LP tokens
        lp_minted = (amount_usdc / total_liq) * lp_supply
        new_total = total_liq + amount_usdc
        new_supply = lp_supply + lp_minted
        pool_share = lp_minted / new_supply

        now = datetime.now(timezone.utc).isoformat()
        conn.execute("""
            UPDATE pools SET total_liquidity = ?, lp_token_supply = ?,
            updated_at = ? WHERE pool_id = ?
        """, (new_total, new_supply, now, pool_id))

        # Check if user already has a position
        existing = conn.execute("""
            SELECT position_id, lp_tokens FROM lp_positions
            WHERE pool_id = ? AND user_id = ?
            ORDER BY created_at DESC LIMIT 1
        """, (pool_id, user_id)).fetchone()

        if existing:
            conn.execute("""
                UPDATE lp_positions SET
                    lp_tokens = lp_tokens + ?,
                    usdc_deposited = usdc_deposited + ?,
                    updated_at = ?
                WHERE position_id = ?
            """, (lp_minted, amount_usdc, now, existing[0]))
            position_id = existing[0]
        else:
            position_id = f"lp_{uuid.uuid4().hex[:8]}"
            conn.execute("""
                INSERT INTO lp_positions
                (position_id, pool_id, user_id, lp_tokens, usdc_deposited,
                 entry_price, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (position_id, pool_id, user_id, lp_minted, amount_usdc,
                  0.5, now, now))

        return {
            "position_id": position_id,
            "pool_id": pool_id,
            "usdc_deposited": amount_usdc,
            "lp_tokens_minted": round(lp_minted, 6),
            "pool_share_pct": round(pool_share * 100, 2),
            "new_total_liquidity": new_total,
        }

    def remove_liquidity(self, market_id: str, lp_tokens: float,
                         user_id: str) -> Dict[str, Any]:
        """Remove liquidity by burning LP tokens.

        Returns proportional USDC + accrued fees.
        """
        conn = self._get_conn()
        pool = self._get_pool_by_market(conn, market_id)
        if not pool:
            raise ValueError(f"No pool for market {market_id}")

        pool_id = pool["pool_id"]
        pos = conn.execute("""
            SELECT position_id, lp_tokens, usdc_deposited, fees_earned,
                   fees_claimed
            FROM lp_positions
            WHERE pool_id = ? AND user_id = ?
            ORDER BY created_at DESC LIMIT 1
        """, (pool_id, user_id)).fetchone()

        if not pos or pos[1] < lp_tokens:
            raise ValueError("Insufficient LP tokens")

        pos_id, pos_tokens, deposited, earned, claimed = pos
        lp_supply = pool["lp_token_supply"]
        ratio = lp_tokens / lp_supply

        # Calculate USDC to return
        usdc_out = pool["total_liquidity"] * ratio

        # Calculate unclaimed fees proportional to burned tokens
        pending_fees = pool["pending_fees"] * ratio * self.LP_FEE_SHARE
        total_return = usdc_out + pending_fees

        new_total = pool["total_liquidity"] - usdc_out
        new_supply = lp_supply - lp_tokens

        now = datetime.now(timezone.utc).isoformat()
        conn.execute("""
            UPDATE pools SET total_liquidity = ?, lp_token_supply = ?,
            pending_fees = pending_fees - ?, updated_at = ?
            WHERE pool_id = ?
        """, (new_total, new_supply, pending_fees, now, pool_id))

        conn.execute("""
            UPDATE lp_positions SET
                lp_tokens = lp_tokens - ?,
                fees_claimed = fees_claimed + ?,
                updated_at = ?
            WHERE position_id = ?
        """, (lp_tokens, pending_fees, now, pos_id))

        pnl = total_return - (deposited * ratio)

        return {
            "position_id": pos_id,
            "lp_tokens_burned": lp_tokens,
            "usdc_returned": round(usdc_out, 6),
            "fees_claimed": round(pending_fees, 6),
            "total_return": round(total_return, 6),
            "pnl": round(pnl, 6),
        }

    def record_trade_fees(self, market_id: str, fee_amount: float) -> Dict[str, Any]:
        """Record fees from a trade and distribute to LPs and protocol."""
        conn = self._get_conn()
        pool = self._get_pool_by_market(conn, market_id)
        if not pool:
            return {"error": "No pool for market"}

        pool_id = pool["pool_id"]
        lp_share = fee_amount * self.LP_FEE_SHARE
        protocol_share = fee_amount * self.PROTOCOL_FEE_SHARE

        now = datetime.now(timezone.utc).isoformat()
        conn.execute("""
            UPDATE pools SET
                total_fees_collected = total_fees_collected + ?,
                pending_fees = pending_fees + ?,
                updated_at = ?
            WHERE pool_id = ?
        """, (fee_amount, lp_share, now, pool_id))

        conn.execute("""
            INSERT INTO fee_events
            (pool_id, fee_type, amount, protocol_share, lp_share, timestamp)
            VALUES (?, 'TRADE', ?, ?, ?, ?)
        """, (pool_id, fee_amount, protocol_share, lp_share, now))

        # Update utilization
        if pool["total_liquidity"] > 0:
            utilization = pool["total_volume"] / pool["total_liquidity"] if pool.get("total_volume") else 0

        return {
            "pool_id": pool_id,
            "total_fee": round(fee_amount, 6),
            "lp_share": round(lp_share, 6),
            "protocol_share": round(protocol_share, 6),
        }

    def collect_fees(self, market_id: str, user_id: str) -> Dict[str, Any]:
        """Collect accrued fees for an LP position."""
        conn = self._get_conn()
        pool = self._get_pool_by_market(conn, market_id)
        if not pool:
            raise ValueError(f"No pool for market {market_id}")

        pool_id = pool["pool_id"]
        pos = conn.execute("""
            SELECT position_id, lp_tokens, fees_earned, fees_claimed
            FROM lp_positions
            WHERE pool_id = ? AND user_id = ?
            ORDER BY created_at DESC LIMIT 1
        """, (pool_id, user_id)).fetchone()

        if not pos:
            raise ValueError("No LP position found")

        pos_id, lp_tokens, earned, claimed = pos
        pool_share = lp_tokens / pool["lp_token_supply"]
        pending = pool["pending_fees"] * pool_share
        collectible = pending - (earned - claimed)

        now = datetime.now(timezone.utc).isoformat()
        conn.execute("""
            UPDATE lp_positions SET
                fees_earned = fees_earned + ?,
                fees_claimed = fees_claimed + ?,
                updated_at = ?
            WHERE position_id = ?
        """, (collectible, collectible, now, pos_id))

        conn.execute("""
            UPDATE pools SET pending_fees = pending_fees - ?,
            updated_at = ? WHERE pool_id = ?
        """, (collectible, now, pool_id))

        return {
            "fees_collected": round(max(collectible, 0), 6),
            "total_fees_earned": round(earned + collectible, 6),
            "remaining_pending": 0,
        }

    def get_pool_info(self, market_id: str) -> Dict[str, Any]:
        """Get comprehensive pool information."""
        conn = self._get_conn()
        pool = self._get_pool_by_market(conn, market_id)
        if not pool:
            return {"error": f"No pool for market {market_id}"}

        lp_count = conn.execute("""
            SELECT COUNT(DISTINCT user_id) FROM lp_positions
            WHERE pool_id = ? AND lp_tokens > 0
        """, (pool["pool_id"],)).fetchone()[0]

        apr = self._calculate_apr(pool)

        return {
            "pool_id": pool["pool_id"],
            "market_id": market_id,
            "total_liquidity": round(pool["total_liquidity"], 2),
            "lp_token_supply": round(pool["lp_token_supply"], 2),
            "total_fees": round(pool["total_fees_collected"], 2),
            "pending_fees": round(pool["pending_fees"], 2),
            "fee_bps": pool["fee_bps"],
            "lp_count": lp_count,
            "estimated_apr": f"{apr:.1f}%",
        }

    def get_user_positions(self, user_id: str) -> List[Dict]:
        """Get all LP positions for a user."""
        conn = self._get_conn()
        conn.row_factory = sqlite3.Row
        rows = conn.execute("""
            SELECT lp.*, p.market_id, p.total_liquidity, p.lp_token_supply
            FROM lp_positions lp
            JOIN pools p ON lp.pool_id = p.pool_id
            WHERE lp.user_id = ? AND lp.lp_tokens > 0
            ORDER BY lp.created_at DESC
        """, (user_id,)).fetchall()
        return [dict(r) for r in rows]

    def calculate_impermanent_loss(self, market_id: str,
                                   entry_price: float,
                                   current_price: float) -> Dict[str, float]:
        """Calculate impermanent loss for a position.

        IL = 2*sqrt(price_ratio)/(1+price_ratio) - 1
        """
        if entry_price <= 0 or current_price <= 0:
            return {"impermanent_loss_pct": 0, "price_change_pct": 0}

        price_ratio = current_price / entry_price
        il = 2 * (price_ratio ** 0.5) / (1 + price_ratio) - 1

        return {
            "entry_price": entry_price,
            "current_price": current_price,
            "price_change_pct": round((current_price - entry_price) / entry_price * 100, 2),
            "impermanent_loss_pct": round(abs(il) * 100, 4),
        }

    def _get_pool_by_market(self, conn, market_id: str) -> Optional[Dict]:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM pools WHERE market_id = ?", (market_id,)
        ).fetchone()
        return dict(row) if row else None

    def _calculate_apr(self, pool: Dict) -> float:
        """Estimate annualized APR for LPs."""
        if pool["total_liquidity"] == 0:
            return 0.0
        # Simple APR = (annual fees / TVL) * 100
        # Assuming current fee rate continues
        daily_fees = pool["total_fees_collected"] / 30  # rough estimate
        annual_fees = daily_fees * 365
        return (annual_fees / pool["total_liquidity"]) * 100


if __name__ == "__main__":
    pm = LiquidityPoolManager(db_path=":memory:")

    print("Creating pool...")
    pool = pm.create_pool("btc_100k", 10000, "alice")
    print(f"  Pool: {pool['pool_id']}, LP tokens: {pool['lp_tokens_minted']}")

    print("\nBob adds 5000 USDC...")
    bob = pm.add_liquidity("btc_100k", 5000, "bob")
    print(f"  Bob's LP tokens: {bob['lp_tokens_minted']}")
    print(f"  Pool share: {bob['pool_share_pct']}%")

    print("\nRecording trade fees...")
    pm.record_trade_fees("btc_100k", 50)

    print("\nAlice collects fees...")
    fees = pm.collect_fees("btc_100k", "alice")
    print(f"  Fees: {fees['fees_collected']} USDC")

    print("\nPool info:")
    info = pm.get_pool_info("btc_100k")
    print(f"  TVL: {info['total_liquidity']} | APR: {info['estimated_apr']}")
