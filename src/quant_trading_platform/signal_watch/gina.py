"""Completed canonical Hyperliquid candle corroboration from Gina SQL row JSON.

The producer must query a table created with canonical coin/1m/closedOnly arguments.
This adapter does not create remote tables or turn chart/AI summaries into evidence.
"""

import json
import re
from decimal import Decimal

from quant_trading_platform.market_data.derivatives import SIGNAL_SYMBOLS, exact_decimal
from quant_trading_platform.models import normalize_symbol


def _clock(value: object) -> int:
    if type(value) is int and value > 0:
        return value
    if isinstance(value, str) and re.fullmatch(r"[1-9][0-9]{0,15}", value):
        return int(value)
    raise ValueError("Unverified candle clock")


def adapt_gina_candles(symbol: str, raw_json: str, *, now_ms: int) -> dict[str, object]:
    symbol = normalize_symbol(symbol)
    if symbol not in SIGNAL_SYMBOLS or type(now_ms) is not int or now_ms <= 0:
        raise ValueError("Unsupported source request")
    if len(raw_json) > 1_048_576:
        raise ValueError("Oversize source report")
    payload = json.loads(raw_json, parse_float=Decimal)
    if not isinstance(payload, dict):
        raise ValueError("Missing row evidence")
    rows = payload.get("results")
    if (
        not isinstance(rows, list)
        or not 25 <= len(rows) <= 50
        or type(payload.get("rowCount")) is not int
        or payload["rowCount"] != len(rows)
    ):
        raise ValueError("Missing bounded completed candle tape")
    candles: list[tuple[int, Decimal]] = []
    for row in rows:
        if (
            not isinstance(row, dict)
            or row.get("coin") != symbol.split("/")[0]
            or row.get("interval") != "1m"
            or row.get("isClosed") is not True
            or row.get("venueProviderId") not in (None, "hyperliquid")
            or row.get("venueKind") not in (None, "canonical")
        ):
            raise ValueError("Wrong canonical candle identity")
        opening = _clock(row.get("openTimestamp"))
        closing = _clock(row.get("closeTimestamp"))
        observed = _clock(row.get("observedAt"))
        if opening % 60_000 or closing != opening + 59_999 or not closing < observed <= now_ms:
            raise ValueError("Unverified completed source timestamp")
        values = [
            exact_decimal(row.get(key), positive=key != "volume")
            for key in ("open", "high", "low", "close", "volume")
        ]
        if any(value is None for value in values):
            raise ValueError("Unverified candle measurements")
        o, high, low, close, volume = [value for value in values if value is not None]
        if low > min(o, close) or high < max(o, close) or volume < 0:
            raise ValueError("Invalid candle range/volume")
        candles.append((opening, close))
    candles.sort()
    if any(
        current[0] != previous[0] + 60_000
        for previous, current in zip(candles, candles[1:], strict=False)
    ):
        raise ValueError("Incomplete candle sequence")
    timestamp = candles[-1][0] + 60_000
    if not 0 <= now_ms - timestamp <= 60_000:
        raise ValueError("Stale candle tape")
    average = sum((c[1] for c in candles[-20:]), Decimal(0)) / 20
    previous_average = sum((c[1] for c in candles[-21:-1]), Decimal(0)) / 20
    strength = Decimal(1) if candles[-1][1] > average > previous_average else Decimal(0)
    return {
        "symbol": symbol,
        "observations": [
            {
                "domain": "A",
                "strength": str(strength),
                "timestamp_ms": timestamp,
                "origin": "hyperliquid_canonical_usdc_candles",
            }
        ],
    }
