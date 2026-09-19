"""Discord webhook logs for CSL explore — strategy tags distinct from RN1."""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Optional
from zoneinfo import ZoneInfo

from src.utils.discord_alerts import SOURCE, send_discord_message

CN_TZ = ZoneInfo("Asia/Shanghai")
STATE_PATH = Path("data") / "csl_explore_discord_state.json"

# Clear labels so Discord is not confused with RN1 sports copy.
STRATEGY_LABELS: Dict[str, str] = {
    "fingerprint": "比分指纹 fingerprint",
    "time_lag": "时间错位 time_lag",
    "completeness": "完备缺口 completeness",
    "narrative": "叙事对冲 narrative",
    "anti_whale": "反向砸盘 anti_whale",
    "dog_basket": "反热门弱胜 dog_basket",
    "ucl_balance": "欧冠均衡对冲 ucl_balance",
}


def strategy_label(strategy_id: str) -> str:
    sid = (strategy_id or "").strip()
    return STRATEGY_LABELS.get(sid, sid or "unknown")


def _enabled() -> bool:
    raw = (os.getenv("POLYMARKET_CSL_DISCORD") or "true").strip().lower()
    return raw not in ("0", "false", "no", "off")


def _cooldown_s() -> int:
    """Per-fingerprint cooldown; fills use unique keys so usually always send."""
    raw = (os.getenv("POLYMARKET_CSL_DISCORD_COOLDOWN_S") or "60").strip()
    try:
        return max(0, int(raw))
    except ValueError:
        return 60


def _load_state() -> Dict[str, Any]:
    try:
        if not STATE_PATH.exists():
            return {}
        raw = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save_state(state: Dict[str, Any]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(STATE_PATH)


def _should_send(key: str) -> bool:
    cd = _cooldown_s()
    if cd <= 0:
        return True
    state = _load_state()
    last = state.get(key)
    if not last:
        return True
    try:
        sent = datetime.fromisoformat(str(last))
        if sent.tzinfo is None:
            sent = sent.replace(tzinfo=CN_TZ)
        return datetime.now(CN_TZ) - sent.astimezone(CN_TZ) >= timedelta(seconds=cd)
    except ValueError:
        return True


def _mark_sent(key: str) -> None:
    state = _load_state()
    state[key] = datetime.now(CN_TZ).isoformat()
    _save_state(state)


def notify_csl_order(
    *,
    strategy: str,
    match_title: str,
    question: str,
    reason: str,
    shares: int,
    price: float,
    cost_usdc: float,
    live: bool,
    condition_id: str,
    match_slug: str = "",
    meta: Optional[Dict[str, Any]] = None,
) -> bool:
    """Webhook for a placed / planned CSL explore bet — strategy clearly tagged."""
    if not _enabled():
        return False
    fp = hashlib.sha256(
        f"csl_order:{strategy}:{condition_id}:{shares}:{price:.4f}".encode()
    ).hexdigest()[:12]
    key = f"csl_order:{fp}"
    if not _should_send(key):
        return False

    mode = "LIVE" if live else "DRY"
    label = strategy_label(strategy)
    meta = meta or {}
    leg = meta.get("leg") or meta.get("score") or ""
    leg_line = f"腿: `{leg}`\n" if leg else ""
    content = (
        f"**[{SOURCE}]** 🧪 **CSL Explore** {mode}\n"
        f"**策略** `{strategy}` · {label}\n"
        f"**场次** {(match_title or match_slug or '—')[:80]}\n"
        f"{leg_line}"
        f"**下单** YES x{shares} @ ${price:.2f} ≈ **${cost_usdc:.2f}**\n"
        f"**原因** {(reason or '—')[:160]}\n"
        f"**市场** {(question or '—')[:120]}\n"
        f"`cond:{condition_id[:18]}…`"
    )
    if send_discord_message(content[:1900]):
        _mark_sent(key)
        return True
    return False


def notify_csl_error(
    *,
    strategy: str,
    match_title: str,
    reason: str,
    error: str,
    condition_id: str = "",
) -> bool:
    if not _enabled():
        return False
    fp = hashlib.sha256(
        f"csl_err:{strategy}:{condition_id}:{error[:80]}".encode()
    ).hexdigest()[:12]
    key = f"csl_err:{fp}"
    if not _should_send(key):
        return False
    label = strategy_label(strategy)
    content = (
        f"**[{SOURCE}]** 🧪 **CSL Explore** ERROR\n"
        f"**策略** `{strategy}` · {label}\n"
        f"**场次** {(match_title or '—')[:80]}\n"
        f"**意图** {(reason or '—')[:120]}\n"
        f"**错误** `{error[:300]}`"
    )
    if send_discord_message(content[:1900]):
        _mark_sent(key)
        return True
    return False


def notify_csl_exit(
    *,
    strategy: str,
    match_title: str,
    question: str,
    reason: str,
    shares: int,
    entry_price: float,
    exit_price: float,
    live: bool,
    condition_id: str,
    match_slug: str = "",
) -> bool:
    """Webhook for CSL explore exit (stop-loss). No auto TP."""
    if not _enabled():
        return False
    fp = hashlib.sha256(
        f"csl_exit:{strategy}:{condition_id}:{shares}:{exit_price:.4f}:{reason}".encode()
    ).hexdigest()[:12]
    key = f"csl_exit:{fp}"
    if not _should_send(key):
        return False

    mode = "LIVE" if live else "DRY"
    label = strategy_label(strategy)
    pnl = (exit_price - entry_price) * shares
    content = (
        f"**[{SOURCE}]** 🧪 **CSL Explore** {mode} 止损出场\n"
        f"**策略** `{strategy}` · {label}\n"
        f"**场次** {(match_title or match_slug or '—')[:80]}\n"
        f"**平仓** YES x{shares} @ ${exit_price:.2f} "
        f"(入场 ${entry_price:.2f}) · 估 PnL **${pnl:+.2f}**\n"
        f"**原因** {(reason or '—')[:160]}\n"
        f"**市场** {(question or '—')[:120]}\n"
        f"`cond:{condition_id[:18]}…`"
    )
    if send_discord_message(content[:1900]):
        _mark_sent(key)
        return True
    return False


def notify_csl_cycle_summary(
    *,
    live: bool,
    placed: int,
    deployed_usdc: float,
    week_spent: float,
    by_strategy: Dict[str, int],
) -> bool:
    """Optional end-of-cycle digest when something was placed."""
    if not _enabled() or placed <= 0:
        return False
    mode = "LIVE" if live else "DRY"
    key = f"csl_cycle:{datetime.now(CN_TZ).strftime('%Y%m%d%H%M')}:{placed}"
    if not _should_send(key):
        return False
    lines = [
        f"**[{SOURCE}]** 🧪 **CSL Explore** {mode} 本轮汇总",
        f"成交 **{placed}** 笔 · 本轮 ${deployed_usdc:.2f} · 周累计 ${week_spent:.2f}",
        "按策略:",
    ]
    for sid, n in sorted(by_strategy.items()):
        lines.append(f"· `{sid}` {strategy_label(sid)} ×{n}")
    if send_discord_message("\n".join(lines)[:1900]):
        _mark_sent(key)
        return True
    return False
