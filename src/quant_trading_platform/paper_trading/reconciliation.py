"""Read-only accounting checks, independent of venue clients."""

from collections import defaultdict
from dataclasses import asdict
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from quant_trading_platform.paper_trading.models import OPEN_STATUSES, ReconciliationIssue


def reconcile_records(
    account: dict[str, Any],
    balances: list[dict[str, Any]],
    orders: list[dict[str, Any]],
    fills: list[dict[str, Any]],
    positions: list[dict[str, Any]],
) -> dict[str, Any]:
    """Rebuild balances from seed plus fills, and reservations from open groups."""
    issues: list[ReconciliationIssue] = []
    timestamp = datetime.now(UTC).isoformat()

    def issue(
        code: str, text: str, correlation: str | None = None, asset: str | None = None
    ) -> None:
        issues.append(ReconciliationIssue(code, text, asset or correlation or "account",
                                           timestamp, "error", correlation))

    def number(value: Any, correlation: str | None = None) -> Decimal:
        try:
            result = Decimal(str(value))
            if not result.is_finite():
                raise ValueError("nonfinite")
            return result
        except (InvalidOperation, ValueError):
            issue("invalid_numeric_value", "Некорректное число в учётной записи", correlation)
            return Decimal(0)

    initial = account.get("initial_balances", {})
    expected = defaultdict(
        lambda: Decimal(0), {asset: number(value) for asset, value in initial.items()}
    )
    reserved: dict[str, Decimal] = defaultdict(lambda: Decimal(0))
    by_order = {str(order["order_id"]): order for order in orders}
    fill_quantities: dict[tuple[str, str], Decimal] = defaultdict(lambda: Decimal(0))
    fees = Decimal(0)
    slippage = Decimal(0)
    for fill in fills:
        order_id = str(fill["order_id"])
        if order_id not in by_order:
            issue("orphan_fill", "Исполнение не связано с сохранённым ордером", order_id)
        base = str(fill["symbol"]).split("/")[0]
        quantity, notional = (
            number(fill["quantity"], order_id),
            number(fill["notional_usd"], order_id),
        )
        fee = number(fill.get("fee_usd", "0"), order_id)
        reserve_cost = number(fill.get("slippage_cost_usd", "0"), order_id)
        if min(quantity, notional, fee, reserve_cost) < 0:
            issue("negative_fill", "Исполнение содержит отрицательные значения", order_id)
        order = by_order.get(order_id, {})
        fee_rate = fill.get("fee_rate_pct", order.get("fee_rate_pct"))
        if fee_rate is None:
            issue("missing_fee_rate", "Не сохранена ставка комиссии исполнения", order_id)
        elif fee != notional * number(fee_rate, order_id) / 100:
            issue(
                "fill_fee_mismatch",
                "Комиссия не соответствует ставке и исполненному объёму",
                order_id,
            )
        side = str(fill["side"])
        sign = Decimal(1) if side == "buy" else Decimal(-1)
        if side not in ("buy", "sell"):
            issue("invalid_fill_side", "Неизвестное направление исполнения", order_id)
        expected[base] += sign * quantity
        expected["USDT"] -= sign * notional + fee + reserve_cost
        fees += fee
        slippage += reserve_cost
        fill_quantities[(order_id, side)] += quantity
    for order in orders:
        order_id = str(order["order_id"])
        base = str(order["symbol"]).split("/")[0]
        quote_reserve = number(order.get("reserved_quote", "0"), order_id)
        base_reserve = number(order.get("reserved_base", "0"), order_id)
        if order["status"] in OPEN_STATUSES:
            reserved["USDT"] += quote_reserve
            reserved[base] += base_reserve
        elif quote_reserve or base_reserve:
            issue("closed_order_reservation", "Закрытый ордер удерживает резерв", order_id)
        bought = fill_quantities[(order_id, "buy")]
        sold = fill_quantities[(order_id, "sell")]
        order_side = str(order.get("side", "spread"))
        filled = sold if order_side == "sell" else bought
        if order["status"] in ("accepted", "partially_filled", "filled") and not filled:
            issue("order_without_fills", "Принятый ордер не содержит исполнений", order_id)
        if order_side == "spread" and bought != sold:
            issue("unmatched_spread_legs", "Объёмы двух исполненных сторон не совпадают", order_id)
        if order_side == "buy" and sold:
            issue("unexpected_fill_side", "Покупка содержит исполнение продажи", order_id)
        if order_side == "sell" and bought:
            issue("unexpected_fill_side", "Продажа содержит исполнение покупки", order_id)
        if filled != number(order.get("filled_quantity", "0"), order_id):
            issue("order_fill_mismatch", "Объём ордера расходится с исполнениями", order_id)
        if order["status"] in ("rejected", "failed", "created", "accepted") and filled:
            issue("order_status_mismatch", "Статус ордера не соответствует исполнениям", order_id)
        remaining = number(order.get("remaining_notional_usd", "0"), order_id)
        if order["status"] == "filled" and remaining != 0:
            issue(
                "order_status_mismatch",
                "Исполненный ордер содержит неисполненный остаток",
                order_id,
            )
    totals: dict[str, Decimal] = {}
    for balance in balances:
        asset = str(balance["asset"])
        available, held = number(balance["available"]), number(balance["reserved"])
        total = available + held
        totals[asset] = total
        if min(available, held) < 0:
            issue("negative_balance", "Доступный баланс или резерв отрицателен", asset=asset)
        if initial and total != expected[asset]:
            issue(
                "balance_fill_mismatch",
                "Баланс не совпадает с начальным остатком и исполнениями",
                asset=asset,
            )
        if held != reserved[asset]:
            issue("reservation_mismatch", "Резерв не совпадает с открытыми ордерами", asset=asset)
        if "total" in balance and number(balance["total"]) != total:
            issue(
                "balance_total_mismatch",
                "Итог не равен доступному балансу плюс резерв",
                asset=asset,
            )
    for asset in expected:
        if asset not in totals and expected[asset] != 0:
            issue("missing_balance", "Для актива отсутствует строка баланса", asset=asset)
    for position in positions:
        asset = str(position.get("asset", str(position.get("symbol", "")).split("/")[0]))
        if number(position["quantity"]) != totals.get(asset, Decimal(0)):
            issue("position_fill_mismatch", "Позиция не совпадает с остатком актива", asset=asset)
    if "fees_paid_usd" in account and number(account["fees_paid_usd"]) != fees:
        issue("fees_total_mismatch", "Сумма комиссий расходится с исполнениями")
    if "slippage_paid_usd" in account and number(account["slippage_paid_usd"]) != slippage:
        issue("slippage_total_mismatch", "Резерв проскальзывания расходится с исполнениями")
    reconstructed_inventory: dict[str, tuple[Decimal, Decimal]] = {}
    reconstructed_pnl = Decimal(0)
    fills_by_order: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for fill in fills:
        fills_by_order[str(fill["order_id"])].append(fill)
    for order in sorted(orders, key=lambda item: int(item.get("timestamp_ms", 0))):
        order_id = str(order["order_id"])
        side = str(order.get("side", "spread"))
        order_fills = fills_by_order[order_id]
        if side == "spread":
            for fill in order_fills:
                cash = number(fill.get("notional_usd", "0"), order_id)
                fee = number(fill.get("fee_usd", "0"), order_id)
                slip = number(fill.get("slippage_cost_usd", "0"), order_id)
                signed_cash = cash if fill.get("side") == "sell" else -cash
                reconstructed_pnl += signed_cash - fee - slip
            continue
        asset = str(order["symbol"]).split("/")[0]
        quantity, cost = reconstructed_inventory.get(asset, (Decimal(0), Decimal(0)))
        for fill in order_fills:
            fill_quantity = number(fill.get("quantity", "0"), order_id)
            notional = number(fill.get("notional_usd", "0"), order_id)
            fee = number(fill.get("fee_usd", "0"), order_id)
            slip = number(fill.get("slippage_cost_usd", "0"), order_id)
            if side == "buy":
                quantity += fill_quantity
                cost += notional + fee + slip
            elif fill_quantity > quantity or quantity <= 0:
                issue(
                    "strategy_inventory_mismatch",
                    "Продажа превышает позицию, открытую стратегией",
                    order_id,
                )
            else:
                released = cost / quantity * fill_quantity
                quantity -= fill_quantity
                cost -= released
                reconstructed_pnl += notional - fee - slip - released
        reconstructed_inventory[asset] = (quantity, cost)
    if (
        initial
        and "realized_pnl_usd" in account
        and number(account["realized_pnl_usd"]) != reconstructed_pnl
    ):
        issue(
            "realized_pnl_mismatch",
            "Реализованный результат расходится с денежным потоком",
        )
    if "strategy_inventory" in account:
        saved_inventory = account.get("strategy_inventory", {})
        for asset in set(reconstructed_inventory) | set(saved_inventory):
            saved = saved_inventory.get(asset, {})
            actual = reconstructed_inventory.get(asset, (Decimal(0), Decimal(0)))
            if (
                number(saved.get("quantity", "0"), asset) != actual[0]
                or number(saved.get("cost_usdt", "0"), asset) != actual[1]
            ):
                issue(
                    "strategy_inventory_mismatch",
                    "Позиция стратегии расходится с журналом исполнений",
                    asset=asset,
                )
    return {
        "status": "ok" if not issues else "error",
        "issues": [asdict(i) for i in issues],
        "issue_count": len(issues),
        "account_id": account.get("account_id", account.get("id")),
        "timestamp": timestamp,
        "checked_at": timestamp,
        "correlation_id": str(account.get("account_id", account.get("id", ""))),
    }
