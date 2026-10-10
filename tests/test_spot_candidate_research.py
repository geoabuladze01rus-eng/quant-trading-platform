import asyncio
import tempfile
import unittest
from pathlib import Path

from quant_trading_platform.market_data.spot_candidate_research import (
    SpotCandidateResearchService,
)
from quant_trading_platform.notifications.dry_run_journal import append_record
from quant_trading_platform.strategies.spot_momentum import RESEARCH_SYMBOLS, DailyCandle
from spot_fixtures import NOW_MS, base_inputs


class FakeSource:
    """Serves the synthetic setup from test_spot_signal_generator for every symbol."""

    def __init__(self, failing: frozenset[str] = frozenset()) -> None:
        self.failing = failing
        inputs = base_inputs()
        self._by_bar: dict[str, tuple[DailyCandle, ...]] = {
            "1Dutc": inputs["candles_1d"],  # type: ignore[dict-item]
            "4H": inputs["candles_4h"],  # type: ignore[dict-item]
            "1H": inputs["candles_1h"],  # type: ignore[dict-item]
            "15m": inputs["candles_15m"],  # type: ignore[dict-item]
        }

    def fetch(self, symbol: str, *, now_ms: int, bar: str = "1Dutc") -> tuple[DailyCandle, ...]:
        if symbol in self.failing:
            raise ValueError("OKX public candles unavailable")
        return self._by_bar[bar]


def poll(service: SpotCandidateResearchService) -> None:
    asyncio.run(service.poll_once(now_ms=NOW_MS))


class SpotCandidateResearchTests(unittest.TestCase):
    def test_no_poll_yet_reports_no_data(self) -> None:
        snapshot = SpotCandidateResearchService(FakeSource()).snapshot(now_ms=NOW_MS)
        self.assertEqual(snapshot["status"], "no_data")

    def test_candidates_never_deliverable_without_news_filter(self) -> None:
        service = SpotCandidateResearchService(FakeSource())
        poll(service)
        snapshot = service.snapshot(now_ms=NOW_MS)
        self.assertEqual(snapshot["status"], "ok")
        self.assertEqual(snapshot["news_checked"], False)
        self.assertEqual(snapshot["deliverable_count"], 0)
        rows = snapshot["candidates"]
        self.assertEqual(len(rows), len(RESEARCH_SYMBOLS))  # type: ignore[arg-type]
        for row in rows:  # type: ignore[union-attr]
            self.assertFalse(row["deliverable"])
            self.assertIn("новостной фильтр не проверен", " ".join(row["reasons"]))

    def test_failed_pair_is_unavailable_and_others_still_reported(self) -> None:
        service = SpotCandidateResearchService(FakeSource(failing=frozenset({"ETH/USDT"})))
        poll(service)
        snapshot = service.snapshot(now_ms=NOW_MS)
        self.assertEqual(snapshot["status"], "partial")
        by_symbol = {r["symbol"]: r for r in snapshot["candidates"]}  # type: ignore[union-attr]
        self.assertEqual(by_symbol["ETH/USDT"]["status"], "unavailable")
        self.assertEqual(by_symbol["BTC/USDT"]["status"], "ok")

    def test_old_snapshot_is_stale(self) -> None:
        service = SpotCandidateResearchService(FakeSource())
        poll(service)
        later = NOW_MS + 3 * 3600 * 1000 + 10_000
        self.assertEqual(service.snapshot(now_ms=later)["status"], "stale")

    def test_snapshot_is_journal_serialisable(self) -> None:
        service = SpotCandidateResearchService(FakeSource())
        poll(service)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "journal.jsonl"
            append_record(path, service.snapshot(now_ms=NOW_MS), recorded_at_ms=NOW_MS)
            self.assertIn('"telegram"', path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
