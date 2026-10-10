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
# Focus clubs need room for win + optional structure leg.
WEEK_BUDGET_USDC = float(os.getenv("CSL_EXPLORE_WEEK_BUDGET_USDC", "12.12"))
MAX_PER_MATCH_USDC = float(os.getenv("CSL_EXPLORE_MAX_PER_MATCH_USDC", "2.02"))

# Comma-separated strategy ids. Default: focus clubs (+ structure helpers).
# narrative OFF — burned budget on draws / favorites.
_DEFAULT_STRATS = "focus_club,fingerprint,completeness,time_lag,anti_whale"
ENABLED_STRATS = tuple(
    s.strip()
    for s in os.getenv("CSL_EXPLORE_ENABLED_STRATS", _DEFAULT_STRATS).split(",")
    if s.strip()
)

# Only trade fixtures involving these clubs (title / slug keywords).
# Default: 成都蓉城 / 辽宁铁人 / 重庆铜梁龙.
_DEFAULT_FOCUS = (
    "chengdu rongcheng,rongcheng,ron,"
    "liaoning tieren,tieren,tie,"
    "chongqing tonglianglong,tonglianglong,ton"
)
FOCUS_TEAMS = tuple(
    s.strip()
    for s in os.getenv("CSL_EXPLORE_FOCUS_TEAMS", _DEFAULT_FOCUS).split(",")
    if s.strip()
)
FOCUS_CLUB_PRICE_MIN = float(os.getenv("CSL_EXPLORE_FOCUS_PRICE_MIN", "0.22"))
FOCUS_CLUB_PRICE_MAX = float(os.getenv("CSL_EXPLORE_FOCUS_PRICE_MAX", "0.72"))

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

# Optional hard-coded slugs. Empty / unset → auto-discover upcoming chi-* matches.
# Set CSL_EXPLORE_EVENT_SLUGS=slug1,slug2 to pin a round (legacy manual mode).
EVENT_SLUGS = tuple(
    s.strip()
    for s in os.getenv("CSL_EXPLORE_EVENT_SLUGS", "").split(",")
    if s.strip()
)
# Auto-discover horizon (hours ahead / hours after kickoff still tradeable).
DISCOVER_HOURS_AHEAD = float(os.getenv("CSL_EXPLORE_DISCOVER_HOURS_AHEAD", "168"))
DISCOVER_HOURS_AFTER = float(os.getenv("CSL_EXPLORE_DISCOVER_HOURS_AFTER", "3"))

GAMMA_HOST = os.getenv("GAMMA_HOST", "https://gamma-api.polymarket.com").rstrip("/")

# Stop-loss: sell YES when mark ≤ entry × (1 - pct). No auto take-profit.
STOP_LOSS_PCT = float(os.getenv("CSL_EXPLORE_STOP_LOSS_PCT", "0.50"))
EXIT_FAIL_COOLDOWN_S = float(os.getenv("CSL_EXPLORE_EXIT_FAIL_COOLDOWN_S", "1800"))
# Opens with no live book older than this → mark lost (zombie settle).
STALE_OPEN_HOURS = float(os.getenv("CSL_EXPLORE_STALE_OPEN_HOURS", "72"))
