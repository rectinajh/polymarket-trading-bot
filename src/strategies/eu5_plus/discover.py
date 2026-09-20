"""Discover EU5 match bundles (1X2 + totals + spreads) from Gamma."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set

from src.clients.gamma_client import GammaClient, KNOWN_TAG_IDS
from src.strategies.csl_explore.models import BinaryMarket, MatchBundle
from src.strategies.eu5_plus.config import (
    EU5_SLUG_PREFIXES,
    EU5_TAG_SLUGS,
    MAX_DAYS_AHEAD,
    MIN_MINUTES_BEFORE,
)
from src.strategies.lottery.discover import LOTTERY_TAG_IDS

WIN_ON_RE = re.compile(
    r"^Will (.+?) win on (\d{4}-\d{2}-\d{2})\?\s*$",
    re.IGNORECASE,
)
DRAW_RE = re.compile(r"end in a draw", re.IGNORECASE)
EXACT_RE = re.compile(r"exact score|score:", re.IGNORECASE)
OU_RE = re.compile(r"o/u\s*([0-9.]+)|over/under", re.IGNORECASE)
SPREAD_RE = re.compile(r"spread:", re.IGNORECASE)
BTTS_RE = re.compile(r"both teams? (to )?score|btts", re.IGNORECASE)


def _parse_prices(m: Dict[str, Any]) -> tuple[float, float]:
    prices = m.get("_outcome_prices") or m.get("outcomePrices")
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
    toks = m.get("_token_ids") or m.get("clobTokenIds")
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
    vol = m.get("_volume_num")
    if vol is None:
        try:
            vol = float(m.get("volume") or 0)
        except (TypeError, ValueError):
            vol = 0.0
    return BinaryMarket(
        condition_id=str(m.get("_condition_id") or m.get("conditionId") or ""),
        question=str(m.get("question") or m.get("title") or ""),
        yes_price=yes,
        no_price=no,
        yes_token=yt,
        no_token=nt,
        volume=float(vol or 0),
        neg_risk=bool(m.get("negRisk") or m.get("neg_risk")),
        group_item=str(m.get("groupItemTitle") or ""),
    )


def _is_eu5_event(slug: str, tag_slugs: Set[str], title: str) -> bool:
    s = (slug or "").lower()
    if any(s.startswith(p) for p in EU5_SLUG_PREFIXES):
        return True
    if tag_slugs & EU5_TAG_SLUGS:
        return True
    blob = f"{slug} {title}".lower()
    return any(
        k in blob
        for k in (
            "premier league", "la liga", "serie a", "bundesliga", "ligue 1", "ucl",
        )
    )


def _event_key(m: Dict[str, Any]) -> tuple[str, str, Set[str], Optional[float], float]:
    events = m.get("events") or []
    ev = events[0] if events and isinstance(events[0], dict) else {}
    slug = str(ev.get("slug") or m.get("slug") or "").lower()
    title = str(ev.get("title") or "")
    tags: Set[str] = set()
    for t in ev.get("tags") or []:
        if isinstance(t, dict):
            s = (t.get("slug") or t.get("label") or "").strip().lower()
            if s:
                tags.add(s)
    start_ts = None
    for key in ("startTime", "startDate", "endDate"):
        raw = ev.get(key)
        if not raw:
            continue
        try:
            if isinstance(raw, (int, float)):
                start_ts = float(raw)
            else:
                dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                start_ts = dt.timestamp()
            break
        except ValueError:
            continue
    if start_ts is None and m.get("_end_ts"):
        try:
            start_ts = float(m["_end_ts"])
        except (TypeError, ValueError):
            pass
    try:
        volume = float(ev.get("volume") or m.get("_volume_num") or 0)
    except (TypeError, ValueError):
        volume = 0.0
    return slug, title, tags, start_ts, volume


def _classify_group(
    slug: str,
    title: str,
    start_ts: Optional[float],
    volume: float,
    markets: List[Dict[str, Any]],
) -> MatchBundle:
    win_markets: List[BinaryMarket] = []
    draw: Optional[BinaryMarket] = None
    totals: List[BinaryMarket] = []
    exact: List[BinaryMarket] = []
    spreads: List[BinaryMarket] = []
    btts: List[BinaryMarket] = []
    raw: List[Dict[str, Any]] = []

    for m in markets:
        raw.append(m)
        q = str(m.get("question") or "")
        b = _to_binary(m)
        if not b.condition_id:
            continue
        if DRAW_RE.search(q):
            draw = b
        elif WIN_ON_RE.match(q):
            win_markets.append(b)
        elif EXACT_RE.search(q):
            exact.append(b)
        elif SPREAD_RE.search(q):
            spreads.append(b)
        elif BTTS_RE.search(q):
            btts.append(b)
        elif OU_RE.search(q) or "o/u" in q.lower():
            totals.append(b)

    home_win: Optional[BinaryMarket] = None
    away_win: Optional[BinaryMarket] = None
    if len(win_markets) >= 2:
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

    bundle = MatchBundle(
        slug=slug,
        title=title or slug,
        start_ts=start_ts,
        volume=volume,
        home_win=home_win,
        away_win=away_win,
        draw=draw,
        totals=totals,
        exact_scores=exact,
        raw_markets=raw,
    )
    # Stash extras on object for line_cross / narrative BTTS.
    bundle.spreads = spreads  # type: ignore[attr-defined]
    bundle.btts = btts  # type: ignore[attr-defined]
    return bundle


async def fetch_eu5_match_bundles(
    gamma: GammaClient,
    *,
    max_days: float = MAX_DAYS_AHEAD,
    min_minutes: float = MIN_MINUTES_BEFORE,
    max_results: int = 900,
) -> List[MatchBundle]:
    """Return EU5 fixtures with classified markets."""
    tags = sorted(set(LOTTERY_TAG_IDS) | {KNOWN_TAG_IDS.get("soccer", 100350)})
    markets = await gamma.get_markets(
        active=True,
        closed=False,
        archived=False,
        accepting_orders=True,
        tag_ids=tags,
        max_time_to_expiry_days=max_days,
        min_time_to_expiry_minutes=min_minutes,
        order="volume24hr",
        ascending=False,
        max_results=max_results,
    )

    groups: Dict[str, Dict[str, Any]] = {}
    for m in markets:
        slug, title, tag_slugs, start_ts, volume = _event_key(m)
        if not slug or not _is_eu5_event(slug, tag_slugs, title):
            continue
        g = groups.setdefault(slug, {
            "slug": slug,
            "title": title,
            "start_ts": start_ts,
            "volume": volume,
            "markets": [],
        })
        if title and len(title) > len(g["title"] or ""):
            g["title"] = title
        if start_ts and (g["start_ts"] is None or start_ts < g["start_ts"]):
            g["start_ts"] = start_ts
        g["volume"] = max(float(g["volume"] or 0), float(volume or 0))
        g["markets"].append(m)

    out: List[MatchBundle] = []
    for g in groups.values():
        # Need at least a moneyline or draw to be useful.
        b = _classify_group(
            g["slug"], g["title"], g["start_ts"], g["volume"], g["markets"],
        )
        if b.home_win or b.away_win or b.draw or b.totals or getattr(b, "spreads", None):
            out.append(b)
    out.sort(key=lambda x: (x.start_ts or 1e18, x.slug))
    return out
