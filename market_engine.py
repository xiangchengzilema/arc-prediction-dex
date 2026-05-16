#!/usr/bin/env python3
"""Prediction Market Lifecycle Engine.

Manages the complete lifecycle of prediction markets:
  Create -> Open -> Trade -> Close -> Resolve -> Settle

Supports binary (YES/NO) and multi-outcome markets with:
- Configurable resolution criteria
- Timed auto-close
- Dispute period for contested resolutions
- Fee distribution on settlement
- Market categories and tagging

Usage:
    engine = MarketEngine(db_path=":memory:")
    market = engine.create_market(
        question="Will BTC reach $150k by end of 2026?",
        outcomes=["YES", "NO"],
        deadline="2026-12-31T23:59:59Z",
        category="crypto",
        initial_liquidity=5000,
        creator_id="alice"
    )
    engine.open_market(market["market_id"])
"""
import sqlite3
import uuid
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional


class MarketEngine:
    """Full prediction market lifecycle manager."""

    MARKET_STATUSES = ("DRAFT", "OPEN", "CLOSED", "RESOLVED", "SETTLED", "DISPUTED", "CANCELLED")
    DEFAULT_FEE_BPS = 100  # 1% platform fee
    DISPUTE_PERIOD_HOURS = 48
    MIN_LIQUIDITY = 100.0  # Minimum USDC to open a market

    def __init__(self, db_path: str = "markets.db"):
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
            CREATE TABLE IF NOT EXISTS markets (
                market_id TEXT PRIMARY KEY,
                question TEXT NOT NULL,
                description TEXT,
                outcomes TEXT NOT NULL,
                category TEXT NOT NULL DEFAULT 'general',
                tags TEXT,
                creator_id TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'DRAFT',
                resolution_source TEXT,
                deadline TEXT NOT NULL,
                created_at TEXT NOT NULL,
                opened_at TEXT,
                closed_at TEXT,
                resolved_at TEXT,
                settled_at TEXT,
                winning_outcome TEXT,
                dispute_deadline TEXT,
                total_volume REAL NOT NULL DEFAULT 0,
                total_liquidity REAL NOT NULL DEFAULT 0,
                fee_bps INTEGER NOT NULL DEFAULT 100,
                trade_count INTEGER NOT NULL DEFAULT 0,
                unique_traders INTEGER NOT NULL DEFAULT 0
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS market_events (
                event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                market_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                actor_id TEXT,
                details TEXT,
                timestamp TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS market_outcomes (
                market_id TEXT NOT NULL,
                outcome TEXT NOT NULL,
                current_price REAL NOT NULL DEFAULT 0.5,
                total_shares REAL NOT NULL DEFAULT 0,
                total_volume REAL NOT NULL DEFAULT 0,
                PRIMARY KEY (market_id, outcome)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS market_settlements (
                settlement_id INTEGER PRIMARY KEY AUTOINCREMENT,
                market_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                outcome TEXT NOT NULL,
                shares REAL NOT NULL,
                payout_per_share REAL NOT NULL,
                total_payout REAL NOT NULL,
                fee_deducted REAL NOT NULL,
                settled_at TEXT NOT NULL
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_markets_status ON markets(status)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_markets_category ON markets(category)")

    def create_market(self, question: str, outcomes: List[str],
                      deadline: str, creator_id: str,
                      description: str = "", category: str = "general",
                      tags: List[str] = None, resolution_source: str = "",
                      fee_bps: int = None) -> Dict[str, Any]:
        """Create a new prediction market.

        Args:
            question: The question being predicted.
            outcomes: List of possible outcomes (e.g., ["YES", "NO"]).
            deadline: ISO timestamp when market closes for trading.
            creator_id: Creator's user ID.
            description: Detailed market description.
            category: Market category (crypto, sports, politics, etc.).
            tags: List of searchable tags.
            resolution_source: How this market will be resolved.
            fee_bps: Platform fee in basis points.

        Returns:
            Market details including market_id.
        """
        if not question or len(question) < 10:
            raise ValueError("Question must be at least 10 characters")
        if len(outcomes) < 2:
            raise ValueError("Must have at least 2 outcomes")
        if len(outcomes) > 10:
            raise ValueError("Maximum 10 outcomes per market")

        market_id = f"mkt_{uuid.uuid4().hex[:10]}"
        now = datetime.now(timezone.utc).isoformat()

        conn = self._get_conn()
        conn.execute("""
            INSERT INTO markets
            (market_id, question, description, outcomes, category, tags,
             creator_id, status, resolution_source, deadline, fee_bps,
             created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'DRAFT', ?, ?, ?, ?)
        """, (market_id, question, description, json_dumps(outcomes),
              category, json_dumps(tags or []), creator_id,
              resolution_source, deadline, fee_bps or self.DEFAULT_FEE_BPS, now))

        # Initialize outcome rows
        for outcome in outcomes:
            conn.execute("""
                INSERT INTO market_outcomes (market_id, outcome, current_price)
                VALUES (?, ?, ?)
            """, (market_id, outcome, round(1.0 / len(outcomes), 4)))

        self._log_event(conn, market_id, "CREATED", creator_id,
                       f"Market created with {len(outcomes)} outcomes")

        return {
            "market_id": market_id,
            "question": question,
            "outcomes": outcomes,
            "status": "DRAFT",
            "deadline": deadline,
            "category": category,
        }

    def open_market(self, market_id: str, initial_liquidity: float = None) -> Dict[str, Any]:
        """Open a market for trading.

        Args:
            market_id: Market to open.
            initial_liquidity: Optional initial liquidity requirement.

        Returns:
            Updated market state.
        """
        conn = self._get_conn()
        market = self._get_market(conn, market_id)
        if not market:
            raise ValueError(f"Market {market_id} not found")
        if market["status"] != "DRAFT":
            raise ValueError(f"Market must be in DRAFT state, got {market['status']}")

        now = datetime.now(timezone.utc).isoformat()
        conn.execute("""
            UPDATE markets SET status = 'OPEN', opened_at = ? WHERE market_id = ?
        """, (now, market_id))

        if initial_liquidity:
            conn.execute("""
                UPDATE markets SET total_liquidity = ? WHERE market_id = ?
            """, (initial_liquidity, market_id))

        self._log_event(conn, market_id, "OPENED", details=f"Liquidity: {initial_liquidity or 0}")

        return {"market_id": market_id, "status": "OPEN", "opened_at": now}

    def close_market(self, market_id: str, reason: str = "") -> Dict[str, Any]:
        """Close market for trading. Triggers resolution period."""
        conn = self._get_conn()
        market = self._get_market(conn, market_id)
        if not market:
            raise ValueError(f"Market {market_id} not found")
        if market["status"] != "OPEN":
            raise ValueError(f"Can only close OPEN markets, got {market['status']}")

        now = datetime.now(timezone.utc).isoformat()
        dispute_deadline = (
            datetime.now(timezone.utc) + timedelta(hours=self.DISPUTE_PERIOD_HOURS)
        ).isoformat()

        conn.execute("""
            UPDATE markets SET status = 'CLOSED', closed_at = ?,
            dispute_deadline = ? WHERE market_id = ?
        """, (now, dispute_deadline, market_id))

        self._log_event(conn, market_id, "CLOSED", details=reason)

        return {"market_id": market_id, "status": "CLOSED",
                "dispute_deadline": dispute_deadline}

    def resolve_market(self, market_id: str, winning_outcome: str,
                       resolver_id: str = "oracle") -> Dict[str, Any]:
        """Resolve a market with the winning outcome.

        After resolution, there's a dispute period before settlement.
        """
        conn = self._get_conn()
        market = self._get_market(conn, market_id)
        if not market:
            raise ValueError(f"Market {market_id} not found")
        if market["status"] not in ("CLOSED", "DISPUTED"):
            raise ValueError(f"Market must be CLOSED or DISPUTED, got {market['status']}")

        outcomes = json_loads(market["outcomes"])
        if winning_outcome not in outcomes:
            raise ValueError(f"Invalid outcome '{winning_outcome}'. Valid: {outcomes}")

        now = datetime.now(timezone.utc).isoformat()
        dispute_deadline = (
            datetime.now(timezone.utc) + timedelta(hours=self.DISPUTE_PERIOD_HOURS)
        ).isoformat()

        conn.execute("""
            UPDATE markets SET status = 'RESOLVED', winning_outcome = ?,
            resolved_at = ?, dispute_deadline = ? WHERE market_id = ?
        """, (winning_outcome, now, dispute_deadline, market_id))

        self._log_event(conn, market_id, "RESOLVED", resolver_id,
                       f"Winning outcome: {winning_outcome}")

        return {
            "market_id": market_id,
            "status": "RESOLVED",
            "winning_outcome": winning_outcome,
            "dispute_deadline": dispute_deadline,
        }

    def dispute_resolution(self, market_id: str, disputer_id: str,
                           evidence: str) -> Dict[str, Any]:
        """Dispute a market resolution, extending the dispute period."""
        conn = self._get_conn()
        market = self._get_market(conn, market_id)
        if not market:
            raise ValueError(f"Market {market_id} not found")
        if market["status"] != "RESOLVED":
            raise ValueError("Can only dispute RESOLVED markets")

        now = datetime.now(timezone.utc).isoformat()
        new_deadline = (
            datetime.now(timezone.utc) + timedelta(hours=self.DISPUTE_PERIOD_HOURS)
        ).isoformat()

        conn.execute("""
            UPDATE markets SET status = 'DISPUTED', dispute_deadline = ?
            WHERE market_id = ?
        """, (new_deadline, market_id))

        self._log_event(conn, market_id, "DISPUTED", disputer_id, evidence)

        return {
            "market_id": market_id,
            "status": "DISPUTED",
            "new_dispute_deadline": new_deadline,
        }

    def settle_market(self, market_id: str) -> Dict[str, Any]:
        """Settle a resolved market: calculate payouts for all positions.

        Winning shares pay out $1.00 each, losing shares pay $0.
        Platform fee is deducted from winnings.
        """
        conn = self._get_conn()
        market = self._get_market(conn, market_id)
        if not market:
            raise ValueError(f"Market {market_id} not found")
        if market["status"] != "RESOLVED":
            raise ValueError("Market must be RESOLVED before settlement")

        # Check dispute period has passed
        if market["dispute_deadline"]:
            deadline = datetime.fromisoformat(market["dispute_deadline"].replace("Z", "+00:00"))
            if datetime.now(timezone.utc) < deadline:
                raise ValueError("Dispute period has not expired")

        winning = market["winning_outcome"]
        fee_rate = market["fee_bps"] / 10000.0
        now = datetime.now(timezone.utc).isoformat()

        # Get all outstanding positions
        positions = conn.execute("""
            SELECT market_id, outcome, total_shares
            FROM market_outcomes
            WHERE market_id = ? AND total_shares > 0
        """, (market_id,)).fetchall()

        total_payout = 0
        settlement_count = 0

        for _, outcome, shares in positions:
            if shares <= 0:
                continue
            if outcome == winning:
                payout_per_share = 1.0
                gross_payout = shares * payout_per_share
                fee = gross_payout * fee_rate
                net_payout = gross_payout - fee
            else:
                payout_per_share = 0.0
                gross_payout = 0.0
                fee = 0.0
                net_payout = 0.0

            conn.execute("""
                INSERT INTO market_settlements
                (market_id, user_id, outcome, shares, payout_per_share,
                 total_payout, fee_deducted, settled_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (market_id, "pool", outcome, shares, payout_per_share,
                  net_payout, fee, now))

            total_payout += net_payout
            settlement_count += 1

        conn.execute("""
            UPDATE markets SET status = 'SETTLED', settled_at = ? WHERE market_id = ?
        """, (now, market_id))

        self._log_event(conn, market_id, "SETTLED",
                       details=f"Payout: {total_payout:.2f} USDC, {settlement_count} positions")

        return {
            "market_id": market_id,
            "status": "SETTLED",
            "winning_outcome": winning,
            "total_payout": round(total_payout, 2),
            "settlements": settlement_count,
        }

    def get_market(self, market_id: str) -> Optional[Dict[str, Any]]:
        """Get full market details."""
        conn = self._get_conn()
        return self._get_market(conn, market_id)

    def list_markets(self, status: str = None, category: str = None,
                     page: int = 1, limit: int = 20) -> Dict[str, Any]:
        """List markets with filtering and pagination."""
        conn = self._get_conn()
        conn.row_factory = sqlite3.Row
        query = "SELECT * FROM markets WHERE 1=1"
        params = []
        if status:
            query += " AND status = ?"
            params.append(status)
        if category:
            query += " AND category = ?"
            params.append(category)

        total = conn.execute(
            query.replace("SELECT *", "SELECT COUNT(*)"), params
        ).fetchone()[0]

        query += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
        params.extend([limit, (page - 1) * limit])
        rows = conn.execute(query, params).fetchall()

        return {
            "markets": [dict(r) for r in rows],
            "total": total,
            "page": page,
            "limit": limit,
            "pages": (total + limit - 1) // limit,
        }

    def search_markets(self, query: str, limit: int = 10) -> List[Dict]:
        """Search markets by question, description, or tags."""
        conn = self._get_conn()
        conn.row_factory = sqlite3.Row
        pattern = f"%{query}%"
        rows = conn.execute("""
            SELECT * FROM markets
            WHERE question LIKE ? OR description LIKE ? OR tags LIKE ?
            ORDER BY total_volume DESC LIMIT ?
        """, (pattern, pattern, pattern, limit)).fetchall()
        return [dict(r) for r in rows]

    def update_volume(self, market_id: str, amount: float, trader_id: str = None):
        """Update market volume after a trade."""
        conn = self._get_conn()
        conn.execute("""
            UPDATE markets SET
                total_volume = total_volume + ?,
                trade_count = trade_count + 1
            WHERE market_id = ?
        """, (amount, market_id))

    def get_market_stats(self, market_id: str) -> Dict[str, Any]:
        """Get comprehensive market statistics."""
        market = self.get_market(market_id)
        if not market:
            return {"error": "Market not found"}

        conn = self._get_conn()
        conn.row_factory = sqlite3.Row
        outcomes = conn.execute("""
            SELECT * FROM market_outcomes WHERE market_id = ?
        """, (market_id,)).fetchall()

        events = conn.execute("""
            SELECT * FROM market_events WHERE market_id = ?
            ORDER BY timestamp DESC LIMIT 10
        """, (market_id,)).fetchall()

        return {
            "market_id": market_id,
            "question": market["question"],
            "status": market["status"],
            "total_volume": market["total_volume"],
            "trade_count": market["trade_count"],
            "outcomes": [dict(o) for o in outcomes],
            "recent_events": [dict(e) for e in events],
        }

    def get_trending_markets(self, limit: int = 10) -> List[Dict]:
        """Get trending markets by recent volume."""
        conn = self._get_conn()
        conn.row_factory = sqlite3.Row
        rows = conn.execute("""
            SELECT market_id, question, category, total_volume,
                   trade_count, status
            FROM markets WHERE status = 'OPEN'
            ORDER BY total_volume DESC LIMIT ?
        """, (limit,)).fetchall()
        return [dict(r) for r in rows]

    def _get_market(self, conn, market_id: str) -> Optional[Dict]:
        """Get market from database."""
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM markets WHERE market_id = ?", (market_id,)
        ).fetchone()
        return dict(row) if row else None

    def _log_event(self, conn, market_id: str, event_type: str,
                   actor_id: str = None, details: str = None):
        """Log a market lifecycle event."""
        now = datetime.now(timezone.utc).isoformat()
        conn.execute("""
            INSERT INTO market_events (market_id, event_type, actor_id, details, timestamp)
            VALUES (?, ?, ?, ?, ?)
        """, (market_id, event_type, actor_id, details, now))


def json_dumps(obj):
    """Simple JSON serialization."""
    import json
    return json.dumps(obj)


def json_loads(s):
    """Simple JSON deserialization."""
    import json
    return json.loads(s)


if __name__ == "__main__":
    engine = MarketEngine(db_path=":memory:")

    print("Creating market...")
    mkt = engine.create_market(
        question="Will BTC reach $150k by end of 2026?",
        outcomes=["YES", "NO"],
        deadline="2026-12-31T23:59:59Z",
        creator_id="alice",
        category="crypto",
        description="Bitcoin price prediction"
    )
    print(f"  Market: {mkt['market_id']}")
    print(f"  Status: {mkt['status']}")

    print("\nOpening market...")
    engine.open_market(mkt["market_id"], initial_liquidity=5000)

    print("\nMarket stats:")
    stats = engine.get_market_stats(mkt["market_id"])
    print(f"  Outcomes: {[o['outcome'] for o in stats['outcomes']]}")
    print(f"  Events: {len(stats['recent_events'])}")

    print("\nFull lifecycle: close -> resolve -> settle")
    engine.close_market(mkt["market_id"])
    engine.resolve_market(mkt["market_id"], "YES")
    # Note: settle would fail in demo since dispute period hasn't expired
    print("  Market resolved: YES wins!")
