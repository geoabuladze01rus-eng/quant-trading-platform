"""Funded, single-venue OKX spot paper commands; no exchange order transport."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from time import time
from typing import Any
from uuid import uuid4

from quant_trading_platform.config import MarketScope, Settings, TradingMode
from quant_trading_platform.market_data.models import NormalizedOrderBook, OrderBookLevel
from quant_trading_platform.models import MarketType, Venue, normalize_symbol
from quant_trading_platform.paper_trading.models import PaperCommandError, decimal_text
from quant_trading_platform.paper_trading.service import PersistentPaperService, _plain
from quant_trading_platform.persistence.store import SQLitePaperStore

FEE_RATE_PCT = Decimal("0.10")
SLIPPAGE_RATE_PCT = Decimal("0.05")
PAIRS = frozenset(("BTC/USDT", "ETH/USDT", "LTC/USDT"))


def _depth_fill(levels: tuple[OrderBookLevel, ...], gross: Decimal) -> tuple[Decimal, Decimal]:
    """Exact quote-notional fill over observed depth; reject incomplete books."""
    remaining = gross
    quantity = Decimal(0)
    for level in levels:
        if level.quantity * level.price >= remaining:
            quantity += remaining / level.price
            return quantity, gross / quantity
        quantity += level.quantity
        remaining -= level.quantity * level.price
    raise PaperCommandError("insufficient_depth", "Недостаточная глубина стакана")


class OKXSpotPaperService(PersistentPaperService):
    def __init__(self, store: SQLitePaperStore) -> None:
        super().__init__(store)

    def execute_spot(
        self,
        *,
        symbol: str,
        side: str,
        notional_usdt: Decimal,
        book: NormalizedOrderBook | None,
        settings: Settings,
        idempotency_key: str,
        account_id: str,
    ) -> dict[str, Any]:
        try:
            symbol = normalize_symbol(symbol)
        except ValueError as error:
            raise PaperCommandError("market_mismatch", "Некорректная пара") from error
        if symbol not in PAIRS or side not in ("buy", "sell"):
            raise PaperCommandError("unsupported_market", "Недоступная спотовая операция")
        if not notional_usdt.is_finite() or notional_usdt <= 0:
            raise PaperCommandError("invalid_order", "Размер должен быть положительным")
        payload = {
            "operation": "okx_spot_paper", "account_id": account_id,
            "symbol": symbol, "side": side, "notional_usdt": decimal_text(notional_usdt),
        }
        with self.store.transaction() as conn:
            account = self._account(account_id, conn)
            replay = self._replay(account_id, idempotency_key, payload, conn)
            if replay is not None:
                return replay
            if account.get("account_kind") != "okx_spot" or account.get("status") != "active":
                raise PaperCommandError("reconciliation_mismatch", "Спотовый счёт остановлен")
            if settings.trading_mode != TradingMode.PAPER or settings.live_trading_enabled:
                raise PaperCommandError("live_trading_locked", "Допустим только виртуальный режим")
            if settings.market_scope == MarketScope.RUSSIAN_STOCKS:
                raise PaperCommandError("market_type_mismatch", "Криптовалютный рынок выключен")
            if notional_usdt > settings.max_trade_notional_usd:
                raise PaperCommandError("notional_limit_exceeded", "Превышен лимит сделки")
            starting_usdt = Decimal(account["initial_balances"]["USDT"])
            today = datetime.now(UTC).date().isoformat()
            daily_pnl = (
                Decimal(account.get("spot_daily_pnl_usdt", "0"))
                if account.get("spot_day_utc") == today else Decimal(0)
            )
            if (
                starting_usdt <= 0 or
                -daily_pnl * 100 / starting_usdt >= settings.max_daily_loss_pct
            ):
                raise PaperCommandError("daily_loss_limit_reached", "Достигнут предел потерь")
            timestamp_ms = int(time() * 1000)
            if (
                book is None or book.venue != Venue.OKX or book.symbol != symbol
                or book.market_type != MarketType.CRYPTO
                or min(book.timestamp_ms, book.received_at_ms) > timestamp_ms
                or timestamp_ms - min(book.timestamp_ms, book.received_at_ms)
                > settings.max_market_data_age_ms
            ):
                raise PaperCommandError("stale_market_data", "Нет свежего стакана OKX")
            quantity, price = _depth_fill(
                book.asks if side == "buy" else book.bids, notional_usdt
            )
            fee = notional_usdt * FEE_RATE_PCT / 100
            slippage = notional_usdt * SLIPPAGE_RATE_PCT / 100
            base = symbol.split("/")[0]
            quote_balance = self.store.get_balance(account_id, "USDT", conn=conn)
            base_balance = self.store.get_balance(account_id, base, conn=conn)
            if quote_balance is None or base_balance is None:
                raise PaperCommandError("balance_missing", "Нет баланса актива")
            inventory = dict(account.get("spot_inventory", {}))
            position = dict(inventory.get(base, {"quantity": "0", "cost_usdt": "0"}))
            held = Decimal(position["quantity"])
            basis = Decimal(position["cost_usdt"])
            if side == "buy":
                spent = notional_usdt + fee + slippage
                if Decimal(quote_balance["available"]) < spent:
                    raise PaperCommandError("insufficient_paper_balance", "Недостаточно USDT")
                total_basis = sum(
                    (Decimal(item["cost_usdt"]) for item in inventory.values()), Decimal(0)
                )
                if (
                    basis + spent > starting_usdt * settings.okx_spot_max_asset_pct / 100
                    or total_basis + spent > starting_usdt * settings.okx_spot_max_total_pct / 100
                ):
                    raise PaperCommandError("notional_limit_exceeded", "Превышена доля актива")
                self._change_balance(account_id, "USDT", -spent, Decimal(0), conn)
                self._change_balance(account_id, base, quantity, Decimal(0), conn)
                position = {"quantity": decimal_text(held + quantity),
                            "cost_usdt": decimal_text(basis + spent)}
                realized = Decimal(0)
            else:
                if held < quantity or Decimal(base_balance["available"]) < quantity:
                    raise PaperCommandError(
                        "insufficient_paper_balance", "Нет купленного виртуального актива"
                    )
                allocated = basis * quantity / held
                proceeds = notional_usdt - fee - slippage
                self._change_balance(account_id, base, -quantity, Decimal(0), conn)
                self._change_balance(account_id, "USDT", proceeds, Decimal(0), conn)
                position = {"quantity": decimal_text(held - quantity),
                            "cost_usdt": decimal_text(basis - allocated)}
                realized = proceeds - allocated
            inventory[base] = position
            cost_basis = dict(account.get("cost_basis", {}))
            held_after = Decimal(position["quantity"])
            if held_after:
                cost_basis[base] = decimal_text(Decimal(position["cost_usdt"]) / held_after)
            else:
                cost_basis.pop(base, None)
            order_id = str(uuid4())
            order: dict[str, Any] = {
                "order_id": order_id, "execution_id": order_id, "account_id": account_id,
                "symbol": symbol, "side": side, "status": "filled", "venue": "okx",
                "strategy": "okx_spot_paper", "market_type": "crypto",
                "buy_venue": "okx" if side == "buy" else "",
                "sell_venue": "okx" if side == "sell" else "",
                "requested_notional_usd": notional_usdt, "remaining_notional_usd": Decimal(0),
                "filled_quantity": quantity, "reserved_quote": Decimal(0),
                "reserved_base": Decimal(0), "timestamp_ms": timestamp_ms,
                "fee_rate_pct": FEE_RATE_PCT, "fees": FEE_RATE_PCT,
                "slippage": SLIPPAGE_RATE_PCT,
                "algorithm_version": settings.paper_algorithm_version,
                "reason_code": "paper_order_filled",
                "data_age_ms": timestamp_ms - book.timestamp_ms,
            }
            self.store.insert_order(order, conn=conn)
            fill = self.store.insert_fill({
                "account_id": account_id, "order_id": order_id, "execution_id": order_id,
                "venue": "okx", "symbol": symbol, "side": side, "quantity": quantity,
                "price": price, "notional_usd": notional_usdt, "fee_usd": fee,
                "fee_rate_pct": FEE_RATE_PCT, "slippage_cost_usd": slippage,
                "timestamp_ms": timestamp_ms, "reason_code": "paper_order_filled",
            }, conn=conn)
            marks = dict(account.get("marks", {}))
            marks[base] = decimal_text(book.bids[0].price)
            mark_sources = dict(account.get("mark_sources", {}))
            mark_sources[base] = "okx:public_order_book"
            account.update(
                spot_inventory=inventory, marks=marks, mark_sources=mark_sources,
                cost_basis=cost_basis,
                realized_pnl_usd=Decimal(account.get("realized_pnl_usd", "0")) + realized,
                spot_day_utc=today, spot_daily_pnl_usdt=daily_pnl + realized,
                fees_paid_usd=Decimal(account.get("fees_paid_usd", "0")) + fee,
                slippage_paid_usd=Decimal(account.get("slippage_paid_usd", "0")) + slippage,
            )
            self.store.upsert_account(account_id, account, conn=conn)
            self._positions(account_id, account, conn)
            self._audit(order, "system", "paper_order_filled", conn)
            snapshot = self._snapshot(account_id, conn)
            reconciliation = snapshot["accounting_reconciliation"]
            if reconciliation["status"] != "ok":
                raise PaperCommandError("reconciliation_mismatch", "Сверка сделки не пройдена")
            self.store.insert_reconciliation({
                **reconciliation, "account_id": account_id, "execution_id": order_id,
                "correlation_id": order_id,
            }, conn=conn)
            response = _plain({
                "order": order, "fills": [fill], **snapshot, "paper_only": True,
                "live_execution": False, "executed_at": datetime.now(UTC).isoformat(),
            })
            self.store.complete_idempotency(account_id, idempotency_key, response, conn=conn)
            return dict(response)
