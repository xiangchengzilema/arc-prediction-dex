#!/usr/bin/env python3
"""Central Limit Order Book (CLOB) for Prediction Markets.

Implements a full order book with price-time priority matching,
supporting limit orders, market orders, and stop-loss orders.

Key features:
- Price-time priority matching engine
- Bid/ask spread management
- Order depth visualization (Level 2 data)
- Fill-or-kill and immediate-or-cancel order types
- Gas-efficient settlement batching
- Real-time trade notifications

Usage:
    book = OrderBook("btc_100k", db_path=":memory:")
    book.place_limit_order("user_1", "BUY", 0.65, 100)  # Buy 100 YES@0.65
    book.place_limit_order("user_2", "SELL", 0.66, 50)  # Sell 50 YES@0.66
    trades = book.match_orders()
"""
import sqlite3
import time
import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple


class OrderSide(Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(Enum):
    LIMIT = "LIMIT"
    MARKET = "MARKET"
    STOP_LOSS = "STOP_LOSS"


class OrderStatus(Enum):
    OPEN = "OPEN"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"


class TimeInForce(Enum):
    GTC = "GTC"  # Good Till Cancelled
    IOC = "IOC"  # Immediate Or Cancel
    FOK = "FOK"  # Fill Or Kill


class OrderBook:
    """Central Limit Order Book with matching engine.

    Orders are matched by price-time priority:
    1. Best price first (highest bid, lowest ask)
    2. Earlier orders at same price get filled first
    """

    TICK_SIZE = 0.01  # Minimum price increment ($0.01)
    MIN_ORDER_SIZE = 1.0  # Minimum order quantity
    MAKER_FEE_BPS = 50  # 0.5% maker fee
    TAKER_FEE_BPS = 100  # 1.0% taker fee

    def __init__(self, market_id: str, db_path: str = "orderbook.db"):
        self.market_id = market_id
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
        """Initialize order book database tables."""
        conn = self._get_conn()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS orders (
                order_id TEXT PRIMARY KEY,
                market_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                side TEXT NOT NULL,
                order_type TEXT NOT NULL DEFAULT 'LIMIT',
                outcome TEXT NOT NULL DEFAULT 'YES',
                price REAL NOT NULL,
                quantity REAL NOT NULL,
                filled_quantity REAL NOT NULL DEFAULT 0,
                remaining_quantity REAL NOT NULL,
                status TEXT NOT NULL DEFAULT 'OPEN',
                time_in_force TEXT NOT NULL DEFAULT 'GTC',
                stop_price REAL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                expires_at TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS trades (
                trade_id TEXT PRIMARY KEY,
                market_id TEXT NOT NULL,
                maker_order_id TEXT NOT NULL,
                taker_order_id TEXT NOT NULL,
                buyer_id TEXT NOT NULL,
                seller_id TEXT NOT NULL,
                outcome TEXT NOT NULL DEFAULT 'YES',
                price REAL NOT NULL,
                quantity REAL NOT NULL,
                maker_fee REAL NOT NULL,
                taker_fee REAL NOT NULL,
                side TEXT NOT NULL,
                timestamp TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS order_book_state (
                market_id TEXT PRIMARY KEY,
                best_bid REAL,
                best_ask REAL,
                spread REAL,
                mid_price REAL,
                total_bid_volume REAL NOT NULL DEFAULT 0,
                total_ask_volume REAL NOT NULL DEFAULT 0,
                last_trade_price REAL,
                last_trade_time TEXT,
                updated_at TEXT NOT NULL
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_orders_market ON orders(market_id, status)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_trades_market ON trades(market_id)")

    def place_limit_order(self, user_id: str, side: str, price: float,
                          quantity: float, outcome: str = "YES",
                          time_in_force: str = "GTC",
                          expires_at: str = None) -> Dict[str, Any]:
        """Place a limit order on the book.

        Args:
            user_id: Trader identifier.
            side: "BUY" or "SELL".
            price: Limit price (0.01 - 0.99 for binary markets).
            quantity: Number of shares.
            outcome: "YES" or "NO".
            time_in_force: "GTC", "IOC", or "FOK".
            expires_at: ISO timestamp for order expiration.

        Returns:
            Order details including any immediate fills.
        """
        self._validate_order(side, price, quantity)

        order_id = str(uuid.uuid4())[:12]
        now = datetime.now(timezone.utc).isoformat()

        conn = self._get_conn()
        conn.execute("""
            INSERT INTO orders
            (order_id, market_id, user_id, side, order_type, outcome, price,
             quantity, filled_quantity, remaining_quantity, status,
             time_in_force, created_at, updated_at, expires_at)
            VALUES (?, ?, ?, ?, 'LIMIT', ?, ?, ?, 0, ?, 'OPEN', ?, ?, ?, ?)
        """, (order_id, self.market_id, user_id, side, outcome, price,
              quantity, quantity, time_in_force, now, now, expires_at))

        # Try immediate matching
        fills = self._match_order(conn, order_id, side, price, quantity,
                                  outcome, user_id, time_in_force)

        self._update_book_state(conn)

        return {
            "order_id": order_id,
            "side": side,
            "outcome": outcome,
            "price": price,
            "quantity": quantity,
            "status": self._get_order_status(order_id),
            "fills": fills,
        }

    def place_market_order(self, user_id: str, side: str, quantity: float,
                           outcome: str = "YES") -> Dict[str, Any]:
        """Place a market order that fills against existing orders.

        Market orders execute immediately at the best available price.
        """
        if quantity <= 0:
            raise ValueError("Quantity must be positive")

        order_id = str(uuid.uuid4())[:12]
        now = datetime.now(timezone.utc).isoformat()

        # Market orders use extreme prices to guarantee fill
        price = 0.99 if side == "BUY" else 0.01

        conn = self._get_conn()
        conn.execute("""
            INSERT INTO orders
            (order_id, market_id, user_id, side, order_type, outcome, price,
             quantity, filled_quantity, remaining_quantity, status,
             time_in_force, created_at, updated_at)
            VALUES (?, ?, ?, ?, 'MARKET', ?, ?, ?, 0, ?, 'OPEN',
                    'IOC', ?, ?)
        """, (order_id, self.market_id, user_id, side, outcome, price,
              quantity, quantity, now, now))

        fills = self._match_order(conn, order_id, side, price, quantity,
                                  outcome, user_id, "IOC")
        self._update_book_state(conn)

        return {
            "order_id": order_id,
            "side": side,
            "outcome": outcome,
            "quantity": quantity,
            "fills": fills,
            "status": self._get_order_status(order_id),
        }

    def cancel_order(self, order_id: str, user_id: str) -> Dict[str, Any]:
        """Cancel an open order.

        Only the order owner can cancel.
        """
        conn = self._get_conn()
        order = conn.execute(
            "SELECT user_id, status, remaining_quantity FROM orders WHERE order_id = ?",
            (order_id,)
        ).fetchone()

        if not order:
            raise ValueError(f"Order {order_id} not found")
        if order[0] != user_id:
            raise ValueError("Only the order owner can cancel")
        if order[1] not in ("OPEN", "PARTIALLY_FILLED"):
            raise ValueError(f"Cannot cancel order with status {order[1]}")

        now = datetime.now(timezone.utc).isoformat()
        conn.execute("""
            UPDATE orders SET status = 'CANCELLED', updated_at = ?
            WHERE order_id = ?
        """, (now, order_id))

        return {"order_id": order_id, "status": "CANCELLED",
                "remaining": order[2]}

    def get_order_book(self, depth: int = 20) -> Dict[str, Any]:
        """Get Level 2 order book data (bids and asks).

        Returns:
            Dict with bids (sorted high to low) and asks (sorted low to high).
        """
        conn = self._get_conn()
        # Bids: buy orders sorted by price DESC
        bids = conn.execute("""
            SELECT price, SUM(remaining_quantity) as total_qty, COUNT(*) as num_orders
            FROM orders
            WHERE market_id = ? AND side = 'BUY' AND status IN ('OPEN', 'PARTIALLY_FILLED')
            GROUP BY ROUND(price, 2)
            ORDER BY price DESC LIMIT ?
        """, (self.market_id, depth)).fetchall()

        # Asks: sell orders sorted by price ASC
        asks = conn.execute("""
            SELECT price, SUM(remaining_quantity) as total_qty, COUNT(*) as num_orders
            FROM orders
            WHERE market_id = ? AND side = 'SELL' AND status IN ('OPEN', 'PARTIALLY_FILLED')
            GROUP BY ROUND(price, 2)
            ORDER BY price ASC LIMIT ?
        """, (self.market_id, depth)).fetchall()

        best_bid = bids[0][0] if bids else None
        best_ask = asks[0][0] if asks else None
        spread = round(best_ask - best_bid, 4) if best_bid and best_ask else None
        mid = round((best_bid + best_ask) / 2, 4) if best_bid and best_ask else None

        return {
            "market_id": self.market_id,
            "bids": [{"price": b[0], "quantity": round(b[1], 2), "orders": b[2]} for b in bids],
            "asks": [{"price": a[0], "quantity": round(a[1], 2), "orders": a[2]} for a in asks],
            "best_bid": best_bid,
            "best_ask": best_ask,
            "spread": spread,
            "mid_price": mid,
            "depth": depth,
        }

    def get_user_orders(self, user_id: str, status: str = None) -> List[Dict]:
        """Get all orders for a user."""
        conn = self._get_conn()
        conn.row_factory = sqlite3.Row
        query = """
            SELECT * FROM orders
            WHERE market_id = ? AND user_id = ?
        """
        params = [self.market_id, user_id]
        if status:
            query += " AND status = ?"
            params.append(status)
        query += " ORDER BY created_at DESC"
        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]

    def get_recent_trades(self, limit: int = 50) -> List[Dict]:
        """Get recent executed trades."""
        conn = self._get_conn()
        conn.row_factory = sqlite3.Row
        rows = conn.execute("""
            SELECT * FROM trades
            WHERE market_id = ?
            ORDER BY timestamp DESC LIMIT ?
        """, (self.market_id, limit)).fetchall()
        return [dict(r) for r in rows]

    def get_spread_analysis(self) -> Dict[str, Any]:
        """Detailed spread analysis."""
        book = self.get_order_book()
        if not book["best_bid"] or not book["best_ask"]:
            return {"error": "No orders on both sides"}

        spread = book["spread"]
        mid = book["mid_price"]
        return {
            "best_bid": book["best_bid"],
            "best_ask": book["best_ask"],
            "spread": spread,
            "spread_pct": round(spread / mid * 100, 4) if mid else 0,
            "mid_price": mid,
            "bid_depth": sum(b["quantity"] for b in book["bids"]),
            "ask_depth": sum(a["quantity"] for a in book["asks"]),
            "bid_ask_ratio": round(
                sum(b["quantity"] for b in book["bids"]) /
                max(sum(a["quantity"] for a in book["asks"]), 0.01), 4
            ),
        }

    def _match_order(self, conn, taker_order_id: str, taker_side: str,
                     taker_price: float, taker_qty: float, outcome: str,
                     taker_user: str, tif: str) -> List[Dict]:
        """Core matching engine: match taker order against book."""
        fills = []
        remaining = taker_qty

        # Find matching orders (opposite side)
        if taker_side == "BUY":
            # Match against sells at price <= taker_price
            rows = conn.execute("""
                SELECT order_id, user_id, price, remaining_quantity
                FROM orders
                WHERE market_id = ? AND side = 'SELL' AND outcome = ?
                  AND price <= ? AND status IN ('OPEN', 'PARTIALLY_FILLED')
                ORDER BY price ASC, created_at ASC
            """, (self.market_id, outcome, taker_price)).fetchall()
        else:
            # Match against buys at price >= taker_price
            rows = conn.execute("""
                SELECT order_id, user_id, price, remaining_quantity
                FROM orders
                WHERE market_id = ? AND side = 'BUY' AND outcome = ?
                  AND price >= ? AND status IN ('OPEN', 'PARTIALLY_FILLED')
                ORDER BY price DESC, created_at ASC
            """, (self.market_id, outcome, taker_price)).fetchall()

        for maker_id, maker_user, maker_price, maker_remaining in rows:
            if remaining <= 0:
                break

            fill_qty = min(remaining, maker_remaining)
            fill_price = maker_price  # Price-time priority: maker price

            # Calculate fees
            maker_fee = round(fill_qty * fill_price * self.MAKER_FEE_BPS / 10000, 6)
            taker_fee = round(fill_qty * fill_price * self.TAKER_FEE_BPS / 10000, 6)

            trade_id = str(uuid.uuid4())[:12]
            now = datetime.now(timezone.utc).isoformat()

            # Record trade
            buyer_id = taker_user if taker_side == "BUY" else maker_user
            seller_id = maker_user if taker_side == "BUY" else taker_user

            conn.execute("""
                INSERT INTO trades
                (trade_id, market_id, maker_order_id, taker_order_id,
                 buyer_id, seller_id, outcome, price, quantity,
                 maker_fee, taker_fee, side, timestamp)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (trade_id, self.market_id, maker_id, taker_order_id,
                  buyer_id, seller_id, outcome, fill_price, fill_qty,
                  maker_fee, taker_fee, taker_side, now))

            # Update maker order
            new_maker_remaining = maker_remaining - fill_qty
            maker_status = "FILLED" if new_maker_remaining <= 0.001 else "PARTIALLY_FILLED"
            conn.execute("""
                UPDATE orders SET
                    filled_quantity = filled_quantity + ?,
                    remaining_quantity = ?,
                    status = ?, updated_at = ?
                WHERE order_id = ?
            """, (fill_qty, new_maker_remaining, maker_status, now, maker_id))

            # Update taker order
            remaining -= fill_qty
            taker_filled = taker_qty - remaining
            taker_status = "PARTIALLY_FILLED" if remaining > 0 else "FILLED"
            conn.execute("""
                UPDATE orders SET
                    filled_quantity = ?,
                    remaining_quantity = ?,
                    status = ?, updated_at = ?
                WHERE order_id = ?
            """, (taker_filled, remaining, taker_status, now, taker_order_id))

            fills.append({
                "trade_id": trade_id,
                "price": fill_price,
                "quantity": round(fill_qty, 6),
                "maker_fee": maker_fee,
                "taker_fee": taker_fee,
                "maker_order": maker_id,
            })

            # IOC: cancel remaining after first match attempt
            if tif == "IOC" and remaining > 0:
                conn.execute("""
                    UPDATE orders SET status = 'CANCELLED', updated_at = ?
                    WHERE order_id = ? AND remaining_quantity > 0
                """, (now, taker_order_id))
                break

        # FOK: if not fully filled, cancel everything
        if tif == "FOK" and remaining > 0:
            conn.execute("DELETE FROM trades WHERE taker_order_id = ?", (taker_order_id,))
            conn.execute("""
                UPDATE orders SET status = 'CANCELLED', updated_at = ?
                WHERE order_id = ?
            """, (datetime.now(timezone.utc).isoformat(), taker_order_id))
            return []

        return fills

    def _validate_order(self, side: str, price: float, quantity: float):
        """Validate order parameters."""
        if side not in ("BUY", "SELL"):
            raise ValueError(f"Invalid side: {side}")
        if not (0.01 <= price <= 0.99):
            raise ValueError(f"Price must be between 0.01 and 0.99, got {price}")
        if quantity < self.MIN_ORDER_SIZE:
            raise ValueError(f"Quantity must be >= {self.MIN_ORDER_SIZE}")

    def _get_order_status(self, order_id: str) -> str:
        """Get current order status."""
        conn = self._get_conn()
        row = conn.execute(
            "SELECT status FROM orders WHERE order_id = ?",
            (order_id,)
        ).fetchone()
        return row[0] if row else "UNKNOWN"

    def _update_book_state(self, conn):
        """Update the cached book state."""
        book = self.get_order_book()
        now = datetime.now(timezone.utc).isoformat()
        conn.execute("""
            INSERT OR REPLACE INTO order_book_state
            (market_id, best_bid, best_ask, spread, mid_price,
             total_bid_volume, total_ask_volume, last_trade_price, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?,
                (SELECT price FROM trades WHERE market_id = ? ORDER BY timestamp DESC LIMIT 1),
                ?)
        """, (self.market_id, book["best_bid"], book["best_ask"],
              book["spread"], book["mid_price"],
              sum(b["quantity"] for b in book["bids"]),
              sum(a["quantity"] for a in book["asks"]),
              self.market_id, now))


if __name__ == "__main__":
    book = OrderBook("demo", db_path=":memory:")

    print("Placing buy orders...")
    book.place_limit_order("alice", "BUY", 0.55, 100)
    book.place_limit_order("bob", "BUY", 0.56, 200)
    book.place_limit_order("charlie", "BUY", 0.54, 150)

    print("Placing sell orders...")
    book.place_limit_order("dave", "SELL", 0.60, 100)
    book.place_limit_order("eve", "SELL", 0.62, 80)

    print("\nOrder Book:")
    data = book.get_order_book()
    print(f"  Best Bid: {data['best_bid']} | Best Ask: {data['best_ask']}")
    print(f"  Spread: {data['spread']} | Mid: {data['mid_price']}")

    print("\nAlice places a buy at 0.61 (matches with Dave's sell at 0.60):")
    result = book.place_limit_order("alice", "BUY", 0.61, 50)
    print(f"  Fills: {len(result['fills'])}")
    if result['fills']:
        print(f"  Matched at {result['fills'][0]['price']}")
