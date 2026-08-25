"""Match Polymarket team names to The Odds API reference events."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import date
from difflib import SequenceMatcher
from typing import Dict, List, Optional, Sequence

from src.clients.odds_api_client import ReferenceEvent
from src.strategies.sports.discover import MatchWinnerMarket

_STRIP_SUFFIXES = (
    " fc",
    " cf",
    " sc",
    " afc",
    " club",
    " saudi club",
    " united fc",
    " city fc",
)


def normalize_team(name: str) -> str:
    """Lowercase, strip accents and common suffixes for fuzzy match."""
    text = unicodedata.normalize("NFKD", (name or "").lower())
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    for suffix in _STRIP_SUFFIXES:
        if text.endswith(suffix):
            text = text[: -len(suffix)].strip()
    return text


def _similar(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    if a in b or b in a:
        return 0.92
    return SequenceMatcher(None, a, b).ratio()


@dataclass(frozen=True)
class MatchedReference:
    event: ReferenceEvent
    team_name: str
    fair_prob: float
    match_score: float


def match_market_to_reference(
    market: MatchWinnerMarket,
    events: Sequence[ReferenceEvent],
    *,
    min_score: float = 0.72,
) -> Optional[MatchedReference]:
    """Find Pinnacle fair win prob for market.team on market.match_date."""
    target = normalize_team(market.team)
    if not target:
        return None

    best: Optional[MatchedReference] = None
    for event in events:
        if event.commence_time.date() != market.match_date:
            continue

        candidates = (
            (event.home_team, event.outcomes.get(event.home_team)),
            (event.away_team, event.outcomes.get(event.away_team)),
        )
        for team_name, outcome in candidates:
            if not outcome:
                continue
            score = _similar(target, normalize_team(team_name))
            if score < min_score:
                continue
            if best is None or score > best.match_score:
                best = MatchedReference(
                    event=event,
                    team_name=team_name,
                    fair_prob=outcome.fair_prob,
                    match_score=score,
                )
    return best


def index_events_by_date(events: Sequence[ReferenceEvent]) -> Dict[date, List[ReferenceEvent]]:
    out: Dict[date, List[ReferenceEvent]] = {}
    for event in events:
        d = event.commence_time.date()
        out.setdefault(d, []).append(event)
    return out
