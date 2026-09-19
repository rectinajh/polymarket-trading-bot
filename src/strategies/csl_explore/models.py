"""Shared dataclasses for CSL explore signals."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class BinaryMarket:
    condition_id: str
    question: str
    yes_price: float
    no_price: float
    yes_token: str = ""
    no_token: str = ""
    volume: float = 0.0
    neg_risk: bool = False
    group_item: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class MatchBundle:
    """One CSL fixture with moneyline + optional props."""

    slug: str
    title: str
    start_ts: Optional[float]
    volume: float
    home_win: Optional[BinaryMarket] = None
    away_win: Optional[BinaryMarket] = None
    draw: Optional[BinaryMarket] = None
    totals: List[BinaryMarket] = field(default_factory=list)
    exact_scores: List[BinaryMarket] = field(default_factory=list)
    raw_markets: List[Dict[str, Any]] = field(default_factory=list)

    @property
    def home_px(self) -> Optional[float]:
        return self.home_win.yes_price if self.home_win else None

    @property
    def away_px(self) -> Optional[float]:
        return self.away_win.yes_price if self.away_win else None

    @property
    def draw_px(self) -> Optional[float]:
        return self.draw.yes_price if self.draw else None

    def three_way_sum(self) -> Optional[float]:
        if None in (self.home_px, self.away_px, self.draw_px):
            return None
        return float(self.home_px) + float(self.away_px) + float(self.draw_px)

    def favorite_side(self) -> Optional[str]:
        """Return 'home' | 'away' | None."""
        if self.home_px is None or self.away_px is None:
            return None
        if self.home_px >= self.away_px:
            return "home"
        return "away"

    def underdog_side(self) -> Optional[str]:
        fav = self.favorite_side()
        if fav is None:
            return None
        return "away" if fav == "home" else "home"

    def to_summary(self) -> Dict[str, Any]:
        return {
            "slug": self.slug,
            "title": self.title,
            "start_ts": self.start_ts,
            "volume": self.volume,
            "home_px": self.home_px,
            "away_px": self.away_px,
            "draw_px": self.draw_px,
            "three_way_sum": self.three_way_sum(),
            "n_totals": len(self.totals),
            "n_exact": len(self.exact_scores),
        }


@dataclass
class ExploreSignal:
    strategy: str
    match_slug: str
    match_title: str
    action: str  # buy_yes | buy_no | skip | watch
    reason: str
    usdc: float
    condition_id: str = ""
    question: str = ""
    yes_token: str = ""
    limit_price: Optional[float] = None
    priority: int = 50  # lower = higher priority when budget binds
    meta: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        return d
