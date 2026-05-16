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
    # market_idx 0: BTC $200k — heavy 2-sided action
    {"market_idx": 0, "outcome": "YES", "amount": 100, "user": "alice"},
    {"market_idx": 0, "outcome": "YES", "amount": 50, "user": "bob"},
    {"market_idx": 0, "outcome": "NO", "amount": 75, "user": "carol"},
    {"market_idx": 0, "outcome": "YES", "amount": 60, "user": "dave"},
    {"market_idx": 0, "outcome": "NO", "amount": 40, "user": "eve"},
    # market_idx 1: ETH ETF
    {"market_idx": 1, "outcome": "YES", "amount": 200, "user": "alice"},
    {"market_idx": 1, "outcome": "YES", "amount": 80, "user": "frank"},
    {"market_idx": 1, "outcome": "NO", "amount": 30, "user": "carol"},
    # market_idx 2: GPT-6
    {"market_idx": 2, "outcome": "NO", "amount": 80, "user": "dave"},
    {"market_idx": 2, "outcome": "YES", "amount": 40, "user": "alice"},
    {"market_idx": 2, "outcome": "NO", "amount": 25, "user": "bob"},
    # market_idx 3: USDC mcap
    {"market_idx": 3, "outcome": "YES", "amount": 150, "user": "eve"},
    {"market_idx": 3, "outcome": "YES", "amount": 70, "user": "frank"},
    {"market_idx": 3, "outcome": "NO", "amount": 35, "user": "alice"},
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
    _seed_orderbook_if_empty(db_path, existing)
    _seed_oracle_if_empty(db_path, existing)
    return True


def _seed_orderbook_if_empty(db_path, existing):
    """Seed a few resting limit orders so the order book panel is non-empty."""
    from orderbook import OrderBook
    markets = sorted(existing.get("markets", []), key=lambda m: m.get("created_at", ""))
    if not markets:
        return
    samples = [
        # (market_idx, side, price, qty, outcome, user)
        (0, "BUY",  0.55, 20, "YES", "trader1"),
        (0, "BUY",  0.50, 35, "YES", "trader2"),
        (0, "BUY",  0.45, 50, "YES", "trader3"),
        (0, "SELL", 0.62, 15, "YES", "trader4"),
        (0, "SELL", 0.65, 25, "YES", "trader5"),
        (0, "SELL", 0.70, 40, "YES", "trader6"),
        (1, "BUY",  0.48, 30, "YES", "trader1"),
        (1, "BUY",  0.45, 50, "YES", "trader7"),
        (1, "SELL", 0.55, 20, "YES", "trader8"),
        (1, "SELL", 0.60, 30, "YES", "trader9"),
    ]
    # Pre-check: skip whole market if it already has resting orders.
    skip_idx = set()
    for idx in {s[0] for s in samples}:
        if idx >= len(markets):
            skip_idx.add(idx); continue
        try:
            ob = OrderBook(markets[idx]["market_id"], db_path=db_path)
            book = ob.get_order_book(depth=1)
            if book.get("bids") or book.get("asks"):
                skip_idx.add(idx)
        except Exception:
            skip_idx.add(idx)

    for idx, side, price, qty, outcome, user in samples:
        if idx in skip_idx:
            continue
        try:
            ob = OrderBook(markets[idx]["market_id"], db_path=db_path)
            ob.place_limit_order(user_id=user, side=side, price=price,
                                 quantity=qty, outcome=outcome)
        except Exception as e:
            print(f"Seed orderbook failed: {e}")


def _seed_oracle_if_empty(db_path, existing):
    """Seed one pending oracle proposal so the resolve page is non-empty."""
    from oracle import ResolutionOracle
    markets = sorted(existing.get("markets", []), key=lambda m: m.get("created_at", ""))
    if len(markets) < 2:
        return
    oracle_inst = ResolutionOracle(db_path=db_path)
    try:
        existing_pending = oracle_inst.get_pending_resolutions()
        if existing_pending:
            return
    except Exception:
        return
    # Propose YES on market 0 (BTC), with carol as proposer
    try:
        oracle_inst.propose_resolution(
            market_id=markets[0]["market_id"],
            proposed_outcome="YES",
            proposer_id="oracle_node_1",
            evidence="Coinbase + Binance spot price closed > $200,000 USD on resolution date",
            bond_amount=500,
        )
    except Exception as e:
        print(f"Seed oracle failed: {e}")


def _seed_trades_if_needed(db_path, engine, existing):
    """Backfill demo trades + portfolio entries if all markets have volume==0."""
    markets = existing.get("markets", [])
    if not markets:
        return
    if not all(m.get("total_volume", 0) == 0 for m in markets):
        return
    from portfolio import PortfolioManager
    pm = PortfolioManager(db_path=db_path)
    ordered = sorted(markets, key=lambda m: m.get("created_at", ""))
    for t in DEMO_TRADES:
        if t["market_idx"] >= len(ordered):
            continue
        mid = ordered[t["market_idx"]]["market_id"]
        pool = AMMPool(mid, db_path=db_path)
        try:
            res = pool.buy_outcome(t["outcome"], t["amount"], user_id=t["user"],
                                   max_slippage=0.15)
            try:
                pm.open_position(t["user"], mid, t["outcome"],
                                 res.get("shares_received", 0),
                                 res.get("avg_price", 0.5),
                                 fee=res.get("fee_charged", 0))
            except Exception:
                pass
            try:
                engine.update_volume(mid, t["amount"], t["user"])
            except Exception:
                pass
        except Exception as e:
            print(f"Seed trade failed for {mid}: {e}")


if __name__ == "__main__":
    seeded = seed_if_empty()
    print(f"Seeded: {seeded}")
