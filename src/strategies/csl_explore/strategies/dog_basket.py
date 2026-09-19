"""Optional: anti-favorite dog-win basket (disabled by default)."""

from __future__ import annotations

from typing import List

from src.strategies.csl_explore.config import NARRATIVE_TOSSUP_MAX_GAP, ORDER_USDC
from src.strategies.csl_explore.models import ExploreSignal, MatchBundle


class DogBasketStrategy:
    id = "dog_basket"

    def __init__(
        self,
        tossup_gap: float = NARRATIVE_TOSSUP_MAX_GAP,
        usdc: float = ORDER_USDC,
    ) -> None:
        self.tossup_gap = tossup_gap
        self.usdc = usdc

    def generate(self, match: MatchBundle, *, context: dict) -> List[ExploreSignal]:
        home, away = match.home_px, match.away_px
        if home is None or away is None or match.home_win is None or match.away_win is None:
            return [ExploreSignal(
                strategy=self.id,
                match_slug=match.slug,
                match_title=match.title,
                action="skip",
                reason="missing moneyline",
                usdc=0.0,
                priority=90,
            )]
        gap = abs(home - away)
        if gap <= self.tossup_gap:
            # Away win (anti home bias)
            mkt = match.away_win
            reason = "dog_basket toss-up → away"
        else:
            mkt = match.away_win if home >= away else match.home_win
            reason = "dog_basket underdog win"
        return [ExploreSignal(
            strategy=self.id,
            match_slug=match.slug,
            match_title=match.title,
            action="buy_yes",
            reason=reason,
            usdc=self.usdc,
            condition_id=mkt.condition_id,
            question=mkt.question,
            yes_token=mkt.yes_token,
            limit_price=mkt.yes_price,
            priority=50,
            meta={"home_px": home, "away_px": away},
        )]
