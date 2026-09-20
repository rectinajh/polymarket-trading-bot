"""Line cross: heavy favorite → cheap dog spread or under."""

from __future__ import annotations

import re
from typing import List, Optional

from src.strategies.csl_explore.models import BinaryMarket, ExploreSignal, MatchBundle
from src.strategies.eu5_plus.config import (
    LINE_FAV_MIN,
    LINE_SPREAD_MAX,
    LINE_UNDER_MAX,
    ORDER_USDC,
)


def _pick_dog_spread(match: MatchBundle) -> Optional[BinaryMarket]:
    spreads = getattr(match, "spreads", None) or []
    fav = match.favorite_side()
    dog = match.underdog_side()
    if not dog:
        return None
    dog_name = ""
    if dog == "home" and match.home_win:
        mo = re.match(r"^Will (.+?) win on", match.home_win.question, re.I)
        dog_name = (mo.group(1) if mo else "").lower()
    elif dog == "away" and match.away_win:
        mo = re.match(r"^Will (.+?) win on", match.away_win.question, re.I)
        dog_name = (mo.group(1) if mo else "").lower()
    cands = []
    for m in spreads:
        q = (m.question or "").lower()
        # Prefer underdog named on spread (positive handicap implied by low yes).
        if dog_name and dog_name.split()[0] not in q and dog_name not in q:
            # still allow if cheap
            if m.yes_price > LINE_SPREAD_MAX:
                continue
        if 0.05 <= m.yes_price <= LINE_SPREAD_MAX:
            cands.append(m)
    if not cands:
        return None
    return min(cands, key=lambda x: x.yes_price)


def _pick_under(match: MatchBundle) -> Optional[BinaryMarket]:
    unders = []
    for m in match.totals:
        q = (m.question or "").lower()
        gi = (m.group_item or "").lower()
        if "under" in q or gi.startswith("u"):
            unders.append(m)
    if not unders:
        return None
    band = [m for m in unders if 0.20 <= m.yes_price <= LINE_UNDER_MAX]
    pool = band or [m for m in unders if m.yes_price <= LINE_UNDER_MAX]
    if not pool:
        return None
    return min(pool, key=lambda x: abs(x.yes_price - 0.40))


class LineCrossStrategy:
    id = "line_cross"

    def generate(self, match: MatchBundle, *, context: dict) -> List[ExploreSignal]:
        home, away = match.home_px, match.away_px
        if home is None or away is None:
            return [ExploreSignal(
                strategy=self.id, match_slug=match.slug, match_title=match.title,
                action="skip", reason="no moneyline", usdc=0.0, priority=90,
            )]
        fav_px = max(home, away)
        if fav_px < LINE_FAV_MIN:
            return [ExploreSignal(
                strategy=self.id, match_slug=match.slug, match_title=match.title,
                action="skip", reason=f"no heavy fav ({fav_px:.3f})", usdc=0.0,
                priority=75,
            )]
        signals: List[ExploreSignal] = []
        spread = _pick_dog_spread(match)
        if spread is not None:
            signals.append(ExploreSignal(
                strategy=self.id,
                match_slug=match.slug,
                match_title=match.title,
                action="buy_yes",
                reason=f"line cross dog spread @{spread.yes_price:.3f} (fav={fav_px:.3f})",
                usdc=ORDER_USDC,
                condition_id=spread.condition_id,
                question=spread.question,
                yes_token=spread.yes_token,
                limit_price=spread.yes_price,
                priority=30,
                meta={"leg": "spread", "fav_px": fav_px},
            ))
        under = _pick_under(match)
        if under is not None and not signals:
            signals.append(ExploreSignal(
                strategy=self.id,
                match_slug=match.slug,
                match_title=match.title,
                action="buy_yes",
                reason=f"line cross under @{under.yes_price:.3f} (fav={fav_px:.3f})",
                usdc=ORDER_USDC,
                condition_id=under.condition_id,
                question=under.question,
                yes_token=under.yes_token,
                limit_price=under.yes_price,
                priority=32,
                meta={"leg": "under", "fav_px": fav_px},
            ))
        if not signals:
            return [ExploreSignal(
                strategy=self.id, match_slug=match.slug, match_title=match.title,
                action="skip", reason="no cheap spread/under", usdc=0.0, priority=70,
            )]
        return signals
