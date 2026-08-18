# Changelog

All notable changes to the Polymarket AI Trading Bot project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

Live Conservative 口径与逐条改动（中文）见 [`docs/CHANGELOG.md`](docs/CHANGELOG.md)。PnL 台账见 [`docs/NET_PNL.md`](docs/NET_PNL.md)。

### 2026-08-18 — Large-capital policy (`2e16627`)
- Shared `capital_policy.py`: take 25% of top-2 asks, NAV-tier caps, max 6 entries/day, correlation clusters
- Completeness sized by book depth (removed hard 20-share cap)
- Redeem resolved inventory; proxy/deposit wallets log `redeem_needed` (UI/Relayer)

### 2026-08-18 — Conservative sizing (`2ddaede`)
- NAV = cash + MTM; FOK the measured NO ask; no time-to-expiry edge boost
- Token-cache flush once per cycle; Gamma parent events overwritten with tags
- Reused CLOB/Gamma clients across loop cycles; orderbook semaphore(8)

### 2026-08-17 — Conservative live path (`9e98bd9`, `f234d57`, `6a6ab04`)
- Live bot runs Safe Compounder + completeness arb instead of AI directional
- Quiet expected CLOB noise; v2 `OrderPayload` cancels
- Skip sports props; raise gates; archive no-book zombies (legacy AI path)

### Migration to Polymarket
- **New** `src/clients/polymarket_client.py` — async wrapper over `py-clob-client` mirroring the legacy exchange interface (1:1 method signatures so existing strategies port without changes)
- **New** `src/clients/gamma_client.py` — Polymarket Gamma API for market discovery (separate from CLOB; uses `/events` so category tags survive)
- **New** `src/clients/__init__.py` factory `build_polymarket_clients()` — async context manager wiring CLOB + Gamma together
- **New** `scripts/set_allowances.py` — idempotent USDC + ERC1155 allowance provisioning, dry-run by default
- **New** `scripts/dry_run_smoke.py` — end-to-end pipeline check against live Gamma+CLOB without sending an order
- **New** `tests/test_polymarket_client.py` (31 unit tests, all passing) and `tests/test_safe_compounder.py` (mocked end-to-end)
- **New** persistent token-id cache at `data/token_cache.json` to skip Gamma round-trip on cold-start orders
- **New** `cli.py health --full` flag — gates the slow EIP-712 signing check so routine health checks don't burn rate-limit
- **New** Polygon-wallet address shown in Streamlit dashboard sidebar with PolygonScan link

### Changed
- `cli.py` health/status/close-all all rewritten for Polymarket: USDC.e on Polygon, condition_id instead of ticker, dollars instead of cents, Polymarket order-book shape
- `src/jobs/ingest.py` — discovery via Gamma; YES/NO token_ids + `neg_risk` + `tick_size` registered on the client at ingest time so order placement routes correctly without re-fetch
- `src/jobs/{decide,execute,track,trade}.py` — switched from legacy exchange client to `PolymarketClient`
- `src/strategies/safe_compounder.py` — KX-prefix skiplist replaced with Polymarket tag exclusion via `GammaClient.resolve_tag_slugs`; orderbook + place_order calls now go through Polymarket adapter
- `place_order` now passes `PartialCreateOrderOptions(tick_size, neg_risk)` to the SDK so neg-risk markets route to the right CTF exchange
- `get_balance` includes mark-to-market on open positions in the `portfolio_value` field (was always 0)
- `position_limits.py` and `cash_reserves.py` — fixed pre-existing bug reading `'positions'` instead of `'market_positions'` (silently disabled the entire position-value calculation)
- `env.template` rewritten with `POLYMARKET_*` keys and a `DRY_RUN=true` default
- `requirements.txt` / `pyproject.toml` — added `py-clob-client`, `web3`, `eth-account`; dropped unused `xai_sdk`
- Streamlit dashboard reads `current_price` directly from the data API (1 round-trip instead of N per-position market fetches)
- Allowance threshold lowered from 10M USDC to 10k (still much higher than typical bot capital, far less misleading)

### Removed
- `src/clients/kalshi_client.py` and `src/clients/kalshi_ws.py` archived to `*.legacy.bak`
- All authenticated calls to the legacy exchange API
- Hardcoded "Kalshi" mentions across docstrings, comments, prompts, dashboards, and READMEs

### Fixed
- 4 mismatches between `polymarket_client.py` assumptions and the actual `py-clob-client` SDK surface (BUY/SELL import path, `OpenOrderParams`/`TradeParams` wrappers, `get_positions` not in SDK — now uses Polymarket Data API directly)
- Indentation bug in `src/jobs/performance_analyzer.py` lines 137-165 that made the module fail to import
- `tests/conftest.py` now auto-skips test modules whose hard deps aren't installed instead of erroring out the entire collection
- Token-id cache isolation in tests via session-level fixture so test runs don't pollute the working tree

## [1.0.0-pre-migration]

### Added
- Initial public release of Polymarket AI Trading Bot
- Multi-agent AI decision engine with Forecaster, Critic, and Trader agents
- Real-time market scanning and analysis
- Portfolio optimization using Kelly Criterion and risk parity
- Live trading integration with Polymarket CLOB
- Web-based dashboard for monitoring and control
- Performance analytics and reporting
- Market making strategy implementation
- Dynamic exit strategies
- Cost optimization for AI usage
- Comprehensive test suite
- Database management with SQLite support
- Configuration management system
- Logging and monitoring capabilities

### Features
- **Beast Mode Trading**: Aggressive multi-strategy trading system
- **Grok-4 Integration**: Primary AI model for market analysis
- **Real-time Dashboard**: Web interface for monitoring and control
- **Portfolio Management**: Advanced position sizing and risk management
- **Market Making**: Automated spread trading and liquidity provision
- **Performance Tracking**: Comprehensive analytics and reporting

### Technical
- Python 3.12+ compatibility
- Async/await architecture for high performance
- Type hints throughout the codebase
- Comprehensive error handling
- Rate limiting and API management
- Modular design for easy extension

## [1.0.0] - 2024-01-XX

### Added
- Initial release
- Core trading system with AI integration
- Multi-agent decision making
- Portfolio optimization
- Real-time market analysis
- Web dashboard
- Performance monitoring
- Database management
- Configuration system
- Testing framework

---

## Version History

### Version 1.0.0
- **Release Date**: January 2024
- **Status**: Initial public release
- **Key Features**: 
  - Multi-agent AI trading system
  - Real-time market analysis
  - Portfolio optimization
  - Web dashboard
  - Performance tracking

---

## Migration Guide

### From Development to Production
1. Set up environment variables in `.env` file
2. Initialize database with `python init_database.py`
3. Configure trading parameters in `src/config/settings.py`
4. Test with paper trading before live trading
5. Monitor performance and adjust settings as needed

---

## Deprecation Notices

No deprecations in current version.

---

## Breaking Changes

No breaking changes in current version.

---

## Known Issues

- Limited to SQLite database (PostgreSQL support planned)
- Requires manual API key management
- Performance may vary based on market conditions

---

## Future Roadmap

### Planned Features
- PostgreSQL database support
- Additional AI models
- Advanced risk management
- Mobile dashboard
- API rate limit optimization
- Enhanced backtesting capabilities

### Version 1.1.0 (Planned)
- Database migration tools
- Enhanced error handling
- Performance optimizations
- Additional trading strategies

### Version 1.2.0 (Planned)
- PostgreSQL support
- Advanced analytics
- Mobile interface
- API improvements 