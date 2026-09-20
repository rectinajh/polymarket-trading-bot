"""In-play draw rise: clock heuristic when draw ask lags expected lift."""

from __future__ import annotations

import time
from typing import List

from src.strategies.csl_explore.models import ExploreSignal, MatchBundle
from src.strategies.eu5_plus.config import (
    LIVE_DRAW_MAX_MINUTE,
    LIVE_DRAW_MIN_LIFT,
    LIVE_DRAW_MINUTE,
    LIVE_DRAW_PRICE_MAX,
    ORDER_USDC,
)
from src.strategies.eu5_plus.ref import draw_fair


class LiveDrawStrategy:
    id = "live_draw"

    def generate(self, match: MatchBundle, *, context: dict) -> List[ExploreSignal]:
        if match.draw is None or match.draw_px is None or not match.start_ts:
            return [ExploreSignal(
                strategy=self.id, match_slug=match.slug, match_title=match.title,
                action="skip", reason="no draw/kickoff", usdc=0.0, priority=90,
            )]
        elapsed_m = (time.time() - float(match.start_ts)) / 60.0
        if not (LIVE_DRAW_MINUTE <= elapsed_m <= LIVE_DRAW_MAX_MINUTE):
            return [ExploreSignal(
                strategy=self.id, match_slug=match.slug, match_title=match.title,
                action="skip",
                reason=f"not in live window ({elapsed_m:.0f}')",
                usdc=0.0, priority=75, meta={"elapsed_m": elapsed_m},
            )]
        px = float(match.draw_px)
        if px > LIVE_DRAW_PRICE_MAX:
            return [ExploreSignal(
                strategy=self.id, match_slug=match.slug, match_title=match.title,
                action="skip", reason=f"draw already high {px:.3f}", usdc=0.0,
                priority=70,
            )]
        ref_map = context.get("ref_by_slug") or {}
        ev = ref_map.get(match.slug)
        fair0 = draw_fair(ev) if ev else None
        # Expected lift ~ +1–3pp by min 55–70 if still level; use linear proxy.
        expected = (fair0 or 0.26) + LIVE_DRAW_MIN_LIFT + max(0.0, (elapsed_m - 55) * 0.001)
        lag = expected - px
        if lag < LIVE_DRAW_MIN_LIFT:
            return [ExploreSignal(
                strategy=self.id, match_slug=match.slug, match_title=match.title,
                action="skip",
                reason=f"draw not lagging (px={px:.3f} exp={expected:.3f})",
                usdc=0.0, priority=65,
                meta={"px": px, "expected": expected, "elapsed_m": elapsed_m},
            )]
        # Soft assumption: no live score feed → treat as 0-0 candidate only.
        live = (context.get("live") or {})
        score = str(live.get(match.slug) or live.get("score") or "unknown")
        if score not in ("unknown", "0-0", "0–0"):
            return [ExploreSignal(
                strategy=self.id, match_slug=match.slug, match_title=match.title,
                action="skip", reason=f"score={score} not 0-0", usdc=0.0, priority=60,
            )]
        return [ExploreSignal(
            strategy=self.id,
            match_slug=match.slug,
            match_title=match.title,
            action="buy_yes",
            reason=(
                f"live draw lag {elapsed_m:.0f}' px={px:.3f} "
                f"exp≥{expected:.3f} (score={score})"
            ),
            usdc=ORDER_USDC,
            condition_id=match.draw.condition_id,
            question=match.draw.question,
            yes_token=match.draw.yes_token,
            limit_price=px,
            priority=25,
            meta={"elapsed_m": elapsed_m, "expected": expected, "score": score},
        )]
