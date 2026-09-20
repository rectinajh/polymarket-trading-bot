"""Strategy registry for eu5_plus."""

from .completeness import CompletenessStrategy
from .draw_fv import DrawFvStrategy
from .kickoff_lag import KickoffLagStrategy
from .line_cross import LineCrossStrategy
from .live_draw import LiveDrawStrategy
from .narrative import NarrativeStrategy

STRATEGY_REGISTRY = {
    DrawFvStrategy.id: DrawFvStrategy,
    KickoffLagStrategy.id: KickoffLagStrategy,
    CompletenessStrategy.id: CompletenessStrategy,
    LiveDrawStrategy.id: LiveDrawStrategy,
    LineCrossStrategy.id: LineCrossStrategy,
    NarrativeStrategy.id: NarrativeStrategy,
}

__all__ = ["STRATEGY_REGISTRY"]
