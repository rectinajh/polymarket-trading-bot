"""Time-lag live reactions (score/minute from context)."""

from __future__ import annotations

import time
from typing import List, Optional

from src.strategies.csl_explore.config import ORDER_USDC, TIME_LAG_DRAW_MINUTE
from src.strategies.csl_explore.models import ExploreSignal, MatchBundle


class TimeLagStrategy:
    id = "time_lag"

    def __init__(
        self,
        draw_minute: float = TIME_LAG_DRAW_MINUTE,
        usdc: float = ORDER_USDC,
    ) -> None:
        self.draw_minute = draw_minute
        self.usdc = usdc

    def generate(self, match: MatchBundle, *, context: dict) -> List[ExploreSignal]:
        live = (context.get("live") or {}).get(match.slug) or {}
        # live: {minute, home_goals, away_goals, red_home, red_away}
        minute = live.get("minute")
        if minute is None and match.start_ts:
            elapsed = (time.time() - float(match.start_ts)) / 60.0
            if elapsed < 0:
                return [self._watch(match, "pre-kickoff; waiting for live feed")]
            if elapsed > 120:
                return [self._skip(match, "match likely finished")]
            # Without a score feed we only emit watch after estimated kickoff.
            return [self._watch(
                match,
                f"est_minute={elapsed:.0f}; need live score feed for fire",
                meta={"est_minute": elapsed},
            )]

        if minute is None:
            return [self._watch(match, "no kickoff/live context")]

        hg = int(live.get("home_goals") or 0)
        ag = int(live.get("away_goals") or 0)
        red_h = int(live.get("red_home") or 0)
        red_a = int(live.get("red_away") or 0)

        # Early red → buy the team with more players (opponent of red)
        if red_h > red_a and match.away_win:
            return [ExploreSignal(
                strategy=self.id,
                match_slug=match.slug,
                match_title=match.title,
                action="buy_yes",
                reason=f"time_lag red_home→away win @'{minute}",
                usdc=self.usdc,
                condition_id=match.away_win.condition_id,
                question=match.away_win.question,
                yes_token=match.away_win.yes_token,
                limit_price=match.away_win.yes_price,
                priority=20,
                meta={"minute": minute, "score": f"{hg}-{ag}"},
            )]
        if red_a > red_h and match.home_win:
            return [ExploreSignal(
                strategy=self.id,
                match_slug=match.slug,
                match_title=match.title,
                action="buy_yes",
                reason=f"time_lag red_away→home win @'{minute}",
                usdc=self.usdc,
                condition_id=match.home_win.condition_id,
                question=match.home_win.question,
                yes_token=match.home_win.yes_token,
                limit_price=match.home_win.yes_price,
                priority=20,
                meta={"minute": minute, "score": f"{hg}-{ag}"},
            )]

        if (
            float(minute) >= self.draw_minute
            and hg == 0
            and ag == 0
            and match.draw is not None
        ):
            return [ExploreSignal(
                strategy=self.id,
                match_slug=match.slug,
                match_title=match.title,
                action="buy_yes",
                reason=f"time_lag 0-0 after {self.draw_minute}' → draw",
                usdc=self.usdc,
                condition_id=match.draw.condition_id,
                question=match.draw.question,
                yes_token=match.draw.yes_token,
                limit_price=match.draw.yes_price,
                priority=25,
                meta={"minute": minute},
            )]

        return [self._watch(
            match,
            f"live {hg}-{ag} @'{minute}; no rule fired",
            meta={"minute": minute, "score": f"{hg}-{ag}"},
        )]

    def _watch(
        self,
        match: MatchBundle,
        reason: str,
        meta: Optional[dict] = None,
    ) -> ExploreSignal:
        return ExploreSignal(
            strategy=self.id,
            match_slug=match.slug,
            match_title=match.title,
            action="watch",
            reason=reason,
            usdc=0.0,
            priority=80,
            meta=meta or {},
        )

    def _skip(self, match: MatchBundle, reason: str) -> ExploreSignal:
        return ExploreSignal(
            strategy=self.id,
            match_slug=match.slug,
            match_title=match.title,
            action="skip",
            reason=reason,
            usdc=0.0,
            priority=90,
        )
