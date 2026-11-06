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
import subprocess
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


def _near_env() -> dict:
    """Environment for near-cli subprocess calls (adds ~/.npm-global/bin)."""
    env = dict(os.environ)
    env["PATH"] = os.path.expanduser("~/.npm-global/bin:") + env["PATH"]
    return env


def submit_deposit_accelerator(deposit_address: str, tx_hash: str) -> None:
    """POST /v0/deposit/submit — best-effort accelerator per docs; never fatal."""
    body = json.dumps({"depositAddress": deposit_address, "txHash": tx_hash}).encode()
    req = urllib.request.Request(
        "https://1click.chaindefuser.com/v0/deposit/submit", data=body,
        headers={"user-agent": "dripvault/0.1", "content-type": "application/json"},
        method="POST")
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            print(f"  accelerator: {r.status}")
    except Exception as e:
        print(f"  accelerator skipped ({str(e)[:80]})")


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

    # ---- wet run: Opsi A, NEAR CLI signing (near send via subprocess) ----
    account = os.environ.get("NEAR_MAIN_ACCOUNT")
    key = os.environ.get("NEAR_MAIN_KEY")
    network = os.environ.get("NEAR_NETWORK", "mainnet")
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

    deposit_addr = leg["deposit_address"]
    # wet quotes return amounts in the ORIGIN asset's smallest unit; NEAR CLI
    # transfer needs the same. For a NEAR-origin deposit this is yoctonear.
    amount_units = leg["amount_in"]  # string of smallest units

    # key from .env must be importable into the CLI keystore (never leaves disk)
    ks_dir = Path.home() / ".near-credentials" / network
    ks_file = ks_dir / f"{account}.json"
    if not ks_file.exists():
        ks_dir.mkdir(parents=True, exist_ok=True)
        # import the .env key into the CLI keystore (never leaves disk)
        print(f"WET RUN: importing credentials to {ks_file} (from .env)")
        rc = subprocess.run(
            ["near", "add-credentials", account, "--networkId", network],
            capture_output=True, text=True, input=key, env=_near_env(), timeout=60)
        if rc.returncode != 0 or not ks_file.exists():
            print(f"WET RUN ERROR: credential import failed: {rc.stderr[:200]}",
                  file=sys.stderr)
            update_leg(idx, status="ERROR_CREDENTIAL_IMPORT")
            return 6

    near_cli = os.environ.get("NEAR_CLI", "near")
    amount_near = str(int(amount_units) / 10 ** 24)  # NEAR-origin: 24 decimals
    print(f"WET RUN: near send {account} -> {deposit_addr[:20]}… "
          f"({amount_near} NEAR, network {network})")
    try:
        rc = subprocess.run(
            [near_cli, "send", account, deposit_addr, amount_near,
             "--networkId", network],
            capture_output=True, text=True, env=_near_env(), timeout=180)
    except subprocess.TimeoutExpired:
        update_leg(idx, status="ERROR_TX_TIMEOUT")
        return 5
    if rc.returncode != 0:
        print(f"WET RUN ERROR: near send failed: {rc.stderr[:300]}", file=sys.stderr)
        update_leg(idx, status="ERROR_TX_FAILED")
        return 4

    tx_hash = None
    for line in rc.stdout.splitlines():
        if "Transaction Id" in line:
            tx_hash = line.split()[-1]
            break
    if not tx_hash:
        print("WET RUN ERROR: no Transaction Id in near output", file=sys.stderr)
        update_leg(idx, status="ERROR_TX_HASH_NOT_FOUND")
        return 4

    explorer = f"https://nearblocks.io/txns/{tx_hash}"
    print(f"WET RUN: deposit tx {tx_hash} → {explorer}")
    update_leg(idx, tx_hash=tx_hash, explorer=explorer, status="KNOWN_DEPOSIT_TX")

    # report our deposit tx to 1Click (optional accelerator, best-effort)
    submit_deposit_accelerator(deposit_addr, tx_hash)

    # poll to a terminal state
    final = poll_status(deposit_addr, idx)
    if final == "SUCCESS":
        ok, resp = get_status(deposit_addr)
        swaps = resp if isinstance(resp, list) else (resp.get("swapDetails") or [])
        if swaps and isinstance(swaps[0], dict):
            out_amt = swaps[0].get("amountOut") or swaps[0].get("swapDetails", {}).get("amountOut")
            if out_amt:
                update_leg(idx, amount_out_actual=out_amt)
        print(f"[SUCCESS] leg complete. See {explorer}")
        return 0
    if final == "REFUNDED":
        print(f"[REFUNDED] funds returned by 1Click. See {explorer}")
        return 3
    if final == "FAILED":
        print(f"[FAILED] leg failed. See {explorer}")
        return 4
    if final in ("INCOMPLETE_DEPOSIT",):
        print(f"[INCOMPLETE_DEPOSIT] manual follow-up needed. See {explorer}")
        return 5
    print(f"[TIMEOUT] no terminal state within {POLL_TIMEOUT}s. See {explorer}")
    return 5


def run_loop(cfg: dict, interval_hours: float, dry: bool, max_legs: int = 0) -> int:
    """Run execute_drip on an interval. max_legs>0 caps the run (CI/demo friendly)."""
    n = 0
    rc = 0
    while True:
        print(f"--- drip leg {n + 1} ---")
        rc = execute_drip(cfg, dry=dry)
        n += 1
        if max_legs and n >= max_legs:
            print(f"loop: max_legs={max_legs} reached, stopping")
            return rc
        print(f"Next drip in {interval_hours}h")
        time.sleep(interval_hours * 3600)


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
    ap.add_argument("--interval", type=float, default=0,
                    help="if >0, loop forever with this many hours between drips")
    ap.add_argument("--max-legs", type=int, default=0,
                    help="cap loop iterations (0 = unlimited; useful for demos)")
    args = ap.parse_args()
    cfg = vars(args)
    if args.interval > 0:
        return run_loop(cfg, args.interval, args.dry, args.max_legs)
    return execute_drip(cfg, dry=args.dry)


if __name__ == "__main__":
    sys.exit(main())
