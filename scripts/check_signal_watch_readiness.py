"""Probe direct or hosted backend using bounded public GETs; optionally sample collection."""

import argparse
import asyncio
import json

import httpx

from quant_trading_platform.signal_watch.acceptance import observe_collections
from quant_trading_platform.signal_watch.readiness import check_readiness


async def run(origin: str, *, hosted: bool = False, samples: int | None = None,
              interval_seconds: int = 60) -> bool:
    async with httpx.AsyncClient(trust_env=False) as client:
        if samples is None:
            report = await check_readiness(origin, client=client, hosted=hosted)
        else:
            report = await observe_collections(origin, client=client, hosted=hosted,
                                               samples=samples, interval_seconds=interval_seconds)
    print(json.dumps(report, indent=2))
    return report['status'] == 'ready'


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--origin', default='http://127.0.0.1:8000')
    parser.add_argument('--hosted', action='store_true', help='Use the fixed public /api gateway')
    parser.add_argument('--samples', type=int, help='Sample repeatedly; 16 at 60s spans 15 minutes')
    parser.add_argument('--interval-seconds', type=int, default=60)
    args = parser.parse_args()
    try:
        ready = asyncio.run(run(args.origin, hosted=args.hosted, samples=args.samples,
                                interval_seconds=args.interval_seconds))
    except ValueError:
        parser.error('Invalid origin or bounded sampling schedule')
    raise SystemExit(0 if ready else 1)
