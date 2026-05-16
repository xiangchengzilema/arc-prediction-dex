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
    existing = engine.list_markets(limit=1)
    if existing.get("total", 0) > 0:
        return False  # already seeded

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

    for t in DEMO_TRADES:
        mid = market_ids[t["market_idx"]]
        pool = AMMPool(mid, db_path=db_path)
        try:
            pool.buy_outcome(t["outcome"], t["amount"], user_id=t["user"], max_slippage=0.15)
        except Exception as e:
            print(f"Seed trade failed: {e}")
    return True


if __name__ == "__main__":
    seeded = seed_if_empty()
    print(f"Seeded: {seeded}")
