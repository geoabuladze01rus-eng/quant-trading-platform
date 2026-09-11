from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import asdict, replace
from decimal import Decimal
from hashlib import sha256
from time import time
from uuid import uuid4

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ConfigDict, Field, field_validator

from quant_trading_platform.audit_log import AuditLog, PersistentAuditLog
from quant_trading_platform.config import MarketScope, Settings, TradingMode
from quant_trading_platform.connectors.crypto import BinanceConnector, BybitConnector, OKXConnector
from quant_trading_platform.execution_orchestrator import ExecutionGroup, ExecutionGroupStatus
from quant_trading_platform.explainability import explain_opportunity, explain_paper_execution
from quant_trading_platform.explainability.reasons import human_reason
from quant_trading_platform.market_data.models import NormalizedOrderBook
from quant_trading_platform.market_data.service import MarketDataService
from quant_trading_platform.models import (
    ArbitrageOpportunity,
    MarketQuote,
    MarketType,
    Venue,
    normalize_symbol,
)
from quant_trading_platform.paper_trading import PaperExecutionEngine
from quant_trading_platform.paper_trading.models import PaperCommandError
from quant_trading_platform.paper_trading.service import PersistentPaperService
from quant_trading_platform.persistence import SQLitePaperStore
from quant_trading_platform.risk import RiskDecision, RiskEngine, RiskLimits
from quant_trading_platform.safety import assert_safe_startup
from quant_trading_platform.strategies.arbitrage import CrossVenueSpreadMonitor


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    assert_safe_startup(settings)
    paper_store = SQLitePaperStore(settings.paper_database_path)
    paper_store.seed_account(
        settings.paper_account_id,
        {
            "USDT": settings.paper_initial_usdt,
            "BTC": settings.paper_initial_btc,
            "ETH": settings.paper_initial_eth,
        },
    )
    app.state.paper_store = paper_store
    app.state.paper_service = PersistentPaperService(paper_store)
    app.state.persistent_audit = PersistentAuditLog(paper_store)
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
        app.state.paper_service = None
        app.state.persistent_audit = None
        app.state.paper_store = None
        paper_store.close()


app = FastAPI(title="Quant Trading Platform", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["GET", "POST"],
    allow_headers=["Accept", "Content-Type", "Idempotency-Key"],
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
            persistent: PersistentAuditLog | None = getattr(
                app.state, "persistent_audit", None
            )
            if persistent is None:
                audit_log.record(
                    event, candidate["reason_text"], settings.market_scope, candidate["strategy"],
                    who="market_data_producer", decision=candidate["risk_score"],
                    reason_code=candidate["reason_code"], opportunity_id=candidate["id"],
                )
            else:
                persistent.record(
                    event,
                    str(candidate["reason_code"]),
                    actor="market_data_producer",
                    actor_type="system",
                    strategy=str(candidate["strategy"]),
                    symbol=str(candidate["symbol"]),
                    venue=f"{candidate['buy_venue']}->{candidate['sell_venue']}",
                    market_type="crypto",
                    opportunity_id=str(candidate["id"]),
                    decision=str(candidate["risk_score"]),
                    risk_score=str(candidate["risk_score"]),
                    gross_edge=str(candidate["gross_spread_pct"]),
                    fees=str(candidate["fees_pct"]),
                    slippage=str(candidate["slippage_pct"]),
                    net_edge=str(candidate["expected_net_pct"]),
                    data_age_ms=int(candidate["data_age_ms"]),
                    correlation_id=str(candidate["id"]),
                    algorithm_version=settings.paper_algorithm_version,
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


class PersistentPaperOrderRequest(BaseModel):
    """Local paper intent; price, cost, risk and execution fields remain server-owned."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    symbol: str = Field(min_length=3, max_length=32)
    buy_venue: Venue
    sell_venue: Venue
    notional_usdt: Decimal = Field(gt=0, max_digits=24, decimal_places=12)

    @field_validator("symbol")
    @classmethod
    def canonical_symbol(cls, value: str) -> str:
        return normalize_symbol(value)


def _trusted_paper_origin(request: Request) -> None:
    origin = request.headers.get("origin")
    if origin is not None and origin not in (
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ):
        raise HTTPException(403, "Untrusted browser origin")


def _persistent_paper_service() -> PersistentPaperService:
    service: PersistentPaperService | None = getattr(app.state, "paper_service", None)
    if service is None:
        raise HTTPException(503, "Persistent paper service is unavailable")
    return service


def _paper_market_context(
    symbol: str,
    buy_venue: Venue,
    sell_venue: Venue,
    notional: Decimal,
) -> tuple[ArbitrageOpportunity, NormalizedOrderBook | None, NormalizedOrderBook | None]:
    market_data: MarketDataService | None = getattr(app.state, "market_data", None)
    buy_book = (
        None if market_data is None else market_data.book_for_simulation(buy_venue, symbol)
    )
    sell_book = (
        None if market_data is None else market_data.book_for_simulation(sell_venue, symbol)
    )
    item = ArbitrageOpportunity(
        "cross_venue_spread",
        symbol,
        buy_venue,
        sell_venue,
        Decimal("0"),
        Decimal("-0.25"),
        notional,
        now_ms(),
        fees_pct=Decimal("0.20"),
        slippage_pct=Decimal("0.05"),
    )
    if buy_book is not None and sell_book is not None:
        try:
            buy, sell = buy_book.to_quote(), sell_book.to_quote()
            item = CrossVenueSpreadMonitor().detect(
                buy, sell, Decimal("0.20"), Decimal("0.05")
            )
            item = replace(item, max_notional_usd=notional)
        except (ValueError, IndexError):
            pass
    return item, buy_book, sell_book


def _position_view(position: dict[str, object]) -> dict[str, object]:
    quantity = Decimal(str(position.get("quantity", "0")))
    mark = position.get("mark_price")
    basis = position.get("cost_basis")
    market_value = None if mark is None else quantity * Decimal(str(mark))
    unrealized = (
        None
        if mark is None or basis is None
        else quantity * (Decimal(str(mark)) - Decimal(str(basis)))
    )
    return serialize_record(
        {
            **position,
            "status": "open" if quantity else "closed",
            "avg_entry_price": basis,
            "average_price": basis,
            "current_price": mark,
            "market_value_usdt": market_value,
            "unrealized_pnl_usdt": unrealized,
            "realized_pnl_usdt": "0",
        }
    )


def _paper_result(
    service: PersistentPaperService, result: dict[str, object]
) -> dict[str, object]:
    raw_account = result.get("account")
    account = (
        dict(raw_account)
        if isinstance(raw_account, dict)
        else service.account(settings.paper_account_id)
    )
    raw_positions = result.get("positions")
    stored_positions = (
        raw_positions
        if isinstance(raw_positions, list)
        else service.store.list_positions(settings.paper_account_id)
    )
    positions = [
        _position_view(position)
        for position in stored_positions
        if isinstance(position, dict)
    ]
    raw_order = result.get("order", {})
    order = dict(raw_order) if isinstance(raw_order, dict) else {}
    order.update(
        id=order.get("id", order.get("order_id")),
        notional_usdt=order.get("requested_notional_usd", "0"),
        remaining_notional_usdt=order.get("remaining_notional_usd", "0"),
        paper_only=True,
    )
    fills: list[dict[str, object]] = []
    raw_fills = result.get("fills", [])
    for original in raw_fills if isinstance(raw_fills, list) else []:
        if not isinstance(original, dict):
            continue
        fill = dict(original)
        fill.update(
            fill_id=fill.get("fill_id", fill.get("id")),
            qty=fill.get("quantity", "0"),
            fee_usdt=fill.get("fee_usd", "0"),
            notional_usdt=fill.get("notional_usd", "0"),
            paper_only=True,
        )
        fills.append(fill)
    code = str(result.get("reason_code", order.get("reason_code", "invalid_order")))
    stored_reconciliation = result.get("accounting_reconciliation")
    reconciliation = (
        stored_reconciliation
        if isinstance(stored_reconciliation, dict)
        else service.reconcile(settings.paper_account_id)
    )
    explanation = {
        "summary": human_reason(code),
        "paper_only": True,
        "calculation": {
            "gross_edge": order.get("gross_edge"),
            "fees": order.get("fees"),
            "slippage": order.get("slippage"),
            "net_edge": order.get("net_edge"),
        },
        "risk": {"decision": order.get("status"), "reason_code": code},
        "reasons": [human_reason(code)],
        "technical": {
            "execution_id": order.get("execution_id"),
            "algorithm_version": order.get("algorithm_version"),
        },
    }
    return serialize_record(
        {
            **result,
            "order": order,
            "fills": fills,
            "account": account,
            "balances": account["balances"],
            "positions": positions,
            "reconciliation": reconciliation,
            "explanation": explanation,
            "paper_only": True,
            "live_trading_enabled": False,
        }
    )


def _execution_group_view(group: ExecutionGroup) -> dict[str, object]:
    payload = asdict(group)
    payload["fills"] = [
        {
            **asdict(fill),
            "venue_order_id": None,
            "paper_only": True,
        }
        for fill in group.fills
    ]
    payload.update(
        paper_only=True,
        live_trading_enabled=False,
        live_execution=False,
    )
    return serialize_record(payload)


def _paper_command_http_error(error: PaperCommandError) -> HTTPException:
    status = 404 if error.reason_code in ("account_not_found", "order_not_found") else 409
    if error.reason_code in ("invalid_order", "market_mismatch"):
        status = 422
    return HTTPException(
        status,
        {"reason_code": error.reason_code, "human_reason": human_reason(error.reason_code)},
    )


@app.post("/paper/orders/simulate")
async def simulate_paper_order(body: PaperSimulationRequest, request: Request) -> dict[str, object]:
    _trusted_paper_origin(request)
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


@app.get("/paper/account")
def paper_account() -> dict[str, object]:
    return serialize_record(_persistent_paper_service().account(settings.paper_account_id))


@app.get("/paper/balances")
def paper_balances() -> list[dict[str, object]]:
    account = _persistent_paper_service().account(settings.paper_account_id)
    return list(account["balances"])


@app.get("/paper/positions")
def paper_positions() -> list[dict[str, object]]:
    service = _persistent_paper_service()
    return [
        _position_view(position)
        for position in service.store.list_positions(settings.paper_account_id)
    ]


@app.get("/paper/performance")
def paper_performance() -> dict[str, object]:
    service = _persistent_paper_service()
    account = service.account(settings.paper_account_id)
    orders = service.store.list_orders(settings.paper_account_id)
    return serialize_record(
        {
            "account_id": settings.paper_account_id,
            "virtual_equity_usdt": account["virtual_equity_usdt"],
            "realized_pnl_usdt": account["realized_pnl_usdt"],
            "unrealized_pnl_usdt": account["unrealized_pnl_usdt"],
            "daily_pnl_usdt": account["realized_pnl_usdt"],
            "fees_paid_usdt": account["fees_paid_usdt"],
            "slippage_cost_usdt": account["slippage_cost_usdt"],
            "filled_orders": sum(order["status"] == "filled" for order in orders),
            "partially_filled_orders": sum(
                order["status"] == "partially_filled" for order in orders
            ),
            "rejected_orders": sum(order["status"] == "rejected" for order in orders),
            "paper_only": True,
        }
    )


@app.get("/paper/reconciliation")
def paper_reconciliation() -> dict[str, object]:
    return serialize_record(_persistent_paper_service().reconcile(settings.paper_account_id))


@app.get("/paper/execution-runtime")
def paper_execution_runtime() -> dict[str, object]:
    orchestrator = _persistent_paper_service().execution_orchestrator
    groups = orchestrator.list_groups()
    return {
        "status": "HALTED" if orchestrator.halted else "ACTIVE",
        "halted": orchestrator.halted,
        "groups_total": len(groups),
        "hedge_required": sum(
            group.status == ExecutionGroupStatus.HEDGE_REQUIRED for group in groups
        ),
        "live_trading_enabled": False,
        "paper_only": True,
    }


@app.get("/paper/execution-groups")
def paper_execution_groups() -> list[dict[str, object]]:
    groups = _persistent_paper_service().execution_orchestrator.list_groups()
    return [_execution_group_view(group) for group in groups]


@app.get("/paper/execution-groups/{execution_group_id}")
def paper_execution_group(execution_group_id: str) -> dict[str, object]:
    orchestrator = _persistent_paper_service().execution_orchestrator
    try:
        group = orchestrator.group(execution_group_id)
    except (KeyError, ValueError) as error:
        raise HTTPException(404, "Paper execution group not found") from error
    return {
        **_execution_group_view(group),
        "reconciliation": serialize_record(
            asdict(orchestrator.reconcile_group(group.execution_group_id))
        ),
    }


@app.post("/paper/orders/preview")
async def preview_persistent_paper_order(
    body: PersistentPaperOrderRequest, request: Request
) -> dict[str, object]:
    _trusted_paper_origin(request)
    service = _persistent_paper_service()
    item, buy_book, sell_book = _paper_market_context(
        body.symbol, body.buy_venue, body.sell_venue, body.notional_usdt
    )
    try:
        result = service.preview(
            item,
            buy_book,
            sell_book,
            notional_usd=body.notional_usdt,
            settings=settings,
            account_id=settings.paper_account_id,
        )
    except PaperCommandError as error:
        raise _paper_command_http_error(error) from error
    account = service.account(settings.paper_account_id)
    preview_result: dict[str, object] = {
        **result,
        "order": {
            "id": "",
            "order_id": "",
            "symbol": body.symbol,
            "status": result["status"],
            "reason_code": result["reason_code"],
            "human_reason": result["human_reason"],
            "buy_venue": body.buy_venue.value,
            "sell_venue": body.sell_venue.value,
            "notional_usdt": str(body.notional_usdt),
            "requested_notional_usd": str(body.notional_usdt),
            "simulated_notional_usd": result["simulated_notional_usd"],
            "required_quote": result["required_quote"],
            "required_base": result["required_base"],
            "gross_edge": item.expected_gross_pct,
            "fees": item.fees_pct,
            "slippage": item.slippage_pct,
            "net_edge": item.expected_net_pct,
            "paper_only": True,
        },
        "fills": [],
        "account": account,
        "balances": account["balances"],
        "positions": service.store.list_positions(settings.paper_account_id),
        "accounting_reconciliation": service.reconcile(settings.paper_account_id),
    }
    return _paper_result(service, preview_result)


@app.post("/paper/orders")
async def create_persistent_paper_order(
    body: PersistentPaperOrderRequest,
    request: Request,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=128),
) -> dict[str, object]:
    _trusted_paper_origin(request)
    service = _persistent_paper_service()
    item, buy_book, sell_book = _paper_market_context(
        body.symbol, body.buy_venue, body.sell_venue, body.notional_usdt
    )
    try:
        result = service.execute(
            item,
            buy_book,
            sell_book,
            notional_usd=body.notional_usdt,
            settings=settings,
            idempotency_key=idempotency_key,
            account_id=settings.paper_account_id,
            actor="local_paper_user",
        )
    except PaperCommandError as error:
        raise _paper_command_http_error(error) from error
    return _paper_result(service, result)


@app.get("/paper/orders")
def paper_orders() -> list[dict[str, object]]:
    service: PersistentPaperService | None = getattr(app.state, "paper_service", None)
    if service is not None:
        return [
            serialize_record(
                {
                    **order,
                    "id": order.get("id", order.get("order_id")),
                    "notional_usdt": order.get("requested_notional_usd", "0"),
                    "remaining_notional_usdt": order.get("remaining_notional_usd", "0"),
                    "paper_only": True,
                }
            )
            for order in service.store.list_orders(settings.paper_account_id)
        ]
    return [serialize_record(asdict(order)) for order in paper_engine.orders]


@app.get("/paper/orders/{order_id}")
def paper_order(order_id: str) -> dict[str, object]:
    service = _persistent_paper_service()
    order = service.store.get_order(order_id)
    if order is None or order["account_id"] != settings.paper_account_id:
        raise HTTPException(404, "Paper order not found")
    return serialize_record(
        {
            **order,
            "id": order.get("id", order.get("order_id")),
            "notional_usdt": order.get("requested_notional_usd", "0"),
            "remaining_notional_usdt": order.get("remaining_notional_usd", "0"),
            "paper_only": True,
        }
    )


@app.post("/paper/orders/{order_id}/cancel")
async def cancel_persistent_paper_order(
    order_id: str,
    request: Request,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=128),
) -> dict[str, object]:
    _trusted_paper_origin(request)
    service = _persistent_paper_service()
    try:
        result = service.cancel(
            order_id,
            idempotency_key=idempotency_key,
            account_id=settings.paper_account_id,
            actor="local_paper_user",
        )
    except PaperCommandError as error:
        raise _paper_command_http_error(error) from error
    return _paper_result(service, result)


@app.get("/paper/fills")
def paper_fills() -> list[dict[str, object]]:
    service: PersistentPaperService | None = getattr(app.state, "paper_service", None)
    if service is not None:
        return [
            serialize_record(
                {
                    **fill,
                    "fill_id": fill.get("fill_id", fill.get("id")),
                    "qty": fill.get("quantity", "0"),
                    "fee_usdt": fill.get("fee_usd", "0"),
                    "notional_usdt": fill.get("notional_usd", "0"),
                    "paper_only": True,
                }
            )
            for fill in service.store.list_fills(settings.paper_account_id)
        ]
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
def audit(
    limit: int | None = None,
    offset: int = 0,
    event_type: str | None = None,
    order_id: str | None = None,
    reason_code: str | None = None,
    correlation_id: str | None = None,
) -> list[dict[str, object]] | dict[str, object]:
    """Read detached audit snapshots; GET never records or mutates journal state."""
    persistent: PersistentAuditLog | None = getattr(app.state, "persistent_audit", None)
    if persistent is None:
        return [dict(item) for item in audit_log.list()]
    page_size = 100 if limit is None else limit
    try:
        items = persistent.list(
            page_size,
            offset,
            event_type=event_type,
            order_id=order_id,
            reason_code=reason_code,
            correlation_id=correlation_id,
        )
        total = persistent.count(
            event_type=event_type,
            order_id=order_id,
            reason_code=reason_code,
            correlation_id=correlation_id,
        )
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    no_filters = not any((event_type, order_id, reason_code, correlation_id))
    if limit is None and no_filters and offset == 0:
        return items
    return {"items": items, "total": total, "limit": page_size, "offset": offset}
