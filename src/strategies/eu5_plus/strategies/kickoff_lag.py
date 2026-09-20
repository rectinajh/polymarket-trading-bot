"""Kickoff-window lag: Pinnacle moved, PM still cheap."""

from __future__ import annotations

import time
from typing import List, Optional

from src.strategies.csl_explore.models import BinaryMarket, ExploreSignal, MatchBundle
from src.strategies.eu5_plus.config import (
    LAG_MAX_MINUTES,
    LAG_MIN_EDGE,
    LAG_MIN_MINUTES,
    LAG_PRICE_MAX,
    LAG_PRICE_MIN,
    ORDER_USDC,
)
from src.strategies.eu5_plus.ref import outcome_fair


class KickoffLagStrategy:
    id = "kickoff_lag"

    def generate(self, match: MatchBundle, *, context: dict) -> List[ExploreSignal]:
        if not match.start_ts:
            return [ExploreSignal(
                strategy=self.id, match_slug=match.slug, match_title=match.title,
                action="skip", reason="no kickoff ts", usdc=0.0, priority=90,
            )]
        mins = (float(match.start_ts) - time.time()) / 60.0
        if not (LAG_MIN_MINUTES <= mins <= LAG_MAX_MINUTES):
            return [ExploreSignal(
                strategy=self.id, match_slug=match.slug, match_title=match.title,
                action="skip",
                reason=f"outside lag window ({mins:.0f}m)",
                usdc=0.0, priority=75, meta={"mins": mins},
            )]
        ref_map = context.get("ref_by_slug") or {}
        ev = ref_map.get(match.slug)
        if ev is None:
            return [ExploreSignal(
                strategy=self.id, match_slug=match.slug, match_title=match.title,
                action="skip", reason="no pinnacle match", usdc=0.0, priority=85,
            )]

        best: Optional[ExploreSignal] = None
        for side, mkt in (("home", match.home_win), ("away", match.away_win)):
            if mkt is None:
                continue
            px = float(mkt.yes_price)
            if not (LAG_PRICE_MIN <= px <= LAG_PRICE_MAX):
                continue
            # Team name from question
            team = mkt.question
            if team.lower().startswith("will ") and " win on" in team.lower():
                team = team[5: team.lower().index(" win on")]
            fair = outcome_fair(ev, team if side == "home" else team)
            if fair is None:
                # fallback to home/away team from ref
                fair = outcome_fair(
                    ev, ev.home_team if side == "home" else ev.away_team,
                )
            if fair is None:
                continue
            edge = fair - px
            if edge < LAG_MIN_EDGE:
                continue
            sig = ExploreSignal(
                strategy=self.id,
                match_slug=match.slug,
                match_title=match.title,
                action="buy_yes",
                reason=(
                    f"kickoff lag {mins:.0f}m {side} "
                    f"fair={fair:.3f} ask={px:.3f} edge={edge:.3f}"
                ),
                usdc=ORDER_USDC,
                condition_id=mkt.condition_id,
                question=mkt.question,
                yes_token=mkt.yes_token,
                limit_price=px,
                priority=20,
                meta={"side": side, "fair": fair, "edge": edge, "mins": mins},
            )
            if best is None or edge > float((best.meta or {}).get("edge") or 0):
                best = sig
        if best is None:
            return [ExploreSignal(
                strategy=self.id, match_slug=match.slug, match_title=match.title,
                action="skip", reason="no lag edge in window", usdc=0.0, priority=70,
            )]
        return [best]
