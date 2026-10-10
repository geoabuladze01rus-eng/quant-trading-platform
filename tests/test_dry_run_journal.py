import asyncio
import json
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from quant_trading_platform.notifications.dry_run_journal import (
    append_record,
    journal_path_from_env,
    run_dry_research,
)


class FakeService:
    def __init__(self, snapshot: dict[str, object]) -> None:
        self._snapshot = snapshot
        self.polls = 0

    async def poll_once(self) -> None:
        self.polls += 1

    def snapshot(self, *, now_ms: int | None = None) -> dict[str, object]:
        return dict(self._snapshot)


class DryRunJournalTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "nested" / "dry_run.jsonl"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def read_lines(self) -> list[dict[str, object]]:
        return [json.loads(line) for line in self.path.read_text(encoding="utf-8").splitlines()]

    def test_run_appends_one_marked_record_per_poll(self) -> None:
        service = FakeService({"status": "ok", "candidates": []})
        asyncio.run(run_dry_research(service, self.path, now_ms=1_000))
        asyncio.run(run_dry_research(service, self.path, now_ms=2_000))
        records = self.read_lines()
        self.assertEqual(service.polls, 2)
        self.assertEqual(len(records), 2)
        for record in records:
            self.assertEqual(record["mode"], "dry_run")
            self.assertEqual(record["telegram"], "not_sent")
            self.assertIs(record["live_execution"], False)
        self.assertEqual([r["recorded_at_ms"] for r in records], [1_000, 2_000])

    def test_decimal_values_are_stored_as_strings(self) -> None:
        service = FakeService({"status": "ok", "stop": Decimal("80500.5")})
        asyncio.run(run_dry_research(service, self.path, now_ms=5))
        self.assertEqual(self.read_lines()[0]["stop"], "80500.5")

    def test_non_serialisable_values_are_rejected(self) -> None:
        with self.assertRaises(TypeError):
            append_record(self.path, {"bad": object()}, recorded_at_ms=1)  # type: ignore[dict-item]

    def test_missing_env_path_is_an_error(self) -> None:
        import os

        previous = os.environ.pop("DRY_RUN_JOURNAL_PATH", None)
        try:
            with self.assertRaises(SystemExit):
                journal_path_from_env()
        finally:
            if previous is not None:
                os.environ["DRY_RUN_JOURNAL_PATH"] = previous

    def test_unknown_strategy_is_rejected_before_any_fetch(self) -> None:
        import os

        from quant_trading_platform.notifications.dry_run_journal import main

        os.environ["DRY_RUN_JOURNAL_PATH"] = str(self.path)
        try:
            with self.assertRaises(SystemExit):
                main(["made-up"])
        finally:
            os.environ.pop("DRY_RUN_JOURNAL_PATH", None)
        self.assertFalse(self.path.exists())

    def test_journal_module_does_not_reference_telegram_delivery(self) -> None:
        source = Path(
            "src/quant_trading_platform/notifications/dry_run_journal.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn("telegram_signals", source)
        self.assertNotIn("TelegramSignalNotifier", source)


if __name__ == "__main__":
    unittest.main()
