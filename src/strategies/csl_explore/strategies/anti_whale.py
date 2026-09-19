"""Anti-whale: buy reverse after mid dump in lookback window."""

from __future__ import annotations

from typing import List, Optional

from src.strategies.csl_explore.config import (
    ANTI_WHALE_DROP_PCT,
    ANTI_WHALE_LOOKBACK_S,
    ORDER_USDC,
)
from src.strategies.csl_explore.models import ExploreSignal, MatchBundle


class AntiWhaleStrategy:
    id = "anti_whale"

    def __init__(
        self,
        drop_pct: float = ANTI_WHALE_DROP_PCT,
        lookback_s: float = ANTI_WHALE_LOOKBACK_S,
        usdc: float = ORDER_USDC,
    ) -> None:
        self.drop_pct = drop_pct
        self.lookback_s = lookback_s
        self.usdc = usdc

    def generate(self, match: MatchBundle, *, context: dict) -> List[ExploreSignal]:
        ledger = context.get("ledger")
        if ledger is None:
            return [self._watch(match, "no ledger for mid history")]

        candidates = []
        for label, mkt in (
            ("home", match.home_win),
            ("away", match.away_win),
            ("draw", match.draw),
        ):
            if mkt is None or not mkt.condition_id:
                continue
            drop = ledger.mid_drop(
                mkt.condition_id,
                mkt.yes_price,
                lookback_s=self.lookback_s,
            )
            if drop is not None and drop >= self.drop_pct:
                candidates.append((drop, label, mkt))

        if not candidates:
            return [self._watch(
                match,
                f"no dump ≥{self.drop_pct:.0%} in {self.lookback_s:.0f}s",
            )]

        drop, label, mkt = max(candidates, key=lambda x: x[0])
        # Reverse = buy the dumped side (mean reversion), not the opposite team.
        return [ExploreSignal(
            strategy=self.id,
            match_slug=match.slug,
            match_title=match.title,
            action="buy_yes",
            reason=f"anti_whale fade dump {label} drop={drop:.1%}",
            usdc=self.usdc,
            condition_id=mkt.condition_id,
            question=mkt.question,
            yes_token=mkt.yes_token,
            limit_price=mkt.yes_price,
            priority=15,
            meta={"drop": drop, "leg": label},
        )]

    def _watch(self, match: MatchBundle, reason: str) -> ExploreSignal:
        return ExploreSignal(
            strategy=self.id,
            match_slug=match.slug,
            match_title=match.title,
            action="watch",
            reason=reason,
            usdc=0.0,
            priority=80,
        )
