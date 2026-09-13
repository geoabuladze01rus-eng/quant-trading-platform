"""Stable Russian explanations for deterministic decisions (not investment advice)."""

from types import MappingProxyType

REASON_CATALOG = MappingProxyType({
    "stale_market_data": "Рыночные данные устарели. Дождитесь свежих котировок.",
    "insufficient_net_edge": "После комиссий и проскальзывания доходность ниже порога.",
    "market_mismatch": "Рынок заявки не совпадает с рынком источника данных.",
    "insufficient_paper_balance": "Недостаточно средств на учебном счёте.",
    "duplicate_idempotency_key": "Этот запрос уже обработан. Повторная заявка не создаётся.",
    "invalid_order": "Параметры учебной заявки некорректны.",
    "depth_insufficient": "В стакане недостаточно объёма для заявки.",
    "risk_limit_exceeded": "Заявка превышает установленный лимит риска.",
    "paper_order_filled": "Учебная заявка исполнена полностью. Реальных сделок нет.",
    "paper_order_partially_filled": "Учебная заявка исполнена частично. Реальных сделок нет.",
    "paper_order_rejected": "Учебная заявка отклонена проверками безопасности.",
    "paper_order_created": "Учебная заявка создана локально и передана проверкам.",
    "paper_order_accepted": "Учебная заявка прошла проверки и принята симулятором.",
    "paper_order_cancelled": "Остаток учебной заявки отменён, резервы освобождены.",
    "live_trading_locked": "Реальная торговля отключена. Доступна только учебная симуляция.",
    "reconciliation_mismatch": "Сверка выявила расхождение. Проверьте журнал учебных операций.",
    "source_unavailable": "Источник котировок недоступен. Исполнение заблокировано.",
    "unsupported_market": "Этот рынок не поддерживается для учебного исполнения.",
    "approved": "Проверки пройдены. Разрешена только учебная симуляция.",
})

_ALIASES = {
    "insufficient_edge_after_costs": "insufficient_net_edge",
    "market_type_mismatch": "market_mismatch",
    "insufficient_depth": "depth_insufficient",
    "notional_limit_exceeded": "risk_limit_exceeded",
    "venue_unavailable": "source_unavailable",
    "paper_filled": "paper_order_filled",
    "paper_partial_fill": "paper_order_partially_filled",
    "paper_cancelled": "paper_order_cancelled",
    "insufficient_balance": "insufficient_paper_balance",
    "idempotency_conflict": "duplicate_idempotency_key",
    "idempotency_in_progress": "duplicate_idempotency_key",
    "invalid_idempotency_key": "invalid_order",
    "invalid_notional": "invalid_order",
    "invalid_opportunity": "invalid_order",
    "invalid_book": "invalid_order",
    "invalid_timestamp": "stale_market_data",
    "unsupported_paper_asset": "unsupported_market",
    "account_inactive": "risk_limit_exceeded",
}


def canonical_reason_code(reason_code: str) -> str:
    """Translate legacy engine codes to the stable public Paper Alpha contract."""
    return _ALIASES.get(reason_code, reason_code)


def human_reason(reason_code: str) -> str:
    """Unknown codes never imply approval or repeat potentially sensitive input."""
    return REASON_CATALOG.get(
        canonical_reason_code(reason_code),
        "Решение требует проверки. Посмотрите код причины и журнал операций.",
    )
