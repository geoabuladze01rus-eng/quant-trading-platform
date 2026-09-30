"""Fetch completed OKX spot history and print a reproducible research report.

Run only in an environment with direct access to OKX's public market API.
Never uses credentials, private endpoints or real trading.
"""

import argparse
import json
from dataclasses import asdict
from decimal import Decimal
from time import time

from quant_trading_platform.backtesting.spot import replay_trend
from quant_trading_platform.backtesting.spot_rotation import replay_rotation
from quant_trading_platform.market_data.okx_history import OKXHistorySource
from quant_trading_platform.strategies.spot_momentum import SYMBOLS


def main() -> None:
    parser = argparse.ArgumentParser(description="Read-only OKX spot research")
    parser.add_argument("--days", type=int, default=1460)
    parser.add_argument("--fee-pct", type=Decimal, default=Decimal("0.35"))
    parser.add_argument("--spread-pct", type=Decimal, default=Decimal("0.10"))
    parser.add_argument("--slippage-pct", type=Decimal, default=Decimal("0.10"))
    args = parser.parse_args()
    now_ms = int(time() * 1000)
    source = OKXHistorySource()
    try:
        series = {symbol: source.fetch(symbol, days=args.days, now_ms=now_ms)
                  for symbol in SYMBOLS}
    finally:
        source.close()
    test_start = max(100, int(args.days * Decimal("0.7")))
    costs = {"fee_pct": args.fee_pct, "spread_pct": args.spread_pct,
             "slippage_pct": args.slippage_pct}
    report = {
        "source": "OKX public confirmed 1Dutc candles",
        "dates": [series[SYMBOLS[0]][0].timestamp_ms, series[SYMBOLS[0]][-1].timestamp_ms],
        "days": args.days,
        "costs_per_side_pct": costs,
        "warning": "Research simulation, not real fills, validated profit or live admission.",
        "rotation_all": asdict(replay_rotation(series, **costs)),
        "rotation_holdout": asdict(replay_rotation(series, first_signal_index=test_start, **costs)),
        "btc_hold_all": asdict(replay_rotation(series, strategy="btc_hold", **costs)),
        "btc_hold_holdout": asdict(replay_rotation(
            series, first_signal_index=test_start, strategy="btc_hold", **costs
        )),
        "trend_each_pair": {symbol: asdict(replay_trend(
            symbol, candles, fee_pct=args.fee_pct,
            slippage_pct=args.slippage_pct, spread_pct=args.spread_pct,
        )) for symbol, candles in series.items()},
        "trend_holdout_each_pair": {symbol: asdict(replay_trend(
            symbol, candles, first_signal_index=test_start, fee_pct=args.fee_pct,
            slippage_pct=args.slippage_pct, spread_pct=args.spread_pct,
        )) for symbol, candles in series.items()},
        "cash_return_pct": "0",
    }
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
