<div align="center">

# Pythia

**An autonomous AI agent that trades prediction markets on Arc.**

[![Live demo](https://img.shields.io/badge/demo-live-3fb950?style=flat-square)](https://web-production-b5036.up.railway.app)
[![Python](https://img.shields.io/badge/python-3.11+-1a73e8?style=flat-square)](https://www.python.org)
[![Tests](https://img.shields.io/badge/tests-89%20passing-3fb950?style=flat-square)](#testing)
[![License](https://img.shields.io/badge/license-MIT-c9d1d9?style=flat-square)](#license)

[Live demo](https://web-production-b5036.up.railway.app) · [Architecture](#architecture) · [How Pythia decides](#how-pythia-decides) · [API](#api-reference)

</div>

---

## What this is

Pythia is a self-running trading agent that watches binary YES/NO prediction markets, forms a probabilistic belief about each outcome, and places trades on its own — sized by the Kelly Criterion, settled in USDC, with every cent of P&L visible in real time.

It runs on a complete prediction-market exchange we built from scratch:

- **AMM trading engine** (constant-product, x·y=k)
- **Central limit order book** with price-time priority matching
- **UMA-style optimistic oracle** for resolution
- **Liquidity pools** with LP token accounting
- **Portfolio + analytics** with realized/unrealized P&L

Built for the [Agora Agents Hackathon](https://agora.thecanteenapp.com/) on [Arc Network](https://docs.arc.network) — Circle's stablecoin-native L1.

## Why Arc

| | Ethereum | Arc |
|---|---|---|
| Gas fee | $2–50 | ~$0.01 |
| Finality | 12+ s | sub-second |
| Settlement | ETH (volatile) | USDC (stable) |
| Micropayments | impractical | native |
| Paymaster | complex setup | built-in |

Sub-cent fees make **per-market high-frequency rebalancing economically viable**. An agent can re-evaluate a $5 position every 30 seconds without gas eating the edge. That's the unlock.

## How Pythia decides

Every 30 seconds the agent walks through every open market and runs a five-step pipeline:

```
┌──────────────┐    ┌──────────────┐    ┌──────────────┐
│ Pull current │ →  │ Form a       │ →  │ Compute edge │
│ AMM price    │    │ target prob. │    │ = target - p │
└──────────────┘    └──────────────┘    └──────────────┘
                            │
                            ▼
                    ┌──────────────────┐
                    │ Multi-signal     │
                    │  · momentum      │
                    │  · mean-revert   │
                    │  · volume        │
                    │  · time decay    │
                    └──────────────────┘
                            │
                            ▼
                    ┌──────────────────┐    ┌──────────────┐
                    │ Kelly sizing     │ →  │ Submit AMM   │
                    │ f* = ¼ · e/(1-p) │    │ buy/sell     │
                    └──────────────────┘    └──────────────┘
```

- **Edge threshold:** ignore markets where `|target − price| < 5%` (noise floor).
- **Quarter-Kelly sizing:** prevents blowups; cap per trade at $25.
- **Cool-down:** no second trade in same market within 5 minutes.
- **Position limit:** max 5 open positions across all markets.

Every decision — BUY, HOLD, or SKIP — gets a one-line rationale and is streamed live to the [`/agent`](https://web-production-b5036.up.railway.app/agent) control room.

## Pages

| Route | What you see |
|---|---|
| [`/`](https://web-production-b5036.up.railway.app/) | Market grid with sparkline charts and live prices |
| [`/market/<id>`](https://web-production-b5036.up.railway.app/) | Trade panel (buy/sell/limit), Level 2 order book, price chart, recent trades |
| `/portfolio` | Open positions with live unrealized P&L · one-click close |
| `/orders` | Resting limit orders across all markets · cancel button |
| `/create` | Spin up a new binary market with AMM seed liquidity |
| `/agent` | Pythia control room — beliefs vs market, edge, decision log |
| `/leaderboard` | Trader ranking by total live P&L |
| `/resolve` | Optimistic oracle proposals + dispute window |

## Architecture

```
                  ┌────────────────────────────┐
                  │     Pythia AI Agent        │
                  │  (autonomous loop, 30s)    │
                  └─────────────┬──────────────┘
                                │ buy / sell / hold
                                ▼
┌──────────────────────────────────────────────────────────┐
│                   Web Dashboard (Flask)                   │
├──────────────────────────────────────────────────────────┤
│              REST API   ·   30+ endpoints                 │
├──────────┬──────────┬──────────┬──────────┬───────────────┤
│   AMM    │  CLOB    │ Liquidity│  Oracle  │   Portfolio   │
│ (CPAMM)  │ matching │   pools  │  (UMA)   │   + P&L       │
├──────────┴──────────┴──────────┴──────────┴───────────────┤
│                  Analytics engine                         │
├──────────────────────────────────────────────────────────┤
│              SQLite (WAL, multi-thread)                   │
└──────────────────────────────────────────────────────────┘
```

## Core modules

| File | What it does |
|---|---|
| [`agent.py`](agent.py) | Autonomous trading agent — belief formation, Kelly sizing, decision log |
| [`amm.py`](amm.py) | Constant-product AMM with slippage protection and LP tokens |
| [`orderbook.py`](orderbook.py) | CLOB with price-time priority, FOK/IOC, Level 2 depth |
| [`market_engine.py`](market_engine.py) | Market lifecycle: create → open → trade → close → resolve → settle |
| [`liquidity_pool.py`](liquidity_pool.py) | LP token mint/burn, fee distribution, IL calculator |
| [`oracle.py`](oracle.py) | UMA-style optimistic resolution with bonded proposals + disputes |
| [`portfolio.py`](portfolio.py) | Position tracking, realized/unrealized P&L, DCA, leaderboard |
| [`analytics.py`](analytics.py) | Volume, manipulation detection, implied probabilities |
| [`keeper.py`](keeper.py) | Background lifecycle keeper — auto-close at deadline, auto-resolve after dispute window, replenish from a 20-market pool |
| [`trading_sdk.py`](trading_sdk.py) | Zero-dependency Python SDK |
| [`app.py`](app.py) | Flask app — 8 dashboard routes + 30+ JSON endpoints |
| [`cli.py`](cli.py) | Command-line trading tool |

## Quick start

```bash
git clone https://github.com/xiangchengzilema/arc-prediction-dex.git
cd arc-prediction-dex
pip install -r requirements.txt

# Seed a few demo markets + positions
python seed_demo.py

# Run the dashboard + API + agent loop
python app.py
# → open http://localhost:5003
```

## Python SDK

```python
from trading_sdk import PredictionDexSDK

sdk = PredictionDexSDK("http://localhost:5003")

market = sdk.create_market(
    question="Will BTC reach $200k by end of 2026?",
    outcomes=["YES", "NO"],
    deadline="2026-12-31T23:59:59Z",
    category="crypto",
)

result = sdk.quick_trade(market["market_id"], "YES", 100)
print(f"Bought {result['shares_received']} shares at ${result['avg_price']:.4f}")

portfolio = sdk.get_portfolio("alice")
print(f"Live P&L: ${portfolio['total_pnl']:+.2f}")
```

## CLI

```bash
python cli.py markets list
python cli.py markets create "Will ETH hit $10k?" --outcomes YES,NO
python cli.py trade buy mkt_abc123 YES --amount 100
python cli.py portfolio show --user alice
```

## API reference

### Markets
| Method | Endpoint |
|---|---|
| `GET` | `/api/markets` — list (filter by `status`, `category`) |
| `POST` | `/api/markets` — create + seed AMM + auto-open |
| `GET` | `/api/markets/:id` — details |
| `GET` | `/api/markets/search?q=` — full-text search |
| `GET` | `/api/markets/trending` — by 24h volume |

### AMM
| Method | Endpoint |
|---|---|
| `POST` | `/api/trade/amm/buy` |
| `POST` | `/api/trade/amm/sell` |
| `GET` | `/api/trade/amm/quote` |

### Order book
| Method | Endpoint |
|---|---|
| `GET` | `/api/orderbook/:id` — Level 2 bids + asks |
| `POST` | `/api/trade/orderbook/limit` |
| `POST` | `/api/trade/orderbook/market` |
| `POST` | `/api/trade/orderbook/cancel` |
| `GET` | `/api/orders/:user` — all resting orders for a user |

### Portfolio
| Method | Endpoint |
|---|---|
| `GET` | `/api/portfolio/:user` — value summary |
| `GET` | `/api/portfolio/:user/positions` — with live unrealized P&L |
| `GET` | `/api/portfolio/:user/pnl` — realized vs unrealized breakdown |
| `POST` | `/api/portfolio/close` — close a position at current AMM price |
| `GET` | `/api/portfolio/leaderboard` |

### Oracle
| Method | Endpoint |
|---|---|
| `POST` | `/api/oracle/propose` |
| `POST` | `/api/oracle/dispute` |
| `POST` | `/api/oracle/vote` |
| `POST` | `/api/oracle/finalize/:id` |

### Pythia agent
| Method | Endpoint |
|---|---|
| `GET` | `/api/pythia/snapshot` — recent decisions |
| `GET` | `/api/pythia/pnl` — agent's live P&L |

## Testing

```bash
pytest tests/ -v
# 80 tests, all passing
```

Coverage spans AMM math, order matching, market lifecycle, oracle disputes, and end-to-end integration flows.

## Arc integration

- **USDC settlement** — every trade clears in USDC on Arc
- **Paymaster** — gas paid in USDC, no ETH balance needed
- **Sub-second finality** — instant fills feel like a centralized book
- **~$0.01 fees** — micro-positions are economically viable
- **Circle SDK** — wallet management hooks ready

## Project structure

```
arc-prediction-dex/
├── pythia_agent.py      ← AI trading agent
├── amm.py               ← CPAMM trading engine
├── orderbook.py         ← Limit order book
├── market_engine.py     ← Market lifecycle
├── liquidity_pool.py    ← LP token management
├── oracle.py            ← UMA-style oracle
├── portfolio.py         ← Position + P&L tracking
├── analytics.py         ← Market intelligence
├── trading_sdk.py       ← Python SDK
├── app.py               ← Flask app + API
├── cli.py               ← CLI tool
├── seed_demo.py         ← Demo data seeder
├── templates/           ← 8 dashboard pages
│   ├── index.html
│   ├── market_detail.html
│   ├── portfolio.html
│   ├── orders.html
│   ├── create.html
│   ├── agent.html
│   ├── leaderboard.html
│   └── resolve.html
├── tests/               ← 80 tests
├── Dockerfile
└── requirements.txt
```

## License

MIT
