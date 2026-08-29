"""RN1 sports copy-trade sleeve (P4).

Mirrors RN1 **soccer** (+ optional **ATP/WTA tennis**) BUY trades.
Same outcome side; size meets CLOB mins (≥5 shares, ≥$1) while preferring
~$1 and hard-capping at $5. Default price band [0.35, 0.75].

Ledgers: ``data/daily_entries_sports.json`` · ``data/sports_pnl.json``
De-dupe: ``data/rn1_copy_seen.json``
"""

from .strategy import Rn1SportsMaker

__all__ = ["Rn1SportsMaker"]
