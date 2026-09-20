"""1X2 completeness gap on EU5 books."""

from __future__ import annotations

from typing import List

from src.strategies.csl_explore.models import ExploreSignal, MatchBundle
from src.strategies.eu5_plus.config import COMPLETENESS_MIN_GAP, ORDER_USDC


class CompletenessStrategy:
    id = "completeness"

    def generate(self, match: MatchBundle, *, context: dict) -> List[ExploreSignal]:
        s = match.three_way_sum()
        if s is None or match.home_win is None or match.away_win is None or match.draw is None:
            return [ExploreSignal(
                strategy=self.id, match_slug=match.slug, match_title=match.title,
                action="skip", reason="incomplete three-way", usdc=0.0, priority=90,
            )]
        gap = 1.0 - s
        if gap < COMPLETENESS_MIN_GAP:
            return [ExploreSignal(
                strategy=self.id, match_slug=match.slug, match_title=match.title,
                action="skip",
                reason=f"no gap sum={s:.3f} gap={gap:.3f}",
                usdc=0.0, priority=70, meta={"sum": s, "gap": gap},
            )]
        per = min(ORDER_USDC, max(0.34, ORDER_USDC / 3))
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
                reason=f"1X2 completeness gap={gap:.3f} leg={leg}",
                usdc=round(per, 4),
                condition_id=mkt.condition_id,
                question=mkt.question,
                yes_token=mkt.yes_token,
                limit_price=mkt.yes_price,
                priority=12,
                meta={"sum": s, "gap": gap, "leg": leg},
            ))
        return out
