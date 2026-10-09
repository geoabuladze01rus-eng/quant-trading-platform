"""Direct decision/risk engine and durable notification outbox for local paper bots."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import asdict, dataclass
from decimal import Decimal
from time import monotonic_ns, time_ns
from typing import Literal, cast

from quant_trading_platform.okx_controller.manager import BotManager, ControlError, atomic, money
from quant_trading_platform.signal_watch.engine import Frame
from quant_trading_platform.signal_watch.journal import Candidate
from quant_trading_platform.signal_watch.notifications import NotificationRouter

Decision = Literal["EXECUTE", "IGNORE", "STOP", "CLOSE"]


@dataclass(frozen=True)
class Signal:
    signal_id: str
    symbol: str
    direction: str
    confidence: str
    score: Decimal
    rr: Decimal
    market_state: str
    volatility: Decimal
    atr_pct: Decimal
    trend_strength: Decimal
    notional_usd: Decimal
    risk_usd: Decimal
    timestamp_ms: int
    valid_until_ms: int | None = None

    def __post_init__(self) -> None:
        for value in (
            self.score,
            self.rr,
            self.volatility,
            self.atr_pct,
            self.trend_strength,
            self.notional_usd,
            self.risk_usd,
        ):
            money(value)
        if (
            not self.signal_id.strip()
            or len(self.signal_id) > 128
            or self.symbol not in ("BTC/USDT", "ETH/USDT", "SOL/USDT")
            or self.direction not in ("LONG", "SHORT", "STOP", "CLOSE")
            or type(self.timestamp_ms) is not int
            or self.timestamp_ms <= 0
            or self.score > 100
            or max(self.volatility, self.atr_pct, self.trend_strength) > 1
        ):
            raise ValueError("Invalid paper execution signal")
        if self.valid_until_ms is not None and (
            type(self.valid_until_ms) is not int or self.valid_until_ms <= 0
        ):
            raise ValueError("Invalid signal deadline")


@dataclass(frozen=True)
class RiskLimits:
    daily_risk_usd: Decimal = Decimal("200")
    max_exposure_usd: Decimal = Decimal("1000")
    max_drawdown: Decimal = Decimal(".10")
    min_rr: Decimal = Decimal("2")
    cooldown_ms: int = 60_000
    max_signal_age_ms: int = 1_000

    def __post_init__(self) -> None:
        for value in (self.daily_risk_usd, self.max_exposure_usd, self.max_drawdown, self.min_rr):
            money(value, positive=True)
        if (
            self.max_drawdown > 1
            or type(self.cooldown_ms) is not int
            or self.cooldown_ms < 0
            or type(self.max_signal_age_ms) is not int
            or self.max_signal_age_ms <= 0
        ):
            raise ValueError("Invalid risk limits")


@dataclass(frozen=True)
class ExecutionResult:
    event_id: str
    signal_id: str
    bot_id: str | None
    decision: Decision
    reason: str
    execution_result: str
    delivery_result: str
    latency_us: int
    paper_only: bool = True
    live_execution: bool = False


class ExecutionEngine:
    def __init__(
        self,
        manager: BotManager,
        router: NotificationRouter,
        *,
        limits: RiskLimits | None = None,
        clock: Callable[[], int] = lambda: time_ns() // 1_000_000,
    ) -> None:
        if manager.connection is not router.journal.connection:
            raise ValueError("Control, risk, audit and router must share one database")
        self.manager, self.router, self.limits, self.clock = (
            manager,
            router,
            limits or RiskLimits(),
            clock,
        )
        self.connection = manager.connection
        with atomic(self.connection):
            self.connection.execute(
                "CREATE TABLE IF NOT EXISTS bot_action_events ("
                "signal_id TEXT PRIMARY KEY, event_id TEXT UNIQUE, payload TEXT NOT NULL, "
                "delivery_result TEXT NOT NULL, message TEXT NOT NULL)"
            )
            self.connection.execute(
                "CREATE TABLE IF NOT EXISTS bot_execution_audit ("
                "event_id TEXT PRIMARY KEY, timestamp INTEGER NOT NULL, payload TEXT NOT NULL)"
            )
            self.connection.execute(
                "CREATE TABLE IF NOT EXISTS bot_daily_risk ("
                "day INTEGER PRIMARY KEY, used TEXT NOT NULL)"
            )

    def result(self, signal_id: str) -> ExecutionResult | None:
        row = self.connection.execute(
            "SELECT payload, delivery_result FROM bot_action_events " "WHERE signal_id=?",
            (signal_id,),
        ).fetchone()
        if not row:
            return None
        payload = cast(dict[str, object], json.loads(row[0]))
        payload["delivery_result"] = row[1]
        return ExecutionResult(**payload)  # type: ignore[arg-type]

    def _decision(self, signal: Signal, now: int) -> tuple[Decision, str, str | None]:
        if not 0 <= now - signal.timestamp_ms <= self.limits.max_signal_age_ms or (
            signal.valid_until_ms is not None and now > signal.valid_until_ms
        ):
            return "IGNORE", "stale_signal", None
        active = self.connection.execute(
            "SELECT b.bot_id FROM paper_bots b LEFT JOIN "
            "paper_bot_allocations a USING(bot_id) WHERE b.symbol=? AND "
            "(b.status IN ('RUNNING','PAUSED') OR a.bot_id IS NOT NULL) ORDER BY b.bot_id",
            (signal.symbol,),
        ).fetchall()
        if signal.direction in ("STOP", "CLOSE") or signal.market_state == "risk_off":
            if len(active) != 1:
                return "IGNORE", "no_unique_active_bot", None
            return ("CLOSE" if signal.direction == "CLOSE" else "STOP"), "risk_exit", active[0][0]
        if signal.direction != "LONG":
            return "IGNORE", "spot_long_only", None
        if (
            signal.confidence not in ("HIGH", "VERY_HIGH")
            or signal.score < 72
            or (signal.confidence == "HIGH" and signal.score >= 84)
            or (signal.confidence == "VERY_HIGH" and signal.score < 84)
        ):
            return "IGNORE", "confidence", None
        if signal.rr < self.limits.min_rr:
            return "IGNORE", "rr", None
        if signal.market_state not in ("trend", "range"):
            return "IGNORE", "market_state", None
        if not 0 < signal.atr_pct <= Decimal(".03") or signal.volatility > Decimal(".05"):
            return "IGNORE", "volatility", None
        if (
            signal.notional_usd <= 0
            or signal.risk_usd <= 0
            or signal.risk_usd > signal.notional_usd
        ):
            return "IGNORE", "invalid_size", None
        if active:
            return "IGNORE", "position_overlap", active[0][0]
        strategy = (
            "DCA"
            if signal.market_state == "trend"
            and signal.trend_strength >= Decimal(".6")
            and signal.volatility <= Decimal(".03")
            else "GRID"
        )
        row = self.connection.execute(
            "SELECT bot_id, status, last_action_ms FROM paper_bots "
            "WHERE symbol=? AND strategy=?",
            (signal.symbol, strategy),
        ).fetchone()
        if row is None or row[1] != "STOPPED":
            return "IGNORE", "bot_state", None
        bot_id = str(row[0])
        if row[2] and now - row[2] < self.limits.cooldown_ms:
            return "IGNORE", "cooldown", bot_id
        drawdown = self.connection.execute(
            "SELECT value FROM paper_bot_risk WHERE key='drawdown'"
        ).fetchone()
        if drawdown and Decimal(drawdown[0]) >= self.limits.max_drawdown:
            return "IGNORE", "drawdown", bot_id
        day = now // 86_400_000
        risk = self.connection.execute(
            "SELECT used FROM bot_daily_risk WHERE day=?", (day,)
        ).fetchone()
        if (
            Decimal(risk[0]) if risk else Decimal(0)
        ) + signal.risk_usd > self.limits.daily_risk_usd:
            return "IGNORE", "daily_risk", bot_id
        exposure = sum(
            (
                Decimal(row[0])
                for row in self.connection.execute("SELECT notional FROM paper_bot_allocations")
            ),
            Decimal(0),
        )
        if exposure + signal.notional_usd > self.limits.max_exposure_usd:
            return "IGNORE", "max_exposure", bot_id
        return "EXECUTE", "qualified_signal", bot_id

    def process(self, signal: Signal) -> ExecutionResult:
        start = monotonic_ns()
        now = self.clock()
        with atomic(self.connection):
            existing = self.result(signal.signal_id)
            if existing:
                return existing
            decision, reason, bot_id = self._decision(signal, now)
            event_id = hashlib.sha256(("bot-action:" + signal.signal_id).encode()).hexdigest()
            response: dict[str, object] = {"paper_only": True, "live_execution": False}
            execution_result = "PAPER_IGNORED"
            if decision != "IGNORE" and bot_id is not None:
                try:
                    if decision == "EXECUTE":
                        response = self.router.dispatch_paper_control(
                            event_id,
                            signal.signal_id,
                            bot_id,
                            lambda: self.manager.start_bot(
                                bot_id, signal_id=signal.signal_id, notional=signal.notional_usd
                            ),
                        )
                        day = now // 86_400_000
                        used = self.connection.execute(
                            "SELECT used FROM bot_daily_risk WHERE day=?", (day,)
                        ).fetchone()
                        total = (Decimal(used[0]) if used else Decimal(0)) + signal.risk_usd
                        self.connection.execute(
                            "INSERT OR REPLACE INTO bot_daily_risk VALUES (?,?)", (day, str(total))
                        )
                    elif decision == "STOP":
                        response = self.router.dispatch_paper_control(
                            event_id,
                            signal.signal_id,
                            bot_id,
                            lambda: self.manager.stop_bot(bot_id, signal_id=signal.signal_id),
                        )
                    else:
                        response = self.router.dispatch_paper_control(
                            event_id,
                            signal.signal_id,
                            bot_id,
                            lambda: self.manager.close_position_if_supported(
                                bot_id, signal_id=signal.signal_id
                            ),
                        )
                    execution_result = "PAPER_APPLIED"
                except ControlError:
                    decision, reason, execution_result = "IGNORE", "control_failed", "PAPER_FAILED"
                    response = {
                        "error": "control_failed",
                        "paper_only": True,
                        "live_execution": False,
                    }
            result = ExecutionResult(
                event_id,
                signal.signal_id,
                bot_id,
                decision,
                reason,
                execution_result,
                "PENDING",
                (monotonic_ns() - start) // 1000,
            )
            audit = {
                "timestamp": now,
                "symbol": signal.symbol,
                "reason": reason,
                "signal_score": str(signal.score),
                "confidence": signal.confidence,
                "bot_action": decision,
                "okx_response": response,
                "signal_id": signal.signal_id,
                "bot_id": bot_id,
                "execution_result": execution_result,
                "paper_only": True,
                "live_execution": False,
            }
            self.connection.execute(
                "INSERT INTO bot_execution_audit VALUES (?,?,?)", (event_id, now, json.dumps(audit))
            )
            message = json.dumps(asdict(result) | audit, ensure_ascii=False)
            self.connection.execute(
                "INSERT INTO bot_action_events VALUES (?,?,?,?,?)",
                (
                    signal.signal_id,
                    event_id,
                    json.dumps(asdict(result)),
                    "PENDING",
                    "PAPER ONLY · Direct OKX control v5\n" + message,
                ),
            )
            return result

    def consume_candidate(
        self,
        candidate: Candidate,
        frame: Frame,
        *,
        valid_until_ms: int,
        healthy: bool,
    ) -> ExecutionResult:
        risk_distance = frame.close - frame.support
        reward_distance = frame.resistance - frame.close
        rr = (
            reward_distance / risk_distance
            if risk_distance > 0 and reward_distance > 0
            else Decimal(0)
        )
        # No invented target or RR for a breakout above the last observed resistance.
        signal = Signal(
            candidate.signal_id,
            candidate.asset,
            "LONG",
            candidate.result.confidence.replace(" ", "_"),
            candidate.result.score,
            rr,
            (
                (
                    "risk_off"
                    if (frame.high - frame.low) / frame.close > Decimal(".05")
                    else candidate.market_regime
                )
                if healthy
                else "unavailable"
            ),
            (frame.high - frame.low) / frame.close,
            frame.atr / frame.close,
            max(Decimal(0), frame.trend),
            Decimal("100"),
            Decimal("10"),
            self.clock(),
            valid_until_ms,
        )
        return self.process(signal)

    async def deliver_pending(self) -> None:
        # Delivery is separated from local control latency; never repeat the bot action.
        rows = self.connection.execute(
            "SELECT signal_id, message FROM bot_action_events "
            "WHERE delivery_result='PENDING' ORDER BY rowid LIMIT 100"
        ).fetchall()
        for signal_id, text in rows:
            delivered = await self.router.route("bot-action:" + signal_id, text)
            with atomic(self.connection):
                self.connection.execute(
                    "UPDATE bot_action_events SET delivery_result=? " "WHERE signal_id=?",
                    (delivered.delivery_status, signal_id),
                )

    def snapshot(self) -> list[dict[str, object]]:
        ids = self.connection.execute(
            "SELECT signal_id FROM bot_action_events " "ORDER BY rowid DESC LIMIT 100"
        ).fetchall()
        return [asdict(result) for row in ids if (result := self.result(row[0])) is not None]
