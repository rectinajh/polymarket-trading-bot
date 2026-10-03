"""Match Polymarket team names to The Odds API reference events."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import date, timedelta
from difflib import SequenceMatcher
from typing import Dict, List, Optional, Sequence

from src.clients.odds_api_client import ReferenceEvent
from src.strategies.sports.discover import MatchWinnerMarket

# Strip club suffixes only. Never strip " city" / " united" as a blob —
# that turned "Manchester City FC" into "manchester" and collided with United.
_STRIP_SUFFIXES = (
    " saudi club",
    " football club",
    " soccer club",
    " afc",
    " cf",
    " sc",
    " fc",
    " club",
)

_ALIASES = {
    "man city": "manchester city",
    "mcfc": "manchester city",
    "man utd": "manchester united",
    "man united": "manchester united",
    "manchester utd": "manchester united",
    "mufc": "manchester united",
    "spurs": "tottenham",
    "tottenham hotspur": "tottenham",
    "wolves": "wolverhampton",
    "wolverhampton wanderers": "wolverhampton",
    "nottm forest": "nottingham forest",
    "nottingham forest": "nottingham forest",
    "psg": "paris saint germain",
    "paris sg": "paris saint germain",
    "paris saint germain": "paris saint germain",
    "inter": "internazionale",
    "inter milan": "internazionale",
    "fc internazionale": "internazionale",
    "athletic club": "athletic bilbao",
    "athletic bilbao": "athletic bilbao",
    "brighton hove albion": "brighton",
    "brighton and hove albion": "brighton",
    "west ham united": "west ham",
    "newcastle united": "newcastle",
    "leeds united": "leeds",
    "leicester city": "leicester",
    "norwich city": "norwich",
    "ipswich town": "ipswich",
    "sheffield wednesday": "sheffield wednesday",
    "sheffield united": "sheffield united",
    "notts county": "notts county",
    "bayern": "bayern munich",
    "bayern munchen": "bayern munich",
    "fc bayern munich": "bayern munich",
    "borussia dortmund": "dortmund",
    "bvb": "dortmund",
    "rb leipzig": "leipzig",
    "rasenballsport leipzig": "leipzig",
    "eintracht frankfurt": "frankfurt",
    "bayer leverkusen": "leverkusen",
    "tsg hoffenheim": "hoffenheim",
    "borussia monchengladbach": "monchengladbach",
    "gladbach": "monchengladbach",
    "atletico madrid": "atletico madrid",
    "atletico de madrid": "atletico madrid",
    "real sociedad": "real sociedad",
    "real betis": "real betis",
    "ac milan": "milan",
    "as roma": "roma",
    "ss lazio": "lazio",
    "olympique marseille": "marseille",
    "olympique lyonnais": "lyon",
    "ogc nice": "nice",
}


def normalize_team(name: str) -> str:
    """Lowercase, strip accents and club suffixes for fuzzy match."""
    text = unicodedata.normalize("NFKD", (name or "").lower())
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    for suffix in _STRIP_SUFFIXES:
        if text.endswith(suffix):
            text = text[: -len(suffix)].strip()
    return _ALIASES.get(text, text)


def _similar(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    # Require a shared token so "manchester" cannot match both City and United.
    tokens_a = set(a.split())
    tokens_b = set(b.split())
    if tokens_a and tokens_b and tokens_a.isdisjoint(tokens_b):
        return SequenceMatcher(None, a, b).ratio() * 0.5
    if a in b or b in a:
        # Prefer the longer name remaining distinctive (city vs united).
        extra = tokens_a.symmetric_difference(tokens_b)
        if extra & {"city", "united", "hotspur", "wanderers", "forest"}:
            return 0.68  # below default min_score 0.72
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
    date_slack_days: int = 1,
) -> Optional[MatchedReference]:
    """Find Pinnacle fair win prob for market.team near market.match_date."""
    target = normalize_team(market.team)
    if not target:
        return None

    best: Optional[MatchedReference] = None
    best_date_penalty = 99
    slack = max(0, int(date_slack_days))
    for event in events:
        delta = abs((event.commence_time.date() - market.match_date).days)
        if delta > slack:
            continue

        candidates = (
            (event.home_team, event.outcomes.get(event.home_team)),
            (event.away_team, event.outcomes.get(event.away_team)),
        )
        for team_name, outcome in candidates:
            if not outcome:
                # Odds API keys are raw names; also try normalized lookup.
                outcome = event.outcomes.get(team_name)
            if not outcome:
                continue
            score = _similar(target, normalize_team(team_name))
            if score < min_score:
                continue
            # Prefer exact calendar date, then higher name score.
            better = False
            if best is None:
                better = True
            elif delta < best_date_penalty:
                better = True
            elif delta == best_date_penalty and score > best.match_score:
                better = True
            if better:
                best = MatchedReference(
                    event=event,
                    team_name=team_name,
                    fair_prob=outcome.fair_prob,
                    match_score=score,
                )
                best_date_penalty = delta
    return best


def index_events_by_date(events: Sequence[ReferenceEvent]) -> Dict[date, List[ReferenceEvent]]:
    out: Dict[date, List[ReferenceEvent]] = {}
    for event in events:
        d = event.commence_time.date()
        out.setdefault(d, []).append(event)
        # Mirror ±1 day buckets so callers using this index still find TZ slips.
        out.setdefault(d - timedelta(days=1), []).append(event)
        out.setdefault(d + timedelta(days=1), []).append(event)
    return out
