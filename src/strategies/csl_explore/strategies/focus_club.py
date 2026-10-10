"""Buy the focus club's win YES when they appear in a CSL fixture."""

from __future__ import annotations

import re
from typing import List, Optional, Sequence, Tuple

from src.strategies.csl_explore.config import (
    FOCUS_CLUB_PRICE_MAX,
    FOCUS_CLUB_PRICE_MIN,
    FOCUS_TEAMS,
    ORDER_USDC,
)
from src.strategies.csl_explore.models import BinaryMarket, ExploreSignal, MatchBundle


def _norm(text: str) -> str:
    t = (text or "").lower()
    t = re.sub(r"[^a-z0-9\s]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def match_involves_focus(
    match: MatchBundle,
    focus: Sequence[str] = FOCUS_TEAMS,
) -> List[str]:
    """Return which focus keywords hit this match (title or slug)."""
    blob = _norm(f"{match.title} {match.slug}")
    hits: List[str] = []
    for key in focus:
        k = _norm(key)
        if not k:
            continue
        # slug short codes: ron / tie / ton as whole slug tokens
        if len(k) <= 3 and re.search(rf"(^|-){re.escape(k)}(-|$)", match.slug.lower()):
            hits.append(key)
            continue
        if k in blob:
            hits.append(key)
    return hits


def _team_market(
    match: MatchBundle,
    focus_key: str,
) -> Optional[Tuple[str, BinaryMarket]]:
    """Pick home/away win market that belongs to the focus club."""
    key = _norm(focus_key)
    # Prefer matching Will X win question / title side.
    for side, mkt in (("home", match.home_win), ("away", match.away_win)):
        if mkt is None:
            continue
        q = _norm(mkt.question)
        # "will chengdu rongcheng fc win on ..."
        if key and key in q:
            return side, mkt
    # Fall back: title "A vs. B" — which side contains the key?
    parts = re.split(r"\s+vs\.?\s+", match.title, flags=re.IGNORECASE)
    if len(parts) >= 2:
        home_n, away_n = _norm(parts[0]), _norm(parts[1])
        if key in home_n and match.home_win:
            return "home", match.home_win
        if key in away_n and match.away_win:
            return "away", match.away_win
    # Slug codes: chi-{home}-{away}-date
    m = re.match(r"^chi-([a-z0-9]+)-([a-z0-9]+)-\d{4}-\d{2}-\d{2}$", match.slug.lower())
    if m and len(key) <= 3:
        if m.group(1) == key and match.home_win:
            return "home", match.home_win
        if m.group(2) == key and match.away_win:
            return "away", match.away_win
    return None


class FocusClubStrategy:
    """Primary sleeve: stake the named clubs' win markets."""

    id = "focus_club"

    def __init__(
        self,
        focus: Sequence[str] = FOCUS_TEAMS,
        usdc: float = ORDER_USDC,
        price_min: float = FOCUS_CLUB_PRICE_MIN,
        price_max: float = FOCUS_CLUB_PRICE_MAX,
    ) -> None:
        self.focus = tuple(focus)
        self.usdc = usdc
        self.price_min = price_min
        self.price_max = price_max

    def generate(self, match: MatchBundle, *, context: dict) -> List[ExploreSignal]:
        hits = match_involves_focus(match, self.focus)
        if not hits:
            return [ExploreSignal(
                strategy=self.id,
                match_slug=match.slug,
                match_title=match.title,
                action="skip",
                reason="not a focus club match",
                usdc=0.0,
                priority=95,
            )]
        # Prefer longer / more specific keyword first.
        for key in sorted(hits, key=lambda k: len(k), reverse=True):
            picked = _team_market(match, key)
            if not picked:
                continue
            side, mkt = picked
            px = float(mkt.yes_price or 0)
            if px <= 0:
                continue
            if not (self.price_min <= px <= self.price_max):
                return [ExploreSignal(
                    strategy=self.id,
                    match_slug=match.slug,
                    match_title=match.title,
                    action="skip",
                    reason=f"focus {key} {side} px={px:.3f} outside "
                           f"[{self.price_min:.2f},{self.price_max:.2f}]",
                    usdc=0.0,
                    priority=40,
                    meta={"focus": key, "side": side, "price": px},
                )]
            return [ExploreSignal(
                strategy=self.id,
                match_slug=match.slug,
                match_title=match.title,
                action="buy_yes",
                reason=f"focus_club {key} {side} win @{px:.3f}",
                usdc=self.usdc,
                condition_id=mkt.condition_id,
                question=mkt.question,
                yes_token=mkt.yes_token,
                limit_price=px,
                priority=10,
                meta={"focus": key, "side": side, "price": px},
            )]
        return [ExploreSignal(
            strategy=self.id,
            match_slug=match.slug,
            match_title=match.title,
            action="skip",
            reason=f"focus hit {hits} but no win market",
            usdc=0.0,
            priority=80,
        )]
