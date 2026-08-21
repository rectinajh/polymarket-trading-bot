"""BTC 15-minute Up/Down Completeness sleeve.

Discovers markets via deterministic slugs ``btc-updown-15m-{unix}``,
checks YES(Up)+NO(Down) asks for completeness edge, optionally FOK-buys
both legs. Independent of Conservative DailyEntryLog / scan_stats.

Default is dry-run. Live requires explicit --live and small sleeve sizing.
"""

from .strategy import Btc15mCompleteness

__all__ = ["Btc15mCompleteness"]
