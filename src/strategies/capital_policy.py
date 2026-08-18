"""Shared large-capital policy: depth sizing, NAV-tiered caps, daily entry limit.

No daily PnL target. Idle cash is a valid outcome when books have no edge.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple
from zoneinfo import ZoneInfo

CN_TZ = ZoneInfo("Asia/Shanghai")
DEFAULT_LEDGER_PATH = Path("data") / "daily_entries.json"

# Take this fraction of the top N ask levels so a large NAV cannot eat the book.
DEPTH_TAKE_PCT = 0.25
DEPTH_LEVELS = 2
MAX_ENTRIES_PER_DAY = 6

# Weather / short-dated favorites stay a satellite sleeve as NAV grows.
def nav_max_position_pct(nav_cents: int) -> float:
    """Tighter % cap as NAV grows. Small accounts keep 5% so live tests still fill."""
    nav_usd = max(0.0, nav_cents / 100.0)
    if nav_usd < 500:
        return 0.05
    if nav_usd < 5_000:
        return 0.02
    if nav_usd < 20_000:
        return 0.01
    return 0.005


def trading_day(now: Optional[datetime] = None) -> str:
    ts = now or datetime.now(CN_TZ)
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=CN_TZ)
    return ts.astimezone(CN_TZ).strftime("%Y-%m-%d")


def correlation_key(title: str) -> str:
    """Cluster titles so we do not stack the same weather city / up-down coin."""
    t = (title or "").lower()
    m = re.search(r"temperature in ([a-z]+)", t)
    if m:
        return f"weather:{m.group(1)}"
    m = re.search(r"highest temperature in ([a-z]+)", t)
    if m:
        return f"weather:{m.group(1)}"
    m = re.search(r"([a-z0-9]+) up or down", t)
    if m:
        return f"updown:{m.group(1)}"
    cleaned = re.sub(r"\d+", " ", t)
    cleaned = re.sub(r"[^a-z\s]", " ", cleaned)
    words = " ".join(cleaned.split()[:6])
    return words or (t[:40] or "unknown")


def parse_ask_levels(rows: Sequence) -> List[Tuple[float, float]]:
    """Return [(price_dollars, size), ...] from [[price, size], ...] asks."""
    priced: List[Tuple[float, float]] = []
    for row in rows or []:
        try:
            p = float(row[0])
            sz = float(row[1])
            if p > 1.0:
                p = p / 100.0
            if p > 0 and sz > 0:
                priced.append((p, sz))
        except (TypeError, ValueError, IndexError):
            continue
    priced.sort(key=lambda x: x[0])
    return priced


def top_ask_depth(rows: Sequence, levels: int = DEPTH_LEVELS) -> Tuple[Optional[float], float]:
    """Best ask price and size summed across the first `levels` rungs."""
    priced = parse_ask_levels(rows)
    if not priced:
        return None, 0.0
    best = priced[0][0]
    depth = sum(sz for _, sz in priced[: max(1, levels)])
    return best, depth


def depth_share_cap(ask_depth: float, take_pct: float = DEPTH_TAKE_PCT) -> int:
    if ask_depth <= 0:
        return 0
    return max(1, int(ask_depth * take_pct))


def size_shares(
    *,
    price: float,
    nav_cents: int,
    cash_cents: int,
    ask_depth: float,
    kelly_fraction_value: float = 0.0,
    extra_cap_pct: Optional[float] = None,
) -> int:
    """Shares = min(depth slice, NAV-tier cap, half-Kelly, cash). Never round up past cash."""
    if price <= 0:
        return 0
    price_cents = int(round(price * 100))
    if price_cents <= 0:
        return 0
    pct = nav_max_position_pct(nav_cents)
    if extra_cap_pct is not None:
        pct = min(pct, extra_cap_pct)
    nav_cap_cents = int(nav_cents * pct)
    if kelly_fraction_value > 0:
        nav_cap_cents = min(nav_cap_cents, int(nav_cents * kelly_fraction_value))
    by_nav = nav_cap_cents // price_cents
    by_cash = cash_cents // price_cents
    by_depth = depth_share_cap(ask_depth)
    return max(0, min(by_nav, by_cash, by_depth))


class DailyEntryLog:
    """Persist today's entries so a reused client loop cannot exceed the cap."""

    def __init__(self, path: Path = DEFAULT_LEDGER_PATH, limit: int = MAX_ENTRIES_PER_DAY):
        self.path = path
        self.limit = limit

    def _load(self) -> Dict:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"date": trading_day(), "entries": []}
        if not isinstance(raw, dict):
            return {"date": trading_day(), "entries": []}
        if raw.get("date") != trading_day():
            return {"date": trading_day(), "entries": []}
        entries = raw.get("entries") or []
        if not isinstance(entries, list):
            entries = []
        return {"date": trading_day(), "entries": entries}

    def _save(self, payload: Dict) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(self.path.suffix + ".tmp")
            tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            tmp.replace(self.path)
        except OSError:
            pass

    def snapshot(self) -> Dict:
        return self._load()

    def remaining(self) -> int:
        data = self._load()
        return max(0, self.limit - len(data["entries"]))

    def clusters(self) -> set:
        return {
            str(e.get("cluster") or "")
            for e in self._load()["entries"]
            if e.get("cluster")
        }

    def tickers(self) -> set:
        return {
            str(e.get("ticker") or "")
            for e in self._load()["entries"]
            if e.get("ticker")
        }

    def record(self, ticker: str, title: str = "", kind: str = "compounder") -> None:
        data = self._load()
        data["entries"].append({
            "ticker": ticker,
            "cluster": correlation_key(title),
            "kind": kind,
            "title": (title or "")[:80],
            "ts": datetime.now(CN_TZ).isoformat(timespec="seconds"),
        })
        self._save(data)


def clusters_from_positions(positions: Iterable[Dict]) -> set:
    out = set()
    for p in positions or []:
        title = p.get("title") or p.get("question") or ""
        if title:
            out.add(correlation_key(str(title)))
    return out
