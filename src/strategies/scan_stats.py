"""Persist Conservative scan-cycle metrics for the monitoring dashboard."""

from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from src.strategies.capital_policy import CN_TZ, trading_day

DEFAULT_STATS_PATH = Path("data") / "scan_stats.json"
MAX_CYCLES = 2000


def _now_cn() -> datetime:
    return datetime.now(CN_TZ)


def _pick(d: Optional[Dict], *keys: str, default: Any = 0) -> Any:
    if not d:
        return default
    for k in keys:
        if k in d and d[k] is not None:
            return d[k]
    return default


def _sum_rejects(cycles: List[Dict], strategy_key: str) -> Dict[str, int]:
    total: Counter = Counter()
    for c in cycles:
        rejects = (c.get(strategy_key) or {}).get("rejects") or {}
        if isinstance(rejects, dict):
            total.update({k: int(v) for k, v in rejects.items() if v})
    return dict(total)


class ScanStatsLog:
    """Append-only ledger of conservative scan cycles (JSON on disk)."""

    def __init__(self, path: Path = DEFAULT_STATS_PATH):
        self.path = path

    def _load(self) -> Dict:
        try:
            if not self.path.exists():
                return {"cycles": []}
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                return {"cycles": []}
            cycles = raw.get("cycles")
            if not isinstance(cycles, list):
                return {"cycles": []}
            return {"cycles": cycles}
        except (OSError, json.JSONDecodeError):
            return {"cycles": []}

    def _save(self, data: Dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    @staticmethod
    def _normalize_strategy_stats(stats: Optional[Dict]) -> Dict[str, Any]:
        if not stats:
            return {}
        out = dict(stats)
        rejects = out.get("rejects")
        if rejects is not None and not isinstance(rejects, dict):
            out["rejects"] = dict(rejects)
        return out

    def record_conservative_cycle(
        self,
        sc_stats: Optional[Dict] = None,
        arb_stats: Optional[Dict] = None,
        *,
        now: Optional[datetime] = None,
    ) -> Dict:
        ts = now or _now_cn()
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=CN_TZ)
        ts = ts.astimezone(CN_TZ)
        entry = {
            "ts": ts.isoformat(),
            "day": trading_day(ts),
            "mode": "conservative",
            "safe_compounder": self._normalize_strategy_stats(sc_stats),
            "completeness_arb": self._normalize_strategy_stats(arb_stats),
        }
        data = self._load()
        cycles = data.setdefault("cycles", [])
        cycles.append(entry)
        if len(cycles) > MAX_CYCLES:
            data["cycles"] = cycles[-MAX_CYCLES:]
        self._save(data)
        return entry

    def cycles_for_day(self, day: Optional[str] = None) -> List[Dict]:
        day = day or trading_day()
        return [c for c in self._load()["cycles"] if c.get("day") == day]

    def cycles_since(self, days: int = 7) -> List[Dict]:
        cutoff = (_now_cn() - timedelta(days=days)).date()
        out: List[Dict] = []
        for c in self._load()["cycles"]:
            day = c.get("day") or ""
            try:
                if datetime.strptime(day, "%Y-%m-%d").date() >= cutoff:
                    out.append(c)
            except ValueError:
                continue
        return out

    def latest_cycle(self) -> Optional[Dict]:
        cycles = self._load()["cycles"]
        return cycles[-1] if cycles else None

    def daily_summary(self, day: Optional[str] = None) -> Dict[str, Any]:
        day = day or trading_day()
        cycles = self.cycles_for_day(day)
        latest = cycles[-1] if cycles else None
        sc_latest = (latest or {}).get("safe_compounder") or {}
        arb_latest = (latest or {}).get("completeness_arb") or {}

        def _sum_sc(key: str) -> int:
            return int(sum(_pick(c.get("safe_compounder"), key, default=0) for c in cycles))

        def _sum_arb(key: str) -> int:
            return int(sum(_pick(c.get("completeness_arb"), key, default=0) for c in cycles))

        # Aggregate category breakdown across all cycles today
        cat_total: Counter = Counter()
        for c in cycles:
            cb = (c.get("safe_compounder") or {}).get("category_breakdown") or {}
            if isinstance(cb, dict):
                cat_total.update({k: int(v) for k, v in cb.items()})

        return {
            "day": day,
            "cycles": len(cycles),
            "last_scan_ts": latest.get("ts") if latest else None,
            "safe_compounder": {
                "latest_markets_scanned": _pick(sc_latest, "markets_scanned"),
                "latest_candidates": _pick(sc_latest, "candidates"),
                "latest_opportunities": _pick(sc_latest, "opportunities"),
                "total_placed": _sum_sc("placed"),
                "total_filled": _sum_sc("filled"),
                "total_errors": _sum_sc("errors"),
                "total_redeemed": _sum_sc("redeemed"),
                "rejects": _sum_rejects(cycles, "safe_compounder"),
                "near_misses": sc_latest.get("near_misses") or [],
                "near_miss_count": _pick(sc_latest, "near_miss_count"),
                "top_edge": sc_latest.get("top_edge") or [],
                "category_breakdown": dict(cat_total.most_common(15)),
            },
            "completeness_arb": {
                "latest_markets_scanned": _pick(arb_latest, "scanned"),
                "latest_checked_books": _pick(arb_latest, "checked_books"),
                "latest_opportunities": _pick(arb_latest, "opportunities"),
                "total_attempted": _sum_arb("attempted"),
                "total_filled_pairs": _sum_arb("filled_pairs"),
                "total_unwound": _sum_arb("unwound"),
                "total_errors": _sum_arb("errors"),
                "rejects": _sum_rejects(cycles, "completeness_arb"),
            },
            "total_filled": _sum_sc("filled") + _sum_arb("filled_pairs"),
        }

    def daily_filled_series(self, days: int = 7) -> List[Dict[str, Any]]:
        """One row per calendar day: filled counts for charting."""
        today = _now_cn().date()
        rows: List[Dict[str, Any]] = []
        for offset in range(days - 1, -1, -1):
            d = (today - timedelta(days=offset)).strftime("%Y-%m-%d")
            summary = self.daily_summary(d)
            rows.append(
                {
                    "day": d,
                    "sc_filled": summary["safe_compounder"]["total_filled"],
                    "arb_filled": summary["completeness_arb"]["total_filled_pairs"],
                    "total_filled": summary["total_filled"],
                    "cycles": summary["cycles"],
                    "sc_opportunities": int(
                        sum(
                            _pick(c.get("safe_compounder"), "opportunities", default=0)
                            for c in self.cycles_for_day(d)
                        )
                    ),
                    "arb_opportunities": int(
                        sum(
                            _pick(c.get("completeness_arb"), "opportunities", default=0)
                            for c in self.cycles_for_day(d)
                        )
                    ),
                }
            )
        return rows
