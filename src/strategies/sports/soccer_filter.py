"""Detect Polymarket soccer + tennis markets for RN1 copy (exclude CS, MLB, …)."""

from __future__ import annotations

import re
from typing import Any, Optional

# Match-winner markets RN1 uses heavily (mostly soccer).
WIN_ON_RE = re.compile(
    r"^Will .+ win on 20\d{2}-\d{2}-\d{2}\??$",
    re.IGNORECASE,
)

# Explicit non-soccer sports / esports (tennis handled separately).
EXCLUDE_RE = re.compile(
    r"\b("
    r"itf|atp|wta|tennis|singles|doubles|"
    r"cs2|counter-strike|dota|valorant|\blol\b|league of legends|"
    r"nba|nfl|nhl|mlb|kbo|npb|ufc|mma|ncaa|ncaaf|cfb|"
    r"san jose state|ohio state|alabama|michigan|clemson|"
    r"red sox|yankees|dodgers|braves|giants|tigers|guardians|"
    r"eagles vs|landers|bears vs|wiz\b|lions vs|heroes|giants vs|tigers\b"
    r")\b",
    re.IGNORECASE,
)

# ATP/WTA tour match markets (prefer singles; doubles opted out by default).
TENNIS_TOUR_RE = re.compile(r"\b(atp|wta)\b", re.IGNORECASE)
TENNIS_SLUG_RE = re.compile(
    r"(?:^|[-_/])(atp|wta)(?:-|$)|(?:^|[-_/])tennis(?:-|$)",
    re.IGNORECASE,
)
ITF_RE = re.compile(r"\bitf\b", re.IGNORECASE)
TENNIS_DOUBLES_RE = re.compile(r"\bdoubles\b", re.IGNORECASE)
# Block non-tennis when classifying tennis (esports / ball sports).
TENNIS_EXCLUDE_RE = re.compile(
    r"\b("
    r"cs2|counter-strike|dota|valorant|\blol\b|league of legends|"
    r"nba|nfl|nhl|mlb|kbo|npb|ufc|mma|ncaa|ncaaf|cfb|"
    r"soccer|football|epl|lal|bun|spread:"
    r")\b",
    re.IGNORECASE,
)

# Polymarket soccer event slug prefixes (league codes).
# NOTE: do NOT include bare `usc` — that matched college-football USC.
SOCCER_SLUG_RE = re.compile(
    r"(?:^|[-_/])("
    r"epl|elc|efl|fac|car|"
    r"bun|fl1|sea|lal|spa|isa|"
    r"ucl|uel|uecl|"
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
    # "Spread: Team (±N)" is American/college football style — never soccer copy.
    if re.search(r"^spread:\s", title.strip(), re.IGNORECASE):
        return False
    if re.search(r"\busc\b|\bucla\b|san jose state", blob, re.IGNORECASE):
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


def is_tennis_market(
    title: str = "",
    slug: str = "",
    event_slug: str = "",
    *,
    include_itf: bool = False,
    include_doubles: bool = False,
) -> bool:
    """Return True for ATP/WTA (optional ITF) match markets safe to copy."""
    title = str(title or "")
    slug = str(slug or "")
    event_slug = str(event_slug or "")
    blob = f"{title} {slug} {event_slug}"

    if TENNIS_EXCLUDE_RE.search(blob):
        return False
    if re.search(r"^spread:\s", title.strip(), re.IGNORECASE):
        return False
    if not include_doubles and TENNIS_DOUBLES_RE.search(blob):
        return False

    has_tour = bool(
        TENNIS_TOUR_RE.search(blob)
        or TENNIS_SLUG_RE.search(slug)
        or TENNIS_SLUG_RE.search(event_slug)
    )
    has_itf = bool(ITF_RE.search(blob))
    if has_tour:
        return True
    if include_itf and has_itf:
        return True
    return False


def is_copyable_market(
    title: str = "",
    slug: str = "",
    event_slug: str = "",
    *,
    allow_tennis: bool = True,
    include_itf: bool = False,
    include_doubles: bool = False,
) -> bool:
    """Soccer always; tennis when ``allow_tennis`` (default ATP/WTA singles)."""
    if is_soccer_market(title, slug, event_slug):
        return True
    if allow_tennis and is_tennis_market(
        title,
        slug,
        event_slug,
        include_itf=include_itf,
        include_doubles=include_doubles,
    ):
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
