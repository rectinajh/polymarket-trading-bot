#!/usr/bin/env python3
"""One-line / JSON daily review for the sports RN1 copy sleeve.

Aggregates Shanghai trading-day signals, fills, rejects, exits, and PnL from:
  - data/scan_stats_sports.json
  - data/sports_pnl.json
  - data/daily_entries_sports.json

Usage:
  .venv/bin/python scripts/sports_daily_review.py
  .venv/bin/python scripts/sports_daily_review.py --day 2026-08-30
  .venv/bin/python scripts/sports_daily_review.py --json
  .venv/bin/python scripts/sports_daily_review.py --append   # append to data/sports_daily_review.jsonl
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env", override=True)

from src.strategies.capital_policy import CN_TZ, DailyEntryLog, trading_day
from src.strategies.sports.config import (
    COPY_PRICE_MAX,
    COPY_PRICE_MIN,
    DEFAULT_LEDGER,
    DEFAULT_PNL_PATH,
    DEFAULT_SCAN_LOG,
    MAX_ENTRIES_PER_DAY,
)
from src.strategies.sports.sports_pnl import SportsPnL

REVIEW_JSONL = ROOT / "data" / "sports_daily_review.jsonl"


def _parse_ts(raw: Any) -> Optional[datetime]:
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=CN_TZ)
        return dt.astimezone(CN_TZ)
    except ValueError:
        return None


def _cycle_day(cycle: Dict[str, Any]) -> Optional[str]:
    ts = _parse_ts(cycle.get("ts"))
    if ts is None:
        return None
    return trading_day(ts)


def build_review(
    *,
    day: Optional[str] = None,
    scan_path: Path = DEFAULT_SCAN_LOG,
    pnl_path: Path = DEFAULT_PNL_PATH,
    ledger_path: Path = DEFAULT_LEDGER,
) -> Dict[str, Any]:
    day = day or trading_day()
    scan_path = Path(scan_path)
    pnl = SportsPnL(Path(pnl_path))

    cycles: List[Dict[str, Any]] = []
    if scan_path.exists():
        try:
            raw = json.loads(scan_path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                cycles = [c for c in (raw.get("cycles") or []) if isinstance(c, dict)]
        except (OSError, json.JSONDecodeError):
            cycles = []

    day_cycles = [c for c in cycles if _cycle_day(c) == day]
    reject_tot: Counter = Counter()
    signals = 0
    placed = 0
    attempted = 0
    exits = 0
    stop_losses = 0
    rn1_follows = 0
    guard_halts = 0
    errors = 0
    copyable_trades = 0

    for c in day_cycles:
        signals += int(c.get("new_signals") or 0)
        placed += int(c.get("placed") or 0)
        attempted += int(c.get("attempted") or 0)
        exits += int(c.get("exits") or 0)
        stop_losses += int(c.get("stop_losses") or 0)
        rn1_follows += int(c.get("rn1_follows_exit") or 0)
        errors += int(c.get("errors") or 0)
        copyable_trades = max(copyable_trades, int(c.get("copyable_trades") or 0))
        if int(c.get("guard_halted") or 0):
            guard_halts += 1
        rejects = c.get("rejects") or {}
        if isinstance(rejects, dict):
            for k, v in rejects.items():
                try:
                    reject_tot[str(k)] += int(v)
                except (TypeError, ValueError):
                    continue

    day_sum = pnl.summary_for_day(day)
    daily_realized = pnl.daily_realized_cents(day)
    exp = pnl.experiment_summary()

    entries = DailyEntryLog(path=Path(ledger_path), limit=MAX_ENTRIES_PER_DAY)
    used = MAX_ENTRIES_PER_DAY - entries.remaining()
    # remaining() uses trading_day(); if reviewing another day, read file directly
    if day != trading_day() and Path(ledger_path).exists():
        try:
            led = json.loads(Path(ledger_path).read_text(encoding="utf-8"))
            if isinstance(led, dict) and led.get("date") == day:
                used = len(led.get("entries") or [])
            else:
                used = 0
        except (OSError, json.JSONDecodeError):
            used = 0

    top_rejects = reject_tot.most_common(8)
    price_band = int(reject_tot.get("price_band") or 0)
    reject_n = sum(reject_tot.values())

    return {
        "day": day,
        "generated_at": datetime.now(CN_TZ).isoformat(timespec="seconds"),
        "config": {
            "max_entries_per_day": MAX_ENTRIES_PER_DAY,
            "price_band": [COPY_PRICE_MIN, COPY_PRICE_MAX],
        },
        "scan": {
            "cycles": len(day_cycles),
            "new_signals": signals,
            "attempted": attempted,
            "placed": placed,
            "exits": exits,
            "stop_losses": stop_losses,
            "rn1_follows_exit": rn1_follows,
            "guard_halt_cycles": guard_halts,
            "errors": errors,
            "copyable_trades_peak": copyable_trades,
            "rejects_total": reject_n,
            "price_band_rejects": price_band,
            "top_rejects": top_rejects,
        },
        "ledger": {
            "entries_used": used,
            "entries_limit": MAX_ENTRIES_PER_DAY,
            "day_open": int(day_sum.get("open") or 0),
            "day_won": int(day_sum.get("won") or 0),
            "day_lost": int(day_sum.get("lost") or 0),
            "day_realized_cents": daily_realized,
            "day_deployed_cents": int(day_sum.get("deployed_cents") or 0),
            "experiment_realized_cents": int(exp.get("realized_pnl_cents") or 0),
            "experiment_open": int(exp.get("open") or 0),
        },
    }


def format_text(report: Dict[str, Any]) -> str:
    s = report["scan"]
    led = report["ledger"]
    cfg = report["config"]
    realized = led["day_realized_cents"] / 100.0
    top = ", ".join(f"{k}={v}" for k, v in (s.get("top_rejects") or [])[:5]) or "—"
    lines = [
        f"SPORTS REVIEW {report['day']} | "
        f"signals={s['new_signals']} placed={s['placed']} "
        f"exits={s['exits']} (SL={s['stop_losses']} follow={s['rn1_follows_exit']}) | "
        f"day_pnl=${realized:+.2f} "
        f"open={led['day_open']} won={led['day_won']} lost={led['day_lost']} | "
        f"cap={led['entries_used']}/{led['entries_limit']} "
        f"band=[{cfg['price_band'][0]:.2f},{cfg['price_band'][1]:.2f}] "
        f"price_band_rej={s['price_band_rejects']} "
        f"guard_cycles={s['guard_halt_cycles']} cycles={s['cycles']}",
        f"  top_rejects: {top}",
        f"  experiment_realized=${led['experiment_realized_cents']/100:+.2f} "
        f"open_holds={led['experiment_open']}",
    ]
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description="Sports RN1 daily review")
    ap.add_argument("--day", default=None, help="YYYY-MM-DD (Asia/Shanghai)")
    ap.add_argument("--json", action="store_true", help="Print JSON")
    ap.add_argument(
        "--append",
        action="store_true",
        help=f"Append JSON line to {REVIEW_JSONL.relative_to(ROOT)}",
    )
    args = ap.parse_args()

    report = build_review(day=args.day)
    text = format_text(report)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(text)

    if args.append:
        REVIEW_JSONL.parent.mkdir(parents=True, exist_ok=True)
        with REVIEW_JSONL.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(report, ensure_ascii=False) + "\n")
        print(f"  appended → {REVIEW_JSONL}", flush=True)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
