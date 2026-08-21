"""Discover BTC 15m Up/Down markets by deterministic Polymarket slug."""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from src.clients.gamma_client import GammaAPIError, GammaClient

WINDOW_S = 900  # 15 minutes
SLUG_PREFIX = "btc-updown-15m-"


def aligned_window_start(ts: Optional[float] = None) -> int:
    """Unix second of the current 15m window start (UTC)."""
    now = int(ts if ts is not None else time.time())
    return now - (now % WINDOW_S)


def window_slugs(
    *,
    now_ts: Optional[float] = None,
    behind: int = 1,
    ahead: int = 3,
) -> List[str]:
    """Slugs for nearby windows: previous, current, and next few."""
    base = aligned_window_start(now_ts)
    return [
        f"{SLUG_PREFIX}{base + i * WINDOW_S}"
        for i in range(-behind, ahead + 1)
    ]


def seconds_to_window_end(ts: Optional[float] = None) -> int:
    start = aligned_window_start(ts)
    now = int(ts if ts is not None else time.time())
    return max(0, start + WINDOW_S - now)


async def fetch_btc_15m_markets(
    gamma: GammaClient,
    *,
    behind: int = 1,
    ahead: int = 3,
    now_ts: Optional[float] = None,
) -> List[Dict[str, Any]]:
    """Resolve slug windows to Gamma markets (skip missing / closed)."""
    out: List[Dict[str, Any]] = []
    seen: set = set()
    for slug in window_slugs(now_ts=now_ts, behind=behind, ahead=ahead):
        try:
            m = await gamma.get_market_by_slug(slug)
        except GammaAPIError:
            continue
        except Exception:
            continue
        cond = m.get("_condition_id") or m.get("conditionId") or ""
        if not cond or cond in seen:
            continue
        if m.get("closed") or m.get("_status") == "closed":
            continue
        yes_id, no_id = m.get("_token_ids") or ("", "")
        if not yes_id or not no_id:
            continue
        seen.add(cond)
        m["_slug"] = slug
        m["_window_start"] = _slug_to_start(slug)
        out.append(m)
    return out


def _slug_to_start(slug: str) -> Optional[int]:
    try:
        return int(slug.rsplit("-", 1)[-1])
    except (TypeError, ValueError):
        return None
