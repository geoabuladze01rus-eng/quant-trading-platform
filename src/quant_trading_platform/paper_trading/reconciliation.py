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
        issues.append(
            ReconciliationIssue(
                code, text, asset or correlation or "account", timestamp, "error", correlation
            )
        )

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
    if account.get("account_kind") == "okx_spot" and any(
        asset != "USDT" and number(value) != 0 for asset, value in initial.items()
    ):
        issue(
            "unexpected_spot_seed_inventory",
            "Спотовый счёт должен начинать с нулевого количества монет",
        )
    expected = defaultdict(
        lambda: Decimal(0), {asset: number(value) for asset, value in initial.items()}
    )
    reserved: dict[str, Decimal] = defaultdict(lambda: Decimal(0))
    by_order = {str(order["order_id"]): order for order in orders}
    fill_quantities: dict[tuple[str, str], Decimal] = defaultdict(lambda: Decimal(0))
    fees = Decimal(0)
    slippage = Decimal(0)
    spot_inventory: dict[str, tuple[Decimal, Decimal]] = {}
    spot_pnl = Decimal(0)
    spot_daily_pnl = Decimal(0)
    # Replay cash and fee arithmetic in commit order, matching the ledger. Even
    # Decimal additions are not associative at finite precision for fractional fills.
    for fill in reversed(fills):
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
        if account.get("account_kind") == "okx_spot":
            price = number(fill.get("price"), order_id)
            if (
                quantity <= 0
                or notional <= 0
                or price <= 0
                or (quantity > 0 and price != notional / quantity)
            ):
                issue(
                    "spot_fill_price_mismatch",
                    "Цена и объём спотового исполнения расходятся",
                    order_id,
                )
            if (
                fill.get("venue") != "okx"
                or fill.get("symbol") not in ("BTC/USDT", "ETH/USDT", "LTC/USDT")
                or fill.get("side") != order.get("side")
                or order.get("strategy") != "okx_spot_paper"
                or fill.get("symbol") != order.get("symbol")
                or fill.get("account_id") != account.get("id")
            ):
                issue(
                    "spot_fill_identity_mismatch",
                    "Исполнение не соответствует спотовому ордеру",
                    order_id,
                )
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
    if account.get("account_kind") == "okx_spot":
        # Storage returns newest first. Rebuild the acquired inventory and its
        # average cost in durable insertion order, independent of account metadata.
        for fill in reversed(fills):
            order_id = str(fill["order_id"])
            base = str(fill["symbol"]).split("/")[0]
            quantity = number(fill["quantity"], order_id)
            notional = number(fill["notional_usd"], order_id)
            fee = number(fill.get("fee_usd", "0"), order_id)
            slip = number(fill.get("slippage_cost_usd", "0"), order_id)
            held, basis = spot_inventory.get(base, (Decimal(0), Decimal(0)))
            if fill["side"] == "buy":
                spent = notional + fee + slip
                spot_inventory[base] = (held + quantity, basis + spent)
            elif fill["side"] == "sell":
                if quantity > held or held <= 0:
                    issue("unfunded_spot_sale", "Продажа превышает купленный актив", order_id)
                    continue
                sold_basis = basis if quantity == held else basis * quantity / held
                proceeds = notional - fee - slip
                realized = proceeds - sold_basis
                spot_pnl += realized
                try:
                    fill_day = (
                        datetime.fromtimestamp(
                            int(number(fill["timestamp_ms"], order_id)) / 1000, UTC
                        )
                        .date()
                        .isoformat()
                    )
                except (OverflowError, ValueError):
                    issue("invalid_fill_time", "Некорректное время исполнения", order_id)
                    fill_day = None
                if fill_day == account.get("spot_day_utc"):
                    spot_daily_pnl += realized
                spot_inventory[base] = (held - quantity, basis - sold_basis)
        saved_inventory = account.get("spot_inventory", {})
        for asset in set(spot_inventory) | set(saved_inventory):
            quantity, basis = spot_inventory.get(asset, (Decimal(0), Decimal(0)))
            saved = saved_inventory.get(asset, {})
            if quantity != number(saved.get("quantity", "0")) or basis != number(
                saved.get("cost_usdt", "0")
            ):
                issue("spot_inventory_mismatch", "Учётная стоимость актива расходится", asset=asset)
            unit_basis = account.get("cost_basis", {}).get(asset)
            if quantity > 0 and (unit_basis is None or number(unit_basis) != basis / quantity):
                issue(
                    "spot_cost_basis_mismatch",
                    "Средняя стоимость актива не совпадает с исполнениями",
                    asset=asset,
                )
        if account.get("spot_day_utc") and spot_daily_pnl != number(
            account.get("spot_daily_pnl_usdt", "0")
        ):
            issue("spot_daily_pnl_mismatch", "Дневной результат расходится")
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
        spot_order = order.get("strategy") == "okx_spot_paper"
        filled = bought if order.get("side") == "buy" else sold if spot_order else bought
        if order["status"] in ("accepted", "partially_filled", "filled") and not filled:
            issue("order_without_fills", "Принятый ордер не содержит исполнений", order_id)
        if spot_order and (bought and sold or not (bought or sold) and order["status"] == "filled"):
            issue("invalid_spot_legs", "Некорректные стороны спотового исполнения", order_id)
        elif not spot_order and bought != sold:
            issue("unmatched_spread_legs", "Объёмы двух исполненных сторон не совпадают", order_id)
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
    position_assets = set()
    for position in positions:
        asset = str(position.get("asset", str(position.get("symbol", "")).split("/")[0]))
        position_assets.add(asset)
        if number(position["quantity"]) != totals.get(asset, Decimal(0)):
            issue("position_fill_mismatch", "Позиция не совпадает с остатком актива", asset=asset)
        if (
            account.get("account_kind") == "okx_spot"
            and totals.get(asset, Decimal(0)) > 0
            and number(position.get("cost_basis"))
            != number(account.get("cost_basis", {}).get(asset))
        ):
            issue(
                "spot_position_cost_mismatch",
                "Стоимость позиции не совпадает с учётом актива",
                asset=asset,
            )
    if account.get("account_kind") == "okx_spot":
        for asset, total in totals.items():
            if asset != "USDT" and total > 0 and asset not in position_assets:
                issue(
                    "missing_spot_position", "Открытая позиция отсутствует в журнале", asset=asset
                )
    if "fees_paid_usd" in account and number(account["fees_paid_usd"]) != fees:
        issue("fees_total_mismatch", "Сумма комиссий расходится с исполнениями")
    if "slippage_paid_usd" in account and number(account["slippage_paid_usd"]) != slippage:
        issue("slippage_total_mismatch", "Резерв проскальзывания расходится с исполнениями")
    if initial and "realized_pnl_usd" in account:
        pnl = (
            spot_pnl
            if account.get("account_kind") == "okx_spot"
            else expected["USDT"] - number(initial.get("USDT", "0"))
        )
        if number(account["realized_pnl_usd"]) != pnl:
            issue("realized_pnl_mismatch", "Реализованный результат расходится с денежным потоком")
    return {
        "status": "ok" if not issues else "error",
        "issues": [asdict(i) for i in issues],
        "issue_count": len(issues),
        "account_id": account.get("account_id", account.get("id")),
        "timestamp": timestamp,
        "checked_at": timestamp,
        "correlation_id": str(account.get("account_id", account.get("id", ""))),
    }
