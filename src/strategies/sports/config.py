"""RN1 sports sleeve constants — isolated from Conservative."""

from __future__ import annotations

import os
from pathlib import Path

# The Odds API sport keys for European football (RN1 focus).
EU_SOCCER_SPORT_KEYS = [
    "soccer_epl",
    "soccer_spain_la_liga",
    "soccer_italy_serie_a",
    "soccer_germany_bundesliga",
    "soccer_france_ligue_one",
    "soccer_efl_champ",
    "soccer_england_efl_cup",
    "soccer_uefa_europa_league",
    "soccer_uefa_champs_league_qualification",
]

DEFAULT_LEDGER = Path("data") / "daily_entries_sports.json"
DEFAULT_SCAN_LOG = Path("data") / "scan_stats_sports.json"

# RN1 favorite band (Polymarket YES price).
FAVORITE_MIN = float(os.getenv("SPORTS_RN1_FAVORITE_MIN", "0.50"))
FAVORITE_MAX = float(os.getenv("SPORTS_RN1_FAVORITE_MAX", "0.85"))

# Minimum edge: Pinnacle fair prob minus our maker bid (probability points).
MIN_EDGE = float(os.getenv("SPORTS_RN1_MIN_EDGE", "0.05"))

# ~$119 NAV: 1% per position, max 2 entries/day.
SLEEVE_CAP_PCT = float(os.getenv("SPORTS_RN1_SLEEVE_CAP_PCT", "0.01"))
MAX_ENTRIES_PER_DAY = int(os.getenv("SPORTS_RN1_MAX_ENTRIES_PER_DAY", "2"))

# Skip matches starting within N hours (adverse selection near kickoff).
MIN_HOURS_BEFORE_KICKOFF = float(os.getenv("SPORTS_RN1_MIN_HOURS_BEFORE_KICKOFF", "2.0"))

# Maker bid: one tick below best ask (or best bid + tick if no ask).
MAKER_TICK = float(os.getenv("SPORTS_RN1_MAKER_TICK", "0.01"))

MAX_SCAN_CYCLES = 2000

# Smart-money confirmation — RN1 proxy wallet on Polymarket.
DEFAULT_RN1_WALLET = "0x2005d16a84ceefa912d4e380cd32e7ff827875ea"
RN1_PROXY_WALLET = (
    os.getenv("RN1_PROXY_WALLET") or DEFAULT_RN1_WALLET
).lower()
# strict = same market YES BUY + price band; event = same match event activity; off = skip check
RN1_CONFIRM_MODE = os.getenv("RN1_CONFIRM_MODE", "strict").lower().strip()
RN1_LOOKBACK_HOURS = float(os.getenv("RN1_LOOKBACK_HOURS", "24"))
RN1_PRICE_TOLERANCE_TICKS = int(os.getenv("RN1_PRICE_TOLERANCE_TICKS", "2"))
