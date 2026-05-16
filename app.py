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
try:
    from seed_demo import seed_if_empty
    seed_if_empty(db_path=DB_PATH)
except Exception as _e:
    print(f"Seed skipped: {_e}")


def get_amm(market_id: str) -> AMMPool:
    """Get or create AMM pool for a market."""
    if market_id not in _amm_pools:
        _amm_pools[market_id] = AMMPool(market_id, db_path=DB_PATH)
    return _amm_pools[market_id]


def get_orderbook(market_id: str) -> OrderBook:
    """Get or create order book for a market."""
    if market_id not in _order_books:
        _order_books[market_id] = OrderBook(market_id, db_path=DB_PATH)
    return _order_books[market_id]


# ─── Dashboard ────────────────────────────────────────────────

@app.route("/")
def dashboard():
    """Web dashboard homepage."""
    markets_data = market_engine.list_markets(status="OPEN", limit=20)
    stats = {
        "total_markets": markets_data.get("total", 0),
        "open_markets": len(markets_data.get("markets", [])),
    }
    return render_template("index.html", markets=markets_data.get("markets", []),
                           stats=stats)


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


@app.route("/api/trade/amm/quote", methods=["GET"])
def amm_quote():
    """Get a price quote without executing."""
    market_id = request.args.get("market_id")
    outcome = request.args.get("outcome", "YES")
    amount = float(request.args.get("amount_usdc", 100))

    pool = get_amm(market_id)
    slip = pool.calculate_slippage(outcome, amount)
    return jsonify({
        "market_id": market_id,
        "outcome": outcome,
        "amount_usdc": amount,
        "current_price": pool.get_price(outcome),
        "slippage": slip,
    })


# ─── Order Book ───────────────────────────────────────────────

@app.route("/api/orderbook/<market_id>", methods=["GET"])
def get_orderbook(market_id):
    """Get order book for a market."""
    depth = int(request.args.get("depth", 20))
    book = get_orderbook(market_id)
    return jsonify(book.get_order_book(depth=depth))


@app.route("/api/orderbook/<market_id>/spread", methods=["GET"])
def get_spread(market_id):
    """Get bid/ask spread analysis."""
    book = get_orderbook(market_id)
    return jsonify(book.get_spread_analysis())


@app.route("/api/trade/orderbook/limit", methods=["POST"])
def place_limit_order():
    """Place a limit order."""
    data = request.json or {}
    book = get_orderbook(data.get("market_id", ""))
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
    book = get_orderbook(data.get("market_id", ""))
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
    book = get_orderbook(data.get("market_id", ""))
    try:
        result = book.cancel_order(data["order_id"], data["user_id"])
        return jsonify(result), 200
    except ValueError as e:
        return jsonify({"error": str(e)}), 400


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
    return jsonify(portfolio.get_positions(user_id, status=status))


@app.route("/api/portfolio/<user_id>/trades", methods=["GET"])
def get_trades(user_id):
    """Get user trade history."""
    limit = int(request.args.get("limit", 50))
    return jsonify(portfolio.get_trade_history(user_id, limit=limit))


@app.route("/api/portfolio/<user_id>/pnl", methods=["GET"])
def get_pnl(user_id):
    """Get user P&L."""
    return jsonify(portfolio.calculate_pnl(user_id))


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


if __name__ == "__main__":
    port = int(os.environ.get("PORT") or os.environ.get("PREDICT_DEX_PORT") or 5003)
    print(f"Starting Arc Prediction DEX API on port {port}...")
    print(f"Dashboard: http://localhost:{port}/")
    print(f"API docs:  http://localhost:{port}/api/health")
    app.run(host="0.0.0.0", port=port, debug=False)
