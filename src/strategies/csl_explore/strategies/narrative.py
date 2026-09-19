"""Narrative hedge: favorite+under or draw+under structure."""

from __future__ import annotations

from typing import List, Optional

from src.strategies.csl_explore.config import (
    NARRATIVE_FAVORITE_MIN,
    NARRATIVE_TOSSUP_MAX_GAP,
    ORDER_USDC,
)
from src.strategies.csl_explore.models import BinaryMarket, ExploreSignal, MatchBundle


def _pick_under(match: MatchBundle) -> Optional[BinaryMarket]:
    """Prefer Under Yes near 0.4–0.6 among totals."""
    unders = []
    for m in match.totals:
        q = (m.question or "").lower()
        gi = (m.group_item or "").lower()
        if "under" in q or gi.startswith("u") or " under" in q:
            unders.append(m)
        # Polymarket often uses Yes=Over on O/U — skip ambiguous
    if not unders:
        # fallback: market with "under" in question only
        unders = [m for m in match.totals if "under" in (m.question or "").lower()]
    if not unders:
        return None
    band = [m for m in unders if 0.35 <= m.yes_price <= 0.65]
    pool = band or unders
    return min(pool, key=lambda x: abs(x.yes_price - 0.5))


class NarrativeStrategy:
    id = "narrative"

    def __init__(
        self,
        favorite_min: float = NARRATIVE_FAVORITE_MIN,
        tossup_gap: float = NARRATIVE_TOSSUP_MAX_GAP,
        usdc: float = ORDER_USDC,
    ) -> None:
        self.favorite_min = favorite_min
        self.tossup_gap = tossup_gap
        self.usdc = usdc

    def generate(self, match: MatchBundle, *, context: dict) -> List[ExploreSignal]:
        half = round(self.usdc / 2.0, 4)
        under = _pick_under(match)
        home, away = match.home_px, match.away_px
        if home is None or away is None:
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
        fav_side = match.favorite_side()
        fav_px = max(home, away)

        signals: List[ExploreSignal] = []

        if fav_px >= self.favorite_min and fav_side:
            # Hot favorite: favorite win + under
            fav_mkt = match.home_win if fav_side == "home" else match.away_win
            if fav_mkt:
                signals.append(ExploreSignal(
                    strategy=self.id,
                    match_slug=match.slug,
                    match_title=match.title,
                    action="buy_yes",
                    reason=f"narrative favorite({fav_side})+under structure",
                    usdc=half if under else self.usdc,
                    condition_id=fav_mkt.condition_id,
                    question=fav_mkt.question,
                    yes_token=fav_mkt.yes_token,
                    limit_price=fav_mkt.yes_price,
                    priority=35,
                    meta={"leg": "favorite", "fav_px": fav_px},
                ))
        elif gap <= self.tossup_gap and match.draw is not None:
            signals.append(ExploreSignal(
                strategy=self.id,
                match_slug=match.slug,
                match_title=match.title,
                action="buy_yes",
                reason="narrative toss-up → draw+under",
                usdc=half if under else self.usdc,
                condition_id=match.draw.condition_id,
                question=match.draw.question,
                yes_token=match.draw.yes_token,
                limit_price=match.draw.yes_price,
                priority=35,
                meta={"leg": "draw", "gap": gap},
            ))
        else:
            # Soft favorite / away favorite: draw bias if available
            if match.draw is not None:
                signals.append(ExploreSignal(
                    strategy=self.id,
                    match_slug=match.slug,
                    match_title=match.title,
                    action="buy_yes",
                    reason="narrative mid-field → draw leg",
                    usdc=half if under else self.usdc,
                    condition_id=match.draw.condition_id,
                    question=match.draw.question,
                    yes_token=match.draw.yes_token,
                    limit_price=match.draw.yes_price,
                    priority=45,
                    meta={"leg": "draw_soft", "gap": gap},
                ))
            else:
                return [ExploreSignal(
                    strategy=self.id,
                    match_slug=match.slug,
                    match_title=match.title,
                    action="skip",
                    reason="no narrative template matched",
                    usdc=0.0,
                    priority=85,
                )]

        if under and signals:
            signals.append(ExploreSignal(
                strategy=self.id,
                match_slug=match.slug,
                match_title=match.title,
                action="buy_yes",
                reason="narrative under hedge leg",
                usdc=half,
                condition_id=under.condition_id,
                question=under.question,
                yes_token=under.yes_token,
                limit_price=under.yes_price,
                priority=36,
                meta={"leg": "under", "yes_px": under.yes_price},
            ))
        elif not under and signals:
            signals[0].meta["under_missing"] = True

        return signals
