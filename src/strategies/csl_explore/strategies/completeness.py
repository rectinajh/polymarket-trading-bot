"""Three-way completeness gap scan."""

from __future__ import annotations

from typing import List

from src.strategies.csl_explore.config import COMPLETENESS_MIN_GAP, ORDER_USDC
from src.strategies.csl_explore.models import ExploreSignal, MatchBundle


class CompletenessStrategy:
    id = "completeness"

    def __init__(
        self,
        min_gap: float = COMPLETENESS_MIN_GAP,
        usdc: float = ORDER_USDC,
    ) -> None:
        self.min_gap = min_gap
        self.usdc = usdc

    def generate(self, match: MatchBundle, *, context: dict) -> List[ExploreSignal]:
        s = match.three_way_sum()
        if s is None or match.home_win is None or match.away_win is None or match.draw is None:
            return [ExploreSignal(
                strategy=self.id,
                match_slug=match.slug,
                match_title=match.title,
                action="skip",
                reason="incomplete three-way book",
                usdc=0.0,
                priority=90,
            )]
        gap = 1.0 - s
        if gap < self.min_gap:
            return [ExploreSignal(
                strategy=self.id,
                match_slug=match.slug,
                match_title=match.title,
                action="skip",
                reason=f"no gap (sum={s:.3f} gap={gap:.3f} < {self.min_gap})",
                usdc=0.0,
                priority=70,
                meta={"sum": s, "gap": gap},
            )]
        # Emit three legs; orchestrator may cap total.
        per = min(self.usdc, max(0.34, self.usdc / 3))
        out: List[ExploreSignal] = []
        for leg, mkt in (
            ("home", match.home_win),
            ("draw", match.draw),
            ("away", match.away_win),
        ):
            out.append(ExploreSignal(
                strategy=self.id,
                match_slug=match.slug,
                match_title=match.title,
                action="buy_yes",
                reason=f"completeness gap={gap:.3f} leg={leg}",
                usdc=round(per, 4),
                condition_id=mkt.condition_id,
                question=mkt.question,
                yes_token=mkt.yes_token,
                limit_price=mkt.yes_price,
                priority=10,
                meta={"sum": s, "gap": gap, "leg": leg},
            ))
        return out
