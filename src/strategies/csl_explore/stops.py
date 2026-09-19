"""Stop-loss helpers for CSL explore (no auto take-profit)."""

from __future__ import annotations


def stop_loss_triggered(
    entry_price: float,
    mark_price: float,
    *,
    stop_loss_pct: float = 0.20,
) -> bool:
    """True when mark ≤ entry × (1 - stop_loss_pct)."""
    if entry_price <= 0 or mark_price <= 0 or stop_loss_pct <= 0:
        return False
    return mark_price <= entry_price * (1.0 - stop_loss_pct) + 1e-9


def stop_loss_reason(entry_price: float, mark_price: float, stop_loss_pct: float) -> str:
    thr = entry_price * (1.0 - stop_loss_pct)
    return (
        f"stop_loss mark={mark_price:.3f} ≤ entry={entry_price:.3f}"
        f"×{1.0 - stop_loss_pct:.2f} ({thr:.3f})"
    )
