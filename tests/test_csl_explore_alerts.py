"""Tests for CSL explore Discord strategy labels."""

from __future__ import annotations

from unittest.mock import patch

from src.strategies.csl_explore.alerts import (
    notify_csl_order,
    strategy_label,
)


def test_strategy_labels_distinct():
    assert "narrative" in strategy_label("narrative")
    assert "叙事" in strategy_label("narrative")
    assert "fingerprint" in strategy_label("fingerprint")
    assert "anti_whale" in strategy_label("anti_whale")


@patch("src.strategies.csl_explore.alerts.send_discord_message", return_value=True)
@patch("src.strategies.csl_explore.alerts._enabled", return_value=True)
@patch("src.strategies.csl_explore.alerts._should_send", return_value=True)
@patch("src.strategies.csl_explore.alerts._mark_sent")
def test_notify_csl_order_mentions_strategy(mock_mark, mock_should, mock_en, mock_send):
    ok = notify_csl_order(
        strategy="narrative",
        match_title="Home vs Away",
        question="Will Home win?",
        reason="narrative favorite",
        shares=5,
        price=0.60,
        cost_usdc=3.0,
        live=True,
        condition_id="0xabc",
        match_slug="chi-test",
        meta={"leg": "favorite"},
    )
    assert ok is True
    body = mock_send.call_args[0][0]
    assert "CSL Explore" in body
    assert "narrative" in body
    assert "叙事对冲" in body
    assert "favorite" in body
    # Must not look like RN1 copy
    assert "RN1" not in body
    assert "跟单" not in body


@patch("src.strategies.csl_explore.alerts.send_discord_message", return_value=True)
@patch("src.strategies.csl_explore.alerts._enabled", return_value=True)
@patch("src.strategies.csl_explore.alerts._should_send", return_value=True)
@patch("src.strategies.csl_explore.alerts._mark_sent")
def test_notify_csl_exit_stop_loss(mock_mark, mock_should, mock_en, mock_send):
    from src.strategies.csl_explore.alerts import notify_csl_exit

    ok = notify_csl_exit(
        strategy="narrative",
        match_title="Home vs Away",
        question="Will draw?",
        reason="stop_loss mark=0.40 ≤ entry=0.50×0.80",
        shares=5,
        entry_price=0.50,
        exit_price=0.40,
        live=True,
        condition_id="0xabc",
    )
    assert ok is True
    body = mock_send.call_args[0][0]
    assert "止损" in body
    assert "narrative" in body
    assert "CSL Explore" in body
