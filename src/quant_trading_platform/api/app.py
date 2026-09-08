from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import asdict, replace
from decimal import Decimal
from hashlib import sha256
from time import time
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ConfigDict, Field, field_validator

from quant_trading_platform.audit_log import AuditLog
from quant_trading_platform.config import MarketScope, Settings, TradingMode
from quant_trading_platform.connectors.crypto import BinanceConnector, BybitConnector, OKXConnector
from quant_trading_platform.explainability import explain_opportunity, explain_paper_execution
from quant_trading_platform.market_data.service import MarketDataService
from quant_trading_platform.models import (
    ArbitrageOpportunity,
    MarketQuote,
    MarketType,
    Venue,
    normalize_symbol,
)
from quant_trading_platform.paper_trading import PaperExecutionEngine
from quant_trading_platform.risk import RiskDecision, RiskEngine, RiskLimits
from quant_trading_platform.safety import assert_safe_startup
from quant_trading_platform.strategies.arbitrage import CrossVenueSpreadMonitor


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    service = None
    if settings.public_market_data_enabled and settings.market_scope != MarketScope.RUSSIAN_STOCKS:
        service = MarketDataService(
            [BinanceConnector(settings), BybitConnector(settings), OKXConnector(settings)],
            _QUOTE_CACHE,
            symbol=settings.market_data_symbol,
            interval_seconds=settings.market_data_poll_interval_seconds,
            max_age_ms=settings.max_market_data_age_ms,
            on_update=record_detected_opportunities,
        )
    app.state.market_data = service
    try:
        if service is not None:
            await service.start()
        yield
    finally:
        if service is not None:
            await service.stop()
        _QUOTE_CACHE.clear()
        _LAST_SIGNALS.clear()
        app.state.market_data = None


app = FastAPI(title="Quant Trading Platform", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["GET", "POST"],
    allow_headers=["Accept", "Content-Type"],
)
settings = Settings()
assert_safe_startup(settings)
audit_log = AuditLog()
paper_engine = PaperExecutionEngine()


# Producers populate this cache; GET requests only inspect a snapshot.
_QUOTE_CACHE: dict[tuple[str, str], MarketQuote] = {}
_LAST_SIGNALS: dict[tuple[str, str, str], str] = {}


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
        "max_market_data_age_ms": settings.max_market_data_age_ms,
    }


@app.get("/venues")
def venues() -> list[dict[str, object]]:
    service: MarketDataService | None = getattr(app.state, "market_data", None)
    disabled = (
        not settings.public_market_data_enabled
        or settings.market_scope == MarketScope.RUSSIAN_STOCKS
    )
    crypto = service.snapshot() if service is not None else [
        {
            "name": venue, "market": "crypto", "status": "disabled" if disabled else "no_data",
            "mode": "disabled" if disabled else "public_read_only", "live_execution": False,
            "symbol": settings.market_data_symbol, "data_age_ms": None, "error": None,
            "bid": None, "ask": None, "timestamp_source": None,
            "depth_status": "unavailable", "bid_levels": 0, "ask_levels": 0,
        } for venue in ("binance", "bybit", "okx")
    ]
    return [*crypto, {
            "name": "t_invest",
            "market": "russian_stocks",
            "status": "no_data",
            "mode": "sandbox",
            "live_execution": False,
            "symbol": "", "data_age_ms": None, "error": None,
            "bid": None, "ask": None, "timestamp_source": None,
            "depth_status": "unavailable", "bid_levels": 0, "ask_levels": 0,
        }]


@app.get("/opportunities")
def opportunities(explain: bool = False) -> dict[str, object]:
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
            simulation_notional = min(
                Decimal("10"), item.max_notional_usd,
                Decimal(str(settings.max_trade_notional_usd)),
            )
            if explain:
                item = replace(item, max_notional_usd=simulation_notional)
            # A future-dated leg is invalid even if the other leg is old.
            decision = engine.evaluate(
                item,
                data_age_ms=age,
                max_data_age_ms=settings.max_market_data_age_ms,
                api_error=max(buy.timestamp_ms, sell.timestamp_ms) > timestamp,
            )
            if explain and (
                settings.trading_mode != TradingMode.PAPER or settings.live_trading_enabled
            ):
                decision = RiskDecision(
                    False, "Simulation requires paper mode with live disabled",
                    reason_code="live_trading_locked",
                )
            candidate: dict[str, object] = {
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
            }
            if explain:
                candidate.update(explain_opportunity(item, decision))
                candidate["id"] = signal_id(buy, sell)
                candidate["simulation_notional_usd"] = str(simulation_notional)
            candidates.append(candidate)
    return {"status": "ok" if candidates else "no_data", "opportunities": candidates}


def signal_id(buy: MarketQuote, sell: MarketQuote) -> str:
    return sha256(repr((buy, sell)).encode()).hexdigest()[:24]


def record_detected_opportunities() -> None:
    """Producer-side audit only: GET handlers never create events."""
    payload = opportunities(explain=True)
    candidates = payload["opportunities"]
    assert isinstance(candidates, list)
    for candidate in candidates:
        key = (candidate["symbol"], candidate["buy_venue"], candidate["sell_venue"])
        if _LAST_SIGNALS.get(key) == candidate["id"]:
            continue
        _LAST_SIGNALS[key] = candidate["id"]
        for event in (
            "opportunity_detected", "risk_approved" if candidate["approved"] else "risk_rejected",
        ):
            audit_log.record(
                event, candidate["reason_text"], settings.market_scope, candidate["strategy"],
                who="market_data_producer", decision=candidate["risk_score"],
                reason_code=candidate["reason_code"], opportunity_id=candidate["id"],
            )


class PaperSimulationRequest(BaseModel):
    """Explicit intent only: prices, fees, risk approval and mode are server-owned."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    symbol: str = Field(min_length=3, max_length=32)
    buy_venue: Venue
    sell_venue: Venue
    notional_usd: Decimal = Field(gt=0, max_digits=24, decimal_places=12)

    @field_validator("symbol")
    @classmethod
    def canonical_symbol(cls, value: str) -> str:
        return normalize_symbol(value)


@app.post("/paper/orders/simulate")
async def simulate_paper_order(body: PaperSimulationRequest, request: Request) -> dict[str, object]:
    origin = request.headers.get("origin")
    if origin is not None and origin not in ("http://localhost:5173", "http://127.0.0.1:5173"):
        raise HTTPException(403, "Untrusted browser origin")
    service: MarketDataService | None = getattr(app.state, "market_data", None)
    buy_book = None if service is None else service.book_for_simulation(body.buy_venue, body.symbol)
    sell_book = (
        None if service is None else service.book_for_simulation(body.sell_venue, body.symbol)
    )
    # No caller-supplied quotes or risk flags. Missing books form a rejected intent only.
    item = ArbitrageOpportunity(
        "cross_venue_spread", body.symbol, body.buy_venue, body.sell_venue,
        Decimal("0"), Decimal("-0.25"), body.notional_usd, now_ms(),
        fees_pct=Decimal("0.20"), slippage_pct=Decimal("0.05"),
    )
    opportunity_id = str(uuid4())
    if buy_book is not None and sell_book is not None:
        try:
            buy, sell = buy_book.to_quote(), sell_book.to_quote()
            item = CrossVenueSpreadMonitor().detect(buy, sell, Decimal("0.20"), Decimal("0.05"))
            # This is an explicit requested size, not the detector's top-level liquidity.
            # Engine checks the configured trade cap AND full depth independently.
            item = replace(item, max_notional_usd=body.notional_usd)
            opportunity_id = signal_id(buy, sell)
        except (ValueError, IndexError):
            pass  # Engine independently rejects invalid identity/depth.
    report = paper_engine.simulate(
        item, buy_book, sell_book, notional_usd=body.notional_usd, settings=settings,
    )
    common = {
        "who": "local_paper_user", "execution_id": report.execution_id,
        "opportunity_id": opportunity_id, "reason_code": report.reason_code,
        "decision": report.status,
    }
    audit_log.record(
        "opportunity_detected", report.reason_text, settings.market_scope, item.strategy, **common,
    )
    for event in (
        "risk_approved" if report.status == "filled" else "risk_rejected",
        "paper_order_created" if report.status == "filled" else "paper_order_rejected",
    ):
        audit_log.record(event, report.reason_text, settings.market_scope, item.strategy, **common)
    for fill in report.fills:
        audit_log.record(
            "paper_fill_simulated", fill.reason_text, settings.market_scope,
            item.strategy, **common,
        )
    audit_log.record(
        "reconciliation_completed", report.reason_text, settings.market_scope, item.strategy,
        **common,
    )
    return serialize_record({**asdict(report), "explanation": explain_paper_execution(report)})


def serialize_record(record: dict[str, object]) -> dict[str, object]:
    # Financial decimals stay exact JSON strings rather than binary floats.
    return dict(jsonable_encoder(record, custom_encoder={Decimal: str}))


@app.get("/paper/orders")
def paper_orders() -> list[dict[str, object]]:
    return [serialize_record(asdict(order)) for order in paper_engine.orders]


@app.get("/paper/fills")
def paper_fills() -> list[dict[str, object]]:
    return [serialize_record(asdict(fill)) for fill in paper_engine.fills]


@app.get("/reconciliation")
def reconciliation() -> list[dict[str, object]]:
    return [
        serialize_record({"execution_id": report.execution_id, **asdict(report.reconciliation)})
        for report in paper_engine.reports
    ]


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
