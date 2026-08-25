"""RN1 sports Maker sleeve (P4).

Compares Polymarket soccer match-winner favorites to Pinnacle h2h odds
via The Odds API. Layer 2 requires RN1 smart-money confirmation before
placing small GTC YES bids when fair_prob − bid ≥ MIN_EDGE.

Independent ledger: ``data/daily_entries_sports.json`` · ``data/sports_pnl.json``.
L1 is live via PM2 ``--live``; L2 (WebSocket book + bilateral inventory) is queued — see ``docs/P4_L2_NEXT.md``.
"""

from .strategy import Rn1SportsMaker

__all__ = ["Rn1SportsMaker"]
