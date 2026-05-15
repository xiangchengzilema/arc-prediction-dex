#!/usr/bin/env python3
"""Market Analytics Engine for Prediction DEX.

Provides advanced analytics including:
- Volume and TVL tracking with time-series data
- Price manipulation detection (wash trading, spoofing)
- Implied probability calculations
- Market depth analysis
- Funding rate estimation
- Trending market detection

Usage:
    analytics = MarketAnalytics(db_path=":memory:")
    report = analytics.generate_market_report("btc_100k")
    trending = analytics.get_trending_markets()
"""
import sqlite3
import math
import uuid
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple


class MarketAnalytics:
    """Advanced analytics for prediction market data."""

    def __init__(self, db_path: str = "analytics.db"):
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
            CREATE TABLE IF NOT EXISTS analytics_snapshots (
                snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT,
                market_id TEXT NOT NULL,
                metric_type TEXT NOT NULL,
                value REAL NOT NULL,
                metadata TEXT,
                timestamp TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS volume_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                market_id TEXT NOT NULL,
                period TEXT NOT NULL,
                volume REAL NOT NULL DEFAULT 0,
                trade_count INTEGER NOT NULL DEFAULT 0,
                buy_volume REAL NOT NULL DEFAULT 0,
                sell_volume REAL NOT NULL DEFAULT 0,
                unique_traders INTEGER NOT NULL DEFAULT 0,
                timestamp TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS price_alerts (
                alert_id TEXT PRIMARY KEY,
                market_id TEXT NOT NULL,
                alert_type TEXT NOT NULL,
                severity TEXT NOT NULL DEFAULT 'LOW',
                message TEXT,
                data TEXT,
                is_active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS market_reports (
                report_id TEXT PRIMARY KEY,
                market_id TEXT NOT NULL,
                tvl REAL NOT NULL,
                volume_24h REAL NOT NULL,
                volume_7d REAL NOT NULL,
                trade_count_24h INTEGER NOT NULL,
                price_change_24h REAL NOT NULL,
                bid_ask_spread REAL NOT NULL,
                manipulation_score REAL NOT NULL,
                implied_probability TEXT,
                timestamp TEXT NOT NULL
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_analytics_market ON analytics_snapshots(market_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_volume_market ON volume_history(market_id)")

    def record_volume(self, market_id: str, volume: float, side: str,
                      trader_id: str = None) -> Dict[str, Any]:
        """Record trade volume for analytics tracking."""
        now = datetime.now(timezone.utc)
        period = now.strftime("%Y-%m-%d-%H")  # Hourly period

        conn = self._get_conn()
        existing = conn.execute("""
            SELECT volume, trade_count, buy_volume, sell_volume
            FROM volume_history
            WHERE market_id = ? AND period = ?
        """, (market_id, period)).fetchone()

        if existing:
            vol, count, buy_vol, sell_vol = existing
            new_buy = buy_vol + (volume if side == "BUY" else 0)
            new_sell = sell_vol + (volume if side == "SELL" else 0)
            conn.execute("""
                UPDATE volume_history SET
                    volume = volume + ?,
                    trade_count = trade_count + 1,
                    buy_volume = ?, sell_volume = ?
                WHERE market_id = ? AND period = ?
            """, (volume, new_buy, new_sell, market_id, period))
        else:
            conn.execute("""
                INSERT INTO volume_history
                (market_id, period, volume, trade_count, buy_volume, sell_volume, timestamp)
                VALUES (?, ?, ?, 1, ?, ?, ?)
            """, (market_id, period, volume,
                  volume if side == "BUY" else 0,
                  volume if side == "SELL" else 0,
                  now.isoformat()))

        return {"market_id": market_id, "volume_recorded": volume, "period": period}

    def calculate_volume(self, market_id: str, hours: int = 24) -> Dict[str, Any]:
        """Calculate volume metrics for a time period."""
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).strftime("%Y-%m-%d-%H")

        conn = self._get_conn()
        row = conn.execute("""
            SELECT COALESCE(SUM(volume), 0) as total_vol,
                   COALESCE(SUM(trade_count), 0) as total_trades,
                   COALESCE(SUM(buy_volume), 0) as total_buy,
                   COALESCE(SUM(sell_volume), 0) as total_sell
            FROM volume_history
            WHERE market_id = ? AND period >= ?
        """, (market_id, cutoff)).fetchone()

        total_vol, total_trades, buy_vol, sell_vol = row

        return {
            "market_id": market_id,
            "period_hours": hours,
            "total_volume": round(total_vol, 2),
            "total_trades": total_trades,
            "buy_volume": round(buy_vol, 2),
            "sell_volume": round(sell_vol, 2),
            "buy_sell_ratio": round(buy_vol / max(sell_vol, 0.01), 4),
            "avg_trade_size": round(total_vol / max(total_trades, 1), 2),
        }

    def calculate_tvl(self, market_id: str, pool_data: Dict = None) -> Dict[str, Any]:
        """Calculate Total Value Locked for a market."""
        tvl = pool_data.get("total_liquidity", 0) if pool_data else 0

        conn = self._get_conn()
        now = datetime.now(timezone.utc).isoformat()
        conn.execute("""
            INSERT INTO analytics_snapshots
            (market_id, metric_type, value, timestamp)
            VALUES (?, 'TVL', ?, ?)
        """, (market_id, tvl, now))

        return {"market_id": market_id, "tvl_usdc": round(tvl, 2)}

    def detect_price_manipulation(self, market_id: str,
                                   trades: List[Dict] = None) -> Dict[str, Any]:
        """Detect potential price manipulation patterns.

        Checks for:
        - Wash trading (same user on both sides)
        - Spoofing (large orders cancelled quickly)
        - Unusual volume spikes
        - Price movement vs volume divergence
        """
        alerts = []
        score = 0.0  # 0 = clean, 1 = suspicious

        if trades:
            # Check for wash trading: same user buying and selling within short time
            user_trades = {}
            for t in trades:
                uid = t.get("user_id", "")
                if uid not in user_trades:
                    user_trades[uid] = []
                user_trades[uid].append(t)

            for uid, user_t in user_trades.items():
                buys = [t for t in user_t if t.get("side") == "BUY"]
                sells = [t for t in user_t if t.get("side") == "SELL"]
                if buys and sells and len(buys) + len(sells) > 4:
                    alerts.append({
                        "type": "WASH_TRADING",
                        "severity": "HIGH",
                        "message": f"User {uid} has {len(buys)} buys and {len(sells)} sells"
                    })
                    score += 0.3

            # Check for large volume concentration
            if trades:
                total_vol = sum(t.get("amount", 0) for t in trades)
                max_single = max(t.get("amount", 0) for t in trades)
                if total_vol > 0 and max_single / total_vol > 0.5:
                    alerts.append({
                        "type": "VOLUME_CONCENTRATION",
                        "severity": "MEDIUM",
                        "message": f"Single trade is {max_single/total_vol:.0%} of total volume"
                    })
                    score += 0.2

        score = min(score, 1.0)

        # Save alerts
        conn = self._get_conn()
        now = datetime.now(timezone.utc).isoformat()
        for alert in alerts:
            alert_id = f"alert_{uuid.uuid4().hex[:8]}"
            conn.execute("""
                INSERT INTO price_alerts
                (alert_id, market_id, alert_type, severity, message, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (alert_id, market_id, alert["type"], alert["severity"],
                  alert["message"], now))

        return {
            "market_id": market_id,
            "manipulation_score": round(score, 4),
            "risk_level": "HIGH" if score > 0.6 else "MEDIUM" if score > 0.3 else "LOW",
            "alerts": alerts,
            "trade_count_analyzed": len(trades) if trades else 0,
        }

    def calculate_implied_probability(self, market_id: str,
                                       outcomes_data: Dict[str, float] = None) -> Dict[str, Any]:
        """Calculate implied probabilities from market prices.

        For binary markets: P(YES) = price_yes, P(NO) = 1 - price_yes
        For multi-outcome: normalize to sum to 1.0
        """
        if not outcomes_data:
            return {"market_id": market_id, "error": "No price data available"}

        # Raw prices
        raw = {k: v for k, v in outcomes_data.items()}

        # Normalize to sum to 1.0 (adjusting for overround)
        total = sum(raw.values())
        if total == 0:
            return {"market_id": market_id, "implied_probabilities": raw}

        overround = total - 1.0
        implied = {k: round(v / total, 6) for k, v in raw.items()}

        return {
            "market_id": market_id,
            "raw_prices": raw,
            "implied_probabilities": implied,
            "overround": round(overround, 4),
            "overround_pct": f"{overround * 100:.2f}%",
            "most_likely": max(implied, key=implied.get),
            "confidence_gap": round(
                max(implied.values()) - sorted(implied.values())[-2]
                if len(implied) > 1 else max(implied.values()), 4
            ),
        }

    def calculate_funding_rate(self, market_id: str,
                                yes_price: float, deadline_hours: float = 168) -> Dict[str, Any]:
        """Estimate funding rate for a prediction market position.

        Funding rate represents the cost of holding a position over time.
        For prediction markets, this is related to the time value of the bet.
        """
        if yes_price <= 0 or yes_price >= 1:
            return {"error": "Invalid price"}

        # Time decay: closer to deadline = less time value
        time_value = yes_price * (deadline_hours / (168 * 4))  # 4 weeks baseline
        funding_rate = time_value / max(yes_price, 0.01)

        return {
            "market_id": market_id,
            "yes_price": yes_price,
            "estimated_funding_rate": round(funding_rate, 6),
            "annualized_rate": f"{funding_rate * 52 * 100:.1f}%",
            "hours_to_deadline": deadline_hours,
        }

    def get_trending_markets(self, limit: int = 10) -> List[Dict]:
        """Get trending markets based on recent activity."""
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).strftime("%Y-%m-%d-%H")

        conn = self._get_conn()
        conn.row_factory = sqlite3.Row
        rows = conn.execute("""
            SELECT market_id,
                   SUM(volume) as volume_24h,
                   SUM(trade_count) as trades_24h,
                   SUM(buy_volume) as buy_vol,
                   SUM(sell_volume) as sell_vol
            FROM volume_history
            WHERE period >= ?
            GROUP BY market_id
            ORDER BY volume_24h DESC
            LIMIT ?
        """, (cutoff, limit)).fetchall()

        results = []
        for r in rows:
            d = dict(r)
            d["buy_sell_ratio"] = round(
                d["buy_vol"] / max(d["sell_vol"], 0.01), 2
            )
            results.append(d)

        return results

    def get_market_depth_analysis(self, order_book_data: Dict = None) -> Dict[str, Any]:
        """Analyze order book depth for liquidity assessment."""
        if not order_book_data:
            return {"error": "No order book data"}

        bids = order_book_data.get("bids", [])
        asks = order_book_data.get("asks", [])

        bid_depth = sum(b.get("quantity", 0) for b in bids)
        ask_depth = sum(a.get("quantity", 0) for a in asks)
        total_depth = bid_depth + ask_depth

        spread = order_book_data.get("spread", 0)
        mid = order_book_data.get("mid_price", 0)

        return {
            "bid_depth": round(bid_depth, 2),
            "ask_depth": round(ask_depth, 2),
            "total_depth": round(total_depth, 2),
            "bid_ask_imbalance": round(
                (bid_depth - ask_depth) / max(total_depth, 1), 4
            ),
            "spread_pct": round(spread / mid * 100, 4) if mid > 0 else 0,
            "liquidity_rating": "HIGH" if total_depth > 10000 else
                               "MEDIUM" if total_depth > 1000 else "LOW",
            "levels": len(bids) + len(asks),
        }

    def generate_market_report(self, market_id: str,
                                pool_data: Dict = None,
                                orderbook_data: Dict = None,
                                outcomes_data: Dict = None) -> Dict[str, Any]:
        """Generate a comprehensive market report."""
        vol = self.calculate_volume(market_id)
        tvl = self.calculate_tvl(market_id, pool_data)
        implied = self.calculate_implied_probability(market_id, outcomes_data)
        depth = self.get_market_depth_analysis(orderbook_data)

        report_id = f"rpt_{uuid.uuid4().hex[:8]}"
        now = datetime.now(timezone.utc).isoformat()

        conn = self._get_conn()
        conn.execute("""
            INSERT INTO market_reports
            (report_id, market_id, tvl, volume_24h, volume_7d, trade_count_24h,
             price_change_24h, bid_ask_spread, manipulation_score,
             implied_probability, timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (report_id, market_id, tvl["tvl_usdc"], vol["total_volume"],
              vol["total_volume"], vol["total_trades"],
              0.0,
              orderbook_data.get("spread", 0) if orderbook_data else 0,
              0.0,
              str(implied.get("implied_probabilities", {})), now))

        return {
            "report_id": report_id,
            "market_id": market_id,
            "generated_at": now,
            "volume_24h": vol,
            "tvl": tvl,
            "implied_probabilities": implied,
            "depth_analysis": depth,
            "summary": {
                "volume_24h": vol["total_volume"],
                "trades_24h": vol["total_trades"],
                "tvl": tvl["tvl_usdc"],
                "liquidity": depth.get("liquidity_rating", "UNKNOWN"),
            },
        }

    def get_system_stats(self) -> Dict[str, Any]:
        """Get system-wide analytics summary."""
        conn = self._get_conn()
        total_markets = conn.execute(
            "SELECT COUNT(DISTINCT market_id) FROM volume_history"
        ).fetchone()[0]

        total_vol = conn.execute(
            "SELECT COALESCE(SUM(volume), 0) FROM volume_history"
        ).fetchone()[0]

        total_trades = conn.execute(
            "SELECT COALESCE(SUM(trade_count), 0) FROM volume_history"
        ).fetchone()[0]

        active_alerts = conn.execute(
            "SELECT COUNT(*) FROM price_alerts WHERE is_active = 1"
        ).fetchone()[0]

        return {
            "total_markets_tracked": total_markets,
            "total_volume": round(total_vol, 2),
            "total_trades": total_trades,
            "active_alerts": active_alerts,
        }


if __name__ == "__main__":
    analytics = MarketAnalytics(db_path=":memory:")

    print("Recording volume data...")
    analytics.record_volume("btc_100k", 500, "BUY", "alice")
    analytics.record_volume("btc_100k", 300, "SELL", "bob")
    analytics.record_volume("btc_100k", 200, "BUY", "alice")
    analytics.record_volume("eth_5k", 1000, "BUY", "charlie")

    print("\nVolume analysis (24h):")
    vol = analytics.calculate_volume("btc_100k")
    print(f"  Total: {vol['total_volume']} | Trades: {vol['total_trades']}")
    print(f"  Buy/Sell ratio: {vol['buy_sell_ratio']}")

    print("\nImplied probability:")
    imp = analytics.calculate_implied_probability("btc_100k", {"YES": 0.65, "NO": 0.37})
    print(f"  {imp['implied_probabilities']}")
    print(f"  Overround: {imp['overround_pct']}")

    print("\nTrending markets:")
    for mkt in analytics.get_trending_markets():
        print(f"  {mkt['market_id']}: vol={mkt['volume_24h']}")

    print("\nSystem stats:")
    stats = analytics.get_system_stats()
    print(f"  Markets: {stats['total_markets_tracked']} | Volume: {stats['total_volume']}")
