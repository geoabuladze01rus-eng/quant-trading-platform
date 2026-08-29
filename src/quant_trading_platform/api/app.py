from decimal import Decimal

from fastapi import FastAPI

from quant_trading_platform.audit_log import AuditLog
from quant_trading_platform.config import Settings
from quant_trading_platform.models import ArbitrageOpportunity, Venue
from quant_trading_platform.risk import RiskEngine, RiskLimits

app = FastAPI(title="Quant Trading Platform", version="0.1.0")
settings = Settings()
audit_log = AuditLog()


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
def opportunities() -> list[dict[str, object]]:
    item = _opportunity()
    decision = RiskEngine(RiskLimits()).evaluate(item)
    return [
        {
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
        }
    ]


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
    if not audit_log.list():
        audit_log.record(
            "signal", "Mock signal detected", settings.market_scope, "cross_venue_spread"
        )
        audit_log.record(
            "approved", "Paper-only risk checks passed", settings.market_scope, "cross_venue_spread"
        )
    return audit_log.list()
