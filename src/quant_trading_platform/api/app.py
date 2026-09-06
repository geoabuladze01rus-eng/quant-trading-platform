from __future__ import annotations

from decimal import Decimal
from time import time

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from quant_trading_platform.audit_log import AuditLog
from quant_trading_platform.config import MarketScope, Settings
from quant_trading_platform.models import MarketQuote, MarketType
from quant_trading_platform.risk import RiskEngine, RiskLimits
from quant_trading_platform.safety import assert_safe_startup
from quant_trading_platform.strategies.arbitrage import CrossVenueSpreadMonitor

app = FastAPI(title="Quant Trading Platform", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["GET"],
    allow_headers=["Accept"],
)
settings = Settings()
assert_safe_startup(settings)
audit_log = AuditLog()


# Producers populate this cache; GET requests only inspect a snapshot.
_QUOTE_CACHE: dict[tuple[str, str], MarketQuote] = {}


def now_ms() -> int:
    return int(time() * 1000)


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
def opportunities() -> dict[str, object]:
    """Explain paper candidates, including risk rejection, without executing anything."""
    candidates: list[dict[str, object]] = []
    timestamp = now_ms()
    quotes = tuple(_QUOTE_CACHE.values())
    detector = CrossVenueSpreadMonitor()
    engine = RiskEngine(RiskLimits(
        max_daily_loss_pct=Decimal(str(settings.max_daily_loss_pct)),
        max_trade_notional_usd=Decimal(str(settings.max_trade_notional_usd)),
        min_expected_net_pct=Decimal(str(settings.min_expected_net_pct)),
    ))
    for buy in quotes:
        if (
            settings.market_scope == MarketScope.CRYPTO
            and buy.market_type != MarketType.CRYPTO
        ) or (
            settings.market_scope == MarketScope.RUSSIAN_STOCKS
            and buy.market_type != MarketType.RUSSIAN_STOCKS
        ):
            continue
        for sell in quotes:
            if buy.venue == sell.venue:
                continue
            try:
                item = detector.detect(buy, sell, Decimal("0.20"), Decimal("0.05"))
            except ValueError:
                continue
            age = timestamp - min(buy.timestamp_ms, sell.timestamp_ms)
            # A future-dated leg is invalid even if the other leg is old.
            decision = engine.evaluate(
                item,
                data_age_ms=age,
                max_data_age_ms=settings.max_market_data_age_ms,
                api_error=max(buy.timestamp_ms, sell.timestamp_ms) > timestamp,
            )
            candidates.append({
                "strategy": item.strategy,
                "symbol": item.symbol,
                "buy_venue": item.buy_exchange.value,
                "sell_venue": item.sell_exchange.value,
                "gross_spread_pct": str(item.gross_spread_pct),
                "fees_pct": str(item.fees_pct),
                "slippage_pct": str(item.slippage_pct),
                "expected_net_pct": str(item.expected_net_pct),
                "max_notional_usd": str(item.max_notional_usd),
                "approved": decision.approved,
                "reason": decision.reason,
                "data_age_ms": max(0, age),
            })
    return {"status": "ok" if candidates else "no_data", "opportunities": candidates}


@app.get("/risk")
def risk() -> dict[str, object]:
    return {
        "max_daily_loss_pct": settings.max_daily_loss_pct,
        "max_trade_notional_usd": settings.max_trade_notional_usd,
        "min_expected_net_pct": settings.min_expected_net_pct,
        # Effective capability: this MVP has no live execution implementation.
        "live_trading_locked": True,
        "stale_data_protection": True,
        "api_error_protection": True,
        "balance_mismatch_protection": True,
    }


@app.get("/audit")
def audit() -> list[dict[str, str]]:
    # read-only: return deep copies, never record on GET
    return audit_log.list()
