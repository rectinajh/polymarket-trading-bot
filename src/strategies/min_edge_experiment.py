"""P2.3 MIN_EDGE experiment — runs only when near-miss > 0 (read-only, no live threshold change)."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from src.strategies.scan_stats import ScanStatsLog, DEFAULT_STATS_PATH

CN_TZ = ZoneInfo("Asia/Shanghai")
EXPERIMENT_PATH = Path("data") / "min_edge_experiment.json"
THRESHOLDS = (0.010, 0.015, 0.020, 0.025, 0.030)
PRODUCTION_MIN_EDGE = 0.02


def _now_iso() -> str:
    return datetime.now(CN_TZ).isoformat()


def _count_at(near_misses: List[Dict[str, Any]], threshold: float) -> int:
    return sum(1 for nm in near_misses if float(nm.get("edge") or 0) >= threshold)


def evaluate_from_scan_stats(
    log: Optional[ScanStatsLog] = None,
) -> Dict[str, Any]:
    """Analyze latest scan_stats near-misses; never changes live MIN_EDGE."""
    log = log or ScanStatsLog(DEFAULT_STATS_PATH)
    latest = log.latest_cycle() or {}
    sc = latest.get("safe_compounder") or {}
    near_misses = list(sc.get("near_misses") or [])
    near_count = int(sc.get("near_miss_count") or len(near_misses))

    base: Dict[str, Any] = {
        "ts": _now_iso(),
        "scan_ts": latest.get("ts"),
        "near_miss_count": near_count,
        "production_min_edge": PRODUCTION_MIN_EDGE,
        "live_threshold_unchanged": True,
    }

    if near_count <= 0:
        return {
            **base,
            "status": "skipped",
            "reason": "near_miss_count=0 — waiting for P1 trigger",
            "passes_at_threshold": {},
        }

    passes = {
        f"{t:.3f}": _count_at(near_misses, t) for t in THRESHOLDS
    }
    in_band = [
        nm for nm in near_misses
        if 0 < float(nm.get("edge") or 0) < PRODUCTION_MIN_EDGE
    ]
    top = sorted(near_misses, key=lambda x: -float(x.get("edge") or 0))[:10]

    recommendation = "hold"
    if _count_at(near_misses, 0.015) >= 3:
        recommendation = "review_lower_to_1.5c"
    elif _count_at(near_misses, 0.010) >= 5:
        recommendation = "review_lower_to_1c"

    return {
        **base,
        "status": "active",
        "in_band_count": len(in_band),
        "passes_at_threshold": passes,
        "top_near_misses": top,
        "recommendation": recommendation,
        "note": "Experiment only — production MIN_EDGE remains 0.02",
    }


def persist_result(result: Dict[str, Any], path: Optional[Path] = None) -> None:
    path = path or EXPERIMENT_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def run_and_persist(log: Optional[ScanStatsLog] = None) -> Dict[str, Any]:
    result = evaluate_from_scan_stats(log)
    persist_result(result)
    return result


def experiment_alert(result: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Optional ops alert when experiment is active."""
    if result.get("status") != "active":
        return None
    n = int(result.get("near_miss_count") or 0)
    passes = result.get("passes_at_threshold") or {}
    at_15 = passes.get("0.015", 0)
    return {
        "severity": "warning",
        "code": "min_edge_experiment",
        "message": (
            f"P2.3 near-miss={n} (0<edge<2¢); pass@1.5¢={at_15} — "
            f"live MIN_EDGE still ${PRODUCTION_MIN_EDGE:.2f}"
        ),
    }
