#!/usr/bin/env python3
"""Command-Line Interface for Arc Prediction DEX.

Supports all trading operations from the terminal:
- Market creation and search
- AMM trading (buy/sell)
- Order book management
- Liquidity operations
- Portfolio and P&L queries
- Oracle resolution
- System analytics

Usage:
    python cli.py markets list
    python cli.py markets create "Will BTC hit $200k?" --outcomes YES NO
    python cli.py trade buy btc_100k YES --amount 100
    python cli.py portfolio show --user alice
    python cli.py orderbook show btc_100k
"""
import argparse
import json
import os
import sys

# Add parent dir
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from trading_sdk import PredictionDexSDK


def get_sdk():
    """Get SDK instance."""
    base_url = os.environ.get("PREDICT_DEX_URL", "http://localhost:5003")
    return PredictionDexSDK(base_url)


def print_json(data):
    """Pretty print JSON."""
    print(json.dumps(data, indent=2, default=str))


def cmd_markets_list(args):
    """List markets."""
    sdk = get_sdk()
    result = sdk.list_markets(status=args.status, category=args.category, page=args.page)
    markets = result.get("markets", [])
    if not markets:
        print("No markets found.")
        return
    print(f"\nMarkets (page {result['page']}/{result.get('pages', 1)}):")
    print("-" * 80)
    for m in markets:
        status_icon = "🟢" if m.get("status") == "OPEN" else "🔴"
        print(f"  {status_icon} {m['market_id']} | {m['question'][:60]}")
        print(f"     Category: {m.get('category', 'general')} | "
              f"Volume: ${m.get('total_volume', 0):.0f} | "
              f"Trades: {m.get('trade_count', 0)}")
    print("-" * 80)
    print(f"Total: {result['total']} markets")


def cmd_markets_create(args):
    """Create a market."""
    sdk = get_sdk()
    outcomes = args.outcomes.split(",") if "," in args.outcomes else args.outcomes.split()
    result = sdk.create_market(
        question=args.question,
        outcomes=outcomes,
        deadline=args.deadline,
        category=args.category,
        description=args.description or "",
        initial_liquidity=args.liquidity or 0,
    )
    print("Market created!")
    print_json(result)


def cmd_markets_search(args):
    """Search markets."""
    sdk = get_sdk()
    results = sdk.search_markets(args.query)
    if not results:
        print(f"No markets matching '{args.query}'")
        return
    print(f"\nSearch results for '{args.query}':")
    for m in results:
        print(f"  {m['market_id']} | {m['question'][:60]} [{m.get('status', '?')}]")


def cmd_markets_info(args):
    """Get market info."""
    sdk = get_sdk()
    result = sdk.get_market(args.market_id)
    print_json(result)


def cmd_trade_buy(args):
    """Buy outcome shares."""
    sdk = get_sdk()
    try:
        result = sdk.buy(
            market_id=args.market_id,
            outcome=args.outcome,
            amount_usdc=args.amount,
            order_type=args.type,
            price_limit=args.limit,
            max_slippage=args.slippage,
        )
        print(f"Buy order executed!")
        print(f"  Shares: {result.get('shares_received', result.get('quantity', 'N/A'))}")
        print(f"  Price: {result.get('avg_price', result.get('price', 'N/A'))}")
        print(f"  Fee: {result.get('fee_charged', 0):.4f}")
    except Exception as e:
        print(f"Error: {e}")


def cmd_trade_sell(args):
    """Sell outcome shares."""
    sdk = get_sdk()
    try:
        result = sdk.sell(
            market_id=args.market_id,
            outcome=args.outcome,
            shares=args.shares,
            order_type=args.type,
            price_limit=args.limit,
        )
        print(f"Sell order executed!")
        print(f"  USDC received: {result.get('usdc_received', 'N/A')}")
        print(f"  Fee: {result.get('fee_charged', 0):.4f}")
    except Exception as e:
        print(f"Error: {e}")


def cmd_orderbook_show(args):
    """Show order book."""
    sdk = get_sdk()
    result = sdk.get_order_book(args.market_id, depth=args.depth)

    print(f"\nOrder Book: {args.market_id}")
    print(f"  Best Bid: {result.get('best_bid', 'N/A')} | Best Ask: {result.get('best_ask', 'N/A')}")
    print(f"  Spread: {result.get('spread', 'N/A')} | Mid: {result.get('mid_price', 'N/A')}")

    bids = result.get("bids", [])
    asks = result.get("asks", [])

    if asks:
        print("\n  ASKS (sell orders):")
        for a in asks[:5]:
            print(f"    {a['price']:.2f} x {a['quantity']:.0f} ({a['orders']} orders)")

    if bids:
        print("\n  BIDS (buy orders):")
        for b in bids[:5]:
            print(f"    {b['price']:.2f} x {b['quantity']:.0f} ({b['orders']} orders)")


def cmd_liquidity_add(args):
    """Add liquidity."""
    sdk = get_sdk()
    try:
        result = sdk.add_liquidity(args.market_id, args.amount, args.user)
        print("Liquidity added!")
        print(f"  LP tokens: {result.get('lp_tokens_minted', 'N/A')}")
        print(f"  Pool share: {result.get('pool_share_pct', 'N/A')}%")
    except Exception as e:
        print(f"Error: {e}")


def cmd_liquidity_remove(args):
    """Remove liquidity."""
    sdk = get_sdk()
    try:
        result = sdk.remove_liquidity(args.market_id, args.amount, args.user)
        print("Liquidity removed!")
        print(f"  USDC returned: {result.get('usdc_returned', result.get('total_return', 'N/A'))}")
    except Exception as e:
        print(f"Error: {e}")


def cmd_portfolio_show(args):
    """Show portfolio."""
    sdk = get_sdk()
    user = args.user or "default"

    try:
        value = sdk.get_portfolio(user)
        print(f"\nPortfolio: {user}")
        print(f"  Total value: ${value.get('total_value', 0):.2f}")
        print(f"  Total P&L: ${value.get('total_pnl', 0):.2f} ({value.get('roi_pct', 0):.1f}%)")
        print(f"  Realized: ${value.get('realized_pnl', 0):.2f}")
        print(f"  Unrealized: ${value.get('unrealized_pnl', 0):.2f}")

        positions = sdk.get_positions(user)
        if positions:
            print(f"\n  Open Positions ({len(positions)}):")
            for p in positions:
                print(f"    {p['market_id']} | {p['outcome']} | "
                      f"{p['shares']} shares @ {p['avg_entry_price']:.4f}")
    except Exception as e:
        print(f"Error: {e}")


def cmd_portfolio_pnl(args):
    """Show P&L."""
    sdk = get_sdk()
    try:
        pnl = sdk.get_pnl(args.user)
        print_json(pnl)
    except Exception as e:
        print(f"Error: {e}")


def cmd_portfolio_leaderboard(args):
    """Show leaderboard."""
    sdk = get_sdk()
    try:
        board = sdk.get_leaderboard(sort_by=args.sort, limit=args.limit)
        print("\nLeaderboard:")
        print("-" * 60)
        for entry in board:
            print(f"  #{entry['rank']} {entry['user_id']} | "
                  f"PnL: ${entry['total_pnl']:.2f} | "
                  f"Win rate: {entry['win_rate']}% | "
                  f"Trades: {entry['total_trades']}")
    except Exception as e:
        print(f"Error: {e}")


def cmd_oracle_propose(args):
    """Propose resolution."""
    sdk = get_sdk()
    try:
        result = sdk.propose_resolution(args.market_id, args.outcome, args.evidence, args.bond)
        print("Resolution proposed!")
        print_json(result)
    except Exception as e:
        print(f"Error: {e}")


def cmd_analytics_trending(args):
    """Show trending markets."""
    sdk = get_sdk()
    try:
        results = sdk.get_trending(limit=args.limit)
        if not results:
            print("No trending markets.")
            return
        print("\nTrending Markets:")
        for m in results:
            print(f"  {m.get('market_id', '?')} | Vol: ${m.get('volume_24h', 0):.0f} | "
                  f"Trades: {m.get('trades_24h', 0)}")
    except Exception as e:
        print(f"Error: {e}")


def cmd_analytics_stats(args):
    """Show system stats."""
    sdk = get_sdk()
    try:
        stats = sdk.get_system_stats()
        print_json(stats)
    except Exception as e:
        print(f"Error: {e}")


def main():
    parser = argparse.ArgumentParser(
        description="Arc Prediction DEX CLI - Trade prediction markets from terminal",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", help="Command")

    # Markets
    mkts = sub.add_parser("markets", help="Market operations")
    mkt_sub = mkts.add_subparsers(dest="action")

    mkt_list = mkt_sub.add_parser("list", help="List markets")
    mkt_list.add_argument("--status", default="OPEN")
    mkt_list.add_argument("--category", default=None)
    mkt_list.add_argument("--page", type=int, default=1)
    mkt_list.set_defaults(func=cmd_markets_list)

    mkt_create = mkt_sub.add_parser("create", help="Create market")
    mkt_create.add_argument("question")
    mkt_create.add_argument("--outcomes", default="YES,NO")
    mkt_create.add_argument("--deadline", default="2027-01-01T00:00:00Z")
    mkt_create.add_argument("--category", default="general")
    mkt_create.add_argument("--description", default="")
    mkt_create.add_argument("--liquidity", type=float, default=0)
    mkt_create.set_defaults(func=cmd_markets_create)

    mkt_search = mkt_sub.add_parser("search", help="Search markets")
    mkt_search.add_argument("query")
    mkt_search.set_defaults(func=cmd_markets_search)

    mkt_info = mkt_sub.add_parser("info", help="Market details")
    mkt_info.add_argument("market_id")
    mkt_info.set_defaults(func=cmd_markets_info)

    # Trading
    trade = sub.add_parser("trade", help="Trading operations")
    tr_sub = trade.add_subparsers(dest="action")

    buy = tr_sub.add_parser("buy", help="Buy shares")
    buy.add_argument("market_id")
    buy.add_argument("outcome", choices=["YES", "NO"])
    buy.add_argument("--amount", type=float, required=True)
    buy.add_argument("--type", default="amm", choices=["amm", "limit"])
    buy.add_argument("--limit", type=float, help="Limit price for limit orders")
    buy.add_argument("--slippage", type=float, default=0.05)
    buy.set_defaults(func=cmd_trade_buy)

    sell = tr_sub.add_parser("sell", help="Sell shares")
    sell.add_argument("market_id")
    sell.add_argument("outcome", choices=["YES", "NO"])
    sell.add_argument("--shares", type=float, required=True)
    sell.add_argument("--type", default="amm", choices=["amm", "limit"])
    sell.add_argument("--limit", type=float)
    sell.set_defaults(func=cmd_trade_sell)

    # Order Book
    ob = sub.add_parser("orderbook", help="Order book operations")
    ob_sub = ob.add_subparsers(dest="action")

    ob_show = ob_sub.add_parser("show", help="Show order book")
    ob_show.add_argument("market_id")
    ob_show.add_argument("--depth", type=int, default=20)
    ob_show.set_defaults(func=cmd_orderbook_show)

    # Liquidity
    liq = sub.add_parser("liquidity", help="Liquidity operations")
    liq_sub = liq.add_subparsers(dest="action")

    liq_add = liq_sub.add_parser("add", help="Add liquidity")
    liq_add.add_argument("market_id")
    liq_add.add_argument("--amount", type=float, required=True)
    liq_add.add_argument("--user", default="cli_user")
    liq_add.set_defaults(func=cmd_liquidity_add)

    liq_rm = liq_sub.add_parser("remove", help="Remove liquidity")
    liq_rm.add_argument("market_id")
    liq_rm.add_argument("--amount", type=float, required=True)
    liq_rm.add_argument("--user", default="cli_user")
    liq_rm.set_defaults(func=cmd_liquidity_remove)

    # Portfolio
    port = sub.add_parser("portfolio", help="Portfolio operations")
    port_sub = port.add_subparsers(dest="action")

    port_show = port_sub.add_parser("show", help="Show portfolio")
    port_show.add_argument("--user", default="default")
    port_show.set_defaults(func=cmd_portfolio_show)

    port_pnl = port_sub.add_parser("pnl", help="Show P&L")
    port_pnl.add_argument("--user", default="default")
    port_pnl.set_defaults(func=cmd_portfolio_pnl)

    port_lb = port_sub.add_parser("leaderboard", help="Leaderboard")
    port_lb.add_argument("--sort", default="total_pnl")
    port_lb.add_argument("--limit", type=int, default=20)
    port_lb.set_defaults(func=cmd_portfolio_leaderboard)

    # Oracle
    orc = sub.add_parser("oracle", help="Oracle operations")
    orc_sub = orc.add_subparsers(dest="action")

    orc_prop = orc_sub.add_parser("propose", help="Propose resolution")
    orc_prop.add_argument("market_id")
    orc_prop.add_argument("outcome")
    orc_prop.add_argument("--evidence", default="")
    orc_prop.add_argument("--bond", type=float, default=0)
    orc_prop.set_defaults(func=cmd_oracle_propose)

    # Analytics
    ana = sub.add_parser("analytics", help="Analytics")
    ana_sub = ana.add_subparsers(dest="action")

    ana_trend = ana_sub.add_parser("trending", help="Trending markets")
    ana_trend.add_argument("--limit", type=int, default=10)
    ana_trend.set_defaults(func=cmd_analytics_trending)

    ana_stats = ana_sub.add_parser("stats", help="System stats")
    ana_stats.set_defaults(func=cmd_analytics_stats)

    args = parser.parse_args()
    if hasattr(args, "func"):
        args.func(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
