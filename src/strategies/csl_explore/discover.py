"""Fetch CSL match bundles from Gamma by event slug."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence

import httpx

from src.strategies.csl_explore.config import EVENT_SLUGS, GAMMA_HOST
from src.strategies.csl_explore.models import BinaryMarket, MatchBundle

WIN_ON_RE = re.compile(
    r"^Will (.+?) win on (\d{4}-\d{2}-\d{2})\?\s*$",
    re.IGNORECASE,
)
DRAW_RE = re.compile(r"end in a draw", re.IGNORECASE)
EXACT_RE = re.compile(r"exact score|score:", re.IGNORECASE)
OU_RE = re.compile(r"o/u\s*([0-9.]+)|over/under|totals?", re.IGNORECASE)


def _parse_prices(m: Dict[str, Any]) -> tuple[float, float]:
    prices = m.get("outcomePrices") or m.get("_outcome_prices")
    if isinstance(prices, str):
        try:
            prices = json.loads(prices)
        except json.JSONDecodeError:
            prices = None
    if not prices or len(prices) < 2:
        return 0.0, 0.0
    try:
        return float(prices[0]), float(prices[1])
    except (TypeError, ValueError):
        return 0.0, 0.0


def _parse_tokens(m: Dict[str, Any]) -> tuple[str, str]:
    toks = m.get("clobTokenIds") or m.get("_token_ids")
    if isinstance(toks, str):
        try:
            toks = json.loads(toks)
        except json.JSONDecodeError:
            toks = None
    if isinstance(toks, (list, tuple)) and len(toks) >= 2:
        return str(toks[0]), str(toks[1])
    return "", ""


def _to_binary(m: Dict[str, Any]) -> BinaryMarket:
    yes, no = _parse_prices(m)
    yt, nt = _parse_tokens(m)
    vol = m.get("volumeNum")
    if vol is None:
        try:
            vol = float(m.get("volume") or 0)
        except (TypeError, ValueError):
            vol = 0.0
    return BinaryMarket(
        condition_id=str(m.get("conditionId") or m.get("condition_id") or ""),
        question=str(m.get("question") or m.get("title") or ""),
        yes_price=yes,
        no_price=no,
        yes_token=yt,
        no_token=nt,
        volume=float(vol or 0),
        neg_risk=bool(m.get("negRisk") or m.get("neg_risk")),
        group_item=str(m.get("groupItemTitle") or ""),
    )


def _parse_start_ts(event: Dict[str, Any]) -> Optional[float]:
    for key in ("startTime", "startDate", "endDate"):
        raw = event.get(key)
        if not raw:
            continue
        if isinstance(raw, (int, float)):
            return float(raw)
        try:
            dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.timestamp()
        except ValueError:
            continue
    return None


def classify_markets(
    event: Dict[str, Any],
    *,
    slug: str,
) -> MatchBundle:
    """Split event markets into home/away/draw/totals/exact."""
    title = str(event.get("title") or slug)
    markets = event.get("markets") or []
    win_markets: List[BinaryMarket] = []
    draw: Optional[BinaryMarket] = None
    totals: List[BinaryMarket] = []
    exact: List[BinaryMarket] = []
    raw: List[Dict[str, Any]] = []

    for m in markets:
        if not isinstance(m, dict):
            continue
        raw.append(m)
        q = str(m.get("question") or "")
        b = _to_binary(m)
        if DRAW_RE.search(q):
            draw = b
        elif WIN_ON_RE.match(q):
            win_markets.append(b)
        elif EXACT_RE.search(q):
            exact.append(b)
        elif OU_RE.search(q) or "o/u" in q.lower():
            totals.append(b)

    # Heuristic: higher yes among win markets ≈ favorite; map home/away by title order.
    home_win: Optional[BinaryMarket] = None
    away_win: Optional[BinaryMarket] = None
    if len(win_markets) >= 2:
        # Prefer matching team names from title "A vs. B"
        parts = re.split(r"\s+vs\.?\s+", title, flags=re.IGNORECASE)
        home_name = (parts[0] or "").strip().lower() if parts else ""
        away_name = (parts[1] or "").strip().lower() if len(parts) > 1 else ""
        for w in win_markets:
            mo = WIN_ON_RE.match(w.question)
            team = (mo.group(1) if mo else "").strip().lower()
            if home_name and team and (team in home_name or home_name in team):
                home_win = w
            elif away_name and team and (team in away_name or away_name in team):
                away_win = w
        leftovers = [w for w in win_markets if w not in (home_win, away_win)]
        if home_win is None and leftovers:
            home_win = leftovers.pop(0)
        if away_win is None and leftovers:
            away_win = leftovers.pop(0)
    elif len(win_markets) == 1:
        home_win = win_markets[0]

    vol = event.get("volume")
    try:
        volume = float(vol or 0)
    except (TypeError, ValueError):
        volume = 0.0

    return MatchBundle(
        slug=slug,
        title=title,
        start_ts=_parse_start_ts(event),
        volume=volume,
        home_win=home_win,
        away_win=away_win,
        draw=draw,
        totals=totals,
        exact_scores=exact,
        raw_markets=raw,
    )


async def fetch_event_by_slug(
    client: httpx.AsyncClient,
    slug: str,
    *,
    host: str = GAMMA_HOST,
) -> Optional[Dict[str, Any]]:
    url = f"{host}/events/slug/{slug}"
    r = await client.get(url, timeout=30.0)
    if r.status_code != 200:
        return None
    data = r.json()
    return data if isinstance(data, dict) else None


async def fetch_match_bundles(
    slugs: Optional[Sequence[str]] = None,
    *,
    host: str = GAMMA_HOST,
) -> List[MatchBundle]:
    slugs = tuple(slugs or EVENT_SLUGS)
    out: List[MatchBundle] = []
    async with httpx.AsyncClient() as client:
        for slug in slugs:
            event = await fetch_event_by_slug(client, slug, host=host)
            if not event:
                print(f"   [csl_explore] miss slug={slug}", flush=True)
                continue
            out.append(classify_markets(event, slug=slug))
    return out
