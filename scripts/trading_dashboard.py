#!/usr/bin/env python3
"""
Polymarket Trading System Dashboard

A Streamlit-based dashboard for monitoring and analyzing all aspects of the
Polymarket trading system including:
- Strategy performance analytics
- LLM query analysis and review
- Real-time position tracking (USDC.e on Polygon, Polymarket CLOB)
- Risk management monitoring
- System health metrics
- P&L analytics by strategy
"""

import streamlit as st
import asyncio
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import sys
import os
import re
from pathlib import Path
from datetime import datetime, timedelta, timezone
import json
from typing import Optional

# Add parent directory to path for imports
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.append(str(PROJECT_ROOT))

from dotenv import load_dotenv
load_dotenv(PROJECT_ROOT / ".env", override=True)

from src.utils.database import DatabaseManager
from src.clients import build_polymarket_clients
from src.strategies.capital_policy import DailyEntryLog, MAX_ENTRIES_PER_DAY, trading_day
from src.strategies.scan_stats import ScanStatsLog, DEFAULT_STATS_PATH

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
_DEFAULT_LOG_PATH = PROJECT_ROOT / "logs" / "latest.log"


# Configure Streamlit page
st.set_page_config(
    page_title="Polymarket Trading Dashboard",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Custom CSS for better styling
st.markdown("""
<style>
    .metric-card {
        background-color: #f0f2f6;
        padding: 1rem;
        border-radius: 0.5rem;
        margin: 0.5rem 0;
    }
    
    .success-metric {
        background-color: #d4edda;
        border-left: 4px solid #28a745;
    }
    
    .warning-metric {
        background-color: #fff3cd;
        border-left: 4px solid #ffc107;
    }
    
    .danger-metric {
        background-color: #f8d7da;
        border-left: 4px solid #dc3545;
    }
    
    .llm-query {
        background-color: #f8f9fa;
        border: 1px solid #dee2e6;
        border-radius: 0.5rem;
        padding: 1rem;
        margin: 0.5rem 0;
    }

    .log-panel {
        background: #0f172a;
        color: #e2e8f0;
        border-radius: 0.5rem;
        padding: 1rem;
        font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
        font-size: 0.78rem;
        line-height: 1.45;
        max-height: 520px;
        overflow-y: auto;
        white-space: pre-wrap;
        word-break: break-word;
    }
</style>
""", unsafe_allow_html=True)


def _resolve_log_path() -> Path:
    """Prefer logs/latest.log, fall back to newest trading_system_*.log."""
    if _DEFAULT_LOG_PATH.exists():
        return _DEFAULT_LOG_PATH
    logs_dir = PROJECT_ROOT / "logs"
    if not logs_dir.exists():
        return _DEFAULT_LOG_PATH
    candidates = sorted(
        logs_dir.glob("trading_system_*.log"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return candidates[0] if candidates else _DEFAULT_LOG_PATH


def read_latest_logs(lines: int = 200, level_filter: str = "ALL") -> tuple[str, Path, int]:
    """Read the last N lines from the bot log file (ANSI stripped).

    Uses a byte-window tail read so large logs (10MB+) do not get fully loaded.
    """
    path = _resolve_log_path()
    if not path.exists():
        return f"(log file not found: {path})", path, 0

    try:
        cleaned, approx_total = _tail_log_lines(path, max_lines=max(lines, 1) * 4 if level_filter != "ALL" else max(lines, 1))
    except OSError as exc:
        return f"(failed to read log: {exc})", path, 0

    if level_filter and level_filter != "ALL":
        token = f" {level_filter} "
        cleaned = [
            line for line in cleaned
            if token in line or f"[{level_filter.lower()}" in line.lower()
        ]
    selected = cleaned[-lines:] if lines > 0 else cleaned
    return "\n".join(selected) if selected else "(no matching log lines)", path, approx_total


def _tail_log_lines(path: Path, max_lines: int, max_bytes: int = 2_000_000) -> tuple[list[str], int]:
    """Return up to max_lines from end of file without reading the whole file."""
    with open(path, "rb") as f:
        f.seek(0, 2)
        size = f.tell()
        read_size = min(size, max_bytes)
        f.seek(size - read_size)
        raw = f.read()
    text = raw.decode("utf-8", errors="replace")
    lines = text.splitlines()
    if size > read_size and lines:
        # First line may be a partial chunk from mid-line.
        lines = lines[1:]
    cleaned = [_ANSI_RE.sub("", line) for line in lines]
    # Approximate total line count from byte ratio when we only tailed.
    if size > 0 and read_size > 0 and size > read_size:
        approx_total = max(len(cleaned), int(len(cleaned) * (size / read_size)))
    else:
        approx_total = len(cleaned)
    if max_lines > 0:
        cleaned = cleaned[-max_lines:]
    return cleaned, approx_total


_CONF_IN_RATIONALE_RE = re.compile(r"Conf\s*=\s*([0-9.]+)\s*%?", re.IGNORECASE)


def _parse_confidence(confidence, rationale: Optional[str] = None) -> Optional[float]:
    """Prefer DB confidence; fall back to Conf=xx% embedded in rationale."""
    if isinstance(confidence, (int, float)):
        return float(confidence)
    if not rationale:
        return None
    match = _CONF_IN_RATIONALE_RE.search(rationale)
    if not match:
        return None
    value = float(match.group(1))
    return value / 100.0 if value > 1.0 else value


def _max_profit_if_win(entry_price: float, quantity: float) -> float:
    """Binary outcome pays $1/share if correct → max profit = (1 - entry) * qty."""
    return max(0.0, (1.0 - float(entry_price or 0)) * float(quantity or 0))


def _outcome_unrealized_pnl(entry_price: float, current_price: float, size: float) -> float:
    """PnL for a held outcome token. current_price is that token's mark (YES or NO)."""
    return (float(current_price) - float(entry_price)) * float(size)


def _enrich_position_fields(pos: dict) -> dict:
    """Attach display helpers: trade cost, max profit, SL/TP flags, confidence."""
    entry = float(pos.get("entry_price") or 0)
    qty = float(pos.get("quantity") or 0)
    rationale = pos.get("rationale") or ""
    conf = _parse_confidence(pos.get("confidence"), rationale)
    sl = pos.get("stop_loss_price")
    tp = pos.get("take_profit_price")
    pos["confidence"] = conf
    pos["trade_amount"] = round(entry * qty, 4)
    pos["max_profit"] = round(_max_profit_if_win(entry, qty), 4)
    pos["has_stop_loss"] = sl is not None
    pos["has_take_profit"] = tp is not None
    pos["ai_suggestion"] = rationale or "—"
    pos["title"] = pos.get("title") or pos.get("market_id") or "—"
    return pos


def build_participation_rows(positions: list, *, truncate_reason: int = 80) -> list[dict]:
    """Chinese-labeled rows for participation / AI advice / exits."""
    rows = []
    for pos in positions:
        entry = float(pos.get("entry_price") or 0)
        qty = float(pos.get("quantity") or 0)
        cur = pos.get("current_price")
        sl = pos.get("stop_loss_price")
        tp = pos.get("take_profit_price")
        conf = pos.get("confidence")
        reason = pos.get("rationale") or pos.get("ai_suggestion") or ""
        if truncate_reason and len(reason) > truncate_reason:
            reason_short = reason[:truncate_reason] + "…"
        else:
            reason_short = reason or "—"
        trade_amt = pos.get("trade_amount")
        if trade_amt is None:
            trade_amt = entry * qty
        max_profit = pos.get("max_profit")
        if max_profit is None:
            max_profit = _max_profit_if_win(entry, qty)
        try:
            ts = datetime.fromisoformat(pos["timestamp"])
            time_str = ts.strftime("%m/%d %H:%M")
        except Exception:
            time_str = "—"

        sl_tp = []
        if sl is not None:
            sl_tp.append(f"止损 ${float(sl):.3f}")
        if tp is not None:
            sl_tp.append(f"止盈 ${float(tp):.3f}")
        sl_tp_text = " · ".join(sl_tp) if sl_tp else "未设置"

        rows.append({
            "项目": pos.get("title") or (pos.get("market_id") or "")[:24],
            "方向": pos.get("side") or "—",
            "参与理由 / AI建议": reason_short,
            "AI置信度": f"{conf * 100:.0f}%" if isinstance(conf, (int, float)) else "—",
            "投注金额": f"${float(trade_amt):.2f}",
            "份额": qty if qty == int(qty) else round(qty, 2),
            "入场价": f"${entry:.3f}",
            "目前价格": f"${float(cur):.3f}" if isinstance(cur, (int, float)) else "—",
            "理论最大利润": f"${float(max_profit):.2f}",
            "当前浮盈": (
                f"${float(pos['unrealized_pnl']):.3f}"
                if isinstance(pos.get("unrealized_pnl"), (int, float))
                else "—"
            ),
            "止损止盈": sl_tp_text,
            "策略": pos.get("strategy") or "—",
            "入场时间": time_str,
            "_market_id": pos.get("market_id") or "",
        })
    return rows


def _position_note_fingerprint(positions: list) -> tuple:
    """Stable cache key: market/side/rounded price (refresh when MTM moves)."""
    items = []
    for p in positions or []:
        mid = str(p.get("market_id") or "")
        side = str(p.get("side") or "")
        cur = p.get("current_price")
        try:
            cur_r = round(float(cur), 2) if cur is not None else None
        except (TypeError, ValueError):
            cur_r = None
        items.append((mid, side, cur_r))
    return tuple(sorted(items))


def _parse_ai_notes_json(text: str) -> list[dict]:
    """Extract a JSON list from model output (allow markdown fences)."""
    if not text:
        return []
    raw = text.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
    try:
        data = json.loads(raw)
        if isinstance(data, list):
            return [x for x in data if isinstance(x, dict)]
        if isinstance(data, dict) and isinstance(data.get("notes"), list):
            return [x for x in data["notes"] if isinstance(x, dict)]
    except json.JSONDecodeError:
        # try to find first [...] block
        m = re.search(r"\[[\s\S]*\]", raw)
        if m:
            try:
                data = json.loads(m.group(0))
                if isinstance(data, list):
                    return [x for x in data if isinstance(x, dict)]
            except json.JSONDecodeError:
                return []
    return []


@st.cache_data(ttl=900, show_spinner="生成 AI 观察文案（仅展示）…")
def load_ai_watch_notes(fingerprint: tuple, payload: tuple) -> dict:
    """Call LLM once for a batch of positions. Display-only; never places orders.

    Returns {market_id: {"why": str, "risk": str}} or {"__error__": "..."}.
    """
    if not payload:
        return {}

    lines = []
    for i, row in enumerate(payload, start=1):
        (
            mid,
            title,
            side,
            entry,
            cur,
            upnl,
            qty,
            strategy,
        ) = row
        lines.append(
            f"{i}. id={mid[:20]}… | {title[:80]} | side={side} | "
            f"entry={entry} | now={cur} | qty={qty} | upnl={upnl} | strategy={strategy}"
        )

    prompt = (
        "你是预测市场风控助手。下面是账户当前持仓摘要。"
        "请对每个项目用中文各写两句：\n"
        "1) why：为什么值得继续关注（或为何已不值得）\n"
        "2) risk：主要风险一句话\n"
        "硬性要求：不要给出买入/加仓/卖出指令；不要编造盘口数字；"
        "若价格为0或缺失，说明可能无流动性/已结算。\n"
        "只输出 JSON 数组，每项字段：market_id, why, risk。"
        "market_id 必须与输入 id 前缀能对应（用完整 id 若你能从输入还原，"
        "否则用输入里的 id= 值）。\n\n"
        "持仓列表：\n" + "\n".join(lines)
    )

    async def _run():
        from src.clients.xai_client import XAIClient

        client = XAIClient()
        try:
            text = await client.get_completion(
                prompt=prompt,
                max_tokens=800,
                temperature=0.2,
                strategy="dashboard_watch",
                query_type="dashboard_display",
            )
            return text
        finally:
            try:
                await client.close()
            except Exception:
                pass

    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            text = loop.run_until_complete(_run())
        finally:
            loop.close()
    except Exception as exc:
        return {"__error__": str(exc)}

    if not text:
        return {
            "__error__": "AI 无返回（可能日预算已用尽）。文案仅展示，不影响交易。"
        }

    notes = _parse_ai_notes_json(text)
    # Map back to full market_ids from fingerprint
    id_by_prefix = {mid[:20]: mid for mid, _side, _cur in fingerprint}
    out: dict = {}
    for n in notes:
        mid = str(n.get("market_id") or "").strip()
        why = str(n.get("why") or "").strip()
        risk = str(n.get("risk") or "").strip()
        if not mid:
            continue
        full = mid
        if mid not in {m for m, _, _ in fingerprint}:
            # match by prefix
            pref = mid[:20]
            full = id_by_prefix.get(pref, mid)
            for cand, _, _ in fingerprint:
                if cand.startswith(mid) or mid.startswith(cand[:18]):
                    full = cand
                    break
        out[full] = {"why": why or "—", "risk": risk or "—"}

    # Fallback: if model ignored ids, zip in order
    if not out and notes and fingerprint:
        for (mid, _s, _c), n in zip(fingerprint, notes):
            out[mid] = {
                "why": str(n.get("why") or "—"),
                "risk": str(n.get("risk") or "—"),
            }
    return out


def render_ai_watch_notes(positions: list, *, expanded: bool = False) -> dict:
    """UI block: optional AI why/risk lines. Never triggers trading."""
    st.subheader("🤖 AI 观察（仅展示 · 不下单）")
    st.caption(
        "用模型写「为什么值得看 / 风险一句话」。不读取也不可下单；"
        "会消耗少量 AI 日预算，结果缓存约 15 分钟。"
    )
    enable = st.checkbox(
        "生成 / 显示 AI 观察文案",
        value=False,
        key="ai_watch_notes_enable",
        help="关闭时不调用模型。开启后仅用于监控文案。",
    )
    if not enable:
        st.info("已关闭。勾选上方即可生成观察文案。")
        return {}

    # Prefer positions with non-zero MTM; then fill up to 8
    ranked = sorted(
        positions or [],
        key=lambda p: abs(float(p.get("current_price") or 0))
        * float(p.get("quantity") or 0),
        reverse=True,
    )
    ranked = [
        p for p in ranked
        if p.get("market_id")
    ][:8]
    if not ranked:
        st.info("当前没有可注释的持仓。")
        return {}

    fingerprint = _position_note_fingerprint(ranked)
    payload = []
    for p in ranked:
        cur = p.get("current_price")
        upnl = p.get("unrealized_pnl")
        payload.append(
            (
                str(p.get("market_id")),
                str(p.get("title") or p.get("market_id") or "")[:100],
                str(p.get("side") or ""),
                f"{float(p.get('entry_price') or 0):.3f}",
                f"{float(cur):.3f}" if isinstance(cur, (int, float)) else "n/a",
                f"{float(upnl):.3f}" if isinstance(upnl, (int, float)) else "n/a",
                float(p.get("quantity") or 0),
                str(p.get("strategy") or "—"),
            )
        )

    notes = load_ai_watch_notes(fingerprint, tuple(payload))
    if notes.get("__error__"):
        st.warning(notes["__error__"])
        return {}

    for p in ranked:
        mid = p.get("market_id")
        note = notes.get(mid) or {}
        title = p.get("title") or mid
        with st.expander(
            f"{title} · {p.get('side')}",
            expanded=expanded,
        ):
            st.markdown(f"**为什么值得看：** {note.get('why') or '（暂无）'}")
            st.markdown(f"**风险一句话：** {note.get('risk') or '（暂无）'}")
            cur = p.get("current_price")
            st.caption(
                f"入场 ${float(p.get('entry_price') or 0):.3f} · "
                f"现价 {f'${float(cur):.3f}' if isinstance(cur, (int, float)) else '—'} · "
                f"仅供参考，不构成交易指令"
            )
    return notes


@st.cache_data(ttl=45, show_spinner="加载持仓与策略数据…")
def load_performance_data():
    """Load strategy stats + positions (DB TP/SL merged with live CLOB prices) + open orders."""
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        db_manager = DatabaseManager()

        async def get_data():
            await db_manager.initialize()

            performance_raw = await db_manager.get_performance_by_strategy()
            performance = {}
            if performance_raw:
                for strategy, stats in performance_raw.items():
                    performance[str(strategy)] = {
                        str(k): float(v) if isinstance(v, (int, float)) else str(v)
                        for k, v in stats.items()
                    }

            # DB positions carry stop-loss / take-profit / max_hold / strategy.
            db_positions = await db_manager.get_open_live_positions()
            db_by_key = {}
            for p in db_positions:
                key = (str(p.market_id).lower(), str(p.side).upper())
                db_by_key[key] = p

            positions = []
            open_orders = []
            async with build_polymarket_clients() as (client, _gamma):
                try:
                    positions_response = await client.get_positions()
                except Exception:
                    positions_response = {"market_positions": []}

                live_keys = set()
                live_market_ids: set[str] = set()
                for pos in positions_response.get("market_positions", []):
                    size = float(pos.get("size", 0) or 0)
                    if size <= 0:
                        continue
                    cond = str(pos.get("condition_id") or pos.get("ticker") or "")
                    side = str(pos.get("side", "YES")).upper()
                    if side in ("BUY", "LONG"):
                        side = "YES"
                    elif side in ("SELL", "SHORT"):
                        side = "NO"
                    key = (cond.lower(), side)
                    live_keys.add(key)
                    live_market_ids.add(cond)
                    dbp = db_by_key.get(key)
                    entry = float(pos.get("avg_price", 0) or 0) or (
                        float(dbp.entry_price) if dbp else 0.0
                    )
                    cur = float(pos.get("current_price", 0) or 0)
                    qty = int(size) if size >= 1 else size
                    # Data API curPrice is the held outcome token mark — same formula for YES/NO.
                    unrealized = _outcome_unrealized_pnl(entry, cur, float(size))
                    dist_sl = None
                    dist_tp = None
                    sl = dbp.stop_loss_price if dbp else None
                    tp = dbp.take_profit_price if dbp else None
                    if sl and cur:
                        dist_sl = float(cur) - float(sl)
                    if tp and cur:
                        dist_tp = float(tp) - float(cur)

                    positions.append({
                        "id": getattr(dbp, "id", None) if dbp else None,
                        "market_id": cond,
                        "condition_id": cond,
                        "token_id": str(pos.get("token_id", "")),
                        "side": side,
                        "quantity": qty,
                        "entry_price": entry,
                        "current_price": cur,
                        "unrealized_pnl": round(unrealized, 4),
                        "realized_pnl": float(pos.get("realized_pnl_dollars", 0) or 0),
                        "timestamp": (dbp.timestamp.isoformat() if dbp and dbp.timestamp else datetime.now().isoformat()),
                        "strategy": (dbp.strategy if dbp and dbp.strategy else "live_sync"),
                        "status": "open",
                        "live": True,
                        "stop_loss_price": sl,
                        "take_profit_price": tp,
                        "max_hold_hours": dbp.max_hold_hours if dbp else None,
                        "confidence": dbp.confidence if dbp else None,
                        "rationale": (dbp.rationale if dbp else None),
                        "dist_to_sl": round(dist_sl, 4) if dist_sl is not None else None,
                        "dist_to_tp": round(dist_tp, 4) if dist_tp is not None else None,
                        "source": "clob+db" if dbp else "clob_only",
                        "_title_hint": pos.get("title") or pos.get("question"),
                    })

                # DB-only opens not yet visible on CLOB (pending / indexing lag)
                for key, dbp in db_by_key.items():
                    if key in live_keys:
                        continue
                    live_market_ids.add(dbp.market_id)
                    positions.append({
                        "id": dbp.id,
                        "market_id": dbp.market_id,
                        "condition_id": dbp.market_id,
                        "token_id": "",
                        "side": dbp.side,
                        "quantity": dbp.quantity,
                        "entry_price": float(dbp.entry_price),
                        "current_price": None,
                        "unrealized_pnl": None,
                        "realized_pnl": 0.0,
                        "timestamp": dbp.timestamp.isoformat() if dbp.timestamp else datetime.now().isoformat(),
                        "strategy": dbp.strategy or "unknown",
                        "status": dbp.status,
                        "live": bool(dbp.live),
                        "stop_loss_price": dbp.stop_loss_price,
                        "take_profit_price": dbp.take_profit_price,
                        "max_hold_hours": dbp.max_hold_hours,
                        "confidence": dbp.confidence,
                        "rationale": dbp.rationale,
                        "dist_to_sl": None,
                        "dist_to_tp": None,
                        "source": "db_only",
                        "_title_hint": None,
                    })

                title_ids = list(live_market_ids | {p.market_id for p in db_positions})
                titles_by_id = await db_manager.get_market_titles(title_ids) if title_ids else {}
                titles_by_id_lower = {str(k).lower(): v for k, v in titles_by_id.items()}
                for pos in positions:
                    mid = str(pos.get("market_id") or "")
                    title = titles_by_id_lower.get(mid.lower()) or pos.get("_title_hint")
                    if not title:
                        title = mid[:24] + "…" if mid else "—"
                    pos["title"] = title
                    pos.pop("_title_hint", None)
                    _enrich_position_fields(pos)

                try:
                    orders_resp = await client.get_orders()
                    for o in orders_resp.get("orders", []) or []:
                        created = o.get("created_at") or ""
                        try:
                            ts = int(created)
                            if ts > 10_000_000_000:
                                ts //= 1000
                            when = datetime.utcfromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S UTC")
                        except Exception:
                            when = str(created)
                        open_orders.append({
                            "order_id": o.get("order_id") or o.get("id") or "",
                            "status": o.get("status") or "",
                            "side": o.get("side") or "",
                            "outcome": o.get("outcome") or "",
                            "price": float(o.get("price_dollars") or o.get("price") or 0),
                            "size": float(o.get("original_size") or o.get("count") or 0),
                            "matched": float(o.get("size_matched") or 0),
                            "order_type": o.get("order_type") or "",
                            "market": (o.get("market") or o.get("condition_id") or "")[:20] + "…",
                            "created": when,
                        })
                except Exception:
                    pass

            await db_manager.close()
            return performance, positions, open_orders

        performance, positions, open_orders = loop.run_until_complete(get_data())
        loop.close()
        return performance, positions, open_orders

    except Exception as e:
        # Avoid st.* inside cached loader (would sticky-cache UI side effects).
        return {"__load_error__": str(e)}, [], []

@st.cache_data(ttl=45, show_spinner="加载 LLM 数据…")
def load_llm_data():
    """Load LLM query data from database."""
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        db_manager = DatabaseManager()
        
        async def get_data():
            await db_manager.initialize()
            
            # Get recent LLM queries
            queries = await db_manager.get_llm_queries(hours_back=24, limit=100)
            
            # Get LLM stats by strategy with improved token calculation
            stats = await db_manager.get_llm_stats_by_strategy()
            
            # Fix token count issues by recalculating from response lengths if needed
            for strategy, strategy_stats in stats.items():
                if strategy_stats.get('total_tokens', 0) == 0:
                    # Recalculate tokens from query responses for this strategy
                    strategy_queries = [q for q in queries if q.strategy == strategy]
                    estimated_tokens = 0
                    for query in strategy_queries:
                        # Estimate tokens: ~4 characters per token
                        prompt_tokens = len(query.prompt) // 4 if query.prompt else 0
                        response_tokens = len(query.response) // 4 if query.response else 0
                        estimated_tokens += prompt_tokens + response_tokens
                    
                    strategy_stats['total_tokens'] = estimated_tokens
                    strategy_stats['estimated'] = True
            
            await db_manager.close()
            
            return queries, stats
        
        queries, stats = loop.run_until_complete(get_data())
        loop.close()
        
        return queries, stats
        
    except Exception as e:
        st.error(f"Error loading LLM data: {e}")
        return [], {}

@st.cache_data(ttl=30)  # Refresh often — live cash / deposits move quickly
def load_system_health():
    """Load pUSD cash + position MTM + deposit wallet + recent fills/deposits."""
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        async def get_health():
            async with build_polymarket_clients() as (client, _gamma):
                balance_response = await client.get_balance()
                available_cash = float(balance_response.get("balance_dollars", 0) or 0)
                wallet_address = balance_response.get("address", "?")
                eoa_address = balance_response.get("eoa_address") or "?"
                collateral_token = balance_response.get("collateral_token") or "pUSD"
                pusd_dollars = float(balance_response.get("pusd_dollars", 0) or 0)
                usdc_e_dollars = float(balance_response.get("usdc_e_dollars", 0) or 0)

                positions_response = await client.get_positions()
                market_positions = positions_response.get("market_positions", [])

                total_position_value = 0.0
                positions_count = 0
                for pos in market_positions:
                    size = float(pos.get("size", 0) or 0)
                    if size <= 0:
                        continue
                    positions_count += 1
                    cur = float(pos.get("current_price", 0) or 0)
                    total_position_value += size * cur

                total_portfolio_value = available_cash + total_position_value

                recent_trades: list = []
                try:
                    trades_resp = await client.get_trades(limit=25)
                    for t in trades_resp.get("trades", [])[:25]:
                        txh = t.get("transaction_hash") or t.get("transactionHash") or ""
                        match_ts = t.get("match_time") or t.get("last_update") or t.get("timestamp") or ""
                        try:
                            ts_int = int(float(match_ts))
                            if ts_int > 10_000_000_000:
                                ts_int //= 1000
                            when = datetime.fromtimestamp(ts_int, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
                        except Exception:
                            when = str(match_ts)
                        title = (t.get("title") or "").strip()
                        cond = t.get("market") or t.get("conditionId") or ""
                        market_label = title[:60] if title else ((cond[:18] + "…") if cond else "")
                        recent_trades.append(
                            {
                                "when": when,
                                "side": t.get("side") or "",
                                "outcome": t.get("outcome") or "",
                                "size": float(t.get("size", 0) or 0),
                                "price": float(t.get("price", 0) or 0),
                                "status": t.get("status") or "",
                                "market": market_label,
                                "tx_hash": txh,
                            }
                        )
                except Exception as exc:
                    st.caption(f"最近成交暂不可用（CLOB/data-api）：{exc}")

                recent_deposits: list = []
                try:
                    recent_deposits = await client.get_recent_collateral_deposits(
                        lookback_blocks=40_000, limit=15
                    )
                except Exception as exc:
                    st.warning(f"Could not fetch recent deposits: {exc}")

                equity_snapshots: list = []
                realized_pnl = 0.0
                try:
                    dbm = DatabaseManager()
                    await dbm.initialize()
                    realized_pnl = await dbm.get_realized_pnl_total()
                    await dbm.record_equity_snapshot(
                        cash_dollars=available_cash,
                        position_value=total_position_value,
                        portfolio_value=total_portfolio_value,
                        realized_pnl=realized_pnl,
                        source="dashboard",
                        min_interval_seconds=30,
                    )
                    equity_snapshots = await dbm.get_equity_snapshots(hours=24 * 31)
                    await dbm.close()
                except Exception as exc:
                    st.warning(f"Equity snapshot unavailable: {exc}")

                return {
                    "available_cash": available_cash,
                    "total_portfolio_value": total_portfolio_value,
                    "positions_count": positions_count,
                    "position_value": total_position_value,
                    "wallet_address": wallet_address,
                    "eoa_address": eoa_address,
                    "collateral_token": collateral_token,
                    "pusd_dollars": pusd_dollars,
                    "usdc_e_dollars": usdc_e_dollars,
                    "recent_trades": recent_trades,
                    "recent_deposits": recent_deposits,
                    "equity_snapshots": equity_snapshots,
                    "realized_pnl": realized_pnl,
                }

        result = loop.run_until_complete(get_health())
        loop.close()
        return result

    except Exception as e:
        st.error(f"Error loading system health: {e}")
        return {
            "available_cash": 0.0,
            "total_portfolio_value": 0.0,
            "positions_count": 0,
            "position_value": 0.0,
            "wallet_address": "?",
            "eoa_address": "?",
            "collateral_token": "pUSD",
            "pusd_dollars": 0.0,
            "usdc_e_dollars": 0.0,
            "recent_trades": [],
            "recent_deposits": [],
            "equity_snapshots": [],
            "realized_pnl": 0.0,
        }

def main():
    """Main dashboard function."""

    st.title("🚀 Polymarket Trading System Dashboard")
    st.markdown("**Real-time monitoring and analysis of your Polymarket trading system**")

    # Add refresh button to clear cache
    col1, col2 = st.columns([4, 1])
    with col2:
        if st.button("🔄 Refresh Data", help="Clear cache and reload all data"):
            st.cache_data.clear()
            st.rerun()

    # Sidebar for navigation
    st.sidebar.title("📊 Dashboard")

    page = st.sidebar.selectbox(
        "Select View",
        [
            "📈 Overview",
            "📋 Live Logs",
            "🎯 Strategy Performance",
            "🤖 LLM Analysis",
            "💼 Positions & Trades",
            "⚠️ Risk Management",
            "🔧 System Health"
        ]
    )

    # Load data with error handling
    try:
        performance_data, positions, open_orders = load_performance_data()
        if isinstance(performance_data, dict) and performance_data.get("__load_error__"):
            st.error(f"Error loading performance data: {performance_data['__load_error__']}")
            performance_data = {}
        llm_queries, llm_stats = load_llm_data()
        system_health_data = load_system_health()
    except Exception as e:
        st.error(f"Error loading dashboard data: {e}")
        st.info("Please check your system connections and try refreshing.")
        return

    # Show wallet + data status in sidebar
    wallet = system_health_data.get("wallet_address", "?")
    eoa = system_health_data.get("eoa_address", "?")
    token = system_health_data.get("collateral_token", "pUSD")
    st.sidebar.markdown("---")
    st.sidebar.markdown("**🏦 Deposit Wallet (proxy / funder):**")
    if wallet and wallet != "?":
        st.sidebar.code(wallet, language="text")
        st.sidebar.caption(
            f"[PolygonScan](https://polygonscan.com/address/{wallet})"
        )
    else:
        st.sidebar.warning("Deposit wallet unavailable — check POLYMARKET_FUNDER")
    if eoa and eoa != "?" and eoa.lower() != str(wallet).lower():
        st.sidebar.markdown("**🔑 Signer EOA:**")
        st.sidebar.code(eoa, language="text")

    st.sidebar.markdown("---")
    st.sidebar.markdown("**📊 Data Status:**")
    st.sidebar.metric("Active Positions", len(positions) if positions else 0)
    st.sidebar.metric("Open Orders", len(open_orders) if open_orders else 0)
    st.sidebar.metric("LLM Queries (24h)", len(llm_queries) if llm_queries else 0)
    st.sidebar.metric(
        f"{token} Cash",
        f"${system_health_data.get('pusd_dollars', system_health_data.get('available_cash', 0)):.2f}",
    )
    st.sidebar.metric("Portfolio MTM", f"${system_health_data.get('total_portfolio_value', 0):.2f}")
    
    # Page routing
    if page == "📈 Overview":
        show_overview(performance_data, positions, system_health_data, open_orders)
    elif page == "📋 Live Logs":
        show_live_logs()
    elif page == "🎯 Strategy Performance":
        show_strategy_performance(performance_data)
    elif page == "🤖 LLM Analysis":
        show_llm_analysis(llm_queries, llm_stats)
    elif page == "💼 Positions & Trades":
        show_positions_trades(positions, open_orders)
    elif page == "⚠️ Risk Management":
        show_risk_management(performance_data, positions, system_health_data['total_portfolio_value'])
    elif page == "🔧 System Health":
        show_system_health(system_health_data['available_cash'], system_health_data['positions_count'], llm_stats)

def _filter_equity_snapshots(snapshots: list, hours: float) -> pd.DataFrame:
    """Filter equity snapshots to the last `hours` and add profit vs window start."""
    if not snapshots:
        return pd.DataFrame()
    cutoff = datetime.now() - timedelta(hours=float(hours))
    rows = []
    for s in snapshots:
        try:
            ts = datetime.fromisoformat(str(s.get("ts")))
        except (TypeError, ValueError):
            continue
        if ts < cutoff:
            continue
        rows.append(
            {
                "ts": ts,
                "portfolio_value": float(s.get("portfolio_value") or 0),
                "cash_dollars": float(s.get("cash_dollars") or 0),
                "position_value": float(s.get("position_value") or 0),
                "realized_pnl": float(s.get("realized_pnl") or 0),
            }
        )
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows).sort_values("ts")
    baseline = float(df["portfolio_value"].iloc[0])
    df["profit"] = df["portfolio_value"] - baseline
    return df


def _daily_net_profit(
    snapshots: list,
    live_portfolio: float,
) -> tuple[float, float, float]:
    """Return (daily_net, start_of_day_equity, daily_net_pct).

    Daily net = current equity − first snapshot on today's calendar date
    (falls back to earliest snapshot in the last 24h if no same-day point).
    """
    today = datetime.now().date()
    same_day: list[tuple[datetime, float]] = []
    last_24h: list[tuple[datetime, float]] = []
    cutoff_24h = datetime.now() - timedelta(hours=24)
    for s in snapshots or []:
        try:
            ts = datetime.fromisoformat(str(s.get("ts")))
        except (TypeError, ValueError):
            continue
        pv = float(s.get("portfolio_value") or 0)
        if ts.date() == today:
            same_day.append((ts, pv))
        if ts >= cutoff_24h:
            last_24h.append((ts, pv))

    if same_day:
        same_day.sort(key=lambda x: x[0])
        start_equity = same_day[0][1]
    elif last_24h:
        last_24h.sort(key=lambda x: x[0])
        start_equity = last_24h[0][1]
    else:
        start_equity = float(live_portfolio)

    daily_net = float(live_portfolio) - float(start_equity)
    pct = (daily_net / start_equity * 100.0) if start_equity > 1e-9 else 0.0
    return daily_net, float(start_equity), pct


def render_equity_profit_chart(system_health_data: dict) -> None:
    """Realtime-ish capital & profit chart for the Overview homepage."""
    st.subheader("📈 实时资金与利润")
    c1, c2, c3 = st.columns([2, 2, 2])
    with c1:
        window_label = st.selectbox(
            "时间范围",
            ["1 小时", "1 天（日）", "7 天（周）", "30 天（月）"],
            index=1,
            key="equity_window",
        )
    with c2:
        auto_refresh = st.checkbox("自动刷新 (30s)", value=False, key="equity_auto_refresh")
    with c3:
        st.caption("每次打开/刷新会写入资金快照（最少间隔 30 秒）")

    hours = {
        "1 小时": 1.0,
        "1 天（日）": 24.0,
        "7 天（周）": 24.0 * 7,
        "30 天（月）": 24.0 * 30,
    }[window_label]
    snapshots = system_health_data.get("equity_snapshots") or []
    df = _filter_equity_snapshots(snapshots, hours)

    # Always include the live point so the chart isn't empty on first visit
    live_portfolio = float(system_health_data.get("total_portfolio_value") or 0)
    live_cash = float(system_health_data.get("available_cash") or 0)
    live_pos = float(system_health_data.get("position_value") or 0)
    live_realized = float(system_health_data.get("realized_pnl") or 0)
    live_row = {
        "ts": datetime.now(),
        "portfolio_value": live_portfolio,
        "cash_dollars": live_cash,
        "position_value": live_pos,
        "realized_pnl": live_realized,
        "profit": 0.0,
    }
    if df.empty:
        df = pd.DataFrame([live_row])
    else:
        # Recompute profit vs first point in window; append live if newer
        if df["ts"].iloc[-1] < live_row["ts"] - timedelta(seconds=5):
            df = pd.concat([df, pd.DataFrame([live_row])], ignore_index=True)
        baseline = float(df["portfolio_value"].iloc[0])
        df["profit"] = df["portfolio_value"] - baseline

    latest = df.iloc[-1]
    start = df.iloc[0]
    profit_now = float(latest["portfolio_value"] - start["portfolio_value"])
    profit_pct = (
        profit_now / float(start["portfolio_value"]) * 100.0
        if float(start["portfolio_value"]) > 1e-9
        else 0.0
    )
    daily_net, day_start_equity, daily_pct = _daily_net_profit(
        snapshots, live_portfolio
    )

    m1, m2, m3, m4 = st.columns(4)
    with m1:
        st.metric("当前总权益", f"${float(latest['portfolio_value']):.2f}")
    with m2:
        st.metric("现金", f"${float(latest['cash_dollars']):.2f}")
    with m3:
        st.metric("持仓市值", f"${float(latest['position_value']):.2f}")
    with m4:
        st.metric(
            f"区间盈亏 ({window_label})",
            f"${profit_now:+.2f}",
            delta=f"{profit_pct:+.2f}% · 起点 ${float(start['portfolio_value']):.2f}",
        )

    d1, d2, d3 = st.columns(3)
    with d1:
        st.metric(
            "每日净利润",
            f"${daily_net:+.2f}",
            help="今日当前总权益 − 今日首个资金快照权益（含充值影响）",
        )
    with d2:
        st.metric(
            "每日净利润 %",
            f"{daily_pct:+.2f}%",
            delta=f"今日起点 ${day_start_equity:.2f}",
            help="每日净利润 / 今日起点权益",
        )
    with d3:
        st.metric(
            "区间收益率",
            f"{profit_pct:+.2f}%",
            help=f"相对所选时间窗口（{window_label}）起点的总权益变化百分比",
        )

    fig = make_subplots(specs=[[{"secondary_y": True}]])
    fig.add_trace(
        go.Scatter(
            x=df["ts"],
            y=df["portfolio_value"],
            name="总权益 (Cash+持仓)",
            mode="lines+markers",
            line=dict(width=2, color="#2563eb"),
            marker=dict(size=5),
        ),
        secondary_y=False,
    )
    fig.add_trace(
        go.Scatter(
            x=df["ts"],
            y=df["cash_dollars"],
            name="现金",
            mode="lines",
            line=dict(width=1.5, color="#10b981", dash="dot"),
        ),
        secondary_y=False,
    )
    fig.add_trace(
        go.Scatter(
            x=df["ts"],
            y=df["position_value"],
            name="持仓市值",
            mode="lines",
            line=dict(width=1.5, color="#f59e0b", dash="dot"),
        ),
        secondary_y=False,
    )
    fig.add_trace(
        go.Scatter(
            x=df["ts"],
            y=df["profit"],
            name="区间盈亏",
            mode="lines",
            line=dict(width=2, color="#ef4444"),
            fill="tozeroy",
            fillcolor="rgba(239,68,68,0.08)",
        ),
        secondary_y=True,
    )
    fig.update_layout(
        height=380,
        margin=dict(l=20, r=20, t=30, b=20),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0),
        hovermode="x unified",
        template="plotly_white",
    )
    fig.update_yaxes(title_text="资金 ($)", secondary_y=False)
    fig.update_yaxes(title_text="盈亏 ($)", secondary_y=True)
    st.plotly_chart(fig, width="stretch")
    st.caption(
        "区间盈亏 = 当前总权益 − 该时间窗口起点权益。每日净利润按自然日计算。"
        "充值会抬高权益与净利润；"
        f"已实现平仓盈亏合计（trade_logs）≈ ${live_realized:.2f}。"
    )

    if auto_refresh:
        import time as _time
        _time.sleep(30)
        st.cache_data.clear()
        st.rerun()


def _format_scan_ts(ts: Optional[str]) -> str:
    if not ts:
        return "—"
    try:
        dt = datetime.fromisoformat(str(ts))
        return dt.strftime("%m-%d %H:%M")
    except (TypeError, ValueError):
        return str(ts)[:16]


def _reject_rows(rejects: dict, limit: int = 8) -> pd.DataFrame:
    if not rejects:
        return pd.DataFrame(columns=["原因", "次数"])
    items = sorted(rejects.items(), key=lambda x: -int(x[1]))[:limit]
    labels = {
        "edge_lt_min": "edge 不足（0<edge<2¢）",
        "edge_negative": "edge 为负（ask>公允价）",
        "no_ask_lt_min": "NO ask 过低",
        "yes_last_gt_max": "YES last 过高",
        "low_confidence": "置信度不足",
        "book_empty": "盘口为空",
        "disconnect": "盘口请求失败",
        "book_disconnect": "盘口异常",
        "no_real_ask": "无真实 NO ask",
        "thin_ask": "ask 量过小",
        "ask_below_min": "NO ask 低于阈值",
        "combined_ge_max": "YES+NO ≥ $1",
        "combined": "YES+NO ≥ $1",
        "profit_lt_min": "利润不足",
        "depth_lt_min": "深度不足",
        "thin_size": "深度不足",
        "vol_lt_min": "成交量不足",
    }
    return pd.DataFrame(
        [{"原因": labels.get(k, k), "次数": int(v)} for k, v in items if int(v) > 0]
    )


@st.cache_data(ttl=30)
def _load_scan_dashboard_data(stats_path: str) -> dict:
    log = ScanStatsLog(Path(stats_path))
    today = trading_day()
    summary = log.daily_summary(today)
    latest = log.latest_cycle() or {}
    series = log.daily_filled_series(days=7)
    entries = DailyEntryLog()
    return {
        "summary": summary,
        "latest": latest,
        "series": series,
        "entries_used": MAX_ENTRIES_PER_DAY - entries.remaining(),
        "entries_remaining": entries.remaining(),
        "stats_path": stats_path,
    }


def render_conservative_scan_panel(project_root: Path) -> None:
    """Conservative strategy scan metrics for the 7-day observation window."""
    stats_path = project_root / DEFAULT_STATS_PATH
    data = _load_scan_dashboard_data(str(stats_path))
    summary = data["summary"]
    latest = data["latest"]
    sc = summary["safe_compounder"]
    arb = summary["completeness_arb"]
    sc_latest = latest.get("safe_compounder") or {}
    arb_latest = latest.get("completeness_arb") or {}

    st.subheader("🔍 Conservative 扫描观察")
    st.caption(
        f"交易日 {summary['day']}（Asia/Shanghai）· "
        f"最近扫描 {_format_scan_ts(summary.get('last_scan_ts'))} · "
        f"今日轮次 {summary['cycles']} · "
        f"今日新开仓 {data['entries_used']}/{MAX_ENTRIES_PER_DAY}（两策略共用）"
    )

    if summary["cycles"] == 0:
        st.info(
            "尚无扫描记录。Bot 每轮 Conservative 循环结束后会写入 "
            f"`{DEFAULT_STATS_PATH}`。部署本功能后需等 1～2 个扫描周期。"
        )
        return

    c1, c2, c3, c4, c5 = st.columns(5)
    with c1:
        st.metric(
            "最近一轮 · 扫描市场",
            f"{sc_latest.get('markets_scanned', sc['latest_markets_scanned'])}",
            help="Safe Compounder 最近一轮从 Gamma 拉取的市场数",
        )
    with c2:
        st.metric(
            "最近一轮 · 过 edge",
            f"{sc_latest.get('opportunities', sc['latest_opportunities'])}",
            help="满足 NO ask、edge ≥ 2¢ 等条件的候选数",
        )
    with c3:
        st.metric(
            "今日 SC 成交",
            sc["total_filled"],
            delta=f"下单 {sc['total_placed']}",
            help="Safe Compounder 今日 FOK 即时成交笔数",
        )
    with c4:
        st.metric(
            "今日 Arb 成交",
            arb["total_filled_pairs"],
            delta=f"尝试 {arb['total_attempted']}",
            help="Completeness 今日成功 YES+NO 套利对数",
        )
    with c5:
        st.metric(
            "今日合计成交",
            summary["total_filled"],
            help="两策略今日成交总和",
        )

    with st.expander("最近一轮明细", expanded=False):
        l1, l2 = st.columns(2)
        with l1:
            st.markdown("**Safe Compounder**")
            st.markdown(
                f"- 扫描 **{sc_latest.get('markets_scanned', '—')}** · "
                f"NO 候选 **{sc_latest.get('candidates', '—')}** · "
                f"过 edge **{sc_latest.get('opportunities', '—')}**\n"
                f"- 下单 **{sc_latest.get('placed', 0)}** · "
                f"成交 **{sc_latest.get('filled', 0)}** · "
                f"赎回 **{sc_latest.get('redeemed', 0)}**\n"
                f"- 跳过：已有仓 {sc_latest.get('skipped_existing', 0)} · "
                f"聚类 {sc_latest.get('skipped_cluster', 0)} · "
                f"日限 {sc_latest.get('skipped_daily_cap', 0)}\n"
                f"- 耗时 {sc_latest.get('elapsed_s', '—')}s · "
                f"NAV ${float(sc_latest.get('nav_cents', 0) or 0) / 100:.2f}"
            )
            rej_sc = _reject_rows(sc_latest.get("rejects") or {})
            if not rej_sc.empty:
                st.markdown("**拒绝原因（本轮）**")
                st.dataframe(rej_sc, hide_index=True, width="stretch")
        with l2:
            st.markdown("**Completeness Arb**")
            st.markdown(
                f"- 扫描 **{arb_latest.get('scanned', '—')}** · "
                f"查盘口 **{arb_latest.get('checked_books', '—')}** · "
                f"有机会 **{arb_latest.get('opportunities', '—')}**\n"
                f"- 尝试 **{arb_latest.get('attempted', 0)}** · "
                f"成交 **{arb_latest.get('filled_pairs', 0)}** · "
                f"回滚 {arb_latest.get('unwound', 0)}\n"
                f"- 耗时 {arb_latest.get('elapsed_s', '—')}s"
            )
            rej_arb = _reject_rows(arb_latest.get("rejects") or {})
            if not rej_arb.empty:
                st.markdown("**拒绝原因（本轮）**")
                st.dataframe(rej_arb, hide_index=True, width="stretch")

    series = data["series"]
    if series and any(r["cycles"] > 0 for r in series):
        df = pd.DataFrame(series)
        fig = make_subplots(specs=[[{"secondary_y": True}]])
        fig.add_trace(
            go.Bar(x=df["day"], y=df["total_filled"], name="成交笔数", marker_color="#22c55e"),
            secondary_y=False,
        )
        fig.add_trace(
            go.Scatter(
                x=df["day"],
                y=df["sc_opportunities"] + df["arb_opportunities"],
                name="过 edge / 有机会（轮次累计）",
                mode="lines+markers",
                line=dict(color="#3b82f6"),
            ),
            secondary_y=True,
        )
        fig.update_layout(
            height=280,
            margin=dict(l=10, r=10, t=30, b=10),
            legend=dict(orientation="h", yanchor="bottom", y=1.02),
            title="近 7 日：成交 vs 有机会次数",
        )
        fig.update_yaxes(title_text="成交", secondary_y=False)
        fig.update_yaxes(title_text="有机会（累计）", secondary_y=True)
        st.plotly_chart(fig, use_container_width=True)

    # Top edge opportunities (latest cycle)
    top_edge = sc.get("top_edge") or []
    if top_edge:
        with st.expander(f"🏆 最佳 edge 候选 Top {len(top_edge)}（最近一轮）", expanded=False):
            te_df = pd.DataFrame(top_edge)
            te_df.columns = ["标题", "edge ($)", "NO ask ($)", "分类"]
            st.dataframe(te_df, hide_index=True, width="stretch")

    # Near-misses (latest cycle)
    near_misses = sc.get("near_misses") or []
    near_miss_count = sc.get("near_miss_count", 0)
    if near_misses:
        with st.expander(
            f"🎯 Near-miss（edge 1~2¢ 差一点就能交易）共 {near_miss_count} 个，展示 Top {len(near_misses)}",
            expanded=False,
        ):
            nm_df = pd.DataFrame(near_misses)
            nm_df.columns = ["标题", "edge ($)", "NO ask ($)", "分类"]
            st.dataframe(nm_df, hide_index=True, width="stretch")

    # Category breakdown + reject breakdown side by side
    cat_breakdown = sc.get("category_breakdown") or {}
    has_rejects = bool(sc["rejects"] or arb["rejects"])
    if cat_breakdown or has_rejects:
        cols = st.columns(3 if cat_breakdown and has_rejects else (2 if has_rejects else 1))
        col_idx = 0
        if cat_breakdown:
            with cols[col_idx]:
                st.markdown("**今日有 edge 市场分类**")
                cat_df = pd.DataFrame(
                    [{"分类": k, "有 edge 次数": int(v)} for k, v in cat_breakdown.items()]
                )
                st.dataframe(cat_df, hide_index=True, width="stretch")
                fig_cat = px.pie(
                    cat_df, names="分类", values="有 edge 次数",
                    height=250,
                )
                fig_cat.update_layout(margin=dict(l=0, r=0, t=25, b=0), showlegend=True)
                st.plotly_chart(fig_cat, use_container_width=True)
            col_idx += 1
        if sc["rejects"]:
            with cols[col_idx]:
                rej = _reject_rows(sc["rejects"])
                if not rej.empty:
                    st.markdown("**今日 SC 拒绝 Top**")
                    st.dataframe(rej, hide_index=True, width="stretch")
            col_idx += 1
        if arb["rejects"]:
            with cols[min(col_idx, len(cols) - 1)]:
                rej = _reject_rows(arb["rejects"])
                if not rej.empty:
                    st.markdown("**今日 Arb 拒绝 Top**")
                    st.dataframe(rej, hide_index=True, width="stretch")

    st.markdown("---")


def show_overview(performance_data, positions, system_health_data, open_orders=None):
    """Show overview dashboard."""
    
    st.header("📈 System Overview")
    open_orders = open_orders or []

    render_conservative_scan_panel(PROJECT_ROOT)

    token = system_health_data.get("collateral_token", "pUSD")
    deposit_addr = system_health_data.get("wallet_address", "?")
    eoa_addr = system_health_data.get("eoa_address", "?")
    pusd = float(system_health_data.get("pusd_dollars", system_health_data.get("available_cash", 0)) or 0)
    usdc_e = float(system_health_data.get("usdc_e_dollars", 0) or 0)
    trades = system_health_data.get("recent_trades") or []
    deposits = system_health_data.get("recent_deposits") or []

    st.subheader("🏦 Funding & Collateral")
    f1, f2, f3, f4 = st.columns(4)
    with f1:
        st.metric(f"💵 {token} Cash", f"${pusd:.2f}", help="Spendable collateral on the deposit wallet")
    with f2:
        st.metric("🪙 USDC.e (legacy)", f"${usdc_e:.2f}", help="Should be ~0 after wrap to pUSD")
    with f3:
        st.metric(
            "💰 Portfolio MTM",
            f"${system_health_data['total_portfolio_value']:.2f}",
            help="Cash + mark-to-market of open positions",
        )
    with f4:
        st.metric(
            "📊 Position Value",
            f"${system_health_data['position_value']:.2f}",
            help="Current market value of open positions",
        )

    render_equity_profit_chart(system_health_data)

    st.markdown("**充值地址（Deposit / proxyWallet）** — pUSD 必须打到这里才能交易：")
    if deposit_addr and deposit_addr != "?":
        st.code(deposit_addr, language="text")
        st.caption(
            f"[PolygonScan 充值地址](https://polygonscan.com/address/{deposit_addr}) · "
            f"[pUSD token](https://polygonscan.com/token/0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB?a={deposit_addr})"
        )
    if eoa_addr and eoa_addr != "?" and eoa_addr.lower() != str(deposit_addr).lower():
        st.caption(f"Signer EOA（签名私钥地址，勿往这里充值交易资金）: `{eoa_addr}`")

    st.markdown("**最近转入（链上 pUSD → deposit wallet）**")
    if deposits:
        dep_rows = []
        for d in deposits:
            txh = d.get("tx_hash") or ""
            dep_rows.append(
                {
                    "金额 (pUSD)": round(float(d.get("amount_dollars", 0) or 0), 6),
                    "From": d.get("from", ""),
                    "Block": d.get("block_number", ""),
                    "Tx Hash": txh,
                    "PolygonScan": f"https://polygonscan.com/tx/{txh}" if txh else "",
                }
            )
        st.dataframe(pd.DataFrame(dep_rows), width="stretch", hide_index=True)
    else:
        st.info("暂未扫到近期 pUSD 转入（RPC 日志窗口有限，或尚无新充值）。")

    st.markdown("**最近成交（CLOB fills + 交易哈希）**")
    if trades:
        trade_rows = []
        for t in trades:
            txh = t.get("tx_hash") or ""
            trade_rows.append(
                {
                    "时间": t.get("when", ""),
                    "Side": t.get("side", ""),
                    "Outcome": t.get("outcome", ""),
                    "Size": t.get("size", 0),
                    "Price": t.get("price", 0),
                    "Status": t.get("status", ""),
                    "Market": t.get("market", ""),
                    "Tx Hash": txh,
                    "PolygonScan": f"https://polygonscan.com/tx/{txh}" if txh else "",
                }
            )
        st.dataframe(pd.DataFrame(trade_rows), width="stretch", hide_index=True)
    else:
        st.info("暂无成交记录（或 CLOB trades 拉取失败）。")

    st.subheader("📌 参与项目摘要")
    if positions:
        summary_cols = ["项目", "方向", "投注金额", "目前价格", "当前浮盈", "止损止盈", "AI置信度"]
        summary_df = pd.DataFrame(build_participation_rows(positions, truncate_reason=0))
        # Compact overview: drop long rationale column
        keep = [c for c in summary_cols if c in summary_df.columns]
        st.dataframe(summary_df[keep], width="stretch", hide_index=True)
        missing_exits = sum(1 for p in positions if not p.get("stop_loss_price") and not p.get("take_profit_price"))
        if missing_exits:
            st.caption(f"⚠️ {missing_exits} 个持仓缺少 DB 止盈/止损（多为链上同步、未写入本地策略记录）。")
        st.caption("完整 AI 建议、理论最大利润与明细 → 侧边栏 **💼 Positions & Trades**")
        render_ai_watch_notes(positions, expanded=False)
    else:
        st.info("暂无持仓。")
        render_ai_watch_notes([], expanded=False)

    st.subheader("🧾 当前挂单（Open Orders）")
    if open_orders:
        st.dataframe(pd.DataFrame(open_orders), width="stretch", hide_index=True)
    else:
        st.info("当前没有挂单（或全部已成交/取消）。")

    st.markdown("---")
    
    # Key metrics row
    col1, col2, col3, col4 = st.columns(4)
    
    with col1:
        st.metric(
            label="💰 Portfolio Balance",
            value=f"${system_health_data['total_portfolio_value']:.2f}",
            help="Total portfolio value: cash + current positions"
        )
    
    # Add second row for additional financial metrics
    col1b, col2b, col3b, col4b = st.columns(4)
    
    with col1b:
        st.metric(
            label=f"💵 Available {token}",
            value=f"${system_health_data['available_cash']:.2f}",
            help="Cash available for new trades (deposit wallet)"
        )
    
    with col2b:
        st.metric(
            label="📊 Position Value",
            value=f"${system_health_data['position_value']:.2f}",
            help="Current market value of all positions"
        )
    
    with col2:
        total_trades = sum(stats.get('completed_trades', 0) for stats in performance_data.values()) if performance_data else 0
        st.metric(
            label="📈 Total Trades",
            value=total_trades if total_trades else len(trades),
            help="DB completed trades; falls back to recent CLOB fills count"
        )
    
    with col3:
        realized_pnl = sum(stats.get('total_pnl', 0) for stats in performance_data.values()) if performance_data else 0
        unrealized_pnl = 0.0
        if positions:
            for pos in positions:
                up = pos.get("unrealized_pnl")
                if isinstance(up, (int, float)):
                    unrealized_pnl += up
        total_pnl = realized_pnl + unrealized_pnl
        st.metric(
            label="💹 Total P&L",
            value=f"${total_pnl:.2f}",
            delta=f"Realized: ${realized_pnl:.2f}, Unrealized: ${unrealized_pnl:.2f}",
            help="Total profit/loss: realized from completed trades + unrealized from open positions"
        )
    
    with col4:
        st.metric(
            label="🎯 Active Positions",
            value=len(positions) if positions else 0,
            help="Currently open positions"
        )
    
    with col3b:
        if system_health_data['total_portfolio_value'] > 0:
            utilization_pct = (system_health_data['position_value'] / system_health_data['total_portfolio_value']) * 100
        else:
            utilization_pct = 0
        st.metric(
            label="📊 Portfolio Utilization",
            value=f"{utilization_pct:.1f}%",
            help="Percentage of portfolio currently in positions"
        )
    
    with col4b:
        st.metric(
            label="🧾 Open Orders",
            value=len(open_orders),
            help="Resting CLOB orders"
        )
    
    # Strategy performance summary
    if performance_data:
        st.subheader("🎯 Strategy Performance Summary")
        
        # Create strategy performance chart
        strategy_names = []
        strategy_pnl = []
        strategy_trades = []
        strategy_win_rates = []
        
        for strategy, stats in performance_data.items():
            strategy_names.append(strategy.replace('_', ' ').title())
            strategy_pnl.append(stats.get('total_pnl', 0))
            strategy_trades.append(stats.get('completed_trades', 0))
            strategy_win_rates.append(stats.get('win_rate_pct', 0))
        
        col1, col2 = st.columns(2)
        
        with col1:
            # P&L by strategy
            fig_pnl = px.bar(
                x=strategy_names,
                y=strategy_pnl,
                title="P&L by Strategy",
                labels={'x': 'Strategy', 'y': 'P&L ($)'},
                color=strategy_pnl,
                color_continuous_scale='RdYlGn'
            )
            fig_pnl.update_layout(showlegend=False, height=400)
            st.plotly_chart(fig_pnl, width="stretch")
        
        with col2:
            # Win rate by strategy
            fig_winrate = px.bar(
                x=strategy_names,
                y=strategy_win_rates,
                title="Win Rate by Strategy (%)",
                labels={'x': 'Strategy', 'y': 'Win Rate (%)'},
                color=strategy_win_rates,
                color_continuous_scale='Blues'
            )
            fig_winrate.update_layout(showlegend=False, height=400)
            st.plotly_chart(fig_winrate, width="stretch")
    else:
        st.info("📊 **No strategy data yet** - Run the trading system to start collecting performance data")
    
    # Recent activity summary
    st.subheader("📋 当前参与项目")
    
    if positions:
        st.write(f"**{len(positions)} 个活跃持仓**（摘要；详情见 Positions & Trades）")
        summary_cols = ["项目", "方向", "投注金额", "份额", "目前价格", "当前浮盈", "止损止盈"]
        summary_df = pd.DataFrame(build_participation_rows(positions[:15], truncate_reason=0))
        keep = [c for c in summary_cols if c in summary_df.columns]
        st.dataframe(summary_df[keep], width="stretch", hide_index=True)
    else:
        st.info("当前没有参与中的项目。")

    # Compact latest-logs panel on Overview
    st.subheader("📋 Latest Bot Logs")
    log_text, log_path, total_lines = read_latest_logs(lines=40, level_filter="ALL")
    st.caption(f"{log_path} · ~{total_lines} lines total · open **Live Logs** for more")
    safe = (
        log_text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )
    st.markdown(f'<div class="log-panel">{safe}</div>', unsafe_allow_html=True)


def show_live_logs():
    """Dedicated log viewer for the running trading bot."""
    st.header("📋 Live Logs")
    st.caption("Reads `logs/latest.log` from the bot process. Refresh to pull the newest lines.")

    c1, c2, c3, c4 = st.columns([1.2, 1.2, 1, 1])
    with c1:
        line_count = st.selectbox("Lines", [50, 100, 200, 400, 800], index=2)
    with c2:
        level = st.selectbox("Level", ["ALL", "ERROR", "WARNING", "INFO", "DEBUG"], index=0)
    with c3:
        auto_refresh = st.checkbox("Auto refresh (10s)", value=False)
    with c4:
        if st.button("🔄 Refresh logs", width="stretch"):
            st.rerun()

    log_text, log_path, total_lines = read_latest_logs(lines=int(line_count), level_filter=level)
    mtime = ""
    if log_path.exists():
        mtime = datetime.fromtimestamp(log_path.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")

    meta1, meta2, meta3 = st.columns(3)
    meta1.metric("Log file", log_path.name)
    meta2.metric("Total lines", total_lines)
    meta3.metric("Last modified", mtime or "—")

    # Escape HTML entities so log content cannot break the panel.
    safe = (
        log_text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )
    st.markdown(f'<div class="log-panel">{safe}</div>', unsafe_allow_html=True)

    if auto_refresh:
        import time

        time.sleep(10)
        st.rerun()


def show_strategy_performance(performance_data):
    """Show detailed strategy performance analysis."""
    
    st.header("🎯 Strategy Performance Analysis")
    
    if not performance_data:
        st.warning("No strategy performance data available yet.")
        return
    
    # Strategy selector
    strategies = list(performance_data.keys())
    selected_strategy = st.selectbox(
        "Select Strategy for Detailed Analysis",
        ["All Strategies"] + strategies
    )
    
    if selected_strategy == "All Strategies":
        # Compare all strategies
        st.subheader("📊 Strategy Comparison")
        
        # Create comparison table
        comparison_data = []
        for strategy, stats in performance_data.items():
            comparison_data.append({
                'Strategy': strategy.replace('_', ' ').title(),
                'Completed Trades': stats['completed_trades'],
                'Total P&L': f"${stats['total_pnl']:.2f}",
                'Avg P&L per Trade': f"${stats['avg_pnl_per_trade']:.2f}",
                'Win Rate': f"{stats['win_rate_pct']:.1f}%",
                'Best Trade': f"${stats['best_trade']:.2f}",
                'Worst Trade': f"${stats['worst_trade']:.2f}",
                'Open Positions': stats['open_positions'],
                'Capital Deployed': f"${stats['capital_deployed']:.2f}"
            })
        
        df = pd.DataFrame(comparison_data)
        st.dataframe(df, width="stretch", hide_index=True)
        
        # Performance charts
        col1, col2 = st.columns(2)
        
        with col1:
            # Risk-return scatter
            fig_risk = go.Figure()
            
            for strategy, stats in performance_data.items():
                if stats['completed_trades'] > 0:
                    fig_risk.add_trace(go.Scatter(
                        x=[stats['avg_pnl_per_trade']],
                        y=[stats['win_rate_pct']],
                        mode='markers+text',
                        text=[strategy.replace('_', ' ').title()],
                        textposition="top center",
                        marker=dict(
                            size=stats['completed_trades'] * 2,
                            color=stats['total_pnl'],
                            colorscale='RdYlGn',
                            showscale=True
                        ),
                        name=strategy
                    ))
            
            fig_risk.update_layout(
                title="Risk-Return Analysis (Bubble size = Trade count)",
                xaxis_title="Average P&L per Trade ($)",
                yaxis_title="Win Rate (%)",
                height=500
            )
            st.plotly_chart(fig_risk, width="stretch")
        
        with col2:
            # Capital deployment
            fig_capital = px.pie(
                values=[stats['capital_deployed'] for stats in performance_data.values()],
                names=[strategy.replace('_', ' ').title() for strategy in performance_data.keys()],
                title="Capital Deployment by Strategy"
            )
            fig_capital.update_layout(height=500)
            st.plotly_chart(fig_capital, width="stretch")
    
    else:
        # Show individual strategy details
        stats = performance_data[selected_strategy]
        
        st.subheader(f"📋 {selected_strategy.replace('_', ' ').title()} Performance")
        
        # Key metrics
        col1, col2, col3, col4 = st.columns(4)
        
        with col1:
            st.metric("Total P&L", f"${stats['total_pnl']:.2f}")
        with col2:
            st.metric("Win Rate", f"{stats['win_rate_pct']:.1f}%")
        with col3:
            st.metric("Completed Trades", stats['completed_trades'])
        with col4:
            st.metric("Open Positions", stats['open_positions'])
        
        # Detailed metrics
        if stats['completed_trades'] > 0:
            st.subheader("📈 Detailed Metrics")
            
            col1, col2 = st.columns(2)
            
            with col1:
                st.write("**Trade Performance:**")
                st.write(f"- Average P&L per Trade: ${stats['avg_pnl_per_trade']:.2f}")
                st.write(f"- Best Trade: ${stats['best_trade']:.2f}")
                st.write(f"- Worst Trade: ${stats['worst_trade']:.2f}")
                st.write(f"- Winning Trades: {stats['winning_trades']}")
                st.write(f"- Losing Trades: {stats['losing_trades']}")
            
            with col2:
                st.write("**Capital Allocation:**")
                st.write(f"- Capital Deployed: ${stats['capital_deployed']:.2f}")
                st.write(f"- Open Positions: {stats['open_positions']}")
                if stats['capital_deployed'] > 0:
                    avg_position_size = stats['capital_deployed'] / max(stats['open_positions'], 1)
                    st.write(f"- Avg Position Size: ${avg_position_size:.2f}")

def show_llm_analysis(llm_queries, llm_stats):
    """Show LLM query analysis and review."""
    
    st.header("🤖 LLM Analysis & Review")
    st.markdown("**Review all AI queries and responses for insights and improvements**")
    
    if not llm_queries and not llm_stats:
        st.warning("No LLM query data available yet. LLM logging will start with new queries.")
        st.info("💡 **Tip:** The system will automatically log all future Grok queries for analysis.")
        return
    
    # LLM usage stats
    if llm_stats:
        st.subheader("📊 LLM Usage Statistics (Last 7 Days)")
        
        # Create stats summary
        total_queries = sum(stats['query_count'] for stats in llm_stats.values())
        total_cost = sum(stats['total_cost'] for stats in llm_stats.values())
        total_tokens = sum(stats['total_tokens'] for stats in llm_stats.values())
        has_estimated_tokens = any(stats.get('estimated', False) for stats in llm_stats.values())
        
        col1, col2, col3, col4 = st.columns(4)
        
        with col1:
            st.metric("Total Queries", total_queries)
        with col2:
            st.metric("Total Cost", f"${total_cost:.2f}")
        with col3:
            token_label = "Total Tokens*" if has_estimated_tokens else "Total Tokens"
            token_help = "Estimated from response lengths (some token data missing)" if has_estimated_tokens else "Actual token usage"
            st.metric(
                token_label, 
                f"{total_tokens:,}",
                help=token_help
            )
        with col4:
            avg_cost_per_query = total_cost / max(total_queries, 1)
            st.metric("Avg Cost/Query", f"${avg_cost_per_query:.3f}")
        
        if has_estimated_tokens:
            st.caption("*Token counts marked with * are estimated from response text length due to missing usage data")
        
        # Usage by strategy
        if len(llm_stats) > 1:
            fig_usage = px.bar(
                x=list(llm_stats.keys()),
                y=[stats['query_count'] for stats in llm_stats.values()],
                title="LLM Queries by Strategy",
                labels={'x': 'Strategy', 'y': 'Query Count'},
                color=[stats['total_cost'] for stats in llm_stats.values()],
                color_continuous_scale='Blues'
            )
            st.plotly_chart(fig_usage, width="stretch")
    
    # Query filters
    st.subheader("🔍 Query Analysis")
    
    col1, col2, col3 = st.columns(3)
    
    with col1:
        strategies = list(set(query.strategy for query in llm_queries)) if llm_queries else []
        selected_strategy = st.selectbox(
            "Filter by Strategy",
            ["All"] + strategies
        )
    
    with col2:
        query_types = list(set(query.query_type for query in llm_queries)) if llm_queries else []
        selected_type = st.selectbox(
            "Filter by Query Type",
            ["All"] + query_types
        )
    
    with col3:
        hours_back = st.selectbox(
            "Time Range",
            [6, 12, 24, 48, 168],  # Last 6h, 12h, 24h, 48h, 7 days
            index=2,  # Default to 24h
            format_func=lambda x: f"Last {x} hours" if x < 168 else "Last 7 days"
        )
    
    # Filter queries
    filtered_queries = llm_queries
    
    if llm_queries:
        cutoff_time = datetime.now() - timedelta(hours=hours_back)
        filtered_queries = [
            q for q in llm_queries 
            if q.timestamp >= cutoff_time
        ]
        
        if selected_strategy != "All":
            filtered_queries = [q for q in filtered_queries if q.strategy == selected_strategy]
        
        if selected_type != "All":
            filtered_queries = [q for q in filtered_queries if q.query_type == selected_type]
        
        st.write(f"**Showing {len(filtered_queries)} queries**")
        
        # Display queries
        for i, query in enumerate(filtered_queries[:20]):  # Show latest 20
            with st.expander(
                f"🤖 {query.strategy} | {query.query_type} | {query.timestamp.strftime('%H:%M:%S')}",
                expanded=(i < 3)  # Expand first 3
            ):
                
                # Query metadata
                col1, col2, col3 = st.columns(3)
                with col1:
                    st.write(f"**Strategy:** {query.strategy}")
                with col2:
                    st.write(f"**Type:** {query.query_type}")
                with col3:
                    if query.market_id:
                        st.write(f"**Market:** {query.market_id[:20]}...")
                
                if query.cost_usd:
                    st.write(f"**Cost:** ${query.cost_usd:.4f}")
                
                # Prompt and response
                st.markdown("**🔤 Prompt:**")
                st.code(query.prompt, language="text")
                
                st.markdown("**🤖 Response:**")
                st.code(query.response, language="text")
                
                # Extracted data
                if query.confidence_extracted:
                    st.write(f"**Confidence Extracted:** {query.confidence_extracted:.2%}")
                
                if query.decision_extracted:
                    st.write(f"**Decision Extracted:** {query.decision_extracted}")
    
    else:
        st.info("No LLM queries found for the selected filters.")

def show_positions_trades(positions, open_orders=None):
    """Show detailed positions, exit levels, and resting orders."""
    
    st.header("💼 Positions & Trades")
    open_orders = open_orders or []

    st.subheader(f"🧾 Open Orders ({len(open_orders)})")
    if open_orders:
        st.dataframe(pd.DataFrame(open_orders), width="stretch", hide_index=True)
    else:
        st.info("当前没有挂单。")
    
    if not positions:
        st.warning("No active positions found.")
        return
    
    st.subheader(f"📊 参与项目（{len(positions)}）")
    st.caption(
        "理论最大利润 = 方向正确且每份兑付 $1 时的利润：(1 − 入场价) × 份额。"
        "当前浮盈 = (目前价 − 入场价) × 份额（该 outcome token 市值变动）。"
    )

    # Filters
    strategies = sorted({(p.get("strategy") or "Unknown") for p in positions})
    sides = sorted({(p.get("side") or "?") for p in positions})
    col1, col2 = st.columns(2)
    with col1:
        selected_strategies = st.multiselect("按策略筛选", strategies, default=strategies)
    with col2:
        selected_sides = st.multiselect("按方向筛选", sides, default=sides)

    filtered = [
        p for p in positions
        if (p.get("strategy") or "Unknown") in selected_strategies
        and (p.get("side") or "?") in selected_sides
    ]

    st.dataframe(
        pd.DataFrame(build_participation_rows(filtered, truncate_reason=120)),
        width="stretch",
        hide_index=True,
    )

    ai_notes = render_ai_watch_notes(filtered, expanded=False)

    st.subheader("🧠 AI 建议与止损止盈明细")
    for pos in filtered:
        title = pos.get("title") or pos.get("market_id") or "—"
        conf = pos.get("confidence")
        conf_txt = f"{conf * 100:.0f}%" if isinstance(conf, (int, float)) else "—"
        cur = pos.get("current_price")
        sl = pos.get("stop_loss_price")
        tp = pos.get("take_profit_price")
        upnl = pos.get("unrealized_pnl")
        note = (ai_notes or {}).get(pos.get("market_id") or "") or {}
        with st.expander(
            f"{title} · {pos.get('side')} · 投注 ${float(pos.get('trade_amount') or 0):.2f}",
            expanded=False,
        ):
            m1, m2, m3, m4 = st.columns(4)
            m1.metric("份额", pos.get("quantity"))
            m2.metric("投注/交易金额", f"${float(pos.get('trade_amount') or 0):.2f}")
            m3.metric("目前价格", f"${float(cur):.3f}" if isinstance(cur, (int, float)) else "—")
            m4.metric("理论最大利润", f"${float(pos.get('max_profit') or 0):.2f}")
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("入场价", f"${float(pos.get('entry_price') or 0):.3f}")
            c2.metric("当前浮盈", f"${float(upnl):.3f}" if isinstance(upnl, (int, float)) else "—")
            c3.metric("止损", f"${float(sl):.3f}" if sl is not None else "未设置")
            c4.metric("止盈", f"${float(tp):.3f}" if tp is not None else "未设置")
            st.markdown(f"**AI 置信度:** {conf_txt}")
            if note.get("why") or note.get("risk"):
                st.success(f"**为什么值得看：** {note.get('why') or '—'}")
                st.warning(f"**风险一句话：** {note.get('risk') or '—'}")
            st.markdown("**参与理由 / AI 投注建议（开仓时记录）**")
            st.info(pos.get("rationale") or pos.get("ai_suggestion") or "无记录")
            st.caption(
                f"策略: {pos.get('strategy') or '—'} · "
                f"距止损: {pos.get('dist_to_sl') if pos.get('dist_to_sl') is not None else '—'} · "
                f"距止盈: {pos.get('dist_to_tp') if pos.get('dist_to_tp') is not None else '—'} · "
                f"来源: {pos.get('source') or '—'}"
            )

    if filtered:
        st.subheader("📈 Position Analytics")
        value_by_strategy = {}
        for p in filtered:
            strat = p.get("strategy") or "Unknown"
            value_by_strategy[strat] = value_by_strategy.get(strat, 0.0) + float(p.get("trade_amount") or 0)
        col1, col2 = st.columns(2)
        with col1:
            fig_strategy = px.pie(
                values=list(value_by_strategy.values()),
                names=list(value_by_strategy.keys()),
                title="投注金额 by Strategy",
            )
            st.plotly_chart(fig_strategy, width="stretch")
        with col2:
            side_counts = {}
            for p in filtered:
                side = p.get("side") or "?"
                side_counts[side] = side_counts.get(side, 0) + 1
            fig_sides = px.bar(
                x=list(side_counts.keys()),
                y=list(side_counts.values()),
                title="Positions by Side",
                labels={"x": "Side", "y": "Count"},
            )
            st.plotly_chart(fig_sides, width="stretch")

        with_exits = sum(1 for p in positions if p.get("stop_loss_price") or p.get("take_profit_price"))
        st.caption(f"止盈/止损覆盖：{with_exits}/{len(positions)} 个持仓有本地 exit 计划（策略写入 DB）。")

def show_risk_management(performance_data, positions, system_balance):
    """Show risk management dashboard."""
    
    st.header("⚠️ Risk Management")
    
    # Handle empty positions gracefully
    if not positions:
        st.info("No active positions to analyze for risk management.")
        col1, col2, col3, col4 = st.columns(4)
        
        with col1:
            st.metric("Portfolio Utilization", "0.0%")
        with col2:
            st.metric("Total Deployed", "$0.00")
        with col3:
            st.metric("Avg Position Size", "$0.00")
        with col4:
            st.metric("Max Single Position", "0.0%")
        
        st.subheader("🚨 Risk Alerts")
        st.success("✅ All risk metrics within acceptable ranges")
        return
    
    # Calculate risk metrics from live positions
    try:
        total_deployed = sum(pos['quantity'] * pos['entry_price'] for pos in positions if 'quantity' in pos and 'entry_price' in pos)
        portfolio_utilization = (total_deployed / system_balance * 100) if system_balance > 0 else 0
        
        col1, col2, col3, col4 = st.columns(4)
        
        with col1:
            st.metric(
                "Portfolio Utilization",
                f"{portfolio_utilization:.1f}%",
                help="Percentage of balance deployed in positions"
            )
        
        with col2:
            st.metric(
                "Total Deployed",
                f"${total_deployed:.2f}",
                help="Total capital in active positions"
            )
        
        with col3:
            avg_position_size = total_deployed / len(positions) if positions else 0
            st.metric(
                "Avg Position Size",
                f"${avg_position_size:.2f}",
                help="Average size per position"
            )
        
        with col4:
            # Calculate max single position risk
            position_values = [pos['quantity'] * pos['entry_price'] for pos in positions if 'quantity' in pos and 'entry_price' in pos]
            max_position = max(position_values) if position_values else 0
            max_risk_pct = (max_position / system_balance * 100) if system_balance > 0 else 0
            st.metric(
                "Max Single Position",
                f"{max_risk_pct:.1f}%",
                help="Largest position as % of portfolio"
            )
        
        # Risk alerts
        st.subheader("🚨 Risk Alerts")
        
        alerts = []
        
        if portfolio_utilization > 90:
            alerts.append("⚠️ **High Portfolio Utilization**: Over 90% of capital deployed")
        
        if max_risk_pct > 20:
            alerts.append("⚠️ **Large Position Risk**: Single position exceeds 20% of portfolio")
        
        if len(positions) > 50:
            alerts.append("⚠️ **High Position Count**: Over 50 active positions may be difficult to manage")
        
        # Check for positions without stop losses (if supported)
        no_stop_loss = []
        for pos in positions:
            if 'stop_loss_price' in pos and not pos['stop_loss_price']:
                no_stop_loss.append(pos)
        
        if no_stop_loss:
            alerts.append(f"⚠️ **No Stop Losses**: {len(no_stop_loss)} positions lack stop loss protection")
        
        if alerts:
            for alert in alerts:
                st.warning(alert)
        else:
            st.success("✅ All risk metrics within acceptable ranges")
        
        # Risk by strategy breakdown
        strategy_names = [pos['strategy'] for pos in positions if 'strategy' in pos]
        if len(set(strategy_names)) > 1:
            st.subheader("📊 Risk by Strategy")
            
            strategy_risk = {}
            for pos in positions:
                if 'strategy' in pos and 'quantity' in pos and 'entry_price' in pos:
                    strategy = pos['strategy'] or 'Unknown'
                    if strategy not in strategy_risk:
                        strategy_risk[strategy] = {'exposure': 0, 'positions': 0}
                    strategy_risk[strategy]['exposure'] += pos['quantity'] * pos['entry_price']
                    strategy_risk[strategy]['positions'] += 1
            
            if strategy_risk:
                strategy_df = pd.DataFrame([
                    {
                        'Strategy': strategy,
                        'Exposure': f"${data['exposure']:.2f}",
                        'Positions': data['positions'],
                        'Avg Size': f"${data['exposure'] / data['positions']:.2f}",
                        'Portfolio %': f"{(data['exposure'] / system_balance * 100):.1f}%" if system_balance > 0 else "0.0%"
                    }
                    for strategy, data in strategy_risk.items()
                ])
                st.dataframe(strategy_df, width="stretch", hide_index=True)
        
    except Exception as e:
        st.error(f"Error calculating risk metrics: {e}")
        st.info("Using basic risk metrics")
        
        col1, col2, col3, col4 = st.columns(4)
        with col1:
            st.metric("Portfolio Utilization", "Error")
        with col2:
            st.metric("Total Deployed", "Error")
        with col3:
            st.metric("Avg Position Size", "Error")
        with col4:
            st.metric("Max Single Position", "Error")

def show_system_health(available_cash, positions_count, llm_stats):
    """Show system health and monitoring."""
    
    st.header("🔧 System Health")
    
    # System status
    st.subheader("🟢 System Status")
    
    col1, col2, col3 = st.columns(3)
    
    with col1:
        st.success("✅ **Polymarket Connection**: Active")
        st.write(f"Available USDC.e: ${available_cash:.2f}")
        st.write(f"Positions: {positions_count}")
    
    with col2:
        if llm_stats:
            st.success("✅ **LLM Integration**: Active")
            total_queries = sum(stats['query_count'] for stats in llm_stats.values())
            st.write(f"Queries (7d): {total_queries}")
        else:
            st.warning("⚠️ **LLM Logging**: No data")
    
    with col3:
        st.success("✅ **Database**: Connected")
        st.write("All tables operational")
    
    # Recent activity timeline
    st.subheader("📅 System Activity")
    
    if llm_stats:
        st.write("**Recent LLM Activity:**")
        for strategy, stats in llm_stats.items():
            if stats['last_query']:
                last_query_time = datetime.fromisoformat(stats['last_query'])
                time_ago = datetime.now() - last_query_time
                
                if time_ago.days > 0:
                    time_str = f"{time_ago.days} days ago"
                elif time_ago.seconds > 3600:
                    time_str = f"{time_ago.seconds // 3600} hours ago"
                else:
                    time_str = f"{time_ago.seconds // 60} minutes ago"
                
                st.write(f"- **{strategy}**: Last query {time_str}")
    
    # Configuration summary
    st.subheader("⚙️ Configuration")
    
    config_info = {
        "Database Path": "trading_system.db",
        "Dashboard Refresh": "Auto (1 min cache)",
        "LLM Logging": "Enabled" if llm_stats else "Pending first query",
        "Strategy Tracking": "Enabled",
        "Risk Management": "Active"
    }
    
    for key, value in config_info.items():
        st.write(f"**{key}:** {value}")
    
    # System recommendations
    st.subheader("💡 Recommendations")
    
    recommendations = []
    
    if available_cash < 100:
        recommendations.append("💰 Consider increasing account balance for more trading opportunities")
    
    if not llm_stats:
        recommendations.append("🤖 LLM query logging will begin with next trading cycle")
    
    total_queries = sum(stats['query_count'] for stats in llm_stats.values()) if llm_stats else 0
    if total_queries > 1000:
        recommendations.append("📊 High LLM usage - consider optimizing query frequency")
    
    if recommendations:
        for rec in recommendations:
            st.info(rec)
    else:
        st.success("✅ System running optimally - no recommendations at this time")

if __name__ == "__main__":
    main() 