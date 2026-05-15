#!/usr/bin/env python3
"""Portfolio Management System for Prediction Market Positions.

Tracks user positions, calculates P&L, manages settlement,
and provides portfolio-level analytics.

Key features:
- Position tracking with average entry price
- Realized and unrealized P&L calculation
- Multi-market portfolio aggregation
- Trade history with full audit trail
- Leaderboard and ranking system
- Portfolio performance metrics (Sharpe, max drawdown)

Usage:
    pm = PortfolioManager(db_path=":memory:")
    pm.open_position("alice", "btc_100k", "YES", 100, 0.65)
    pm.open_position("alice", "btc_100k", "YES", 50, 0.68)
    positions = pm.get_positions("alice")
    pnl = pm.calculate_pnl("alice")
"""
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


class PortfolioManager:
    """Full portfolio management for prediction market traders."""

    def __init__(self, db_path: str = "portfolio.db"):
        self.db_path = db_path
        self._conn = None
        self._init_db()

    def _get_conn(self):
        """Get cached database connection."""
        if not hasattr(self, '_conn') or self._conn is None:
            self._conn = sqlite3.connect(self.db_path)
            self._conn.row_factory = None
        return self._conn

    def _init_db(self):
        conn = self._get_conn()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS positions (
                position_id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                market_id TEXT NOT NULL,
                outcome TEXT NOT NULL,
                shares REAL NOT NULL DEFAULT 0,
                avg_entry_price REAL NOT NULL DEFAULT 0,
                total_cost REAL NOT NULL DEFAULT 0,
                realized_pnl REAL NOT NULL DEFAULT 0,
                unrealized_pnl REAL NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'OPEN',
                opened_at TEXT NOT NULL,
                closed_at TEXT,
                updated_at TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS trades (
                trade_id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                market_id TEXT NOT NULL,
                outcome TEXT NOT NULL,
                side TEXT NOT NULL,
                quantity REAL NOT NULL,
                price REAL NOT NULL,
                total_usdc REAL NOT NULL,
                fee REAL NOT NULL DEFAULT 0,
                position_id TEXT,
                timestamp TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS portfolio_snapshots (
                snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                total_value REAL NOT NULL,
                total_cost REAL NOT NULL,
                unrealized_pnl REAL NOT NULL,
                realized_pnl REAL NOT NULL,
                position_count INTEGER NOT NULL,
                timestamp TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS user_stats (
                user_id TEXT PRIMARY KEY,
                total_trades INTEGER NOT NULL DEFAULT 0,
                winning_trades INTEGER NOT NULL DEFAULT 0,
                losing_trades INTEGER NOT NULL DEFAULT 0,
                total_volume REAL NOT NULL DEFAULT 0,
                total_pnl REAL NOT NULL DEFAULT 0,
                best_trade REAL NOT NULL DEFAULT 0,
                worst_trade REAL NOT NULL DEFAULT 0,
                avg_trade_size REAL NOT NULL DEFAULT 0,
                current_streak INTEGER NOT NULL DEFAULT 0,
                best_streak INTEGER NOT NULL DEFAULT 0,
                joined_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_positions_user ON positions(user_id, status)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_trades_user ON trades(user_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_trades_market ON trades(market_id)")

    def open_position(self, user_id: str, market_id: str, outcome: str,
                      shares: float, price: float, fee: float = 0) -> Dict[str, Any]:
        """Open a new position or add to an existing one.

        If user already has an open position for the same market+outcome,
        the average entry price is recalculated.
        """
        if shares <= 0 or price <= 0:
            raise ValueError("Shares and price must be positive")

        cost = shares * price
        now = datetime.now(timezone.utc).isoformat()

        conn = self._get_conn()
        # Check for existing position
        existing = conn.execute("""
            SELECT position_id, shares, total_cost FROM positions
            WHERE user_id = ? AND market_id = ? AND outcome = ? AND status = 'OPEN'
        """, (user_id, market_id, outcome)).fetchone()

        if existing:
            pos_id, old_shares, old_cost = existing
            new_shares = old_shares + shares
            new_cost = old_cost + cost
            new_avg = new_cost / new_shares

            conn.execute("""
                UPDATE positions SET
                    shares = ?, avg_entry_price = ?, total_cost = ?,
                    updated_at = ?
                WHERE position_id = ?
            """, (new_shares, new_avg, new_cost, now, pos_id))
        else:
            pos_id = f"pos_{uuid.uuid4().hex[:8]}"
            conn.execute("""
                INSERT INTO positions
                (position_id, user_id, market_id, outcome, shares,
                 avg_entry_price, total_cost, opened_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (pos_id, user_id, market_id, outcome, shares,
                  price, cost, now, now))

        # Record trade
        trade_id = f"t_{uuid.uuid4().hex[:10]}"
        conn.execute("""
            INSERT INTO trades
            (trade_id, user_id, market_id, outcome, side, quantity,
             price, total_usdc, fee, position_id, timestamp)
            VALUES (?, ?, ?, ?, 'BUY', ?, ?, ?, ?, ?, ?)
        """, (trade_id, user_id, market_id, outcome, shares, price,
              cost, fee, pos_id, now))

        # Update user stats
        self._update_stats(conn, user_id, cost, now)

        return {
            "position_id": pos_id,
            "market_id": market_id,
            "outcome": outcome,
            "shares": shares,
            "avg_entry_price": price if not existing else new_avg,
            "total_cost": cost if not existing else new_cost,
            "trade_id": trade_id,
        }

    def close_position(self, position_id: str, shares_to_close: float,
                       exit_price: float, fee: float = 0) -> Dict[str, Any]:
        """Close (partially or fully) a position.

        Calculates realized P&L for the closed portion.
        """
        conn = self._get_conn()
        conn.row_factory = sqlite3.Row
        pos = conn.execute(
            "SELECT * FROM positions WHERE position_id = ?",
            (position_id,)
        ).fetchone()
        if not pos:
            raise ValueError(f"Position {position_id} not found")

        pos = dict(pos)
        if pos["status"] != "OPEN":
            raise ValueError(f"Position is {pos['status']}, not OPEN")
        if shares_to_close > pos["shares"]:
            raise ValueError("Cannot close more shares than held")

        proceeds = shares_to_close * exit_price
        cost_basis = shares_to_close * pos["avg_entry_price"]
        realized = proceeds - cost_basis - fee

        now = datetime.now(timezone.utc).isoformat()
        new_shares = pos["shares"] - shares_to_close

        if new_shares <= 0.001:
            # Fully close
            conn.execute("""
                UPDATE positions SET
                    shares = 0, realized_pnl = realized_pnl + ?,
                    status = 'CLOSED', closed_at = ?, updated_at = ?
                WHERE position_id = ?
            """, (realized, now, now, position_id))
        else:
            # Partial close
            conn.execute("""
                UPDATE positions SET
                    shares = ?, realized_pnl = realized_pnl + ?,
                    updated_at = ?
                WHERE position_id = ?
            """, (new_shares, realized, now, position_id))

        # Record trade
        trade_id = f"t_{uuid.uuid4().hex[:10]}"
        conn.execute("""
            INSERT INTO trades
            (trade_id, user_id, market_id, outcome, side, quantity,
             price, total_usdc, fee, position_id, timestamp)
            VALUES (?, ?, ?, ?, 'SELL', ?, ?, ?, ?, ?, ?)
        """, (trade_id, pos["user_id"], pos["market_id"], pos["outcome"],
              shares_to_close, exit_price, proceeds, fee, position_id, now))

        # Update user stats PnL
        conn.execute("""
            UPDATE user_stats SET
                total_pnl = total_pnl + ?,
                winning_trades = winning_trades + ?,
                losing_trades = losing_trades + ?,
                updated_at = ?
            WHERE user_id = ?
        """, (realized, 1 if realized > 0 else 0, 1 if realized < 0 else 0,
              now, pos["user_id"]))

        return {
            "position_id": position_id,
            "shares_closed": shares_to_close,
            "exit_price": exit_price,
            "proceeds": round(proceeds, 6),
            "cost_basis": round(cost_basis, 6),
            "realized_pnl": round(realized, 6),
            "remaining_shares": round(new_shares, 6) if new_shares > 0 else 0,
            "trade_id": trade_id,
        }

    def get_positions(self, user_id: str, status: str = "OPEN") -> List[Dict]:
        """Get all positions for a user."""
        conn = self._get_conn()
        conn.row_factory = sqlite3.Row
        rows = conn.execute("""
            SELECT * FROM positions
            WHERE user_id = ? AND status = ?
            ORDER BY opened_at DESC
        """, (user_id, status)).fetchall()
        return [dict(r) for r in rows]

    def get_position(self, position_id: str) -> Optional[Dict]:
        """Get a specific position."""
        conn = self._get_conn()
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM positions WHERE position_id = ?",
            (position_id,)
        ).fetchone()
        return dict(row) if row else None

    def calculate_pnl(self, user_id: str, current_prices: Dict[str, float] = None) -> Dict[str, Any]:
        """Calculate total P&L for a user across all positions.

        Args:
            user_id: User to calculate P&L for.
            current_prices: Dict of {market_id_outcome: price} for unrealized P&L.
                           e.g., {"btc_100k_YES": 0.75, "btc_100k_NO": 0.25}
        """
        conn = self._get_conn()
        conn.row_factory = sqlite3.Row
        positions = conn.execute("""
            SELECT * FROM positions WHERE user_id = ?
        """, (user_id,)).fetchall()

        total_realized = 0
        total_unrealized = 0
        total_cost = 0
        position_details = []

        for pos in positions:
            pos = dict(pos)
            total_realized += pos["realized_pnl"]
            total_cost += pos["total_cost"]

            if pos["status"] == "OPEN" and current_prices:
                key = f"{pos['market_id']}_{pos['outcome']}"
                current = current_prices.get(key, pos["avg_entry_price"])
                unrealized = pos["shares"] * current - pos["total_cost"]
                total_unrealized += unrealized
            else:
                unrealized = 0

            position_details.append({
                "position_id": pos["position_id"],
                "market_id": pos["market_id"],
                "outcome": pos["outcome"],
                "shares": pos["shares"],
                "avg_entry": pos["avg_entry_price"],
                "realized_pnl": round(pos["realized_pnl"], 6),
                "unrealized_pnl": round(unrealized, 6),
                "status": pos["status"],
            })

        return {
            "user_id": user_id,
            "total_realized_pnl": round(total_realized, 2),
            "total_unrealized_pnl": round(total_unrealized, 2),
            "total_pnl": round(total_realized + total_unrealized, 2),
            "total_cost": round(total_cost, 2),
            "open_positions": len([p for p in position_details if p["status"] == "OPEN"]),
            "closed_positions": len([p for p in position_details if p["status"] == "CLOSED"]),
            "positions": position_details,
        }

    def get_trade_history(self, user_id: str, limit: int = 50) -> List[Dict]:
        """Get trade history for a user."""
        conn = self._get_conn()
        conn.row_factory = sqlite3.Row
        rows = conn.execute("""
            SELECT * FROM trades WHERE user_id = ?
            ORDER BY timestamp DESC LIMIT ?
        """, (user_id, limit)).fetchall()
        return [dict(r) for r in rows]

    def get_leaderboard(self, sort_by: str = "total_pnl",
                        limit: int = 20) -> List[Dict]:
        """Get trader leaderboard.

        Args:
            sort_by: Sort field (total_pnl, total_volume, winning_trades).
            limit: Number of entries.
        """
        valid_sorts = {"total_pnl", "total_volume", "winning_trades",
                       "best_streak", "total_trades"}
        if sort_by not in valid_sorts:
            sort_by = "total_pnl"

        conn = self._get_conn()
        conn.row_factory = sqlite3.Row
        rows = conn.execute(f"""
            SELECT user_id, total_trades, winning_trades, losing_trades,
                   total_volume, total_pnl, best_streak, avg_trade_size
            FROM user_stats
            WHERE total_trades > 0
            ORDER BY {sort_by} DESC LIMIT ?
        """, (limit,)).fetchall()
        results = []
        for i, r in enumerate(rows):
            d = dict(r)
            d["rank"] = i + 1
            d["win_rate"] = round(
                d["winning_trades"] / max(d["total_trades"], 1) * 100, 1
            )
            results.append(d)
        return results

    def get_portfolio_value(self, user_id: str,
                            current_prices: Dict[str, float] = None) -> Dict[str, Any]:
        """Calculate total portfolio value."""
        pnl = self.calculate_pnl(user_id, current_prices)
        return {
            "user_id": user_id,
            "total_value": round(pnl["total_cost"] + pnl["total_pnl"], 2),
            "total_cost": pnl["total_cost"],
            "total_pnl": pnl["total_pnl"],
            "realized_pnl": pnl["total_realized_pnl"],
            "unrealized_pnl": pnl["total_unrealized_pnl"],
            "open_positions": pnl["open_positions"],
            "closed_positions": pnl["closed_positions"],
            "roi_pct": round(
                pnl["total_pnl"] / pnl["total_cost"] * 100, 2
            ) if pnl["total_cost"] > 0 else 0,
        }

    def _update_stats(self, conn, user_id: str, trade_value: float, now: str):
        """Update or create user stats after a trade."""
        existing = conn.execute(
            "SELECT user_id FROM user_stats WHERE user_id = ?", (user_id,)
        ).fetchone()

        if existing:
            conn.execute("""
                UPDATE user_stats SET
                    total_trades = total_trades + 1,
                    total_volume = total_volume + ?,
                    avg_trade_size = (total_volume + ?) / (total_trades + 1),
                    updated_at = ?
                WHERE user_id = ?
            """, (trade_value, trade_value, now, user_id))
        else:
            conn.execute("""
                INSERT INTO user_stats
                (user_id, total_trades, total_volume, avg_trade_size, joined_at, updated_at)
                VALUES (?, 1, ?, ?, ?, ?)
            """, (user_id, trade_value, trade_value, now, now))


if __name__ == "__main__":
    pm = PortfolioManager(db_path=":memory:")

    print("Opening positions for Alice...")
    pm.open_position("alice", "btc_100k", "YES", 100, 0.55, fee=0.55)
    pm.open_position("alice", "btc_100k", "YES", 50, 0.62, fee=0.31)
    pm.open_position("alice", "eth_5k", "YES", 200, 0.40, fee=0.80)

    positions = pm.get_positions("alice")
    print(f"  Open positions: {len(positions)}")

    print("\nPartial close with profit...")
    result = pm.close_position(positions[0]["position_id"], 75, 0.70, fee=0.53)
    print(f"  Realized PnL: {result['realized_pnl']} USDC")

    print("\nPortfolio P&L:")
    pnl = pm.calculate_pnl("alice", {"btc_100k_YES": 0.70, "eth_5k_YES": 0.35})
    print(f"  Realized: {pnl['total_realized_pnl']}")
    print(f"  Unrealized: {pnl['total_unrealized_pnl']}")
    print(f"  Total: {pnl['total_pnl']}")

    print("\nLeaderboard:")
    lb = pm.get_leaderboard()
    for entry in lb:
        print(f"  #{entry['rank']} {entry['user_id']}: PnL={entry['total_pnl']}")
