"""Isolated demo worker. New entries are disabled unless explicitly enabled."""

import argparse
import fcntl
import json
import os
from pathlib import Path
from time import sleep

from quant_trading_platform.paper_trading.okx_demo import DemoCredentials, OKXDemoBot
from quant_trading_platform.paper_trading.okx_demo_runner import DemoRunner


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--journal", type=Path, required=True)
    parser.add_argument("--signal-file", type=Path)
    parser.add_argument("--enable-demo-entries", action="store_true")
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    if (
        any(
            os.environ.get(k, "false").lower() != "false"
            for k in ("LIVE_TRADING_ENABLED", "LIVE_ORDER_ACCEPTANCE_GATE")
        )
        or os.environ.get("TRADING_MODE", "paper") != "paper"
    ):
        raise SystemExit("Live gates must be false and TRADING_MODE must be paper")
    if args.enable_demo_entries and args.signal_file is None:
        parser.error("--enable-demo-entries requires --signal-file")
    args.journal.parent.mkdir(parents=True, exist_ok=True)
    # One lifecycle worker per journal, including across server restarts.
    with args.journal.with_suffix(".lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit("Another demo worker owns this journal") from None
        credentials = DemoCredentials(
            os.environ.get("OKX_DEMO_API_KEY", ""),
            os.environ.get("OKX_DEMO_API_SECRET", ""),
            os.environ.get("OKX_DEMO_PASSPHRASE", ""),
        )
        bot = OKXDemoBot(credentials, args.journal)
        runner = DemoRunner(
            bot,
            args.signal_file,
            entries_enabled=args.enable_demo_entries,
            emit=lambda item: print(
                json.dumps({"event": "demo_state_changed", **item}), flush=True
            ),
        )
        previous_status = None
        try:
            bot.check_connection()
            while True:
                result = runner.tick()
                if result["status"] != previous_status:
                    print(
                        json.dumps({"event": "demo_worker", "status": result["status"]}), flush=True
                    )
                    previous_status = result["status"]
                if args.once:
                    break
                sleep(5)
        except KeyboardInterrupt:
            pass
        finally:
            bot.close()


if __name__ == "__main__":
    main()
