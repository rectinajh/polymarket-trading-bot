"""Discover Polymarket soccer match-winner markets for RN1."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Any, Dict, List, Optional, Sequence

from src.clients.gamma_client import GammaClient, KNOWN_TAG_IDS

# "Will Wolverhampton Wanderers FC win on 2026-08-25?"
WIN_ON_DATE = re.compile(
    r"^Will (.+?) win on (\d{4}-\d{2}-\d{2})\?\s*$",
    re.IGNORECASE,
)

# Soccer / EPL / UCL only — do NOT include sports=1 (NFL flood).
SOCCER_TAG_IDS = sorted({
    KNOWN_TAG_IDS.get("soccer", 100350),
    KNOWN_TAG_IDS.get("epl", 306),
    KNOWN_TAG_IDS.get("ucl", 100977),
})


@dataclass(frozen=True)
class MatchWinnerMarket:
    """One Polymarket binary: Will {team} win on {date}?"""

    condition_id: str
    question: str
    team: str
    match_date: date
    yes_price: float
    no_price: float
    end_ts: Optional[float]
    volume: float
    neg_risk: bool
    tick_size: float
    yes_token: str
    no_token: str
    raw: Dict[str, Any]


async def fetch_match_winner_markets(
    gamma: GammaClient,
    *,
    max_days: float = 7.0,
    min_minutes: float = 120.0,
    max_results: int = 800,
) -> List[MatchWinnerMarket]:
    """Return active soccer 'Will X win on YYYY-MM-DD?' markets."""
    markets = await gamma.get_markets(
        active=True,
        closed=False,
        archived=False,
        accepting_orders=True,
        tag_ids=SOCCER_TAG_IDS,
        max_time_to_expiry_days=max_days,
        min_time_to_expiry_minutes=min_minutes,
        order="volume24hr",
        ascending=False,
        max_results=max_results,
    )
    out: List[MatchWinnerMarket] = []
    for m in markets:
        parsed = parse_match_winner_market(m)
        if parsed:
            out.append(parsed)
    return out


def parse_match_winner_market(m: Dict[str, Any]) -> Optional[MatchWinnerMarket]:
    q = (m.get("question") or m.get("title") or "").strip()
    mo = WIN_ON_DATE.match(q)
    if not mo:
        return None

    team, date_str = mo.groups()
    try:
        match_date = date.fromisoformat(date_str)
    except ValueError:
        return None

    prices = m.get("_outcome_prices") or m.get("outcomePrices")
    if not prices or len(prices) < 2:
        return None
    try:
        yes_price = float(prices[0])
        no_price = float(prices[1])
    except (TypeError, ValueError):
        return None

    yes_tok, no_tok = m.get("_token_ids") or (None, None)
    if not yes_tok or not no_tok:
        return None

    cond = m.get("_condition_id") or m.get("conditionId") or ""
    if not cond:
        return None

    tick = m.get("orderPriceMinTickSize") or m.get("minimum_tick_size") or 0.01
    try:
        tick_size = float(tick)
    except (TypeError, ValueError):
        tick_size = 0.01

    return MatchWinnerMarket(
        condition_id=str(cond),
        question=q,
        team=team.strip(),
        match_date=match_date,
        yes_price=yes_price,
        no_price=no_price,
        end_ts=m.get("_end_ts"),
        volume=float(m.get("_volume_num") or 0.0),
        neg_risk=bool(m.get("negRisk") or m.get("neg_risk")),
        tick_size=tick_size,
        yes_token=str(yes_tok),
        no_token=str(no_tok),
        raw=m,
    )


def in_favorite_band(
    yes_price: float,
    *,
    lo: float,
    hi: float,
) -> bool:
    return lo <= yes_price <= hi
