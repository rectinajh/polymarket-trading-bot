"""Discover cheap YES tickets for the lottery sleeve."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Set

from src.clients.gamma_client import GammaClient, KNOWN_TAG_IDS
from src.strategies.lottery.config import (
    EU5_HINTS,
    MAX_DAYS_AHEAD,
    MIN_MINUTES_BEFORE,
    PRICE_MAX,
    PRICE_MIN,
    REQUIRE_EU5_HINT,
)

# Soccer only — never include sports=1 (floods NFL and crowds out soccer).
LOTTERY_TAG_IDS = sorted({
    KNOWN_TAG_IDS.get("soccer", 100350),
    KNOWN_TAG_IDS.get("epl", 306),
})

# Event slug prefixes / tag slugs for top-5 EU leagues (+ UCL).
EU5_SLUG_PREFIXES = (
    "epl-", "lal-", "lla-", "sea-", "bun-", "bl1-", "fl1-", "lig-",
    "ucl-", "uel-", "cdf-",  # cup fixtures still EU5 clubs
)
EU5_TAG_SLUGS = {
    "epl", "premier-league", "la-liga", "laliga", "serie-a", "sea",
    "bundesliga", "ligue-1", "ligue1", "ucl", "champions-league",
    "europa-league",
}

WIN_ON_DATE = re.compile(
    r"^Will (.+?) win on (\d{4}-\d{2}-\d{2})\?\s*$",
    re.IGNORECASE,
)
DRAW_MATCH = re.compile(
    r"^Will (.+?) end in a draw\?\s*$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class LotteryTicket:
    condition_id: str
    question: str
    label: str
    yes_price: float
    no_price: float
    end_ts: Optional[float]
    neg_risk: bool
    tick_size: float
    yes_token: str
    no_token: str
    kind: str  # win | draw
    raw: Dict[str, Any]


def _eu5_blob(text: str) -> bool:
    t = (text or "").lower()
    return any(h in t for h in EU5_HINTS)


def _event_meta(m: Dict[str, Any]) -> tuple[str, str, Set[str]]:
    """Return (event_slug, event_title, tag_slugs)."""
    events = m.get("events") or []
    ev = events[0] if events and isinstance(events[0], dict) else {}
    slug = str(ev.get("slug") or m.get("slug") or "").lower()
    title = str(ev.get("title") or "").lower()
    tag_slugs: Set[str] = set()
    for t in ev.get("tags") or []:
        if isinstance(t, dict):
            s = (t.get("slug") or t.get("label") or "").strip().lower()
            if s:
                tag_slugs.add(s)
    return slug, title, tag_slugs


def _is_eu5_market(m: Dict[str, Any], question: str, label: str) -> bool:
    slug, title, tag_slugs = _event_meta(m)
    if any(slug.startswith(p) for p in EU5_SLUG_PREFIXES):
        return True
    if tag_slugs & EU5_TAG_SLUGS:
        return True
    blob = f"{question} {label} {slug} {title}"
    return _eu5_blob(blob)


def _parse_ticket(m: Dict[str, Any]) -> Optional[LotteryTicket]:
    q = (m.get("question") or m.get("title") or "").strip()
    prices = m.get("_outcome_prices") or m.get("outcomePrices")
    if not prices or len(prices) < 2:
        return None
    try:
        yes_price = float(prices[0])
        no_price = float(prices[1])
    except (TypeError, ValueError):
        return None
    if not (PRICE_MIN <= yes_price <= PRICE_MAX):
        return None

    yes_tok, no_tok = m.get("_token_ids") or (None, None)
    if not yes_tok or not no_tok:
        return None
    cond = m.get("_condition_id") or m.get("conditionId") or ""
    if not cond:
        return None

    kind = ""
    label = ""
    mo = WIN_ON_DATE.match(q)
    if mo:
        kind = "win"
        label = mo.group(1).strip()
    else:
        mo2 = DRAW_MATCH.match(q)
        if mo2:
            kind = "draw"
            label = mo2.group(1).strip()
    if kind not in ("win", "draw"):
        return None  # spreads / O/U are not lottery tickets

    if REQUIRE_EU5_HINT and not _is_eu5_market(m, q, label):
        return None

    tick = m.get("orderPriceMinTickSize") or m.get("minimum_tick_size") or 0.01
    try:
        tick_size = float(tick)
    except (TypeError, ValueError):
        tick_size = 0.01

    return LotteryTicket(
        condition_id=str(cond),
        question=q,
        label=label,
        yes_price=yes_price,
        no_price=no_price,
        end_ts=m.get("_end_ts"),
        neg_risk=bool(m.get("negRisk") or m.get("neg_risk")),
        tick_size=tick_size,
        yes_token=str(yes_tok),
        no_token=str(no_tok),
        kind=kind,
        raw=m,
    )


async def fetch_lottery_tickets(
    gamma: GammaClient,
    *,
    max_days: float = MAX_DAYS_AHEAD,
    min_minutes: float = MIN_MINUTES_BEFORE,
    max_results: int = 800,
) -> List[LotteryTicket]:
    markets = await gamma.get_markets(
        active=True,
        closed=False,
        archived=False,
        accepting_orders=True,
        tag_ids=LOTTERY_TAG_IDS,
        max_time_to_expiry_days=max_days,
        min_time_to_expiry_minutes=min_minutes,
        order="volume24hr",
        ascending=False,
        max_results=max_results,
    )
    out: List[LotteryTicket] = []
    seen: set = set()
    for m in markets:
        t = _parse_ticket(m)
        if not t or t.condition_id in seen:
            continue
        seen.add(t.condition_id)
        out.append(t)
    out.sort(key=lambda x: x.yes_price)
    return out
