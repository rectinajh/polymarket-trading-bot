"""Strategy protocol for CSL explore."""

from __future__ import annotations

from typing import List, Protocol

from src.strategies.csl_explore.models import ExploreSignal, MatchBundle


class ExploreStrategy(Protocol):
    id: str

    def generate(self, match: MatchBundle, *, context: dict) -> List[ExploreSignal]:
        ...
