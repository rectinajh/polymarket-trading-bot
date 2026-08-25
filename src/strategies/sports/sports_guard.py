"""Sports sleeve stop-loss and 90-day experiment guardrails."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, Optional, Tuple

from src.strategies.capital_policy import CN_TZ, trading_day
from src.strategies.sports.config import (
    SPORTS_EXPERIMENT_DAYS,
    SPORTS_MAX_DAILY_LOSS_PCT,
    SPORTS_MAX_DRAWDOWN_PCT,
)
from src.strategies.sports.sports_pnl import SportsPnL


def _parse_day(day_str: str) -> Optional[datetime]:
    try:
        return datetime.strptime(day_str, "%Y-%m-%d").replace(tzinfo=CN_TZ)
    except ValueError:
        return None


def check_trading_allowed(
    *,
    nav_cents: int,
    pnl: Optional[SportsPnL] = None,
) -> Tuple[bool, str, Dict[str, Any]]:
    """Return (allowed, reason, meta) before placing new sports entries."""
    ledger = pnl or SportsPnL()
    exp = ledger.experiment_summary()
    meta: Dict[str, Any] = dict(exp)

    start = exp.get("experiment_start") or trading_day()
    start_dt = _parse_day(str(start))
    if start_dt:
        days_elapsed = (datetime.now(CN_TZ).date() - start_dt.date()).days
        meta["days_elapsed"] = days_elapsed
        meta["days_remaining"] = max(0, SPORTS_EXPERIMENT_DAYS - days_elapsed)
        if days_elapsed >= SPORTS_EXPERIMENT_DAYS:
            return False, f"experiment ended ({SPORTS_EXPERIMENT_DAYS}d)", meta

    nav = max(1, int(nav_cents))
    daily_loss_limit = int(nav * SPORTS_MAX_DAILY_LOSS_PCT)
    daily_pnl = ledger.daily_realized_cents()
    meta["daily_realized_cents"] = daily_pnl
    meta["daily_loss_limit_cents"] = daily_loss_limit
    if daily_pnl < 0 and abs(daily_pnl) >= daily_loss_limit:
        return (
            False,
            f"daily loss ${abs(daily_pnl)/100:.2f} ≥ limit ${daily_loss_limit/100:.2f}",
            meta,
        )

    realized = int(exp.get("realized_pnl_cents") or 0)
    peak = int(exp.get("peak_pnl_cents") or 0)
    drawdown = int(exp.get("drawdown_cents") or 0)
    deployed = max(1, int(exp.get("deployed_cents") or 0))
    dd_limit = max(int(deployed * SPORTS_MAX_DRAWDOWN_PCT), int(nav * 0.05))
    meta["drawdown_cents"] = drawdown
    meta["drawdown_limit_cents"] = dd_limit
    meta["realized_pnl_cents"] = realized

    if realized < 0 and abs(realized) >= dd_limit:
        return (
            False,
            f"experiment drawdown ${abs(realized)/100:.2f} ≥ limit ${dd_limit/100:.2f}",
            meta,
        )

    if peak > 0 and drawdown >= int(peak * SPORTS_MAX_DRAWDOWN_PCT):
        return (
            False,
            f"peak drawdown ${drawdown/100:.2f} from high-water ${peak/100:.2f}",
            meta,
        )

    return True, "ok", meta
