import json

import pytest

from quant_trading_platform.signal_watch.gina import adapt_gina_candles

NOW = 3_000_010


def payload():
    rows = []
    for index in range(30):
        opening = 1_200_000 + index * 60_000
        rows.append(
            {
                "coin": "BTC",
                "interval": "1m",
                "openTimestamp": str(opening),
                "closeTimestamp": str(opening + 59_999),
                "observedAt": str(NOW - 1),
                "isClosed": True,
                "open": str(100 + index),
                "high": str(102 + index),
                "low": str(99 + index),
                "close": str(101 + index),
                "volume": "1.23456789",
                "venueProviderId": None,
                "venueKind": None,
            }
        )
    return {"results": list(reversed(rows)), "rowCount": 30}


def test_completed_gina_tape_produces_measured_independent_structure_only():
    result = adapt_gina_candles("BTC/USDT", json.dumps(payload()), now_ms=NOW)
    assert result == {
        "symbol": "BTC/USDT",
        "observations": [
            {
                "domain": "A",
                "strength": "1",
                "timestamp_ms": 3_000_000,
                "origin": "hyperliquid_canonical_usdc_candles",
            }
        ],
    }


@pytest.mark.parametrize(
    "fault",
    [
        "coin",
        "interval",
        "forming",
        "gap",
        "future",
        "stale",
        "observed_future",
        "range",
        "negative_volume",
        "nan",
        "hip3",
        "float_clock",
        "row_count",
        "too_few",
    ],
)
def test_unverified_gina_tapes_fail_closed(fault):
    data = payload()
    row = data["results"][0]
    if fault == "coin":
        row["coin"] = "ETH"
    elif fault == "interval":
        row["interval"] = "5m"
    elif fault == "forming":
        row["isClosed"] = False
    elif fault == "gap":
        data["results"].pop(5)
        data["rowCount"] -= 1
    elif fault == "future":
        row["closeTimestamp"] = str(NOW + 1)
    elif fault == "stale":
        NOW_USED = NOW + 60_001
    elif fault == "observed_future":
        row["observedAt"] = str(NOW + 1)
    elif fault == "range":
        row["low"] = "1000"
    elif fault == "negative_volume":
        row["volume"] = "-1"
    elif fault == "nan":
        row["close"] = "NaN"
    elif fault == "hip3":
        row["venueProviderId"] = "hip3:xyz"
    elif fault == "float_clock":
        row["openTimestamp"] = 2940000.0
    elif fault == "row_count":
        data["rowCount"] = 29
    elif fault == "too_few":
        data["results"] = data["results"][:20]
        data["rowCount"] = 20
    with pytest.raises(ValueError):
        adapt_gina_candles(
            "BTC/USDT", json.dumps(data), now_ms=NOW_USED if fault == "stale" else NOW
        )


def test_chart_summary_and_ai_direction_cannot_supply_candle_confirmation():
    with pytest.raises(ValueError):
        adapt_gina_candles(
            "BTC/USDT",
            json.dumps(
                {"success": True, "chartRendered": True, "trend": "bullish", "confidenceScore": "1"}
            ),
            now_ms=NOW,
        )


def test_valid_bearish_tape_is_zero_bullish_confirmation():
    data = payload()
    for i, row in enumerate(reversed(data["results"])):
        row.update(open=str(200 - i), high=str(201 - i), low=str(198 - i), close=str(199 - i))
    result = adapt_gina_candles("BTC/USDT", json.dumps(data), now_ms=NOW)
    assert result["observations"][0]["strength"] == "0"
