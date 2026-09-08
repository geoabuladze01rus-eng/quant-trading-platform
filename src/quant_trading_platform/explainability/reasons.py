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
}


def human_reason(reason_code: str) -> str:
    """Unknown codes never imply approval or repeat potentially sensitive input."""
    return REASON_CATALOG.get(
        _ALIASES.get(reason_code, reason_code),
        "Решение требует проверки. Посмотрите код причины и журнал операций.",
    )
