"""Pinnacle reference helpers for eu5_plus."""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from src.clients.odds_api_client import OddsAPIClient, ReferenceEvent, ReferenceOutcome
from src.strategies.eu5_plus.config import EU5_SPORT_KEYS, REF_TTL_HOURS, REFERENCE_CACHE
from src.strategies.sports.match import normalize_team, _similar


def _serialize_events(events: List[ReferenceEvent]) -> List[Dict[str, Any]]:
    out = []
    for ev in events:
        out.append({
            "event_id": ev.event_id,
            "sport_key": ev.sport_key,
            "commence_time": ev.commence_time.isoformat(),
            "home_team": ev.home_team,
            "away_team": ev.away_team,
            "outcomes": {
                name: {
                    "team": o.team,
                    "decimal_odds": o.decimal_odds,
                    "fair_prob": o.fair_prob,
                }
                for name, o in ev.outcomes.items()
            },
        })
    return out


def _deserialize_events(rows: List[Dict[str, Any]]) -> List[ReferenceEvent]:
    out: List[ReferenceEvent] = []
    for r in rows or []:
        try:
            commence = datetime.fromisoformat(str(r["commence_time"]))
            if commence.tzinfo is None:
                commence = commence.replace(tzinfo=timezone.utc)
            outcomes = {
                name: ReferenceOutcome(
                    team=str(o.get("team") or name),
                    decimal_odds=float(o.get("decimal_odds") or 0),
                    fair_prob=float(o.get("fair_prob") or 0),
                )
                for name, o in (r.get("outcomes") or {}).items()
            }
            out.append(ReferenceEvent(
                event_id=str(r.get("event_id") or ""),
                sport_key=str(r.get("sport_key") or ""),
                commence_time=commence,
                home_team=str(r.get("home_team") or ""),
                away_team=str(r.get("away_team") or ""),
                outcomes=outcomes,
            ))
        except (KeyError, TypeError, ValueError):
            continue
    return out


async def load_reference(
    odds: OddsAPIClient,
    *,
    cache_path: Path = REFERENCE_CACHE,
    ttl_hours: float = REF_TTL_HOURS,
) -> Tuple[List[ReferenceEvent], str]:
    now = time.time()
    try:
        if cache_path.exists():
            raw = json.loads(cache_path.read_text(encoding="utf-8"))
            ts = float(raw.get("ts") or 0)
            if now - ts < ttl_hours * 3600:
                return _deserialize_events(raw.get("events") or []), "cache"
    except (OSError, ValueError):
        pass

    events = await odds.fetch_eu_soccer_reference(list(EU5_SPORT_KEYS))
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = cache_path.with_suffix(".tmp")
        tmp.write_text(json.dumps({
            "ts": now,
            "events": _serialize_events(events),
        }, ensure_ascii=False), encoding="utf-8")
        tmp.replace(cache_path)
    except OSError:
        pass
    return events, "api"


def match_bundle_to_reference(
    title: str,
    start_ts: Optional[float],
    events: List[ReferenceEvent],
    *,
    min_score: float = 0.70,
) -> Optional[ReferenceEvent]:
    """Fuzzy-match PM event title 'A vs. B' to a Pinnacle fixture."""
    parts = title.split(" vs.")
    if len(parts) < 2:
        parts = title.split(" vs ")
    if len(parts) < 2:
        return None
    home = normalize_team(parts[0])
    away = normalize_team(parts[1].split(":")[0])
    if not home or not away:
        return None

    match_date = None
    if start_ts:
        match_date = datetime.utcfromtimestamp(float(start_ts)).date()

    best: Optional[Tuple[float, ReferenceEvent]] = None
    for ev in events:
        if match_date and ev.commence_time.date() != match_date:
            # allow ±1 day for timezone
            delta = abs((ev.commence_time.date() - match_date).days)
            if delta > 1:
                continue
        hs = _similar(home, normalize_team(ev.home_team))
        aws = _similar(away, normalize_team(ev.away_team))
        score = (hs + aws) / 2.0
        # also try swapped
        hs2 = _similar(home, normalize_team(ev.away_team))
        aws2 = _similar(away, normalize_team(ev.home_team))
        score = max(score, (hs2 + aws2) / 2.0)
        if score < min_score:
            continue
        if best is None or score > best[0]:
            best = (score, ev)
    return best[1] if best else None


def outcome_fair(ev: ReferenceEvent, name: str) -> Optional[float]:
    """Fair prob for home/away/draw by fuzzy name."""
    target = normalize_team(name)
    if target in ("draw", "tie", "x"):
        for k, o in ev.outcomes.items():
            if normalize_team(k) in ("draw", "tie") or k.lower() == "draw":
                return float(o.fair_prob)
        return None
    best = None
    best_s = 0.0
    for k, o in ev.outcomes.items():
        s = _similar(target, normalize_team(k))
        if s > best_s:
            best_s = s
            best = o
    if best is None or best_s < 0.72:
        return None
    return float(best.fair_prob)


def draw_fair(ev: ReferenceEvent) -> Optional[float]:
    for k, o in ev.outcomes.items():
        if k.lower() == "draw" or normalize_team(k) in ("draw", "tie"):
            return float(o.fair_prob)
    return None
