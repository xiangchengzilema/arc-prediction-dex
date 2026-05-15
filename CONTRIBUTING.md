# Contributing to Arc Prediction DEX

Thank you for your interest in contributing! This project is part of the
[Arc ecosystem](https://docs.arc.network) and aims to bring decentralized
prediction markets to Arc's stablecoin-native blockchain.

## How to Contribute

### Bug Reports
1. Check existing [issues](../../issues) to avoid duplicates
2. Open a new issue with:
   - Clear description of the bug
   - Steps to reproduce
   - Expected vs actual behavior
   - Python version and OS

### Feature Requests
1. Describe the feature and why it's useful for prediction markets on Arc
2. Include any relevant research or API references
3. Tag with `enhancement` label

### Pull Requests
1. Fork the repository
2. Create a feature branch: `git checkout -b feat/your-feature`
3. Make your changes with clear, descriptive commits
4. Add tests for new functionality
5. Ensure all tests pass: `pytest tests/ -v`
6. Run linting: `flake8 *.py --max-line-length=120`
7. Submit PR with a clear description

### Development Setup

```bash
git clone https://github.com/xiangchengzilema/arc-prediction-dex.git
cd arc-prediction-dex
pip install -r requirements.txt
pip install pytest flake8

# Run tests
pytest tests/ -v

# Start API server
python app.py

# Use CLI
python cli.py markets list
```

### Code Style
- Python 3.10+ compatible
- Max line length: 120 characters
- Use type hints for function signatures
- Docstrings for all public functions and classes
- Follow PEP 8 conventions

### Architecture

```
AMM Engine (amm.py)         ← Instant trades via bonding curve
Order Book (orderbook.py)   ← Limit orders with matching engine
Market Engine (market_engine.py) ← Market lifecycle management
Liquidity Pool (liquidity_pool.py) ← LP tokens and fee distribution
Oracle (oracle.py)           ← Resolution with dispute mechanism
Portfolio (portfolio.py)     ← Position tracking and P&L
Analytics (analytics.py)     ← Volume, manipulation detection
SDK (trading_sdk.py)         ← Zero-dependency client library
API (app.py)                 ← Flask REST API (30+ endpoints)
CLI (cli.py)                 ← Command-line interface
```

### Areas We'd Love Help With
- On-chain Arc integration (Circle SDK, Paymaster)
- WebSocket real-time updates
- AMM curve improvements (concentrated liquidity)
- Frontend dashboard (React/Vue)
- Additional oracle sources
- Cross-chain bridge support
- Performance optimization for high-frequency trading

## License
By contributing, you agree that your contributions will be licensed under
the MIT License.
