"""EU5 lottery sleeve — entertainment longshots, isolated from fair-value."""

from __future__ import annotations

import os
from pathlib import Path

DEFAULT_PNL_PATH = Path("data") / "lottery_pnl.json"
DEFAULT_SCAN_LOG = Path("data") / "scan_stats_lottery.json"
DEFAULT_LEDGER = Path("data") / "daily_entries_lottery.json"
DEFAULT_STATE = Path("data") / "lottery_state.json"

# Entertainment budget (user: $10 / week).
WEEK_BUDGET_USDC = float(os.getenv("LOTTERY_WEEK_BUDGET_USDC", "10.0"))
MAX_ENTRIES_PER_DAY = int(os.getenv("LOTTERY_MAX_ENTRIES_PER_DAY", "5"))

# CLOB floors — always minimum ticket size.
MIN_SHARES = int(os.getenv("LOTTERY_MIN_SHARES", "5"))
MIN_NOTIONAL = float(os.getenv("LOTTERY_MIN_NOTIONAL", "1.01"))
HARD_MAX_USDC = float(os.getenv("LOTTERY_HARD_MAX_USDC", "2.0"))

# Lottery band: YES ask must be in (0, PRICE_MAX].
PRICE_MAX = float(os.getenv("LOTTERY_PRICE_MAX", "0.15"))
PRICE_MIN = float(os.getenv("LOTTERY_PRICE_MIN", "0.02"))

# Prefer top-5 EU leagues via title keywords (soft filter).
REQUIRE_EU5_HINT = (
    os.getenv("LOTTERY_REQUIRE_EU5", "true").strip().lower()
    not in ("0", "false", "no", "off")
)

MAX_DAYS_AHEAD = float(os.getenv("LOTTERY_MAX_DAYS_AHEAD", "7"))
MIN_MINUTES_BEFORE = float(os.getenv("LOTTERY_MIN_MINUTES_BEFORE", "30"))
MAX_SCAN_CYCLES = 2000

# Soft EU5 name hints (title / team contains any).
EU5_HINTS = (
    "premier league", "epl", "la liga", "serie a", "bundesliga", "ligue 1",
    "manchester", "liverpool", "arsenal", "chelsea", "tottenham", "newcastle",
    "real madrid", "barcelona", "atletico", "bayern", "dortmund", "leverkusen",
    "juventus", "inter", "milan", "napoli", "roma", "psg", "marseille", "lyon",
    "ajax",  # sometimes cross-listed; still fine for lottery
)
