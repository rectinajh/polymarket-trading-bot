"""RN1 soccer copy-trade sleeve (P4).

Mirrors RN1 **football** wallet positions & BUY trades only
(tennis / CS / MLB ignored). Same outcome side; size meets CLOB mins
(≥5 shares, ≥$1) while preferring ~$1 and hard-capping at $5.

Ledgers: ``data/daily_entries_sports.json`` · ``data/sports_pnl.json``
De-dupe: ``data/rn1_copy_seen.json``
"""

from .strategy import Rn1SportsMaker

__all__ = ["Rn1SportsMaker"]
