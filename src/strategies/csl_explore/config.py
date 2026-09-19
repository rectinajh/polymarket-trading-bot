"""CSL exploration sleeve — isolated from RN1 sports copy."""

from __future__ import annotations

import os
from pathlib import Path

DEFAULT_LEDGER = Path("data") / "csl_explore_ledger.jsonl"
DEFAULT_STATE = Path("data") / "csl_explore_state.json"
DEFAULT_SCAN_LOG = Path("data") / "scan_stats_csl_explore.json"

# CLOB floors
ORDER_USDC = float(os.getenv("CSL_EXPLORE_ORDER_USDC", "1.01"))
MIN_SHARES = int(os.getenv("CSL_EXPLORE_MIN_SHARES", "5"))
WEEK_BUDGET_USDC = float(os.getenv("CSL_EXPLORE_WEEK_BUDGET_USDC", "8.08"))
MAX_PER_MATCH_USDC = float(os.getenv("CSL_EXPLORE_MAX_PER_MATCH_USDC", "1.01"))

# Comma-separated strategy ids. Default = all five explore legs.
_DEFAULT_STRATS = "fingerprint,time_lag,completeness,narrative,anti_whale"
ENABLED_STRATS = tuple(
    s.strip()
    for s in os.getenv("CSL_EXPLORE_ENABLED_STRATS", _DEFAULT_STRATS).split(",")
    if s.strip()
)

# Exact-score fingerprint target (Polymarket question substring match).
FINGERPRINT_SCORE = os.getenv("CSL_EXPLORE_FINGERPRINT_SCORE", "1-1").strip()

# Completeness: require home+draw+away Yes sum <= 1 - gap.
COMPLETENESS_MIN_GAP = float(os.getenv("CSL_EXPLORE_COMPLETENESS_MIN_GAP", "0.03"))

# Time-lag: minutes after kickoff with 0-0 → draw signal.
TIME_LAG_DRAW_MINUTE = float(os.getenv("CSL_EXPLORE_TIME_LAG_DRAW_MINUTE", "70"))

# Anti-whale: mid drop vs cached mid (fraction) to trigger reverse buy.
ANTI_WHALE_DROP_PCT = float(os.getenv("CSL_EXPLORE_ANTI_WHALE_DROP_PCT", "0.08"))
ANTI_WHALE_LOOKBACK_S = float(os.getenv("CSL_EXPLORE_ANTI_WHALE_LOOKBACK_S", "600"))

# Favorite threshold for narrative hedge.
NARRATIVE_FAVORITE_MIN = float(os.getenv("CSL_EXPLORE_NARRATIVE_FAVORITE_MIN", "0.55"))
NARRATIVE_TOSSUP_MAX_GAP = float(os.getenv("CSL_EXPLORE_NARRATIVE_TOSSUP_MAX_GAP", "0.05"))

# Week 25 CSL event slugs (override via env for future rounds).
_DEFAULT_SLUGS = (
    "chi-wsz-qin-2026-09-05,"
    "chi-ygb-hai-2026-09-05,"
    "chi-shp-bgu-2026-09-05,"
    "chi-sha-xin-2026-09-05,"
    "chi-ton-sgr-2026-09-06,"
    "chi-hen-ron-2026-09-06,"
    "chi-yun-tie-2026-09-06,"
    "chi-jin-zhe-2026-09-06"
)
EVENT_SLUGS = tuple(
    s.strip()
    for s in os.getenv("CSL_EXPLORE_EVENT_SLUGS", _DEFAULT_SLUGS).split(",")
    if s.strip()
)

GAMMA_HOST = os.getenv("GAMMA_HOST", "https://gamma-api.polymarket.com").rstrip("/")

# Stop-loss: sell YES when mark ≤ entry × (1 - pct). No auto take-profit.
STOP_LOSS_PCT = float(os.getenv("CSL_EXPLORE_STOP_LOSS_PCT", "0.50"))
EXIT_FAIL_COOLDOWN_S = float(os.getenv("CSL_EXPLORE_EXIT_FAIL_COOLDOWN_S", "1800"))
