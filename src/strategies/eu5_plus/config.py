"""EU5 parallel sleeve — six strategies alongside EU5 fair-value + lottery."""

from __future__ import annotations

import os
from pathlib import Path

DEFAULT_LEDGER = Path("data") / "eu5_plus_ledger.jsonl"
DEFAULT_STATE = Path("data") / "eu5_plus_state.json"
DEFAULT_SCAN_LOG = Path("data") / "scan_stats_eu5_plus.json"

ORDER_USDC = float(os.getenv("EU5_PLUS_ORDER_USDC", "1.01"))
MIN_SHARES = int(os.getenv("EU5_PLUS_MIN_SHARES", "5"))
WEEK_BUDGET_USDC = float(os.getenv("EU5_PLUS_WEEK_BUDGET_USDC", "15.0"))
MAX_PER_MATCH_USDC = float(os.getenv("EU5_PLUS_MAX_PER_MATCH_USDC", "2.02"))
MAX_ENTRIES_PER_DAY = int(os.getenv("EU5_PLUS_MAX_ENTRIES_PER_DAY", "8"))
HARD_MAX_USDC = float(os.getenv("EU5_PLUS_HARD_MAX_USDC", "2.0"))

_DEFAULT_STRATS = (
    "draw_fv,kickoff_lag,completeness,live_draw,line_cross,narrative"
)
ENABLED_STRATS = tuple(
    s.strip()
    for s in os.getenv("EU5_PLUS_ENABLED_STRATS", _DEFAULT_STRATS).split(",")
    if s.strip()
)

# Shared with EU5 fair-value leagues.
EU5_SPORT_KEYS = [
    "soccer_epl",
    "soccer_spain_la_liga",
    "soccer_italy_serie_a",
    "soccer_germany_bundesliga",
    "soccer_france_ligue_one",
]

# Draw FV
DRAW_MIN_EDGE = float(os.getenv("EU5_PLUS_DRAW_MIN_EDGE", "0.03"))
DRAW_PRICE_MIN = float(os.getenv("EU5_PLUS_DRAW_PRICE_MIN", "0.18"))
DRAW_PRICE_MAX = float(os.getenv("EU5_PLUS_DRAW_PRICE_MAX", "0.32"))

# Kickoff lag window (minutes before kickoff).
LAG_MIN_MINUTES = float(os.getenv("EU5_PLUS_LAG_MIN_MINUTES", "120"))
LAG_MAX_MINUTES = float(os.getenv("EU5_PLUS_LAG_MAX_MINUTES", "360"))
LAG_MIN_EDGE = float(os.getenv("EU5_PLUS_LAG_MIN_EDGE", "0.035"))
LAG_PRICE_MIN = float(os.getenv("EU5_PLUS_LAG_PRICE_MIN", "0.20"))
LAG_PRICE_MAX = float(os.getenv("EU5_PLUS_LAG_PRICE_MAX", "0.70"))

# Completeness
COMPLETENESS_MIN_GAP = float(os.getenv("EU5_PLUS_COMPLETENESS_MIN_GAP", "0.02"))

# Live draw rise (no live score API — clock heuristic).
LIVE_DRAW_MINUTE = float(os.getenv("EU5_PLUS_LIVE_DRAW_MINUTE", "55"))
LIVE_DRAW_MAX_MINUTE = float(os.getenv("EU5_PLUS_LIVE_DRAW_MAX_MINUTE", "95"))
LIVE_DRAW_MIN_LIFT = float(os.getenv("EU5_PLUS_LIVE_DRAW_MIN_LIFT", "0.03"))
LIVE_DRAW_PRICE_MAX = float(os.getenv("EU5_PLUS_LIVE_DRAW_PRICE_MAX", "0.38"))

# Line cross (spread / totals structure)
LINE_FAV_MIN = float(os.getenv("EU5_PLUS_LINE_FAV_MIN", "0.55"))
LINE_SPREAD_MAX = float(os.getenv("EU5_PLUS_LINE_SPREAD_MAX", "0.28"))
LINE_UNDER_MAX = float(os.getenv("EU5_PLUS_LINE_UNDER_MAX", "0.48"))

# Narrative
NARRATIVE_FAVORITE_MIN = float(os.getenv("EU5_PLUS_NARRATIVE_FAVORITE_MIN", "0.55"))
NARRATIVE_TOSSUP_MAX_GAP = float(os.getenv("EU5_PLUS_NARRATIVE_TOSSUP_MAX_GAP", "0.06"))

MAX_DAYS_AHEAD = float(os.getenv("EU5_PLUS_MAX_DAYS_AHEAD", "5"))
MIN_MINUTES_BEFORE = float(os.getenv("EU5_PLUS_MIN_MINUTES_BEFORE", "15"))
REF_TTL_HOURS = float(os.getenv("EU5_PLUS_REF_TTL_HOURS", "8"))
REFERENCE_CACHE = Path("data") / "eu5_plus_reference.json"

STOP_LOSS_PCT = float(os.getenv("EU5_PLUS_STOP_LOSS_PCT", "0.50"))
EXIT_FAIL_COOLDOWN_S = float(os.getenv("EU5_PLUS_EXIT_FAIL_COOLDOWN_S", "1800"))

# Event slug prefixes for top-5 (+ UCL as bonus EU5 clubs).
EU5_SLUG_PREFIXES = (
    "epl-", "lal-", "lla-", "sea-", "bun-", "bl1-", "fl1-", "lig-", "ucl-",
)
EU5_TAG_SLUGS = {
    "epl", "premier-league", "la-liga", "laliga", "serie-a", "sea",
    "bundesliga", "ligue-1", "ligue1", "ucl", "champions-league",
}
