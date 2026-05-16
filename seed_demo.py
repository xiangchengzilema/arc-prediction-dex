#!/usr/bin/env python3
"""Seed demo data for production deployment.

Idempotent — only creates data if database is empty.
Run automatically on first request via app.py before_request hook.
"""
import os
from market_engine import MarketEngine
from amm import AMMPool
from liquidity_pool import LiquidityPoolManager

DEMO_MARKETS = [
    {
        "question": "Will BTC reach $200k by end of 2026?",
        "category": "crypto",
        "deadline": "2026-12-31T23:59:59Z",
        "initial_liquidity": 10000,
    },
    {
        "question": "Will Ethereum spot ETF approve in Q3 2026?",
        "category": "crypto",
        "deadline": "2026-09-30T23:59:59Z",
        "initial_liquidity": 8000,
    },
    {
        "question": "Will OpenAI release GPT-6 in 2026?",
        "category": "AI",
        "deadline": "2026-12-31T23:59:59Z",
        "initial_liquidity": 5000,
    },
    {
        "question": "Will USDC market cap exceed $100B by end of 2026?",
        "category": "stablecoin",
        "deadline": "2026-12-31T23:59:59Z",
        "initial_liquidity": 15000,
    },
]

DEMO_TRADES = [
    {"market_idx": 0, "outcome": "YES", "amount": 100, "user": "alice"},
    {"market_idx": 0, "outcome": "YES", "amount": 50, "user": "bob"},
    {"market_idx": 0, "outcome": "NO", "amount": 75, "user": "carol"},
    {"market_idx": 1, "outcome": "YES", "amount": 200, "user": "alice"},
    {"market_idx": 2, "outcome": "NO", "amount": 80, "user": "dave"},
    {"market_idx": 3, "outcome": "YES", "amount": 150, "user": "eve"},
]


def seed_if_empty(db_path: str = None):
    db_path = db_path or os.environ.get("PREDICT_DEX_DB", "prediction_dex.db")
    engine = MarketEngine(db_path=db_path)

    # Guard against concurrent seeding by multiple gunicorn workers using a
    # named SQLite lock table — first writer wins, others bail.
    import sqlite3, time
    conn = sqlite3.connect(db_path, timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""CREATE TABLE IF NOT EXISTS _seed_lock (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        seeded_at TEXT NOT NULL
    )""")
    conn.commit()
    cur = conn.execute("SELECT seeded_at FROM _seed_lock WHERE id = 1")
    row = cur.fetchone()
    if row:
        conn.close()
        # Another worker already seeded markets. Still try to backfill trades
        # if volume is empty (handles redeploys where DB persists but volume==0).
        existing = engine.list_markets(limit=100)
        _seed_trades_if_needed(db_path, engine, existing)
        return False

    try:
        conn.execute("INSERT INTO _seed_lock (id, seeded_at) VALUES (1, ?)",
                     (time.strftime("%Y-%m-%dT%H:%M:%SZ"),))
        conn.commit()
    except sqlite3.IntegrityError:
        conn.close()
        existing = engine.list_markets(limit=100)
        _seed_trades_if_needed(db_path, engine, existing)
        return False
    conn.close()

    # We hold the seed lock — create markets fresh.
    existing = engine.list_markets(limit=100)
    market_ids = [m["market_id"] for m in existing.get("markets", [])]
    if existing.get("total", 0) == 0:
        liquidity_mgr = LiquidityPoolManager(db_path=db_path)
        market_ids = []
        for m in DEMO_MARKETS:
            result = engine.create_market(
                question=m["question"],
                outcomes=["YES", "NO"],
                deadline=m["deadline"],
                creator_id="demo_seed",
                category=m["category"],
            )
            mid = result["market_id"]
            market_ids.append(mid)
            pool = AMMPool(mid, db_path=db_path)
            pool.initialize(m["initial_liquidity"], creator_id="demo_seed")
            liquidity_mgr.create_pool(mid, m["initial_liquidity"], "demo_seed")
            engine.open_market(mid, initial_liquidity=m["initial_liquidity"])
        existing = engine.list_markets(limit=100)

    _seed_trades_if_needed(db_path, engine, existing)
    return True


def _seed_trades_if_needed(db_path, engine, existing):
    """Backfill demo trades if all markets have volume==0."""
    markets = existing.get("markets", [])
    if not markets:
        return
    if not all(m.get("total_volume", 0) == 0 for m in markets):
        return
    ordered = sorted(markets, key=lambda m: m.get("created_at", ""))
    for t in DEMO_TRADES:
        if t["market_idx"] >= len(ordered):
            continue
        mid = ordered[t["market_idx"]]["market_id"]
        pool = AMMPool(mid, db_path=db_path)
        try:
            pool.buy_outcome(t["outcome"], t["amount"], user_id=t["user"],
                             max_slippage=0.15)
        except Exception as e:
            print(f"Seed trade failed for {mid}: {e}")


if __name__ == "__main__":
    seeded = seed_if_empty()
    print(f"Seeded: {seeded}")
