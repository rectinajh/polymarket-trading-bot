"""Exact-score fingerprint: same scoreline Yes across all matches."""

from __future__ import annotations

from typing import List

from src.strategies.csl_explore.config import FINGERPRINT_SCORE, ORDER_USDC
from src.strategies.csl_explore.models import ExploreSignal, MatchBundle


def _score_in_question(question: str, score: str) -> bool:
    q = question.lower().replace(" ", "")
    s = score.lower().replace(" ", "")
    # accept "1-1", "1–1", "1:1"
    variants = {s, s.replace("-", ":"), s.replace("-", "–")}
    return any(v in q for v in variants)


class FingerprintStrategy:
    id = "fingerprint"

    def __init__(self, score: str = FINGERPRINT_SCORE, usdc: float = ORDER_USDC) -> None:
        self.score = score
        self.usdc = usdc

    def generate(self, match: MatchBundle, *, context: dict) -> List[ExploreSignal]:
        hits = [m for m in match.exact_scores if _score_in_question(m.question, self.score)]
        if not hits:
            # Also scan raw questions if classification missed
            for m in match.exact_scores:
                if self.score.replace("-", "") in (m.question or "").replace("-", "").replace(":", ""):
                    hits.append(m)
        if not hits:
            return [ExploreSignal(
                strategy=self.id,
                match_slug=match.slug,
                match_title=match.title,
                action="skip",
                reason=f"no exact-score market for {self.score}",
                usdc=0.0,
                priority=90,
            )]
        m = min(hits, key=lambda x: x.yes_price if x.yes_price > 0 else 99)
        return [ExploreSignal(
            strategy=self.id,
            match_slug=match.slug,
            match_title=match.title,
            action="buy_yes",
            reason=f"fingerprint exact {self.score}",
            usdc=self.usdc,
            condition_id=m.condition_id,
            question=m.question,
            yes_token=m.yes_token,
            limit_price=m.yes_price,
            priority=40,
            meta={"score": self.score, "yes_px": m.yes_price},
        )]
