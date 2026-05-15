# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2025-05-15

### Added
- AMM Engine: Constant Product AMM (x*y=k) with slippage protection
- Order Book: CLOB with price-time priority matching, FOK/IOC support
- Market Engine: Full lifecycle (create → open → close → resolve → settle)
- Liquidity Pools: LP token management with fee distribution
- Resolution Oracle: UMA-style optimistic oracle with bonded proposals
- Portfolio Manager: Position tracking, P&L, DCA, leaderboard
- Market Analytics: Volume tracking, manipulation detection, implied probability
- Python SDK: Zero-dependency trading client with 20+ methods
- REST API: 30+ endpoints covering all trading operations
- Web Dashboard: Dark-themed responsive UI with market table
- CLI Tool: Full command-line trading interface
- Docker support with health checks and persistent volumes
- CI/CD with Python 3.10/3.11/3.12 matrix testing
- 80 unit and integration tests
