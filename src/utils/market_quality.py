"""
Market quality gates — skip noisy short-horizon sports / prop markets
that burned capital in live IMMEDIATE trades.
"""

from __future__ import annotations

import re
from typing import Optional


# Title patterns typical of short sports / esports props (case-insensitive).
SPORTS_PROP_PATTERNS = [
    r"\bo/u\b",
    r"\bover/?under\b",
    r"\bhandicap\b",
    r"\bspread\b",
    r"\bvs\.?\b",
    r"\bmap\s*[12]\b",
    r"\btotal kills\b",
    r"\bboth teams to score\b",
    r"\bmatch o/u\b",
    r"\bgames? total\b",
    r"\bpoints?\b.*\bo/u\b",
    r"\bmlb\b",
    r"\bnba\b",
    r"\bnfl\b",
    r"\bnhl\b",
    r"\bcs2\b",
    r"\blol\b",
    r"\bdota\b",
    r"\besports?\b",
    r"\btennis\b",
    r"\batp\b",
    r"\bwta\b",
    r"\bopen:\b",  # "Cincinnati Open: ..."
    r"\bfc vs\b",
    r"\bsk vs\b",
]

_SPORTS_RE = re.compile("|".join(f"(?:{p})" for p in SPORTS_PROP_PATTERNS), re.I)

# Hard block phrases even outside sports regex.
SKIP_TITLE_PHRASES = [
    "mention",
    "say in",
    "speech mention",
    "address mention",
]


def is_short_sports_prop(title: Optional[str]) -> bool:
    """True if title looks like a short sports/esports prop we should not trade."""
    if not title:
        return False
    return bool(_SPORTS_RE.search(title))


def should_skip_market_title(title: Optional[str]) -> tuple[bool, str]:
    """
    Return (skip, reason). Used by immediate + directional entry paths.
    """
    if not title:
        return False, ""
    lower = title.lower()
    for phrase in SKIP_TITLE_PHRASES:
        if phrase in lower:
            return True, f"title blocklist phrase: {phrase!r}"
    if is_short_sports_prop(title):
        return True, "short sports/esports prop market"
    return False, ""


# Disciplined immediate-trade gates (post 2026-08 drawdown).
IMMEDIATE_MIN_EDGE = 0.18
IMMEDIATE_MIN_CONFIDENCE = 0.70
IMMEDIATE_MIN_EXPECTED_RETURN = 0.08
IMMEDIATE_MIN_VOLUME = 5000
IMMEDIATE_MAX_EXPIRY_DAYS = 14
# Small accounts: disable IMMEDIATE entirely (last week's blow-up path).
IMMEDIATE_MIN_PORTFOLIO_USD = 100.0

# No-book zombie archive: if held this long with no book, close in DB.
NO_BOOK_ARCHIVE_HOURS = 6.0

# Near-expiry force exit window (hours).
FORCE_EXIT_HOURS_BEFORE_EXPIRY = 2.0
