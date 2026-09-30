# Прозрачные индикаторы и проект экономических событий

## Существующая стратегия

directional.py использует midpoint observations публичного стакана. SMA12/SMA48: momentum=(SMA12/SMA48-1)*100; threshold±0.12%. Если trend vote=0, mean-reversion по deviation=(last/SMA48-1)*100 с threshold±0.35%. Trend имеет приоритет. Expected move=max(abs(momentum),abs(deviation)); net=edge-2*(fee+slippage), defaults0.10%/0.05% per-side. Это эвристическая оценка, не статистически обоснованный прогноз.

Ограничения: observations, не свечи/фиксированное время; cadence зависит от REST latency; 240 samples in-memory; warmup после restart; max deviation как estimated edge может завышать ожидание; короткий rolling walk-forward не доказывает прибыльность. Terminal valuation не закрытая сделка. Buy-and-hold нужно сравнивать на том же периоде, бюджете и издержках, отдельно seed holdings. Гейт нельзя ослаблять ради торговли.

## Минимальный следующий набор (пока предложение)

1. SMA12/48 на закрытых одноминутных свечах: входы close и UTC close time, ни одного текущего незакрытого бара; те же явные thresholds до отдельной валидации.
2. ATR14: входы high/low/previous close закрытых баров, Wilder smoothing; нормализованный ATR/price только фильтр волатильности/размера, не прогноз направления. Порог заранее фиксировать, не подбирать на test.
3. Spread/depth и реализованные fees/slippage: VWAP по всей требуемой глубине, instrument rules и freshness. Это исполнимость, не alpha.
Не добавлять AI-signals. Для каждого решения публиковать input range/source/time/age/parameters, formula values, gross/cost/net, final risk result и отказ. Без достаточных входов HOLD.

## Новости — компонент пока отсутствует и выключен

В src #18 не найдено получения news/RSS/calendar. Исторические ценовые индикаторы не называют анализом новостей.
Предлагается scheduled event blackout, не directional prediction.

| Кандидат | Проверяемость | Доступность/лицензия до интеграции |
|---|---|---|
| Federal Reserve FOMC calendar/statements на federalreserve.gov | Официальная публикация/календарь | Проверить текущие terms, частоту обновлений, RSS/API availability и redistribution; в этой сессии не проверены |
| BLS releases schedule на bls.gov | Официальные CPI/employment release times | Проверить API terms/rate limits/даты изменений и timezone; не считать website availability гарантией |
| BEA release schedule на bea.gov | GDP/PCE official schedule | Проверить доступность/calendar contract/terms |
| ECB monetary-policy calendar на ecb.europa.eu | Официальные даты/timezone | Проверить reuse conditions/историю corrections |
| Коммерческий calendar vendor | SLA/временные метки возможны | Не использовать без лицензии и подтверждённого исторического point-in-time доступа |

Ни один источник не признан надёжно подключённым этим документом. До fixtures/contract/API проверки NEWS_GATE_ENABLED=false; отсутствие компонента видно в UI.

Event schema: stable source/event ID, source URL, category/country/currency, scheduled_at_utc, first_seen_at_utc, published_at_utc (nullable), fetched_at_utc, revision, original timezone, importance from disclosed mapping. Отдельно latency=fetched-published, freshness и calendar valid-through. Не восстанавливать historical first_seen по сегодняшнему revised timestamp.

Dedup: source stable ID, затем normalized category/scheduled time; cross-source match хранит обе provenance, corrections append-only. Публикационный delay/перенос времени пересчитывает window; missing timestamp/unknown timezone не допускается как fresh событие.

Предложение policy: новые paper BUY запрещены в явно заданном window event time minus15min / plus15min для CPI, employment, FOMC/ECB decision; parameters требуют утверждённой offline validation, не реализованы. Выход из позиции и reconciliation продолжают собственные safety gates. Событие не означает BUY/SELL. При включённом news gate outage/stale calendar блокирует новые входы fail closed; при компоненте disabled UI явно пишет «новостной фильтр не подключён».

До включения: recorded point-in-time fixtures, delay/correction/DST/dedup/outage tests, запрет future information, replay и лицензия. Реальных HTTP/news tests в этой сессии нет. Это следующий этап.
