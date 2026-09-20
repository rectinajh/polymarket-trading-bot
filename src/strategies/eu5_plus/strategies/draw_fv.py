"""Draw fair-value vs Pinnacle de-vigged draw."""

from __future__ import annotations

from typing import List

from src.strategies.csl_explore.models import ExploreSignal, MatchBundle
from src.strategies.eu5_plus.config import (
    DRAW_MIN_EDGE,
    DRAW_PRICE_MAX,
    DRAW_PRICE_MIN,
    ORDER_USDC,
)
from src.strategies.eu5_plus.ref import draw_fair


class DrawFvStrategy:
    id = "draw_fv"

    def generate(self, match: MatchBundle, *, context: dict) -> List[ExploreSignal]:
        if match.draw is None or match.draw_px is None:
            return [ExploreSignal(
                strategy=self.id, match_slug=match.slug, match_title=match.title,
                action="skip", reason="no draw market", usdc=0.0, priority=90,
            )]
        px = float(match.draw_px)
        if not (DRAW_PRICE_MIN <= px <= DRAW_PRICE_MAX):
            return [ExploreSignal(
                strategy=self.id, match_slug=match.slug, match_title=match.title,
                action="skip", reason=f"draw px {px:.3f} out of band", usdc=0.0,
                priority=80, meta={"px": px},
            )]
        refs = context.get("refs") or []
        ref_map = context.get("ref_by_slug") or {}
        ev = ref_map.get(match.slug)
        fair = draw_fair(ev) if ev else None
        if fair is None:
            return [ExploreSignal(
                strategy=self.id, match_slug=match.slug, match_title=match.title,
                action="skip", reason="no pinnacle draw fair", usdc=0.0, priority=85,
            )]
        edge = fair - px
        if edge < DRAW_MIN_EDGE:
            return [ExploreSignal(
                strategy=self.id, match_slug=match.slug, match_title=match.title,
                action="skip",
                reason=f"draw edge {edge:.3f} < {DRAW_MIN_EDGE}",
                usdc=0.0, priority=70,
                meta={"fair": fair, "px": px, "edge": edge},
            )]
        return [ExploreSignal(
            strategy=self.id,
            match_slug=match.slug,
            match_title=match.title,
            action="buy_yes",
            reason=f"draw FV fair={fair:.3f} ask={px:.3f} edge={edge:.3f}",
            usdc=ORDER_USDC,
            condition_id=match.draw.condition_id,
            question=match.draw.question,
            yes_token=match.draw.yes_token,
            limit_price=px,
            priority=15,
            meta={"fair": fair, "edge": edge},
        )]
