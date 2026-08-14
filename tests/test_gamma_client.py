"""Unit tests for GammaClient market lookup and helpers."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock

from src.clients.gamma_client import (
    GammaAPIError,
    GammaClient,
    _as_market_list,
    _clob_market_to_gamma,
    _derive_market_fields,
)


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def test_as_market_list_accepts_list_and_single_dict():
    assert _as_market_list([{"conditionId": "0xa"}])[0]["conditionId"] == "0xa"
    assert _as_market_list({"conditionId": "0xb"})[0]["conditionId"] == "0xb"
    assert _as_market_list({"data": [{"conditionId": "0xc"}]})[0]["conditionId"] == "0xc"
    assert _as_market_list("nope") == []


def test_clob_market_to_gamma_extracts_yes_no_tokens():
    clob = {
        "condition_id": "0xabc",
        "question": "Will it rain?",
        "tokens": [
            {"token_id": "111", "outcome": "Yes"},
            {"token_id": "222", "outcome": "No"},
        ],
        "neg_risk": True,
        "minimum_tick_size": "0.001",
        "active": True,
        "closed": False,
    }
    m = _clob_market_to_gamma(clob, "0xabc")
    derived = _derive_market_fields(m)
    assert derived["_token_ids"] == ("111", "222")
    assert derived["_condition_id"] == "0xabc"
    assert m["negRisk"] is True
    assert m["orderPriceMinTickSize"] == 0.001


def test_get_market_queries_condition_ids_not_a_500_scan():
    """Regression: scanning the first 500 /markets rows missed most ids."""
    gamma = GammaClient()
    payload = [{
        "conditionId": "0xdeadbeef",
        "question": "Test?",
        "clobTokenIds": json.dumps(["yes1", "no1"]),
        "outcomePrices": json.dumps(["0.4", "0.6"]),
        "active": True,
        "closed": False,
    }]
    gamma._request = AsyncMock(return_value=payload)
    try:
        market = _run(gamma.get_market("0xdeadbeef"))
        gamma._request.assert_called()
        args, kwargs = gamma._request.call_args
        assert args[0] == "GET"
        assert args[1].endswith("/markets")
        assert kwargs["params"] == {"condition_ids": "0xdeadbeef"}
        assert market["_token_ids"] == ("yes1", "no1")
        assert market["_condition_id"] == "0xdeadbeef"
    finally:
        _run(gamma.close())


def test_get_market_falls_back_to_clob_lookup():
    gamma = GammaClient()

    async def fake_request(method, url, params=None):
        if url.endswith("/markets") and params:
            return []
        if "/markets/0xclob" in url:
            return {
                "condition_id": "0xclob",
                "question": "From CLOB",
                "tokens": [
                    {"token_id": "y", "outcome": "Yes"},
                    {"token_id": "n", "outcome": "No"},
                ],
                "neg_risk": False,
                "minimum_tick_size": "0.01",
            }
        raise GammaAPIError("unexpected url")

    gamma._request = fake_request
    try:
        market = _run(gamma.get_market("0xclob"))
        assert market["_token_ids"] == ("y", "n")
        assert market["question"] == "From CLOB"
    finally:
        _run(gamma.close())
