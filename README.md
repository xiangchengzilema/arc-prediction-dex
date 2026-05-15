# Arc Prediction DEX

A full-featured decentralized prediction market exchange built for the
[Arc Network](https://docs.arc.network) — Circle's stablecoin-native L1 blockchain.

## Why Arc for Prediction Markets?

| Feature | Ethereum | Arc |
|---------|----------|-----|
| Gas fee | $2-50 | ~$0.01 |
| Finality | 12+ seconds | Sub-second |
| Settlement | ETH (volatile) | USDC (stable) |
| Micropayments | Impractical | Native |
| Paymaster | Complex | Built-in |

Arc's low fees and USDC-native design make **micro-prediction markets viable**
for the first time — markets with $0.01 positions, high-frequency trading,
and instant settlement.

## Architecture

```
┌─────────────────────────────────────────────────────┐
│                   Web Dashboard                      │
├─────────────────────────────────────────────────────┤
│              REST API (30+ endpoints)                │
├──────────┬──────────┬───────────┬───────────────────┤
│   AMM    │ Order    │ Liquidity │    Oracle         │
│  Engine  │  Book    │   Pools   │  Resolution       │
│ (CPAMM)  │ (CLOB)   │  (LP)     │  (UMA-style)      │
├──────────┴──────────┴───────────┴───────────────────┤
│            Portfolio & Analytics Engine              │
├─────────────────────────────────────────────────────┤
│              SQLite Persistence Layer                │
└─────────────────────────────────────────────────────┘
```

## Core Components

### 1. AMM Engine (`amm.py`)
Constant Product AMM (x×y=k) for instant trading:
- Buy/sell outcome shares with price impact calculation
- Configurable trading fees (default 1%)
- Slippage protection with tolerance settings
- LP token minting/burning
- Price history for charting

### 2. Order Book (`orderbook.py`)
Central Limit Order Book (CLOB) for advanced trading:
- Price-time priority matching engine
- Limit orders, market orders
- Fill-or-Kill (FOK) and Immediate-or-Cancel (IOC)
- Level 2 market data (depth, spread)
- Maker (0.5%) and Taker (1.0%) fees

### 3. Market Engine (`market_engine.py`)
Full prediction market lifecycle:
- Create → Open → Trade → Close → Resolve → Settle
- Binary (YES/NO) and multi-outcome markets
- Categories, tags, and search
- Dispute period with evidence tracking

### 4. Liquidity Pools (`liquidity_pool.py`)
LP token management:
- Proportional LP token minting/burning
- Fee distribution (80% to LPs, 20% to protocol)
- Impermanent loss calculator
- Pool APR estimation

### 5. Resolution Oracle (`oracle.py`)
UMA-style optimistic oracle:
- Bonded resolution proposals
- Dispute mechanism with counter-evidence
- Reputation-weighted voting
- Multi-source data aggregation

### 6. Portfolio Manager (`portfolio.py`)
Position and P&L tracking:
- Average entry price (DCA support)
- Realized and unrealized P&L
- Trade history with full audit trail
- Leaderboard and ranking system

### 7. Analytics (`analytics.py`)
Market intelligence:
- Volume tracking (hourly, 24h, 7d)
- Price manipulation detection (wash trading, spoofing)
- Implied probability calculations
- Market depth analysis
- Trending market detection

## Quick Start

### Install

```bash
git clone https://github.com/xiangchengzilema/arc-prediction-dex.git
cd arc-prediction-dex
pip install -r requirements.txt
```

### Run API Server

```bash
python app.py
# Dashboard: http://localhost:5003/
# API: http://localhost:5003/api/health
```

### Python SDK

```python
from trading_sdk import PredictionDexSDK

sdk = PredictionDexSDK("http://localhost:5003")

# Create a market
market = sdk.create_market(
    question="Will BTC reach $200k by end of 2026?",
    outcomes=["YES", "NO"],
    deadline="2026-12-31T23:59:59Z",
    category="crypto"
)

# Buy YES shares
result = sdk.quick_trade(market["market_id"], "YES", 100)
print(f"Bought {result['shares_received']} shares at {result['avg_price']}")

# Check portfolio
portfolio = sdk.get_portfolio("alice")
print(f"Total value: ${portfolio['total_value']}")
```

### CLI

```bash
# List markets
python cli.py markets list

# Create market
python cli.py markets create "Will ETH hit $10k?" --outcomes YES,NO

# Buy shares
python cli.py trade buy btc_200k YES --amount 100

# Show portfolio
python cli.py portfolio show --user alice
```

## API Reference

### Markets
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/markets` | List markets |
| POST | `/api/markets` | Create market |
| GET | `/api/markets/:id` | Market details |
| GET | `/api/markets/search?q=` | Search markets |
| GET | `/api/markets/trending` | Trending markets |
| POST | `/api/markets/:id/lifecycle` | Change state |

### AMM Trading
| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/trade/amm/buy` | Buy from AMM |
| POST | `/api/trade/amm/sell` | Sell to AMM |
| GET | `/api/trade/amm/quote` | Price quote |

### Order Book
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/orderbook/:id` | Order book data |
| POST | `/api/trade/orderbook/limit` | Limit order |
| POST | `/api/trade/orderbook/market` | Market order |
| POST | `/api/trade/orderbook/cancel` | Cancel order |

### Liquidity
| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/liquidity/add` | Add liquidity |
| POST | `/api/liquidity/remove` | Remove liquidity |
| GET | `/api/liquidity/pool/:id` | Pool info |

### Portfolio
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/portfolio/:user` | Portfolio value |
| GET | `/api/portfolio/:user/positions` | Open positions |
| GET | `/api/portfolio/:user/pnl` | P&L breakdown |
| GET | `/api/portfolio/leaderboard` | Leaderboard |

### Oracle
| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/oracle/propose` | Propose resolution |
| POST | `/api/oracle/dispute` | Dispute proposal |
| POST | `/api/oracle/vote` | Vote on dispute |
| POST | `/api/oracle/finalize/:id` | Finalize resolution |

### Analytics
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/analytics/market/:id` | Market report |
| GET | `/api/analytics/trending` | Trending markets |
| GET | `/api/analytics/system` | System stats |

## Docker

```bash
docker-compose up -d
# API available at http://localhost:5003
```

## Testing

```bash
# Run all tests
pytest tests/ -v

# Run specific test file
pytest tests/test_amm.py -v
pytest tests/test_orderbook.py -v

# Run with coverage
pip install pytest-cov
pytest tests/ --cov=. --cov-report=html
```

## Project Structure

```
arc-prediction-dex/
├── amm.py              # AMM trading engine (CPAMM)
├── orderbook.py        # Central Limit Order Book (CLOB)
├── market_engine.py    # Market lifecycle management
├── liquidity_pool.py   # Liquidity pool & LP tokens
├── oracle.py           # Resolution oracle (UMA-style)
├── portfolio.py        # Position tracking & P&L
├── analytics.py        # Market analytics engine
├── trading_sdk.py      # Python SDK (zero dependencies)
├── app.py              # Flask REST API (30+ endpoints)
├── cli.py              # CLI trading tool
├── templates/
│   └── index.html      # Web dashboard
├── tests/
│   ├── test_amm.py     # AMM engine tests
│   ├── test_orderbook.py # Order book tests
│   ├── test_market.py  # Market & oracle tests
│   └── test_integration.py # E2E integration tests
├── Dockerfile          # Docker support
├── docker-compose.yml  # Docker orchestration
└── requirements.txt    # Python dependencies
```

## Arc Integration

This project is designed to integrate with Arc's infrastructure:

- **USDC Settlement**: All trades settle in USDC on Arc
- **Paymaster**: Gas fees paid in USDC (no ETH needed)
- **Sub-second Finality**: Instant trade confirmation
- **~$0.01 Fees**: Makes micro-prediction markets viable
- **Circle SDK**: Wallet management via Circle's APIs

## Use Cases

1. **Crypto Price Predictions** - "Will BTC hit $200k?"
2. **Event Outcomes** - "Who will win the 2028 election?"
3. **Sports Betting** - "Will Lakers win the championship?"
4. **AI Agent Markets** - "Will GPT-5 pass the bar exam?"
5. **DeFi Protocol Events** - "Will ETH merge succeed?"

## License

MIT License
