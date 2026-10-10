"""Dry-run research journal: records intraday snapshots to a local JSONL file.

No Telegram or order code is imported or called here. Each run appends one line
per poll, marked telegram="not_sent", so the output can be reviewed before any
delivery is enabled. The journal path must be set explicitly (no default), and
the file lives outside git (see .gitignore: data files are not committed).
"""

from __future__ import annotations

import asyncio
import json
import os
from decimal import Decimal
from pathlib import Path
from time import time
from typing import Protocol

JOURNAL_ENV = "DRY_RUN_JOURNAL_PATH"


class SnapshotService(Protocol):
    async def poll_once(self) -> None: ...

    def snapshot(self, *, now_ms: int | None = None) -> dict[str, object]: ...


def _encode(value: object) -> str:
    if isinstance(value, Decimal):
        return str(value)
    raise TypeError(f"not JSON serialisable: {type(value).__name__}")


def append_record(path: Path, snapshot: dict[str, object], *, recorded_at_ms: int) -> None:
    record = {
        "recorded_at_ms": recorded_at_ms,
        "mode": "dry_run",
        "telegram": "not_sent",
        "live_execution": False,
        **snapshot,
    }
    line = json.dumps(record, ensure_ascii=False, default=_encode, sort_keys=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")
        handle.flush()
        os.fsync(handle.fileno())


async def run_dry_research(
    service: SnapshotService, path: Path, *, now_ms: int | None = None
) -> dict[str, object]:
    current = int(time() * 1000) if now_ms is None else now_ms
    await service.poll_once()
    snapshot = service.snapshot(now_ms=current)
    append_record(path, snapshot, recorded_at_ms=current)
    return snapshot


def journal_path_from_env() -> Path:
    raw = os.environ.get(JOURNAL_ENV, "").strip()
    if not raw:
        raise SystemExit(f"{JOURNAL_ENV} must be set to a writable file path")
    return Path(raw)


def main() -> None:
    # Imported here so the journal logic can be tested without the HTTP client.
    from quant_trading_platform.market_data.intraday_research import IntradayResearchService
    from quant_trading_platform.market_data.okx_candles import OKXCandleSource

    path = journal_path_from_env()
    service = IntradayResearchService(OKXCandleSource())
    snapshot = asyncio.run(run_dry_research(service, path))
    print(f"dry run recorded: status={snapshot.get('status')} telegram=not_sent")


if __name__ == "__main__":
    main()
