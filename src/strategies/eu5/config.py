"""EU5 fair-value sleeve constants — isolated from RN1 copy sleeve."""

from __future__ import annotations

import os
from pathlib import Path

DEFAULT_PNL_PATH = Path("data") / "eu5_pnl.json"
DEFAULT_SCAN_LOG = Path("data") / "scan_stats_eu5.json"
DEFAULT_LEDGER = Path("data") / "daily_entries_eu5.json"
REFERENCE_CACHE = Path("data") / "eu5_reference.json"

# Top-5 European leagues only (The Odds API sport keys).
EU5_SPORT_KEYS = [
    "soccer_epl",
    "soccer_spain_la_liga",
    "soccer_italy_serie_a",
    "soccer_germany_bundesliga",
    "soccer_france_ligue_one",
]

# Value leg: buy when PM ask <= fair_prob - MIN_EDGE (Pinnacle de-vigged).
MIN_EDGE = float(os.getenv("EU5_MIN_EDGE", "0.04"))
PRICE_MIN = float(os.getenv("EU5_PRICE_MIN", "0.30"))
PRICE_MAX = float(os.getenv("EU5_PRICE_MAX", "0.80"))

# Sizing: CLOB floor is 5 shares / $1.01 notional; we target ~$2 per signal.
STAKE_USDC = float(os.getenv("EU5_STAKE_USDC", "2.0"))
MIN_SHARES = int(os.getenv("EU5_MIN_SHARES", "5"))
HARD_MAX_USDC = float(os.getenv("EU5_HARD_MAX_USDC", "5.0"))

# Risk shell.
MAX_ENTRIES_PER_DAY = int(os.getenv("EU5_MAX_ENTRIES_PER_DAY", "3"))

# Reference odds cache TTL (free tier = 500 req/month; 5 leagues × 3/day ≈ 450).
REF_TTL_HOURS = float(os.getenv("EU5_REF_TTL_HOURS", "8"))

# Only value matches kicking off within this window (days).
MAX_DAYS_AHEAD = float(os.getenv("EU5_MAX_DAYS_AHEAD", "7"))
MIN_MINUTES_BEFORE_KICKOFF = float(os.getenv("EU5_MIN_MINUTES_BEFORE_KICKOFF", "120"))

MAX_SCAN_CYCLES = 2000
