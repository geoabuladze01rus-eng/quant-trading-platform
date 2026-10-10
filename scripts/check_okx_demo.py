"""Read-only demo connectivity check; no order submission or secret output."""

import argparse
import os
from pathlib import Path

from quant_trading_platform.paper_trading.okx_demo import DemoCredentials, OKXDemoBot


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--journal", type=Path, required=True)
    args = parser.parse_args()
    credentials = DemoCredentials(
        os.environ.get("OKX_DEMO_API_KEY", ""),
        os.environ.get("OKX_DEMO_API_SECRET", ""),
        os.environ.get("OKX_DEMO_PASSPHRASE", ""),
    )
    instance = OKXDemoBot(credentials, args.journal)
    try:
        instance.check_connection()
        print("OKX demo: authenticated balance request succeeded; no order submitted")
    finally:
        instance.close()


if __name__ == "__main__":
    main()
