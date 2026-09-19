#!/usr/bin/env python3
"""Ops alerting: PM2 health, HTTP 429 bursts, NAV snapshot jumps, redeem/orphan."""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env", override=True)

from src.strategies.min_edge_experiment import experiment_alert, run_and_persist
from src.strategies.orphan_unwind import OrphanRegistry
from src.strategies.scan_stats import ScanStatsLog, DEFAULT_STATS_PATH
from src.utils.discord_alerts import notify_ops_alerts
from src.utils.ops_metrics import count_since, events_since

CN_TZ = ZoneInfo("Asia/Shanghai")
ALERTS_PATH = ROOT / "data" / "ops_alerts.json"
BTC15M_STATS = ROOT / "data" / "scan_stats_btc15m.json"

PM2_EXPECTED = {
    "polymarket-bot": {"max_stale_s": 480, "stats": DEFAULT_STATS_PATH},
    "polymarket-btc15m": {"max_stale_s": 120, "stats": BTC15M_STATS},
    "polymarket-dashboard": {"max_stale_s": 0, "stats": None},
}

NAV_JUMP_CENTS = 200
RATE_LIMIT_WARN = 5
RATE_LIMIT_CRIT = 15


def _parse_ts(ts_raw: Any) -> Optional[datetime]:
    if not ts_raw:
        return None
    try:
        ts = datetime.fromisoformat(str(ts_raw))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=CN_TZ)
        return ts.astimezone(CN_TZ)
    except ValueError:
        return None


def _pm2_status() -> Tuple[List[Dict[str, Any]], Optional[str]]:
    try:
        out = subprocess.run(
            ["pm2", "jlist"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        if out.returncode != 0:
            return [], out.stderr.strip() or "pm2 jlist failed"
        data = json.loads(out.stdout or "[]")
        return data if isinstance(data, list) else [], None
    except FileNotFoundError:
        return [], "pm2 not installed"
    except (subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
        return [], str(exc)


def _latest_scan_ts(stats_path: Optional[Path]) -> Optional[datetime]:
    if not stats_path or not stats_path.exists():
        return None
    try:
        raw = json.loads(stats_path.read_text(encoding="utf-8"))
        cycles = raw.get("cycles") or []
        if not cycles:
            return None
        last = cycles[-1]
        return _parse_ts(last.get("ts"))
    except (OSError, json.JSONDecodeError):
        return None


def _check_pm2(now: datetime) -> List[Dict[str, Any]]:
    alerts: List[Dict[str, Any]] = []
    procs, err = _pm2_status()
    if err:
        alerts.append(
            {"severity": "critical", "code": "pm2_unavailable", "message": err}
        )
        return alerts

    by_name = {p.get("name"): p for p in procs if p.get("name")}
    for name, cfg in PM2_EXPECTED.items():
        proc = by_name.get(name)
        if not proc:
            alerts.append(
                {
                    "severity": "critical",
                    "code": "pm2_missing",
                    "message": f"PM2 process missing: {name}",
                }
            )
            continue
        env = proc.get("pm2_env") or {}
        status = env.get("status") or proc.get("pm_status") or "unknown"
        restarts = int(env.get("restart_time") or env.get("unstable_restarts") or 0)
        if status != "online":
            alerts.append(
                {
                    "severity": "critical",
                    "code": "pm2_down",
                    "message": f"{name} status={status} restarts={restarts}",
                }
            )
        if restarts >= 10:
            alerts.append(
                {
                    "severity": "warning",
                    "code": "pm2_restarts",
                    "message": f"{name} restarts={restarts}",
                }
            )
        max_stale = int(cfg.get("max_stale_s") or 0)
        if max_stale > 0:
            last = _latest_scan_ts(cfg.get("stats"))
            if last is None:
                alerts.append(
                    {
                        "severity": "warning",
                        "code": "scan_stale",
                        "message": f"{name}: no scan_stats yet",
                    }
                )
            else:
                age = (now - last).total_seconds()
                if age > max_stale:
                    alerts.append(
                        {
                            "severity": "critical" if age > max_stale * 2 else "warning",
                            "code": "scan_stale",
                            "message": (
                                f"{name}: last scan {int(age)}s ago "
                                f"(threshold {max_stale}s)"
                            ),
                        }
                    )
    return alerts


def _check_rate_limits() -> List[Dict[str, Any]]:
    alerts: List[Dict[str, Any]] = []
    hour = count_since(1.0, kind="rate_limit")
    if hour >= RATE_LIMIT_CRIT:
        alerts.append(
            {
                "severity": "critical",
                "code": "rate_limit_burst",
                "message": f"HTTP 429 / rate limit: {hour} events in last hour",
            }
        )
    elif hour >= RATE_LIMIT_WARN:
        alerts.append(
            {
                "severity": "warning",
                "code": "rate_limit_elevated",
                "message": f"HTTP 429 / rate limit: {hour} events in last hour",
            }
        )
    recent = events_since(1.0, kind="rate_limit")[-3:]
    if recent and alerts:
        alerts[-1]["detail"] = recent
    return alerts


def _check_nav_anomaly() -> List[Dict[str, Any]]:
    alerts: List[Dict[str, Any]] = []
    log = ScanStatsLog(DEFAULT_STATS_PATH)
    cycles = log._load().get("cycles") or []
    if len(cycles) < 2:
        return alerts
    prev, cur = cycles[-2], cycles[-1]
    sc_prev = prev.get("safe_compounder") or {}
    sc_cur = cur.get("safe_compounder") or {}
    nav_prev = int(sc_prev.get("nav_cents") or 0)
    nav_cur = int(sc_cur.get("nav_cents") or 0)
    if nav_prev <= 0 or nav_cur <= 0:
        return alerts
    delta = abs(nav_cur - nav_prev)
    filled = int(sc_cur.get("filled") or 0) + int(
        (cur.get("completeness_arb") or {}).get("filled_pairs") or 0
    )
    if delta >= NAV_JUMP_CENTS and filled == 0:
        alerts.append(
            {
                "severity": "warning",
                "code": "nav_jump",
                "message": (
                    f"NAV moved ${delta/100:.2f} with 0 fills "
                    f"(${nav_prev/100:.2f} → ${nav_cur/100:.2f})"
                ),
            }
        )
    return alerts


def _check_redeem_and_orphans(summary: Dict[str, Any]) -> List[Dict[str, Any]]:
    alerts: List[Dict[str, Any]] = []
    sc = summary.get("safe_compounder") or {}
    redeem_needed = int(sc.get("latest_redeem_needed") or 0)
    if redeem_needed > 0:
        alerts.append(
            {
                "severity": "critical",
                "code": "redeem_needed",
                "message": (
                    f"Redeem needed: {redeem_needed} resolved position(s) — "
                    "check Relayer / run scripts/redeem_all.py"
                ),
            }
        )
    orphans = OrphanRegistry().list()
    if orphans:
        alerts.append(
            {
                "severity": "critical",
                "code": "orphan_open",
                "message": f"{len(orphans)} orphan leg(s) pending unwind",
                "detail": orphans[:5],
            }
        )
    return alerts


async def _check_redeemable_positions() -> List[Dict[str, Any]]:
    alerts: List[Dict[str, Any]] = []
    try:
        from src.clients import build_polymarket_clients

        async with build_polymarket_clients() as (client, _gamma):
            resp = await client.get_positions()
            positions = resp.get("market_positions") or []
            redeemable = [
                p for p in positions
                if p.get("redeemable") and abs(float(p.get("size", 0) or 0)) > 0
            ]
            if redeemable:
                titles = [
                    (p.get("title") or p.get("condition_id") or "")[:60]
                    for p in redeemable[:3]
                ]
                alerts.append(
                    {
                        "severity": "critical",
                        "code": "redeemable_positions",
                        "message": (
                            f"{len(redeemable)} redeemable position(s) on-chain — "
                            "redeem in Polymarket UI"
                        ),
                        "detail": titles,
                    }
                )
    except Exception as exc:
        alerts.append(
            {
                "severity": "warning",
                "code": "redeem_check_failed",
                "message": f"Could not verify redeemable positions: {exc}",
            }
        )
    return alerts


def _write_alerts(payload: Dict[str, Any]) -> None:
    ALERTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = ALERTS_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(ALERTS_PATH)
    log_dir = ROOT / "logs" / "alerts"
    log_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(CN_TZ).strftime("%Y%m%d_%H%M%S")
    crit = [a for a in payload.get("alerts") or [] if a.get("severity") == "critical"]
    if crit:
        (log_dir / f"alert_{stamp}.txt").write_text(
            "[Polymarket]\n"
            + "\n".join(f"[{a['severity']}] {a['code']}: {a['message']}" for a in crit),
            encoding="utf-8",
        )


def run_checks(*, skip_positions: bool = False) -> Dict[str, Any]:
    now = datetime.now(CN_TZ)
    log = ScanStatsLog(DEFAULT_STATS_PATH)
    summary = log.daily_summary()
    exp_result = run_and_persist(log)
    alerts: List[Dict[str, Any]] = []
    alerts.extend(_check_pm2(now))
    alerts.extend(_check_rate_limits())
    alerts.extend(_check_nav_anomaly())
    alerts.extend(_check_redeem_and_orphans(summary))
    exp_alert = experiment_alert(exp_result)
    if exp_alert:
        alerts.append(exp_alert)
    if not skip_positions:
        alerts.extend(asyncio.run(_check_redeemable_positions()))

    payload = {
        "ts": now.isoformat(),
        "alert_count": len(alerts),
        "critical_count": sum(1 for a in alerts if a.get("severity") == "critical"),
        "rate_limit_1h": count_since(1.0, kind="rate_limit"),
        "latest_nav_cents": (summary.get("safe_compounder") or {}).get("latest_nav_cents"),
        "latest_redeem_needed": (summary.get("safe_compounder") or {}).get("latest_redeem_needed"),
        "orphan_count": len(OrphanRegistry().list()),
        "min_edge_experiment": exp_result,
        "alerts": alerts,
    }
    _write_alerts(payload)
    notify_ops_alerts(payload)
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="Ops alerting for Polymarket bot")
    parser.add_argument(
        "--skip-positions",
        action="store_true",
        help="Skip live positions/redeemable API check",
    )
    parser.add_argument("--json", action="store_true", help="Print full JSON payload")
    parser.add_argument("--loop", action="store_true", help="Run continuously")
    parser.add_argument("--interval", type=int, default=120, help="Loop interval seconds")
    parser.add_argument(
        "--discord-test",
        action="store_true",
        help="Send a test Polymarket-branded Discord message and exit",
    )
    args = parser.parse_args()

    if args.discord_test:
        from src.utils.discord_alerts import send_discord_message

        ok = send_discord_message(
            f"**[Polymarket]** Discord webhook test OK · "
            f"{datetime.now(CN_TZ).isoformat()[:19]}"
        )
        return 0 if ok else 1

    if args.loop:
        import time
        while True:
            payload = run_checks(skip_positions=args.skip_positions)
            if payload.get("critical_count"):
                print(f"CRITICAL alerts={payload['critical_count']}", flush=True)
            time.sleep(max(30, args.interval))
        return 0

    payload = run_checks(skip_positions=args.skip_positions)
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"Ops alerts @ {payload['ts']}")
        print(f"  critical={payload['critical_count']} total={payload['alert_count']}")
        print(f"  429 (1h)={payload['rate_limit_1h']} orphans={payload['orphan_count']}")
        for a in payload.get("alerts") or []:
            print(f"  [{a.get('severity')}] {a.get('code')}: {a.get('message')}")

    return 1 if payload.get("critical_count") else 0


if __name__ == "__main__":
    raise SystemExit(main())
