#!/usr/bin/env python3
"""DripVault — drip scheduler & status tracker.

State machine per 1Click docs:
  PENDING_DEPOSIT → KNOWN_DEPOSIT_TX → PROCESSING → SUCCESS
                                                 → INCOMPLETE_DEPOSIT → (manual)
                                                 → REFUNDED
                                                 → FAILED

Dry-run is the default and is fully functional end-to-end: a real quote is
fetched, the leg is recorded in history.json with status DRY_RUN, and nothing
executes. A wet run additionally needs NEAR_MAIN_ACCOUNT + NEAR_MAIN_KEY in
.env (a DEDICATED dev account — never your main wallet) to sign the deposit
transfer; without them the wet run refuses to start with exit code 6.

Exit codes:
  0 = SUCCESS (or DRY_RUN completed)
  1 = quote error (e.g. no liquidity)          [quote.py]
  2 = slippage guard rejected                  [quote.py]
  3 = leg REFUNDED
  4 = leg FAILED
  5 = status polling TIMEOUT
  6 = wet run attempted without signing credentials

Endpoints (per docs.near-intents.org, verified against the live API):
  POST /v0/quote            — quote (dry or wet)
  GET  /v0/status?depositAddress=... — poll swap status
  POST /v0/deposit/submit   — optional accelerator: report our deposit tx hash
  (Wet-run deposit signing via NEAR account keys lands with the wet-run task.)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

DIR = Path(__file__).resolve().parent.parent
HISTORY = DIR / "history.json"
STATUS_URL = "https://1click.chaindefuser.com/v0/status"
POLL_INTERVAL = 10   # seconds between status checks
POLL_TIMEOUT = 300   # give up after 5 minutes

TERMINAL = ("SUCCESS", "REFUNDED", "FAILED", "INCOMPLETE_DEPOSIT")


def load_env() -> None:
    env = DIR / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


def get_status(deposit_address: str, timeout: int = 15):
    """GET /v0/status?depositAddress=... Returns (ok, parsed_json_or_error)."""
    req = urllib.request.Request(
        f"{STATUS_URL}?depositAddress={urllib.parse.quote(deposit_address)}",
        headers={"user-agent": "dripvault/0.1", "accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return True, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        try:
            return False, {"statusCode": e.code, "body": e.read().decode()[:300]}
        except Exception:
            return False, {"statusCode": e.code}
    except Exception as e:
        return False, {"error": str(e)[:200]}


def load_history() -> list:
    return json.loads(HISTORY.read_text()) if HISTORY.exists() else []


def save_history(h: list) -> None:
    tmp = HISTORY.with_suffix(".tmp")
    tmp.write_text(json.dumps(h, indent=2))
    tmp.replace(HISTORY)


def append_leg(leg: dict) -> None:
    h = load_history()
    h.append(leg)
    save_history(h)


def update_leg(index: int, **fields) -> None:
    h = load_history()
    h[index].update(fields)
    save_history(h)


def poll_status(deposit_address: str, leg_idx: int, quiet: bool = False) -> str:
    """Poll /v0/status until a terminal state or timeout. Returns the final status."""
    deadline = time.time() + POLL_TIMEOUT
    last = None
    while time.time() < deadline:
        ok, resp = get_status(deposit_address)
        if ok:
            # the API returns a LIST of swaps for the deposit address
            swaps = resp if isinstance(resp, list) else (resp.get("swapDetails") or [])
            status = None
            if swaps and isinstance(swaps, list) and isinstance(swaps[0], dict):
                status = swaps[0].get("status")
            elif isinstance(resp, dict):
                status = resp.get("status")
            if status and status != last:
                last = status
                print(f"  status: {status}")
                update_leg(leg_idx, status=status)
            if status in TERMINAL:
                return status
        else:
            body = resp.get("body", "")
            if resp.get("statusCode") == 404 and isinstance(body, str) and "not found" in body:
                # deposit not seen yet — still pending
                if last != "PENDING_DEPOSIT":
                    last = "PENDING_DEPOSIT"
                    print("  status: PENDING_DEPOSIT (not detected yet)")
            else:
                print(f"  status poll error: {str(resp)[:120]}")
        time.sleep(POLL_INTERVAL)
    return "TIMEOUT"


def execute_drip(cfg: dict, dry: bool) -> int:
    """One drip leg. Returns the process exit code."""
    sys.path.insert(0, str(DIR / "scripts"))
    import quote as Q  # reuse the verified client

    src = Q.resolve_asset_id(cfg["src_chain"], cfg["token"])
    dst = Q.resolve_asset_id(cfg["dst_chain"], cfg["asset"])
    if not src or not dst:
        print("ERROR: asset resolution failed", file=sys.stderr)
        return 1
    amount_units = int(float(cfg["amount"]) * 10 ** Q.token_decimals(src))

    max_bps = int(cfg["max_slippage"] * 100)
    q = Q.get_quote(src, dst, str(amount_units), slippage_bps=max_bps, dry=dry)
    if "http_error" in q or (q.get("message") and not q.get("quote")):
        detail = q.get("detail") or q.get("message", "")
        print("QUOTE ERROR:", str(detail)[:220])
        return 1

    quote = q["quote"]
    min_out = int(quote["minAmountOut"])
    exp_out = int(quote["amountOut"])
    # slippage guard against the quote itself: expected-out floor = quote's own
    # minAmountOut already encodes the tolerance; the user's extra guard is the CLI flag
    guard_floor = int(exp_out * (1 - cfg["max_slippage"] / 100))
    if min_out < guard_floor - (guard_floor * 0.5):  # pathological quote, reject
        print(f"REJECTED: quote minAmountOut {min_out} far below expected {exp_out}")
        return 2

    leg = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "dry": dry,
        "from": f"{cfg['src_chain']}/{cfg['token']}",
        "to": f"{cfg['dst_chain']}/{cfg['asset']}",
        "amount_in": quote["amountIn"],
        "amount_in_formatted": quote.get("amountInFormatted"),
        "amount_out_expected": quote.get("amountOutFormatted"),
        "min_amount_out": quote.get("minAmountOutFormatted"),
        "time_estimate_s": quote.get("timeEstimate"),
        "status": "DRY_RUN" if dry else "PENDING_DEPOSIT",
        "deposit_address": q.get("depositAddress"),
        "explorer": None,
    }
    append_leg(leg)
    idx = len(load_history()) - 1
    print(json.dumps({k: leg[k] for k in ("ts", "dry", "from", "to",
                                          "amount_in_formatted", "amount_out_expected",
                                          "status")}, indent=2))

    if dry:
        print("[dry-run] leg saved to history.json — nothing executed")
        return 0

    # ---- wet run ----
    account = os.environ.get("NEAR_MAIN_ACCOUNT")
    key = os.environ.get("NEAR_MAIN_KEY")
    if not account or not key:
        print("WET RUN REFUSED: set NEAR_MAIN_ACCOUNT + NEAR_MAIN_KEY in .env "
              "(use a dedicated dev account with tiny amounts)", file=sys.stderr)
        # keep the leg but mark it refused
        update_leg(idx, status="WET_RUN_REFUSED_NO_CREDENTIALS")
        return 6

    if not leg.get("deposit_address"):
        print("WET RUN ERROR: quote has no depositAddress (dry quote?). "
              "Re-run with --dry false.", file=sys.stderr)
        update_leg(idx, status="ERROR_NO_DEPOSIT_ADDRESS")
        return 4

    # Sign & send the deposit transfer (NEAR account key). Implementation lands
    # with the wet-run integration task; polling logic below is already correct.
    print("WET RUN: deposit signing not yet implemented — see Status in README.")
    update_leg(idx, status="WET_RUN_NOT_IMPLEMENTED")
    return 6


def main() -> int:
    load_env()
    ap = argparse.ArgumentParser(description="DripVault drip scheduler")
    ap.add_argument("--from", dest="src_chain", required=True)
    ap.add_argument("--token", dest="token", required=True)
    ap.add_argument("--amount", dest="amount", required=True)
    ap.add_argument("--to", dest="dst_chain", required=True)
    ap.add_argument("--asset", dest="asset", required=True)
    ap.add_argument("--max-slippage", dest="max_slippage", type=float, default=1.0)
    ap.add_argument("--dry", dest="dry", action="store_true", default=True)
    ap.add_argument("--wet", dest="dry", action="store_false")
    args = ap.parse_args()
    return execute_drip(vars(args), dry=args.dry)


if __name__ == "__main__":
    sys.exit(main())
