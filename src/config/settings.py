"""
Configuration settings for the Polymarket trading system.
Manages trading parameters, API configurations, and risk management settings.
"""

import os
from typing import Dict, List, Optional, Sequence
from dataclasses import dataclass, field
from urllib.parse import urlparse
from dotenv import load_dotenv

# Load environment variables (.env must win over stale PM2 dumped env)
load_dotenv(override=True)


def _env_flag(name: str, default: str = "false") -> bool:
    """Parse a boolean environment variable."""
    return os.getenv(name, default).strip().lower() in ("1", "true", "yes", "on")


def resolve_live_trading_enabled() -> bool:
    """Resolve whether real orders may be sent.

    DRY_RUN takes precedence when set (including empty-check after strip):
      DRY_RUN=true  → paper (live disabled)
      DRY_RUN=false → live enabled
    Otherwise fall back to LIVE_TRADING_ENABLED (legacy).
    Default is paper / dry-run for safety.
    """
    dry_run_raw = os.getenv("DRY_RUN")
    if dry_run_raw is not None and dry_run_raw.strip() != "":
        # DRY_RUN wins over LIVE_TRADING_ENABLED
        return not _env_flag("DRY_RUN", "true")
    return _env_flag("LIVE_TRADING_ENABLED", "false")


# Official / expected hosts only. Override with POLYMARKET_ALLOW_CUSTOM_HOSTS=true
# only for deliberate private relays / local forks.
_ALLOWED_HOST_SUFFIXES: Sequence[str] = (
    "clob.polymarket.com",
    "gamma-api.polymarket.com",
    "data-api.polymarket.com",
    "polygon-rpc.com",
    # Common Polygon RPC providers
    "tenderly.co",
    "alchemy.com",
    "infura.io",
    "quiknode.pro",
    "quicknode.com",
    "ankr.com",
    "blastapi.io",
    "llamarpc.com",
    "publicnode.com",
    "chainstack.com",
    # LLM gateways
    "openrouter.ai",
    "api.openai.com",
    "moonshot.cn",
    "moonshot.ai",
    "platform.moonshot.cn",
)


def _first_nonempty_env(*names: str) -> str:
    for name in names:
        val = os.getenv(name)
        if val is not None and str(val).strip() != "":
            return str(val).strip()
    return ""


def resolve_llm_provider() -> str:
    """Return 'kimi' | 'openrouter' based on env (explicit LLM_PROVIDER wins)."""
    explicit = os.getenv("LLM_PROVIDER", "").strip().lower()
    if explicit in ("kimi", "moonshot", "moonshot-cn"):
        return "kimi"
    if explicit in ("openrouter", "or"):
        return "openrouter"
    # Auto-detect: Moonshot/Kimi key present and no real OpenRouter key
    has_kimi = bool(_first_nonempty_env("MOONSHOT_API_KEY", "KIMI_API_KEY", "LLM_API_KEY"))
    or_key = os.getenv("OPENROUTER_API_KEY", "").strip()
    or_placeholder = or_key in ("", "your_openrouter_api_key_here")
    if has_kimi and or_placeholder:
        return "kimi"
    return "openrouter"


def resolve_llm_api_key() -> str:
    provider = resolve_llm_provider()
    if provider == "kimi":
        return _first_nonempty_env(
            "LLM_API_KEY", "MOONSHOT_API_KEY", "KIMI_API_KEY", "OPENROUTER_API_KEY"
        )
    return _first_nonempty_env(
        "LLM_API_KEY", "OPENROUTER_API_KEY", "MOONSHOT_API_KEY", "KIMI_API_KEY"
    )


def resolve_llm_base_url() -> str:
    """OpenAI-compatible chat completions base URL."""
    override = os.getenv("LLM_BASE_URL", "").strip() or os.getenv("OPENROUTER_BASE_URL", "").strip()
    if override:
        return validate_endpoint_url(override, what="LLM_BASE_URL")
    if resolve_llm_provider() == "kimi":
        # Domestic Moonshot endpoint; set LLM_BASE_URL=https://api.moonshot.ai/v1 for intl.
        return validate_endpoint_url("https://api.moonshot.cn/v1", what="LLM_BASE_URL")
    return validate_endpoint_url("https://openrouter.ai/api/v1", what="LLM_BASE_URL")


def _default_primary_model() -> str:
    env = os.getenv("PRIMARY_MODEL", "").strip()
    if env:
        return env
    return "moonshot-v1-128k" if resolve_llm_provider() == "kimi" else "anthropic/claude-sonnet-4.5"


def _default_fallback_model() -> str:
    env = os.getenv("FALLBACK_MODEL", "").strip()
    if env:
        return env
    return "moonshot-v1-32k" if resolve_llm_provider() == "kimi" else "deepseek/deepseek-v3.2"


def _default_sentiment_model() -> str:
    env = os.getenv("SENTIMENT_MODEL", "").strip()
    if env:
        return env
    return "moonshot-v1-8k" if resolve_llm_provider() == "kimi" else "google/gemini-3.1-flash-lite-preview"


def validate_endpoint_url(url: str, *, what: str = "endpoint") -> str:
    """Reject non-HTTPS or non-allowlisted hosts unless custom hosts are opted in."""
    raw = (url or "").strip()
    if not raw:
        raise ValueError(f"{what} URL is empty")

    parsed = urlparse(raw if "://" in raw else f"https://{raw}")
    if parsed.scheme not in ("https", "http"):
        raise ValueError(f"{what} URL must be http(s): {raw!r}")

    host = (parsed.hostname or "").lower()
    if not host:
        raise ValueError(f"{what} URL missing host: {raw!r}")

    # Local RPC / dashboards
    if host in ("localhost", "127.0.0.1", "::1"):
        return raw.rstrip("/")

    if _env_flag("POLYMARKET_ALLOW_CUSTOM_HOSTS", "false"):
        return raw.rstrip("/")

    allowed = host in _ALLOWED_HOST_SUFFIXES or any(
        host.endswith("." + suffix) for suffix in _ALLOWED_HOST_SUFFIXES
    )
    if not allowed:
        raise ValueError(
            f"{what} host {host!r} is not allowlisted. "
            f"Use an official Polymarket/RPC endpoint, or set "
            f"POLYMARKET_ALLOW_CUSTOM_HOSTS=true only if you trust the host."
        )
    return raw.rstrip("/")


def _validated_env_url(env_name: str, default: str, what: str) -> str:
    raw = os.getenv(env_name, default)
    if raw is None or str(raw).strip() == "":
        raw = default
    return validate_endpoint_url(raw, what=what)


@dataclass
class APIConfig:
    """API configuration settings."""
    # --- Polymarket (active exchange) ---
    polymarket_private_key: str = field(default_factory=lambda: os.getenv("POLYMARKET_PRIVATE_KEY", ""))
    polymarket_funder: str = field(default_factory=lambda: os.getenv("POLYMARKET_FUNDER", ""))
    polymarket_host: str = field(
        default_factory=lambda: _validated_env_url(
            "POLYMARKET_HOST", "https://clob.polymarket.com", "POLYMARKET_HOST"
        )
    )
    polymarket_chain_id: int = field(default_factory=lambda: int(os.getenv("POLYMARKET_CHAIN_ID", "137")))
    polymarket_signature_type: int = field(default_factory=lambda: int(os.getenv("POLYMARKET_SIGNATURE_TYPE", "3")))
    polymarket_gamma_host: str = field(
        default_factory=lambda: _validated_env_url(
            "POLYMARKET_GAMMA_HOST", "https://gamma-api.polymarket.com", "POLYMARKET_GAMMA_HOST"
        )
    )
    polygon_rpc_url: str = field(
        default_factory=lambda: _validated_env_url(
            "POLYGON_RPC_URL", "https://polygon-rpc.com", "POLYGON_RPC_URL"
        )
    )

    # --- LLM (OpenRouter or Kimi/Moonshot — both OpenAI-compatible) ---
    openai_api_key: str = field(default_factory=lambda: os.getenv("OPENAI_API_KEY", ""))
    # Kept name openrouter_* for compatibility; values resolve from LLM_*/MOONSHOT_*/OPENROUTER_*
    openrouter_api_key: str = field(default_factory=resolve_llm_api_key)
    openai_base_url: str = "https://api.openai.com/v1"
    openrouter_base_url: str = field(default_factory=resolve_llm_base_url)
    llm_provider: str = field(default_factory=resolve_llm_provider)


@dataclass
class EnsembleConfig:
    """Multi-model ensemble configuration."""
    enabled: bool = True
    # Model roster for ensemble decisions — all via OpenRouter (April 2026)
    models: Dict[str, Dict] = field(default_factory=lambda: {
        "anthropic/claude-sonnet-4.5": {"provider": "openrouter", "role": "lead_analyst", "weight": 0.30},
        "google/gemini-3.1-pro": {"provider": "openrouter", "role": "forecaster", "weight": 0.30},
        "openai/gpt-5.4": {"provider": "openrouter", "role": "risk_manager", "weight": 0.20},
        "deepseek/deepseek-v3.2": {"provider": "openrouter", "role": "bull_researcher", "weight": 0.10},
        "x-ai/grok-4.1-fast": {"provider": "openrouter", "role": "bear_researcher", "weight": 0.10},
    })
    min_models_for_consensus: int = 3
    disagreement_threshold: float = 0.25  # Std dev above this = low confidence
    parallel_requests: bool = True
    debate_enabled: bool = True
    calibration_tracking: bool = True
    max_ensemble_cost: float = 0.50  # Max cost per ensemble decision


@dataclass
class SentimentConfig:
    """News and sentiment analysis configuration."""
    enabled: bool = True
    rss_feeds: List[str] = field(default_factory=lambda: [
        "https://feeds.reuters.com/reuters/topNews",
        "https://feeds.reuters.com/reuters/businessNews",
        "https://rss.nytimes.com/services/xml/rss/nyt/Business.xml",
        "https://feeds.bbci.co.uk/news/business/rss.xml",
    ])
    sentiment_model: str = field(default_factory=_default_sentiment_model)
    cache_ttl_minutes: int = 30
    max_articles_per_source: int = 10
    relevance_threshold: float = 0.3


# Trading strategy configuration — DISCIPLINED DEFAULTS (sane risk management)
# Beast mode is still available via --beast flag, but NOT the default.
# Discipline defaults based on live prediction market trading experience.
# NCAAB NO-side: 74% WR, +10% ROI — ONLY profitable category.
# Economic trades: -70% ROI, 78% of all losses.
@dataclass
class TradingConfig:
    """Trading strategy configuration."""
    # Position sizing and risk management — DISCIPLINED DEFAULTS
    max_position_size_pct: float = 3.0  # SANE: 3% per position (was 5% "beast mode")
    max_daily_loss_pct: float = 10.0    # SANE: 10% daily loss limit (was 15%)
    max_positions: int = 10              # SANE: 10 concurrent positions (was 15)
    min_balance: float = 100.0          # SANE: $100 minimum balance (was $50)
    
    # Market filtering criteria — DISCIPLINED
    min_volume: float = 500.0           # SANE: Higher volume requirement (was 200 beast mode)
    max_time_to_expiry_days: int = 14   # SANE: Shorter timeframes (was 30)
    
    # AI decision making — DATA-DRIVEN THRESHOLDS  
    min_confidence_to_trade: float = 0.50   # Balanced live threshold (was 60% too strict / 45% too loose)
    
    # Category-specific confidence adjustments (applied as multipliers to base threshold)
    category_confidence_adjustments: Dict[str, float] = field(default_factory=lambda: {
        "sports": 0.90,      # Sports showed best performance (NCAAB 74% WR), lower threshold
        "economics": 1.15,   # Economics showed -70% ROI, higher threshold required  
        "politics": 1.05,    # Slight increase for political volatility
        "default": 1.0       # Base multiplier for other categories
    })
    
    scan_interval_seconds: int = 60      # SANE: 60-second scan interval (was 30)
    
    # AI model configuration (override via PRIMARY_MODEL / FALLBACK_MODEL)
    primary_model: str = field(default_factory=_default_primary_model)
    fallback_model: str = field(default_factory=_default_fallback_model)
    ai_temperature: float = 0  # Lower temperature for more consistent JSON output
    ai_max_tokens: int = 8000    # Reasonable limit for reasoning models (grok-4 works better with 8000)
    
    # Position sizing (LEGACY - now using Kelly-primary approach)
    default_position_size: float = 3.0  # REDUCED: Now using Kelly Criterion as primary method (was 5%, now 3%)
    position_size_multiplier: float = 1.0  # Multiplier for AI confidence
    
    # Kelly Criterion settings (PRIMARY position sizing method) — DISCIPLINED
    use_kelly_criterion: bool = True        # Use Kelly Criterion for position sizing (PRIMARY METHOD)
    kelly_fraction: float = 0.25            # SANE: Quarter-Kelly (was 0.75 beast mode — gambling)
    max_single_position: float = 0.03       # SANE: 3% max position cap (was 0.05 beast mode)
    
    # Live trading mode control — DRY_RUN wins over LIVE_TRADING_ENABLED (see resolve_live_trading_enabled)
    live_trading_enabled: bool = field(default_factory=resolve_live_trading_enabled)
    paper_trading_mode: bool = field(default_factory=lambda: not resolve_live_trading_enabled())
    
    # Trading frequency - MORE FREQUENT
    market_scan_interval: int = 30          # DECREASED: Scan every 30 seconds (was 60)
    position_check_interval: int = 15       # DECREASED: Check positions every 15 seconds (was 30)
    max_trades_per_hour: int = 20           # INCREASED: Allow more trades per hour (was 10, now 20)
    run_interval_minutes: int = 10          # DECREASED: Run more frequently (was 15, now 10)
    num_processor_workers: int = 5      # Number of concurrent market processor workers
    
    # Market selection preferences
    preferred_categories: List[str] = field(default_factory=lambda: [])
    excluded_categories: List[str] = field(default_factory=lambda: [])
    
    # High-confidence, near-expiry strategy
    enable_high_confidence_strategy: bool = True
    high_confidence_threshold: float = 0.95  # LLM confidence needed
    high_confidence_market_odds: float = 0.90 # Market price to look for
    high_confidence_expiry_hours: int = 24   # Max hours until expiry

    # AI trading criteria - MORE PERMISSIVE
    max_analysis_cost_per_decision: float = 0.15  # INCREASED: Allow higher cost per decision (was 0.10, now 0.15)
    min_confidence_threshold: float = 0.50  # Align with min_confidence_to_trade

    # Cost control and market analysis frequency - MORE PERMISSIVE
    daily_ai_budget: float = 10.0  # INCREASED: Higher daily budget (was 5.0, now 10.0)
    max_ai_cost_per_decision: float = 0.08  # INCREASED: Higher per-decision cost (was 0.05, now 0.08)
    analysis_cooldown_hours: int = 3  # DECREASED: Shorter cooldown (was 6, now 3)
    max_analyses_per_market_per_day: int = 4  # INCREASED: More analyses per day (was 2, now 4)
    
    # Daily AI spending limits - SAFETY CONTROLS
    # Default is $10/day — conservative limit to prevent runaway API spend.
    # Raise via DAILY_AI_COST_LIMIT env var or by editing this value directly.
    # e.g. export DAILY_AI_COST_LIMIT=25  (for more aggressive scanning)
    daily_ai_cost_limit: float = field(default_factory=lambda: float(os.getenv("DAILY_AI_COST_LIMIT", "10.0")))
    enable_daily_cost_limiting: bool = True  # Enable daily cost limits
    sleep_when_limit_reached: bool = True  # Sleep until next day when limit reached

    # Enhanced market filtering to reduce analyses - MORE PERMISSIVE
    min_volume_for_ai_analysis: float = 200.0  # DECREASED: Much lower threshold (was 500, now 200)
    exclude_low_liquidity_categories: List[str] = field(default_factory=lambda: [
        # REMOVED weather and entertainment - trade all categories
    ])


@dataclass
class LoggingConfig:
    """Logging configuration."""
    log_level: str = "DEBUG"
    log_format: str = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    log_file: str = "logs/trading_system.log"
    enable_file_logging: bool = True
    enable_console_logging: bool = True
    max_log_file_size: int = 10 * 1024 * 1024  # 10MB
    backup_count: int = 5


# BEAST MODE UNIFIED TRADING SYSTEM CONFIGURATION 🚀
# These settings control the advanced multi-strategy trading system

# === CAPITAL ALLOCATION ACROSS STRATEGIES ===
# Allocate capital across different trading approaches
market_making_allocation: float = 0.40  # 40% for market making (spread profits)
directional_allocation: float = 0.50    # 50% for directional trading (AI predictions) 
arbitrage_allocation: float = 0.10      # 10% for arbitrage opportunities

  # === PORTFOLIO OPTIMIZATION SETTINGS ===
# Kelly Criterion is now the PRIMARY position sizing method (moved to TradingConfig)
# total_capital: DYNAMICALLY FETCHED from USDC.e wallet balance — never hardcoded!
use_risk_parity: bool = True            # Equal risk allocation vs equal capital
rebalance_hours: int = 6                # Rebalance portfolio every 6 hours
min_position_size: float = 5.0          # Minimum position size ($5 vs $10)
max_opportunities_per_batch: int = 50   # Limit opportunities to prevent optimization issues

# === RISK MANAGEMENT LIMITS ===
# Portfolio-level risk constraints — DISCIPLINED DEFAULTS
# Conservative defaults based on live trading experience. Beast mode available via CLI flag.
max_volatility: float = 0.40            # SANE: 40% volatility max (was 80%)
max_correlation: float = 0.70           # SANE: 70% correlation max (was 95%)
max_drawdown: float = 0.15              # SANE: 15% drawdown limit (was 50% — suicidal)
max_sector_exposure: float = 0.30       # SANE: 30% sector concentration (was 90%)

# === PERFORMANCE TARGETS ===
# System performance objectives - MORE AGGRESSIVE FOR MORE TRADES
target_sharpe: float = 0.3              # DECREASED: Lower Sharpe requirement (was 0.5, now 0.3)
target_return: float = 0.15             # INCREASED: Higher return target (was 0.10, now 0.15)
min_trade_edge: float = 0.08           # DECREASED: Lower edge requirement (was 0.15, now 8%)
min_confidence_for_large_size: float = 0.50  # DECREASED: Lower confidence requirement (was 0.65, now 50%)

# === DYNAMIC EXIT STRATEGIES ===
# Enhanced exit strategy settings - MORE AGGRESSIVE
use_dynamic_exits: bool = True
profit_threshold: float = 0.20          # DECREASED: Take profits sooner (was 0.25, now 0.20)
loss_threshold: float = 0.15            # INCREASED: Allow larger losses (was 0.10, now 0.15)
confidence_decay_threshold: float = 0.25  # INCREASED: Allow more confidence decay (was 0.20, now 0.25)
max_hold_time_hours: int = 240          # INCREASED: Hold longer (was 168, now 240 hours = 10 days)
volatility_adjustment: bool = True      # Adjust exits based on volatility

# === MARKET MAKING STRATEGY ===
# Settings for limit order market making - MORE AGGRESSIVE
enable_market_making: bool = True       # Enable market making strategy
min_spread_for_making: float = 0.01     # DECREASED: Accept smaller spreads (was 0.02, now 1¢)
max_inventory_risk: float = 0.15        # INCREASED: Allow higher inventory risk (was 0.10, now 15%)
order_refresh_minutes: int = 15         # Refresh orders every 15 minutes
max_orders_per_market: int = 4          # Maximum orders per market (2 each side)

# === MARKET SELECTION (ENHANCED FOR MORE OPPORTUNITIES) ===
# Removed time restrictions - trade ANY deadline with dynamic exits!
# max_time_to_expiry_days: REMOVED      # No longer used - trade any timeline!
min_volume_for_analysis: float = 200.0  # DECREASED: Much lower minimum volume (was 1000, now 200)
min_volume_for_market_making: float = 500.0  # DECREASED: Lower volume for market making (was 2000, now 500)
min_price_movement: float = 0.02        # DECREASED: Lower minimum range (was 0.05, now 2¢)
max_bid_ask_spread: float = 0.15        # INCREASED: Allow wider spreads (was 0.10, now 15¢)
min_confidence_long_term: float = 0.45  # DECREASED: Lower confidence for distant expiries (was 0.65, now 45%)

# === COST OPTIMIZATION (MORE GENEROUS) ===
# Enhanced cost controls for the beast mode system
daily_ai_budget: float = 15.0           # INCREASED: Higher budget for more opportunities (was 10.0, now 15.0)
max_ai_cost_per_decision: float = 0.12  # INCREASED: Higher per-decision limit (was 0.08, now 0.12)
analysis_cooldown_hours: int = 2        # DECREASED: Much shorter cooldown (was 4, now 2)
max_analyses_per_market_per_day: int = 6  # INCREASED: More analyses per day (was 3, now 6)
skip_news_for_low_volume: bool = True   # Skip expensive searches for low volume
news_search_volume_threshold: float = 1000.0  # News threshold

# === SYSTEM BEHAVIOR ===
# Overall system behavior settings
beast_mode_enabled: bool = True         # Enable the unified advanced system
fallback_to_legacy: bool = True         # Fallback to legacy system if needed
log_level: str = "INFO"                 # Logging level
performance_monitoring: bool = True     # Enable performance monitoring

# === ADVANCED FEATURES ===
# Cutting-edge features for maximum performance
cross_market_arbitrage: bool = False    # Enable when arbitrage module ready
multi_model_ensemble: bool = False      # Not wired into the live trading path. The scaffolding lives in src/agents/ — fork it if you want real parallel multi-model voting.
sentiment_analysis: bool = True         # News sentiment analysis (ENABLED)
websocket_streaming: bool = True        # WebSocket real-time data (ENABLED)
options_strategies: bool = False        # Complex options strategies (future)
algorithmic_execution: bool = False     # Smart order execution (future)


@dataclass
class Settings:
    """Main settings class combining all configuration."""
    api: APIConfig = field(default_factory=APIConfig)
    trading: TradingConfig = field(default_factory=TradingConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    ensemble: EnsembleConfig = field(default_factory=EnsembleConfig)
    sentiment: SentimentConfig = field(default_factory=SentimentConfig)

    def validate(self) -> bool:
        """Validate configuration settings.

        Polymarket auth is checked here as a soft warning rather than a hard
        failure: importing this module must succeed even on a fresh clone
        without .env so that `python cli.py health` itself can run and report
        what's missing.
        """
        if not self.api.polymarket_private_key:
            raise ValueError(
                "POLYMARKET_PRIVATE_KEY environment variable is required "
                "(see env.template; copy to .env and fill in your Polygon "
                "wallet's private key)"
            )

        if self.trading.max_position_size_pct <= 0 or self.trading.max_position_size_pct > 100:
            raise ValueError("max_position_size_pct must be between 0 and 100")

        if self.trading.min_confidence_to_trade <= 0 or self.trading.min_confidence_to_trade > 1:
            raise ValueError("min_confidence_to_trade must be between 0 and 1")

        return True


# Global settings instance
settings = Settings()

# Validate settings on import
try:
    settings.validate()
except ValueError as e:
    print(f"Configuration validation error: {e}")
    print("Please check your environment variables and configuration.") 