#!/usr/bin/env python3
"""DripVault — schedule planner.

Given a total budget, a drip count and an interval, prints the execution plan
the scheduler will follow. Pure planning: no API calls, no funds involved.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone


def parse_interval(s: str) -> timedelta:
    unit = s[-1].lower()
    n = int(s[:-1])
    return {"h": timedelta(hours=n), "d": timedelta(days=n), "m": timedelta(minutes=n),
            "w": timedelta(weeks=n)}[unit]


def main() -> int:
    ap = argparse.ArgumentParser(description="DripVault schedule planner")
    ap.add_argument("--total", type=float, required=True, help="total budget, human units")
    ap.add_argument("--drips", type=int, required=True, help="number of drips")
    ap.add_argument("--interval", default="24h", help="interval between drips: 60m / 24h / 7d / 2w")
    ap.add_argument("--slippage", type=float, default=1.0, help="max slippage percent per drip")
    ap.add_argument("--start", default=None, help="ISO start (default: now UTC)")
    args = ap.parse_args()

    if args.drips < 1 or args.total <= 0:
        print("ERROR: --total must be > 0 and --drips >= 1", file=sys.stderr)
        return 1

    start = (datetime.fromisoformat(args.start) if args.start
             else datetime.now(timezone.utc)).astimezone(timezone.utc)
    iv = parse_interval(args.interval)
    per = args.total / args.drips

    rows = []
    for i in range(args.drips):
        t = start + iv * i
        rows.append({"n": i + 1, "at": t.isoformat(timespec="seconds"),
                     "amount": round(per, 6), "maxSlippagePct": args.slippage})
    print(json.dumps({
        "total": args.total, "drips": args.drips, "interval": args.interval,
        "perDrip": round(per, 6), "maxSlippagePct": args.slippage,
        "schedule": rows,
    }, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
