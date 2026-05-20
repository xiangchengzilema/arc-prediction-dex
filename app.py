#!/usr/bin/env python3
"""Flask REST API for Arc Prediction DEX.

30+ endpoints covering all trading operations:
- Market CRUD and search
- AMM trading (buy/sell)
- Order book (limit orders, market orders)
- Liquidity management
- Portfolio and P&L
- Oracle resolution
- Analytics and system stats

Run: python app.py
API: http://localhost:5003/api/
"""
import json
import os
import sys
import tempfile
from datetime import datetime, timezone

from flask import Flask, jsonify, request, render_template

from amm import AMMPool
from orderbook import OrderBook
from market_engine import MarketEngine
from liquidity_pool import LiquidityPoolManager
from oracle import ResolutionOracle
from portfolio import PortfolioManager
from analytics import MarketAnalytics
from agent import PythiaAgent
from keeper import MarketKeeper

app = Flask(__name__)

# Database path - use env var or default
DB_PATH = os.environ.get("PREDICT_DEX_DB", "prediction_dex.db")

# Initialize modules
market_engine = MarketEngine(db_path=DB_PATH)
liquidity_mgr = LiquidityPoolManager(db_path=DB_PATH)
oracle = ResolutionOracle(db_path=DB_PATH)
portfolio = PortfolioManager(db_path=DB_PATH)
analytics = MarketAnalytics(db_path=DB_PATH)

# AMM pools and OrderBooks cached per market
_amm_pools = {}
_order_books = {}


# Seed demo data on first import (idempotent — skips if data exists)
if not os.environ.get("PREDICT_DEX_SKIP_SEED"):
    try:
        from seed_demo import seed_if_empty
        seed_if_empty(db_path=DB_PATH)
    except Exception as _e:
        print(f"Seed skipped: {_e}")


# Pythia AI agent — autonomous trading loop. Started after seeding so the
# agent always has at least the demo markets to evaluate.
pythia = PythiaAgent(
    market_engine=market_engine,
    get_amm_pool=lambda mid: get_amm(mid),
    db_path=DB_PATH,
    portfolio=portfolio,
    analytics=analytics,
)
if not os.environ.get("PREDICT_DEX_SKIP_AGENT"):
    pythia.start()


# Market keeper — closes expired markets, auto-resolves CLOSED ones after the
# dispute window, and replenishes the pool of active markets so the homepage
# always feels alive. Skip with PREDICT_DEX_SKIP_KEEPER=1 in tests.
keeper = MarketKeeper(db_path=DB_PATH)
if not os.environ.get("PREDICT_DEX_SKIP_KEEPER"):
    keeper.start()


def get_amm(market_id: str) -> AMMPool:
    """Get or create AMM pool for a market."""
    if market_id not in _amm_pools:
        _amm_pools[market_id] = AMMPool(market_id, db_path=DB_PATH)
    return _amm_pools[market_id]


def get_ob(market_id: str) -> OrderBook:
    """Get or create order book for a market."""
    if market_id not in _order_books:
        _order_books[market_id] = OrderBook(market_id, db_path=DB_PATH)
    return _order_books[market_id]


# ─── Dashboard ────────────────────────────────────────────────

@app.route("/")
def dashboard():
    """Web dashboard homepage."""
    markets_data = market_engine.list_markets(limit=50)
    markets = markets_data.get("markets", [])

    # Sort: OPEN first, then CLOSED/DISPUTED, then RESOLVED, then SETTLED
    status_order = {"OPEN": 0, "CLOSED": 1, "DISPUTED": 1, "RESOLVED": 2, "SETTLED": 3, "DRAFT": 4}
    markets.sort(key=lambda m: (status_order.get(m.get("status", ""), 5), m.get("created_at", "")))

    # Stats compute on OPEN only for the hero
    open_markets = [m for m in markets if m.get("status") == "OPEN"]

    # Enrich markets with real-time AMM prices
    total_volume = 0
    total_tvl = 0
    for m in markets:
        try:
            pool = get_amm(m["market_id"])
            yes_price = pool.get_price("YES")
            m["yes_price"] = yes_price
            m["no_price"] = 1.0 - yes_price
        except Exception:
            m["yes_price"] = 0.5
            m["no_price"] = 0.5
        total_volume += m.get("total_volume") or 0
        total_tvl += m.get("total_liquidity") or 0

    stats = {
        "total_markets": markets_data.get("total", 0),
        "open_markets": len(open_markets),
        "total_volume": total_volume,
        "total_tvl": total_tvl,
    }
    agent_state = pythia.snapshot(limit=15)
    return render_template("index.html", markets=markets, stats=stats,
                           agent=agent_state)


@app.route("/market/<market_id>")
def market_detail(market_id):
    """Market detail page with trading UI."""
    market = market_engine.get_market(market_id)
    if not market:
        return render_template("index.html", markets=[], stats={
            "total_markets": 0, "open_markets": 0,
            "total_volume": 0, "total_tvl": 0,
        }, agent=pythia.snapshot(limit=5)), 404

    try:
        pool = get_amm(market_id)
        market["yes_price"] = pool.get_price("YES")
        market["no_price"] = 1.0 - market["yes_price"]
    except Exception:
        market["yes_price"] = 0.5
        market["no_price"] = 0.5

    try:
        stats = market_engine.get_market_stats(market_id) or {}
    except Exception:
        stats = {}

    return render_template("market_detail.html", market=market,
                           market_stats=stats)


@app.route("/leaderboard")
def leaderboard_page():
    """Trader leaderboard page."""
    try:
        traders = portfolio.get_leaderboard(sort_by="total_pnl", limit=50)
    except Exception:
        traders = []
    # Enrich with live unrealized P&L
    enriched = []
    for t in traders:
        uid = t.get("user_id")
        positions = portfolio.get_positions(uid, status="OPEN")
        cur_prices = {}
        for pos in positions:
            mid = pos["market_id"]
            try:
                pool = get_amm(mid)
                yes = pool.get_price("YES")
                cur_prices[f"{mid}_YES"] = yes
                cur_prices[f"{mid}_NO"] = 1.0 - yes
            except Exception:
                pass
        try:
            pnl = portfolio.calculate_pnl(uid, current_prices=cur_prices)
            t["live_pnl"] = pnl.get("total_pnl", 0)
            t["unrealized"] = pnl.get("total_unrealized_pnl", 0)
            t["realized"] = pnl.get("total_realized_pnl", 0)
            t["open_positions"] = pnl.get("open_positions", 0)
        except Exception:
            t["live_pnl"] = 0
            t["unrealized"] = 0
            t["realized"] = 0
            t["open_positions"] = 0
        enriched.append(t)
    enriched.sort(key=lambda x: x.get("live_pnl", 0), reverse=True)
    return render_template("leaderboard.html", traders=enriched,
                           agent=pythia.snapshot(limit=5))


@app.route("/resolve")
def resolve_page():
    """Oracle resolution page."""
    try:
        pending = oracle.get_pending_resolutions()
    except Exception:
        pending = []
    # Enrich pending with market question
    for p in pending:
        try:
            mkt = market_engine.get_market(p["market_id"])
            p["market_question"] = mkt.get("question", "") if mkt else ""
            p["market_status"] = mkt.get("status", "") if mkt else ""
        except Exception:
            p["market_question"] = ""
    # Recently resolved
    try:
        all_markets = market_engine.list_markets(status=None, limit=100)
        resolved = [m for m in all_markets.get("markets", [])
                    if m.get("status") in ("RESOLVED", "SETTLED")]
        resolved = resolved[:10]
    except Exception:
        resolved = []
    return render_template("resolve.html", pending=pending, resolved=resolved,
                           agent=pythia.snapshot(limit=5))


@app.route("/portfolio")
@app.route("/portfolio/<user_id>")
def portfolio_page(user_id=None):
    """User portfolio page — open positions, P&L, trade history."""
    user_id = user_id or request.args.get("user", "demo_user")
    try:
        positions = portfolio.get_positions(user_id, status="OPEN")
        for p in positions:
            try:
                pool = get_amm(p["market_id"])
                cur = pool.get_price(p["outcome"])
                p["current_price"] = cur
                p["unrealized_pnl"] = round((cur - p["avg_entry_price"]) * p["shares"], 4)
                p["market_value"] = round(cur * p["shares"], 4)
            except Exception:
                p["current_price"] = 0.5
                p["unrealized_pnl"] = 0
                p["market_value"] = 0
            try:
                mkt = market_engine.get_market(p["market_id"])
                p["market_question"] = mkt.get("question", "") if mkt else ""
            except Exception:
                p["market_question"] = ""
    except Exception:
        positions = []
    try:
        pnl = portfolio.calculate_pnl(user_id)
    except Exception:
        pnl = {"total_pnl": 0, "realized": 0, "unrealized": 0,
               "total_trades": 0, "total_volume": 0}
    try:
        trades = portfolio.get_trade_history(user_id, limit=20)
    except Exception:
        trades = []
    return render_template("portfolio.html", user_id=user_id,
                           positions=positions, pnl=pnl, trades=trades)


@app.route("/agent")
def agent_page():
    """Pythia AI agent control room — full decision log + live P&L."""
    snap = pythia.snapshot(limit=50)
    # Build per-market view: target prob, live price, edge, position
    markets_data = market_engine.list_markets(status="OPEN", limit=50)
    market_views = []
    for m in markets_data.get("markets", []):
        mid = m["market_id"]
        try:
            target = pythia._target_probability(mid)
            pool = get_amm(mid)
            cur = pool.get_price("YES")
        except Exception:
            target = 0.5
            cur = 0.5
        try:
            positions = portfolio.get_positions("pythia_agent", status="OPEN")
            pos = next((p for p in positions if p["market_id"] == mid), None)
        except Exception:
            pos = None
        market_views.append({
            "market_id": mid,
            "question": m.get("question", "")[:80],
            "target": round(target, 3),
            "current": round(cur, 3),
            "edge": round(target - cur, 3),
            "position": pos,
        })
    # Live P&L
    try:
        positions = portfolio.get_positions("pythia_agent", status="OPEN")
        prices = {}
        for p in positions:
            try:
                pp = get_amm(p["market_id"]).get_price("YES")
                prices[f"{p['market_id']}_YES"] = pp
                prices[f"{p['market_id']}_NO"] = 1.0 - pp
            except Exception:
                pass
        pnl = portfolio.calculate_pnl("pythia_agent", current_prices=prices)
        closed = [p for p in pnl.get("positions", []) if p["status"] == "CLOSED"]
        wins = sum(1 for p in closed if p["realized_pnl"] > 0)
        pnl["win_rate"] = round(wins / len(closed), 4) if closed else 0.0
        pnl["closed_count"] = len(closed)
    except Exception:
        pnl = {"total_pnl": 0, "total_realized_pnl": 0, "total_unrealized_pnl": 0,
               "open_positions": 0, "win_rate": 0, "closed_count": 0}
    return render_template("agent.html", agent=snap, markets=market_views, pnl=pnl)


# ─── Health & Stats ───────────────────────────────────────────

@app.route("/api/health")
def health():
    """Health check endpoint."""
    return jsonify({
        "status": "healthy",
        "service": "arc-prediction-dex",
        "version": "0.1.0",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })


@app.route("/api/stats")
def system_stats():
    """System-wide statistics."""
    markets = market_engine.list_markets(limit=1000)
    sys_analytics = analytics.get_system_stats()
    return jsonify({
        "total_markets": markets["total"],
        "open_markets": len([m for m in markets["markets"] if m.get("status") == "OPEN"]),
        "analytics": sys_analytics,
    })


# ─── Market Endpoints ─────────────────────────────────────────

@app.route("/api/markets", methods=["GET"])
def list_markets():
    """List markets with optional filters."""
    status = request.args.get("status", "OPEN")
    category = request.args.get("category")
    page = int(request.args.get("page", 1))
    limit = int(request.args.get("limit", 20))
    data = market_engine.list_markets(status=status, category=category,
                                       page=page, limit=limit)
    return jsonify(data)


@app.route("/api/markets", methods=["POST"])
def create_market():
    """Create a new prediction market."""
    data = request.json or {}
    try:
        result = market_engine.create_market(
            question=data["question"],
            outcomes=data["outcomes"],
            deadline=data.get("deadline", "2027-01-01T00:00:00Z"),
            creator_id=data.get("creator_id", "api_user"),
            description=data.get("description", ""),
            category=data.get("category", "general"),
            tags=data.get("tags", []),
            fee_bps=data.get("fee_bps", 100),
        )

        # Initialize AMM pool if liquidity provided
        init_liq = data.get("initial_liquidity", 0)
        if init_liq and init_liq > 0:
            pool = get_amm(result["market_id"])
            pool.initialize(init_liq, creator_id=data.get("creator_id", "api_user"))
            liquidity_mgr.create_pool(result["market_id"], init_liq,
                                       data.get("creator_id", "api_user"))

        # Open the market
        market_engine.open_market(result["market_id"], initial_liquidity=init_liq)
        return jsonify(result), 201
    except (ValueError, KeyError) as e:
        return jsonify({"error": str(e)}), 400


@app.route("/api/markets/<market_id>", methods=["GET"])
def get_market(market_id):
    """Get market details."""
    market = market_engine.get_market(market_id)
    if not market:
        return jsonify({"error": "Market not found"}), 404
    return jsonify(market)


@app.route("/api/markets/<market_id>/stats", methods=["GET"])
def market_stats(market_id):
    """Get market statistics."""
    stats = market_engine.get_market_stats(market_id)
    return jsonify(stats)


@app.route("/api/markets/<market_id>/lifecycle", methods=["POST"])
def market_lifecycle(market_id):
    """Change market lifecycle state (open, close, resolve, settle)."""
    data = request.json or {}
    action = data.get("action")

    try:
        if action == "close":
            result = market_engine.close_market(market_id, data.get("reason", ""))
        elif action == "resolve":
            result = market_engine.resolve_market(
                market_id, data["winning_outcome"], data.get("resolver_id", "oracle"))
        elif action == "settle":
            result = market_engine.settle_market(market_id)
        elif action == "dispute":
            result = market_engine.dispute_resolution(
                market_id, data["disputer_id"], data.get("evidence", ""))
        else:
            return jsonify({"error": f"Unknown action: {action}"}), 400
        return jsonify(result)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400


@app.route("/api/markets/search", methods=["GET"])
def search_markets():
    """Search markets."""
    query = request.args.get("q", "")
    if not query:
        return jsonify({"error": "Query parameter 'q' required"}), 400
    results = market_engine.search_markets(query)
    return jsonify(results)


@app.route("/api/markets/trending", methods=["GET"])
def trending_markets():
    """Get trending markets."""
    limit = int(request.args.get("limit", 10))
    results = market_engine.get_trending_markets(limit)
    return jsonify(results)


# ─── AMM Trading ──────────────────────────────────────────────

@app.route("/api/trade/amm/buy", methods=["POST"])
def amm_buy():
    """Buy shares from AMM pool."""
    data = request.json or {}
    market_id = data.get("market_id")
    outcome = data.get("outcome", "YES")
    amount = data.get("amount_usdc", 0)
    slippage = data.get("max_slippage", 0.05)
    user_id = data.get("user_id", "anonymous")

    # Guard: reject trades on markets that aren't OPEN or are past deadline.
    try:
        mkt = market_engine.get_market(market_id)
        if not mkt:
            return jsonify({"error": "Market not found"}), 404
        if mkt.get("status") != "OPEN":
            return jsonify({"error": f"Market is {mkt.get('status','closed').lower()} — trading paused"}), 400
        from datetime import datetime, timezone
        dl_raw = (mkt.get("deadline") or "").replace("Z", "+00:00")
        if dl_raw:
            try:
                dl = datetime.fromisoformat(dl_raw)
                if dl.tzinfo is None:
                    dl = dl.replace(tzinfo=timezone.utc)
                if dl <= datetime.now(timezone.utc):
                    return jsonify({"error": "Market deadline has passed"}), 400
            except ValueError:
                pass
    except Exception:
        pass  # If guard fails, fall through to original error handling.

    try:
        pool = get_amm(market_id)
        result = pool.buy_outcome(outcome, amount, max_slippage=slippage,
                                   user_id=user_id)
        # Update market volume
        market_engine.update_volume(market_id, amount, user_id)
        # Record trade in portfolio
        portfolio.open_position(user_id, market_id, outcome,
                                result["shares_received"], result["avg_price"],
                                fee=result.get("fee_charged", 0))
        # Record volume analytics
        analytics.record_volume(market_id, amount, "BUY", user_id)
        return jsonify(result), 200
    except ValueError as e:
        return jsonify({"error": str(e)}), 400


@app.route("/api/trade/amm/sell", methods=["POST"])
def amm_sell():
    """Sell shares back to AMM pool."""
    data = request.json or {}
    market_id = data.get("market_id")
    outcome = data.get("outcome", "YES")
    shares = data.get("shares", 0)
    user_id = data.get("user_id", "anonymous")

    try:
        pool = get_amm(market_id)
        result = pool.sell_outcome(outcome, shares, user_id=user_id)
        market_engine.update_volume(market_id, result["usdc_received"], user_id)
        analytics.record_volume(market_id, result["usdc_received"], "SELL", user_id)
        return jsonify(result), 200
    except ValueError as e:
        return jsonify({"error": str(e)}), 400


@app.route("/api/markets/<market_id>/price_history", methods=["GET"])
def price_history(market_id):
    """Get price history for charting."""
    hours = int(request.args.get("hours", 24))
    try:
        pool = get_amm(market_id)
        history = pool.get_price_history(hours=hours)
        # Reverse so oldest first (chart left-to-right)
        history.reverse()
        return jsonify({
            "market_id": market_id,
            "hours": hours,
            "points": history,
        })
    except Exception as e:
        return jsonify({"error": str(e), "points": []}), 200


@app.route("/api/markets/<market_id>/recent_trades", methods=["GET"])
def recent_trades(market_id):
    """Get recent trades for a market."""
    limit = int(request.args.get("limit", 20))
    try:
        pool = get_amm(market_id)
        trades = pool.get_recent_trades(limit=limit)
        return jsonify({"market_id": market_id, "trades": trades})
    except Exception as e:
        return jsonify({"error": str(e), "trades": []}), 200


@app.route("/api/trade/amm/quote", methods=["GET"])
def amm_quote():
    """Get a price quote without executing.

    Hardened: returns 400 with a friendly error instead of 500ing the
    front-end live-quote panel when callers pass a bad market_id, an
    unknown outcome, or a non-numeric amount.
    """
    market_id = request.args.get("market_id")
    outcome = request.args.get("outcome", "YES")
    try:
        amount = float(request.args.get("amount_usdc", 100))
    except (TypeError, ValueError):
        return jsonify({"error": "amount_usdc must be numeric"}), 400
    if not market_id:
        return jsonify({"error": "market_id required"}), 400
    if outcome not in ("YES", "NO"):
        return jsonify({"error": "outcome must be YES or NO"}), 400
    if amount <= 0:
        return jsonify({"error": "amount_usdc must be > 0"}), 400

    try:
        pool = get_amm(market_id)
        slip = pool.calculate_slippage(outcome, amount)
        return jsonify({
            "market_id": market_id,
            "outcome": outcome,
            "amount_usdc": amount,
            "current_price": pool.get_price(outcome),
            "slippage": slip,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 400


# ─── Order Book ───────────────────────────────────────────────

@app.route("/api/orderbook/<market_id>", methods=["GET"])
def get_orderbook_route(market_id):
    """Get order book for a market."""
    depth = int(request.args.get("depth", 20))
    book = get_ob(market_id)
    return jsonify(book.get_order_book(depth=depth))


@app.route("/api/orderbook/<market_id>/spread", methods=["GET"])
def get_spread(market_id):
    """Get bid/ask spread analysis."""
    book = get_ob(market_id)
    return jsonify(book.get_spread_analysis())


@app.route("/api/trade/orderbook/limit", methods=["POST"])
def place_limit_order():
    """Place a limit order."""
    data = request.json or {}
    book = get_ob(data.get("market_id", ""))
    try:
        result = book.place_limit_order(
            user_id=data.get("user_id", "anonymous"),
            side=data.get("side", "BUY"),
            price=float(data.get("price", 0.5)),
            quantity=float(data.get("quantity", 1)),
            outcome=data.get("outcome", "YES"),
            time_in_force=data.get("time_in_force", "GTC"),
        )
        return jsonify(result), 201
    except ValueError as e:
        return jsonify({"error": str(e)}), 400


@app.route("/api/trade/orderbook/market", methods=["POST"])
def place_market_order():
    """Place a market order."""
    data = request.json or {}
    book = get_ob(data.get("market_id", ""))
    try:
        result = book.place_market_order(
            user_id=data.get("user_id", "anonymous"),
            side=data.get("side", "BUY"),
            quantity=float(data.get("quantity", 1)),
            outcome=data.get("outcome", "YES"),
        )
        return jsonify(result), 201
    except ValueError as e:
        return jsonify({"error": str(e)}), 400


@app.route("/api/trade/orderbook/cancel", methods=["POST"])
def cancel_order():
    """Cancel an order."""
    data = request.json or {}
    book = get_ob(data.get("market_id", ""))
    try:
        result = book.cancel_order(data["order_id"], data["user_id"])
        return jsonify(result), 200
    except ValueError as e:
        return jsonify({"error": str(e)}), 400


@app.route("/api/orders/<user_id>", methods=["GET"])
def get_user_open_orders(user_id):
    """Aggregate a user's open limit orders across every market."""
    status_filter = request.args.get("status")  # OPEN/PARTIALLY_FILLED/None=both
    all_markets = market_engine.list_markets(limit=500).get("markets", [])
    rows = []
    for m in all_markets:
        mid = m["market_id"]
        try:
            book = get_ob(mid)
            orders = book.get_user_orders(user_id, status=status_filter)
        except Exception:
            orders = []
        for o in orders:
            if o.get("status") not in ("OPEN", "PARTIALLY_FILLED"):
                continue
            rows.append({
                "order_id": o.get("order_id"),
                "market_id": mid,
                "market_question": m.get("question"),
                "side": o.get("side"),
                "outcome": o.get("outcome"),
                "price": o.get("price"),
                "quantity": o.get("quantity"),
                "remaining_quantity": o.get("remaining_quantity"),
                "filled_quantity": o.get("filled_quantity", 0),
                "status": o.get("status"),
                "time_in_force": o.get("time_in_force"),
                "created_at": o.get("created_at"),
            })
    rows.sort(key=lambda r: r.get("created_at") or "", reverse=True)
    return jsonify({"user_id": user_id, "count": len(rows), "orders": rows})


@app.route("/orders")
@app.route("/orders/<user_id>")
def orders_page(user_id="alice"):
    """Open limit-orders manager — list + cancel."""
    user_id = request.args.get("user", user_id)
    all_markets = market_engine.list_markets(limit=500).get("markets", [])
    open_rows = []
    for m in all_markets:
        mid = m["market_id"]
        try:
            book = get_ob(mid)
            for o in book.get_user_orders(user_id):
                if o.get("status") not in ("OPEN", "PARTIALLY_FILLED"):
                    continue
                open_rows.append({**o,
                                   "market_id": mid,
                                   "market_question": m.get("question")})
        except Exception:
            continue
    open_rows.sort(key=lambda r: r.get("created_at") or "", reverse=True)
    return render_template("orders.html", user_id=user_id, orders=open_rows)


@app.route("/create")
def create_market_page():
    """Market creation form — calls POST /api/markets under the hood."""
    return render_template("create.html")


# ─── Liquidity ────────────────────────────────────────────────

@app.route("/api/liquidity/add", methods=["POST"])
def add_liquidity():
    """Add liquidity to a market pool."""
    data = request.json or {}
    try:
        result = liquidity_mgr.add_liquidity(
            market_id=data["market_id"],
            amount_usdc=float(data["amount_usdc"]),
            user_id=data.get("user_id", "anonymous"),
        )
        return jsonify(result), 201
    except ValueError as e:
        return jsonify({"error": str(e)}), 400


@app.route("/api/liquidity/remove", methods=["POST"])
def remove_liquidity():
    """Remove liquidity from a pool."""
    data = request.json or {}
    try:
        result = liquidity_mgr.remove_liquidity(
            market_id=data["market_id"],
            lp_tokens=float(data["lp_tokens"]),
            user_id=data.get("user_id", "anonymous"),
        )
        return jsonify(result), 200
    except ValueError as e:
        return jsonify({"error": str(e)}), 400


@app.route("/api/liquidity/pool/<market_id>", methods=["GET"])
def pool_info(market_id):
    """Get pool information."""
    return jsonify(liquidity_mgr.get_pool_info(market_id))


@app.route("/api/liquidity/positions/<user_id>", methods=["GET"])
def user_lp_positions(user_id):
    """Get user's LP positions."""
    return jsonify(liquidity_mgr.get_user_positions(user_id))


# ─── Portfolio ────────────────────────────────────────────────

@app.route("/api/portfolio/<user_id>", methods=["GET"])
def get_portfolio(user_id):
    """Get user portfolio."""
    return jsonify(portfolio.get_portfolio_value(user_id))


@app.route("/api/portfolio/<user_id>/positions", methods=["GET"])
def get_positions(user_id):
    """Get user positions."""
    status = request.args.get("status", "OPEN")
    positions = portfolio.get_positions(user_id, status=status)
    # Enrich with live AMM price + unrealized P&L
    for p in positions:
        try:
            pool = get_amm(p["market_id"])
            cur = pool.get_price(p["outcome"])
            p["current_price"] = cur
            p["unrealized_pnl"] = round((cur - p["avg_entry_price"]) * p["shares"], 4)
            p["market_value"] = round(cur * p["shares"], 4)
        except Exception:
            p["current_price"] = None
            p["unrealized_pnl"] = 0
            p["market_value"] = 0
        # Attach market question for display
        try:
            mkt = market_engine.get_market(p["market_id"])
            p["market_question"] = mkt.get("question", "")
        except Exception:
            p["market_question"] = ""
    return jsonify(positions)


@app.route("/api/portfolio/close", methods=["POST"])
def close_position_endpoint():
    """Close (sell) a position via AMM and update portfolio."""
    data = request.json or {}
    position_id = data.get("position_id")
    shares = float(data.get("shares", 0))
    if not position_id or shares <= 0:
        return jsonify({"error": "position_id and positive shares required"}), 400
    pos = portfolio.get_position(position_id)
    if not pos:
        return jsonify({"error": "position not found"}), 404
    if pos["status"] != "OPEN":
        return jsonify({"error": f"position is {pos['status']}"}), 400
    if shares > pos["shares"]:
        shares = pos["shares"]  # cap to held shares
    try:
        pool = get_amm(pos["market_id"])
        sell_res = pool.sell_outcome(pos["outcome"], shares,
                                     user_id=pos["user_id"])
        exit_price = sell_res.get("avg_price", pool.get_price(pos["outcome"]))
        fee = sell_res.get("fee_charged", 0)
        close_res = portfolio.close_position(position_id, shares, exit_price,
                                             fee=fee)
        usdc_received = sell_res.get("usdc_received", 0)
        try:
            market_engine.update_volume(pos["market_id"], usdc_received,
                                        pos["user_id"])
        except Exception:
            pass
        try:
            analytics.record_volume(pos["market_id"], usdc_received, "SELL",
                                    pos["user_id"])
        except Exception:
            pass
        return jsonify({
            "position_id": position_id,
            "shares_closed": shares,
            "exit_price": exit_price,
            "usdc_received": usdc_received,
            "realized_pnl": close_res.get("realized_pnl"),
            "remaining_shares": close_res.get("remaining_shares", 0),
            "status": "CLOSED" if close_res.get("remaining_shares", 0) <= 0.001 else "OPEN",
        }), 200
    except ValueError as e:
        return jsonify({"error": str(e)}), 400


@app.route("/api/portfolio/<user_id>/trades", methods=["GET"])
def get_trades(user_id):
    """Get user trade history."""
    limit = int(request.args.get("limit", 50))
    return jsonify(portfolio.get_trade_history(user_id, limit=limit))


@app.route("/api/portfolio/<user_id>/pnl", methods=["GET"])
def get_pnl(user_id):
    """Get user P&L. Returns zeros + error message on failure (e.g. AMM price
    lookup fails for a stale market) instead of 500ing the dashboard."""
    try:
        return jsonify(portfolio.calculate_pnl(user_id))
    except Exception as e:
        return jsonify({
            "user_id": user_id,
            "open_positions": 0,
            "closed_positions": 0,
            "total_cost": 0,
            "total_value": 0,
            "total_pnl": 0,
            "realized_pnl": 0,
            "unrealized_pnl": 0,
            "roi_pct": 0,
            "error": str(e),
        }), 200


@app.route("/api/portfolio/leaderboard", methods=["GET"])
def leaderboard():
    """Get trader leaderboard."""
    sort_by = request.args.get("sort_by", "total_pnl")
    limit = int(request.args.get("limit", 20))
    return jsonify(portfolio.get_leaderboard(sort_by=sort_by, limit=limit))


# ─── Oracle ───────────────────────────────────────────────────

@app.route("/api/oracle/propose", methods=["POST"])
def oracle_propose():
    """Propose a market resolution."""
    data = request.json or {}
    try:
        result = oracle.propose_resolution(
            market_id=data["market_id"],
            proposed_outcome=data["outcome"],
            proposer_id=data.get("proposer_id", "anonymous"),
            evidence=data.get("evidence", ""),
            bond_amount=float(data.get("bond_amount", 0)),
        )
        return jsonify(result), 201
    except ValueError as e:
        return jsonify({"error": str(e)}), 400


@app.route("/api/oracle/dispute", methods=["POST"])
def oracle_dispute():
    """Dispute a resolution proposal."""
    data = request.json or {}
    try:
        result = oracle.dispute_proposal(
            proposal_id=data["proposal_id"],
            disputer_id=data["disputer_id"],
            counter_outcome=data["counter_outcome"],
            evidence=data.get("evidence", ""),
        )
        return jsonify(result), 201
    except ValueError as e:
        return jsonify({"error": str(e)}), 400


@app.route("/api/oracle/vote", methods=["POST"])
def oracle_vote():
    """Vote on a disputed proposal."""
    data = request.json or {}
    try:
        result = oracle.vote_on_proposal(
            proposal_id=data["proposal_id"],
            voter_id=data["voter_id"],
            vote=data["vote"],
            weight=float(data.get("weight", 1.0)),
        )
        return jsonify(result), 201
    except ValueError as e:
        return jsonify({"error": str(e)}), 400


@app.route("/api/oracle/finalize/<market_id>", methods=["POST"])
def oracle_finalize(market_id):
    """Finalize a market resolution."""
    try:
        result = oracle.finalize_resolution(market_id)
        return jsonify(result), 200
    except ValueError as e:
        return jsonify({"error": str(e)}), 400


@app.route("/api/oracle/pending", methods=["GET"])
def oracle_pending():
    """Get pending resolutions."""
    return jsonify(oracle.get_pending_resolutions())


@app.route("/api/oracle/history/<market_id>", methods=["GET"])
def oracle_history(market_id):
    """Get resolution history for a market."""
    return jsonify(oracle.get_resolution_history(market_id))


# ─── Analytics ────────────────────────────────────────────────

@app.route("/api/analytics/market/<market_id>", methods=["GET"])
def analytics_market(market_id):
    """Get market analytics report."""
    report = analytics.generate_market_report(market_id)
    return jsonify(report)


@app.route("/api/analytics/volume/<market_id>", methods=["GET"])
def analytics_volume(market_id):
    """Get volume analytics."""
    hours = int(request.args.get("hours", 24))
    return jsonify(analytics.calculate_volume(market_id, hours=hours))


@app.route("/api/analytics/trending", methods=["GET"])
def analytics_trending():
    """Get trending markets."""
    limit = int(request.args.get("limit", 10))
    return jsonify(analytics.get_trending_markets(limit=limit))


@app.route("/api/analytics/system", methods=["GET"])
def analytics_system():
    """Get system-wide analytics."""
    return jsonify(analytics.get_system_stats())


# ─── Pythia AI Agent ──────────────────────────────────────────

@app.route("/api/agent/status", methods=["GET"])
def agent_status():
    """Get Pythia agent status and recent decisions."""
    limit = int(request.args.get("limit", 30))
    return jsonify(pythia.snapshot(limit=limit))


@app.route("/api/agent/pnl", methods=["GET"])
def agent_pnl():
    """Get Pythia's live P&L using current AMM prices."""
    try:
        positions = portfolio.get_positions("pythia_agent", status="OPEN")
        current_prices = {}
        for pos in positions:
            mid = pos["market_id"]
            try:
                pool = get_amm(mid)
                yes_price = pool.get_price("YES")
                current_prices[f"{mid}_YES"] = yes_price
                current_prices[f"{mid}_NO"] = 1.0 - yes_price
            except Exception:
                pass
        pnl = portfolio.calculate_pnl("pythia_agent", current_prices=current_prices)
        # Win rate from closed positions
        closed = [p for p in pnl.get("positions", []) if p["status"] == "CLOSED"]
        wins = sum(1 for p in closed if p["realized_pnl"] > 0)
        pnl["win_rate"] = round(wins / len(closed), 4) if closed else 0.0
        pnl["closed_count"] = len(closed)
        return jsonify(pnl)
    except Exception as e:
        return jsonify({
            "user_id": "pythia_agent", "total_pnl": 0,
            "total_realized_pnl": 0, "total_unrealized_pnl": 0,
            "open_positions": 0, "closed_positions": 0,
            "win_rate": 0, "error": str(e),
        })


@app.route("/api/keeper/status")
def keeper_status():
    """Market keeper snapshot — last sweep, recent close/mint events."""
    return jsonify(keeper.snapshot())


@app.errorhandler(404)
def not_found(e):
    if request.path.startswith("/api/"):
        return jsonify({"error": "Not found", "path": request.path}), 404
    return render_template("error.html", code=404,
                           message="That page doesn't exist. Maybe the market was removed, or you mistyped the URL."), 404


@app.errorhandler(500)
def server_error(e):
    if request.path.startswith("/api/"):
        return jsonify({"error": "Internal server error"}), 500
    return render_template("error.html", code=500,
                           message="Something went wrong on our side. Pythia is still trading — try again in a moment."), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT") or os.environ.get("PREDICT_DEX_PORT") or 5003)
    print(f"Starting Arc Prediction DEX API on port {port}...")
    print(f"Dashboard: http://localhost:{port}/")
    print(f"API docs:  http://localhost:{port}/api/health")
    app.run(host="0.0.0.0", port=port, debug=False)
