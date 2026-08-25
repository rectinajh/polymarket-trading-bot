"""Discord notifications for sports sleeve (shared DELPHI webhook)."""

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
STATE_PATH = Path("data") / "sports_discord_state.json"
DEFAULT_COOLDOWN_S = 1800


def _enabled() -> bool:
    raw = (os.getenv("POLYMARKET_SPORTS_DISCORD") or "true").strip().lower()
    return raw not in ("0", "false", "no", "off")


def _cooldown_s() -> int:
    raw = (os.getenv("POLYMARKET_SPORTS_DISCORD_COOLDOWN_S") or "").strip()
    try:
        return max(300, int(raw)) if raw else DEFAULT_COOLDOWN_S
    except ValueError:
        return DEFAULT_COOLDOWN_S


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
    state = _load_state()
    last = state.get(key)
    if not last:
        return True
    try:
        sent = datetime.fromisoformat(str(last))
        if sent.tzinfo is None:
            sent = sent.replace(tzinfo=CN_TZ)
        return datetime.now(CN_TZ) - sent.astimezone(CN_TZ) >= timedelta(seconds=_cooldown_s())
    except ValueError:
        return True


def _mark_sent(key: str) -> None:
    state = _load_state()
    state[key] = datetime.now(CN_TZ).isoformat()
    _save_state(state)


def notify_sports_order(
    *,
    team: str,
    match: str,
    shares: int,
    price: float,
    edge: float,
    fair_prob: float,
    live: bool,
    condition_id: str,
    rn1_reason: str = "",
) -> bool:
    if not _enabled():
        return False
    fp = hashlib.sha256(f"order:{condition_id}:{shares}:{price}".encode()).hexdigest()[:12]
    key = f"order:{fp}"
    if not _should_send(key):
        return False

    mode = "LIVE" if live else "DRY"
    content = (
        f"**[{SOURCE}]** ⚽ 体育 {mode} 挂单\n"
        f"**{team}** YES x{shares} @ ${price:.2f}\n"
        f"Pinn {fair_prob:.2f} · edge {edge:.2f}\n"
        f"{match}\n"
        f"RN1: {rn1_reason[:120] if rn1_reason else '—'}"
    )
    if send_discord_message(content[:1900]):
        _mark_sent(key)
        return True
    return False


def notify_sports_settlement(
    *,
    team: str,
    status: str,
    pnl_cents: int,
    title: str = "",
) -> bool:
    if not _enabled():
        return False
    key = f"settle:{team}:{status}:{pnl_cents}"
    if not _should_send(key):
        return False

    icon = "✅" if pnl_cents >= 0 else "❌"
    content = (
        f"**[{SOURCE}]** ⚽ 体育结算 {icon}\n"
        f"**{team}** → {status}\n"
        f"PnL **${pnl_cents/100:+.2f}**\n"
        f"{(title or '')[:100]}"
    )
    if send_discord_message(content[:1900]):
        _mark_sent(key)
        return True
    return False


def notify_sports_halt(reason: str, meta: Optional[Dict[str, Any]] = None) -> bool:
    if not _enabled():
        return False
    key = f"halt:{reason[:40]}"
    if not _should_send(key):
        return False

    lines = [f"**[{SOURCE}]** ⚽ 体育袖套 **暂停新开仓**", reason]
    if meta:
        if meta.get("days_remaining") is not None:
            lines.append(f"实验剩余 **{meta['days_remaining']}** 天")
        if meta.get("realized_pnl_cents") is not None:
            lines.append(f"累计 PnL **${int(meta['realized_pnl_cents'])/100:+.2f}**")
    if send_discord_message("\n".join(lines)[:1900]):
        _mark_sent(key)
        return True
    return False
