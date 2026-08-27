"""Detect Polymarket soccer/football markets (exclude tennis, CS, MLB, …)."""

from __future__ import annotations

import re
from typing import Any, Optional

# Match-winner markets RN1 uses heavily.
WIN_ON_RE = re.compile(
    r"^Will .+ win on 20\d{2}-\d{2}-\d{2}\??$",
    re.IGNORECASE,
)

# Explicit non-soccer sports / esports.
EXCLUDE_RE = re.compile(
    r"\b("
    r"itf|atp|wta|tennis|singles|doubles|"
    r"cs2|counter-strike|dota|valorant|\blol\b|league of legends|"
    r"nba|nfl|nhl|mlb|kbo|npb|ufc|mma|"
    r"red sox|yankees|dodgers|braves|giants|tigers|guardians|"
    r"eagles vs|landers|bears vs|wiz\b|lions vs|heroes|giants vs|tigers\b"
    r")\b",
    re.IGNORECASE,
)

# Polymarket soccer event slug prefixes (league codes).
SOCCER_SLUG_RE = re.compile(
    r"(?:^|[-_/])("
    r"epl|elc|efl|fac|car|"
    r"bun|fl1|sea|lal|spa|isa|"
    r"ucl|uel|uecl|usc|"
    r"mls|mxc|liga|"
    r"bra|arg|lib|sud|col|chi|uru|par|bol|per|ecu|ven|"
    r"ered|spl|tur|rus|ukr|bel|den|swe|nor|aut|sui|gre|cze|pol|rou|cro|srb|fin|"
    r"fifwc|afc|caf|concacaf|copa|"
    r"ksa|egy|mar|tun|zaf|aus|jpn|kor|chn|"
    r"friendly|soccer|football"
    r")(?:-|$)",
    re.IGNORECASE,
)

# Soccer prop wording (when slug alone is ambiguous).
SOCCER_PROP_RE = re.compile(
    r"("
    r"both teams to score|"
    r"exact score|"
    r"end in a draw|"
    r"1st half o/u|"
    r"\bo/u [0-3]\.\d\b|"  # football totals usually ≤3.5 / 4.5
    r"\bfc\b|\bsc\b|\bcf\b|\bafc\b|\bkp\s|\bpsv\b|"
    r"real madrid|barcelona|liverpool|arsenal|chelsea|tottenham|"
    r"bayern|dortmund|juventus|milan|inter|napoli|psg|"
    r"freiburg|salzburg|mjällby|mjallby|kuopion|shamrock|"
    r"peñarol|penarol|rijeka|midtjylland|modern sc|ghazl"
    r")",
    re.IGNORECASE,
)

# Baseball-style totals (7.5+) — not soccer.
BASEBALL_OU_RE = re.compile(r"\bo/u\s*(?:1[0-9]|[7-9])(?:\.\d)?\b", re.IGNORECASE)


def is_soccer_market(
    title: str = "",
    slug: str = "",
    event_slug: str = "",
) -> bool:
    """Return True if this market is soccer/football and safe to copy."""
    title = str(title or "")
    slug = str(slug or "")
    event_slug = str(event_slug or "")
    blob = f"{title} {slug} {event_slug}"

    if EXCLUDE_RE.search(blob):
        return False
    if BASEBALL_OU_RE.search(title):
        return False
    if WIN_ON_RE.match(title.strip()):
        return True
    if SOCCER_SLUG_RE.search(slug) or SOCCER_SLUG_RE.search(event_slug):
        return True
    if SOCCER_PROP_RE.search(blob):
        return True
    return False


def side_from_outcome_index(outcome_index: Any) -> Optional[str]:
    """Map Polymarket outcomeIndex → place_order side ('yes' / 'no')."""
    try:
        idx = int(outcome_index)
    except (TypeError, ValueError):
        return None
    if idx == 0:
        return "yes"
    if idx == 1:
        return "no"
    return None


def side_from_outcome_label(outcome: str) -> Optional[str]:
    """Fallback when outcomeIndex missing."""
    o = str(outcome or "").strip().upper()
    if o in ("YES", "Y", "UNDER", "OVER"):  # Over/Under need index ideally
        if o in ("YES", "Y"):
            return "yes"
        if o == "NO" or o == "N":
            return "no"
    if o in ("NO", "N"):
        return "no"
    return None
