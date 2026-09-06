from __future__ import annotations

from fastapi import FastAPI, Response, status

from quant_trading_platform.audit_log import AuditLog
from quant_trading_platform.config import Settings
from quant_trading_platform.models import ArbitrageOpportunity, Venue, MarketQuote
from quant_trading_platform.risk import RiskEngine, RiskLimits
from quant_trading_platform.config import MarketScope
from quant_trading_platform import safety

app = FastAPI(title="Quant Trading Platform", version="0.1.0")
settings = Settings()
audit_log = AuditLog()


# simple in-memory quote cache for demo/testing; producers write into connectors
_QUOTE_CACHE: dict[tuple[str, str], MarketQuote] = {}


def _opportunity() -> ArbitrageOpportunity:
    return ArbitrageOpportunity(
        strategy="cross_venue_spread",
        symbol="BTC/USDT",
        buy_exchange=Venue.OKX,
        sell_exchange=Venue.BYBIT,
        expected_gross_pct=Decimal("0.31"),
        expected_net_pct=Decimal("0.16"),
        gross_spread_pct=Decimal("0.31"),
        fees_pct=Decimal("0.10"),
        slippage_pct=Decimal("0.05"),
        max_notional_usd=Decimal("100"),
        detected_at_ms=0,
    )


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "trading_mode": settings.trading_mode.value, "live_trading": "locked"}


@app.get("/settings")
def get_settings() -> dict[str, object]:
    return {
        "trading_mode": settings.trading_mode.value,
        "market_scope": settings.market_scope.value,
        "live_trading_enabled": settings.live_trading_enabled,
        "t_invest_sandbox": settings.t_invest_sandbox,
        "max_daily_loss_pct": settings.max_daily_loss_pct,
        "max_trade_notional_usd": settings.max_trade_notional_usd,
        "min_expected_net_pct": settings.min_expected_net_pct,
    }


@app.get("/venues")
def venues() -> list[dict[str, object]]:
    return [
        {"name": "binance", "market": "crypto", "status": "mock", "live_execution": False},
        {"name": "bybit", "market": "crypto", "status": "mock", "live_execution": False},
        {"name": "okx", "market": "crypto", "status": "mock", "live_execution": False},
        {
            "name": "t_invest",
            "market": "russian_stocks",
            "status": "sandbox",
            "live_execution": False,
        },
    ]


@app.get("/opportunities")
def opportunities() -> Response:
    # read-only endpoint: do not start collectors or write audit entries
    # assemble candidates from in-memory cache
    candidates: list[dict[str, object]] = []
    now_ms = safety.now_ms()
    max_age = settings.max_market_data_age_ms
    for (_venue, _symbol), q in list(_QUOTE_CACHE.items()):
        age = now_ms - q.timestamp_ms
        if age > max_age:
            continue
        # naive single-venue placeholder - real detection done in arbitrage detectors
        # return sample shaped response
        candidates.append({
            "symbol": q.symbol,
            "venue": q.venue.value,
            "market_type": q.market_type.value,
            "data_age_ms": age,
        })

    if not candidates:
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    return {"status": "ok", "candidates": candidates}


@app.get("/risk")
def risk() -> dict[str, object]:
    return {
        "max_daily_loss_pct": settings.max_daily_loss_pct,
        "max_trade_notional_usd": settings.max_trade_notional_usd,
        "min_expected_net_pct": settings.min_expected_net_pct,
        "live_trading_locked": not settings.live_trading_enabled,
        "stale_data_protection": True,
        "api_error_protection": True,
        "balance_mismatch_protection": True,
    }


@app.get("/audit")
def audit() -> list[dict[str, str]]:
    # read-only: return deep copies, never record on GET
    return audit_log.list()
