"""RN1 sports copy-trade sleeve constants — isolated from Conservative."""

from __future__ import annotations

import os
from pathlib import Path

DEFAULT_LEDGER = Path("data") / "daily_entries_sports.json"
DEFAULT_SCAN_LOG = Path("data") / "scan_stats_sports.json"
DEFAULT_COPY_SEEN = Path("data") / "rn1_copy_seen.json"
DEFAULT_PNL_PATH = Path("data") / "sports_pnl.json"

# CLOB exchange floors: ≥5 shares and ≥$1.01 notional. Size = exactly that minimum.
COPY_MIN_SHARES = int(os.getenv("SPORTS_RN1_COPY_MIN_SHARES", "5"))
COPY_MIN_NOTIONAL = float(os.getenv("SPORTS_RN1_COPY_MIN_NOTIONAL", "1.01"))
# Soft label for logs / dashboard (actual spend = max(5×price, $1.01)).
COPY_MAX_USDC = float(os.getenv("SPORTS_RN1_COPY_MAX_USDC", str(COPY_MIN_NOTIONAL)))
# Safety skip if min size would exceed this (5 × high price).
COPY_HARD_MAX_USDC = float(os.getenv("SPORTS_RN1_COPY_HARD_MAX_USDC", "5.0"))

# Max copy fills per Shanghai trading day (each ≤ COPY_MAX_USDC).
# Sample window default 5 — keep directional risk small while validating edge.
MAX_ENTRIES_PER_DAY = int(os.getenv("SPORTS_RN1_MAX_ENTRIES_PER_DAY", "5"))

# Whitelist sports only (soccer + optional tennis). Legacy name kept.
COPY_SOCCER_ONLY = (os.getenv("SPORTS_RN1_SOCCER_ONLY", "true").strip().lower()
                    not in ("0", "false", "no", "off"))
# Allow ATP/WTA singles alongside soccer (ITF/doubles off by default).
COPY_ALLOW_TENNIS = (
    os.getenv("SPORTS_RN1_ALLOW_TENNIS", "true").strip().lower()
    not in ("0", "false", "no", "off")
)
COPY_TENNIS_INCLUDE_ITF = (
    os.getenv("SPORTS_RN1_TENNIS_INCLUDE_ITF", "false").strip().lower()
    in ("1", "true", "yes", "on")
)
COPY_TENNIS_INCLUDE_DOUBLES = (
    os.getenv("SPORTS_RN1_TENNIS_INCLUDE_DOUBLES", "false").strip().lower()
    in ("1", "true", "yes", "on")
)

# Mirror RN1 *open* soccer book on bootstrap. Default OFF — only copy new BUYs.
# (Bulk sync on 2026-08-27 blew the daily/drawdown guard.)
COPY_SYNC_OPEN_POSITIONS = (
    os.getenv("SPORTS_RN1_SYNC_OPEN_POSITIONS", "false").strip().lower()
    in ("1", "true", "yes", "on")
)

# Only follow RN1 BUY trades (YES/NO). SELL ignored.
COPY_SIDES = ("BUY",)

# After EXIT FAIL, skip re-trying the same hold for this many seconds
# (unless redeemable — redeem is attempted immediately).
COPY_EXIT_FAIL_COOLDOWN_S = float(os.getenv("SPORTS_RN1_EXIT_FAIL_COOLDOWN_S", "1800"))

# Per-position stop-loss (fraction). Take-profit is manual — no auto TP.
# 0.20 = exit when mark ≤ entry × 80% (≈ −20%).
COPY_STOP_LOSS_PCT = float(os.getenv("SPORTS_RN1_STOP_LOSS_PCT", "0.20"))
# When RN1 no longer holds the same soccer side, sell our copy.
COPY_FOLLOW_RN1_EXIT = (
    os.getenv("SPORTS_RN1_FOLLOW_EXIT", "true").strip().lower()
    not in ("0", "false", "no", "off")
)

# Skip copy when RN1 price outside band (avoid lottery tails / locks).
COPY_PRICE_MIN = float(os.getenv("SPORTS_RN1_COPY_PRICE_MIN", "0.35"))
COPY_PRICE_MAX = float(os.getenv("SPORTS_RN1_COPY_PRICE_MAX", "0.75"))

MAX_SCAN_CYCLES = 2000

# RN1 proxy wallet on Polymarket (smart-money source).
DEFAULT_RN1_WALLET = "0x2005d16a84ceefa912d4e380cd32e7ff827875ea"
RN1_PROXY_WALLET = (
    os.getenv("RN1_PROXY_WALLET") or DEFAULT_RN1_WALLET
).lower()
RN1_LOOKBACK_HOURS = float(os.getenv("RN1_LOOKBACK_HOURS", "6"))

# Legacy confirm-mode env kept for dashboard/back-compat; copy strategy ignores it.
RN1_CONFIRM_MODE = os.getenv("RN1_CONFIRM_MODE", "copy").lower().strip()
RN1_PRICE_TOLERANCE_TICKS = int(os.getenv("RN1_PRICE_TOLERANCE_TICKS", "2"))

# Experiment / stop-loss (90-day sports sleeve).
SPORTS_EXPERIMENT_DAYS = int(os.getenv("SPORTS_EXPERIMENT_DAYS", "90"))
SPORTS_MAX_DAILY_LOSS_PCT = float(os.getenv("SPORTS_MAX_DAILY_LOSS_PCT", "0.05"))
SPORTS_MAX_DRAWDOWN_PCT = float(os.getenv("SPORTS_MAX_DRAWDOWN_PCT", "0.30"))
# Peak-from-high-water DD only after enough sample (tiny peaks false-trigger).
SPORTS_PEAK_DD_MIN_PEAK_CENTS = int(os.getenv("SPORTS_PEAK_DD_MIN_PEAK_CENTS", "1000"))
SPORTS_PEAK_DD_MIN_SETTLED = int(os.getenv("SPORTS_PEAK_DD_MIN_SETTLED", "15"))
# Close ledger opens missing on-chain longer than this (ghost / expired).
SPORTS_GHOST_RECONCILE_HOURS = float(os.getenv("SPORTS_GHOST_RECONCILE_HOURS", "36"))

# Deprecated Odds/Pinnacle knobs (kept so old .env does not break imports).
FAVORITE_MIN = float(os.getenv("SPORTS_RN1_FAVORITE_MIN", "0.50"))
FAVORITE_MAX = float(os.getenv("SPORTS_RN1_FAVORITE_MAX", "0.85"))
MIN_EDGE = float(os.getenv("SPORTS_RN1_MIN_EDGE", "0.05"))
SLEEVE_CAP_PCT = float(os.getenv("SPORTS_RN1_SLEEVE_CAP_PCT", "0.01"))
MIN_HOURS_BEFORE_KICKOFF = float(os.getenv("SPORTS_RN1_MIN_HOURS_BEFORE_KICKOFF", "2.0"))
MAKER_TICK = float(os.getenv("SPORTS_RN1_MAKER_TICK", "0.01"))
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
