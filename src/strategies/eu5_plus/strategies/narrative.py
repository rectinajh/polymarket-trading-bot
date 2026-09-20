"""Narrative hedge: favorite+under / draw+under / BTTS soft."""

from __future__ import annotations

from typing import List, Optional

from src.strategies.csl_explore.models import BinaryMarket, ExploreSignal, MatchBundle
from src.strategies.eu5_plus.config import (
    NARRATIVE_FAVORITE_MIN,
    NARRATIVE_TOSSUP_MAX_GAP,
    ORDER_USDC,
)


def _pick_under(match: MatchBundle) -> Optional[BinaryMarket]:
    unders = []
    for m in match.totals:
        q = (m.question or "").lower()
        gi = (m.group_item or "").lower()
        if "under" in q or gi.startswith("u"):
            unders.append(m)
    if not unders:
        return None
    band = [m for m in unders if 0.30 <= m.yes_price <= 0.65]
    pool = band or unders
    return min(pool, key=lambda x: abs(x.yes_price - 0.5))


def _pick_btts_no(match: MatchBundle) -> Optional[BinaryMarket]:
    """Prefer cheap 'both score' NO via buying low YES on BTTS if YES is BTTS=yes.

    Polymarket BTTS is usually Yes=both score. Buying Under-style: skip if yes high.
    We buy BTTS Yes only when cheap (upset scoring) — else skip.
    """
    for m in getattr(match, "btts", None) or []:
        if 0.15 <= m.yes_price <= 0.40:
            return m
    return None


class NarrativeStrategy:
    id = "narrative"

    def generate(self, match: MatchBundle, *, context: dict) -> List[ExploreSignal]:
        half = round(ORDER_USDC / 2.0, 4)
        under = _pick_under(match)
        home, away = match.home_px, match.away_px
        if home is None or away is None:
            return [ExploreSignal(
                strategy=self.id, match_slug=match.slug, match_title=match.title,
                action="skip", reason="missing moneyline", usdc=0.0, priority=90,
            )]
        gap = abs(home - away)
        fav_side = match.favorite_side()
        fav_px = max(home, away)
        signals: List[ExploreSignal] = []

        if fav_px >= NARRATIVE_FAVORITE_MIN and fav_side:
            fav_mkt = match.home_win if fav_side == "home" else match.away_win
            if fav_mkt:
                signals.append(ExploreSignal(
                    strategy=self.id,
                    match_slug=match.slug,
                    match_title=match.title,
                    action="buy_yes",
                    reason=f"narrative fav({fav_side})+under",
                    usdc=half if under else ORDER_USDC,
                    condition_id=fav_mkt.condition_id,
                    question=fav_mkt.question,
                    yes_token=fav_mkt.yes_token,
                    limit_price=fav_mkt.yes_price,
                    priority=40,
                    meta={"leg": "favorite", "fav_px": fav_px},
                ))
        elif gap <= NARRATIVE_TOSSUP_MAX_GAP and match.draw is not None:
            signals.append(ExploreSignal(
                strategy=self.id,
                match_slug=match.slug,
                match_title=match.title,
                action="buy_yes",
                reason="narrative toss-up → draw+under",
                usdc=half if under else ORDER_USDC,
                condition_id=match.draw.condition_id,
                question=match.draw.question,
                yes_token=match.draw.yes_token,
                limit_price=match.draw.yes_price,
                priority=40,
                meta={"leg": "draw", "gap": gap},
            ))
        else:
            btts = _pick_btts_no(match)
            if match.draw is not None and match.draw_px and match.draw_px <= 0.30:
                signals.append(ExploreSignal(
                    strategy=self.id,
                    match_slug=match.slug,
                    match_title=match.title,
                    action="buy_yes",
                    reason="narrative soft → cheap draw",
                    usdc=half if under else ORDER_USDC,
                    condition_id=match.draw.condition_id,
                    question=match.draw.question,
                    yes_token=match.draw.yes_token,
                    limit_price=match.draw.yes_price,
                    priority=45,
                    meta={"leg": "draw_soft"},
                ))
            elif btts is not None:
                signals.append(ExploreSignal(
                    strategy=self.id,
                    match_slug=match.slug,
                    match_title=match.title,
                    action="buy_yes",
                    reason="narrative cheap BTTS yes",
                    usdc=ORDER_USDC,
                    condition_id=btts.condition_id,
                    question=btts.question,
                    yes_token=btts.yes_token,
                    limit_price=btts.yes_price,
                    priority=48,
                    meta={"leg": "btts"},
                ))
            else:
                return [ExploreSignal(
                    strategy=self.id, match_slug=match.slug, match_title=match.title,
                    action="skip", reason="no narrative template", usdc=0.0, priority=85,
                )]

        if under and signals:
            signals.append(ExploreSignal(
                strategy=self.id,
                match_slug=match.slug,
                match_title=match.title,
                action="buy_yes",
                reason="narrative under hedge",
                usdc=half,
                condition_id=under.condition_id,
                question=under.question,
                yes_token=under.yes_token,
                limit_price=under.yes_price,
                priority=41,
                meta={"leg": "under"},
            ))
        return signals
