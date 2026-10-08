"""Probe a direct backend using five bounded, public GET requests only."""

import argparse
import asyncio
import json

import httpx

from quant_trading_platform.signal_watch.readiness import check_readiness


async def run(origin: str) -> bool:
    async with httpx.AsyncClient(trust_env=False) as client:
        report = await check_readiness(origin, client=client)
    print(json.dumps(report, indent=2))
    return report['status'] == 'ready'


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--origin', default='http://127.0.0.1:8000')
    args = parser.parse_args()
    raise SystemExit(0 if asyncio.run(run(args.origin)) else 1)
