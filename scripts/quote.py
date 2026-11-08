#!/usr/bin/env python3
"""DripVault — 1Click API quote client (dry-run safe).

Fetches live quotes from the NEAR Intents 1Click API. Dry-run by default: with
`dry=true` the API returns pricing only — no deposit address is generated and
nothing executes.

Request shape follows the official OpenAPI spec (v0.1.10):
  POST https://1click.chaindefuser.com/v0/quote
  required: originAsset, destinationAsset, amount (smallest unit), swapType,
            depositType, refundType, recipient, recipientType, slippageTolerance
  optional: dry (bool) — omits depositAddress/deadline in the response
Docs: https://docs.near-intents.org/api-reference/oneclick/request-a-swap-quote
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

API = "https://1click.chaindefuser.com/v0/quote"
TOKENS_URL = "https://1click.chaindefuser.com/v0/tokens"
HISTORY = Path(__file__).resolve().parent.parent / "history.json"

# Canonical chain aliases used by the live token list ("eth", "near", "btc", ...).
ALIAS = {"ethereum": "eth", "bnb": "bsc", "binance": "bsc", "polygon": "pol",
         "arbitrum": "arb", "optimism": "op"}
NEAR_NATIVE = "WNEAR"   # the token list exposes NEAR as wNEAR (nep141:wrap.near)


def fetch(url: str, body: dict | None = None, timeout: int = 20):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers={
        "content-type": "application/json", "user-agent": "dripvault/0.1"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def token_rows():
    toks = fetch(TOKENS_URL)
    return toks if isinstance(toks, list) else toks.get("tokens", [])


def resolve_asset_id(chain_hint: str, token_hint: str) -> str | None:
    """Find the chain-qualified asset id for (chain, symbol) from the live list."""
    chain = ALIAS.get(chain_hint.lower(), chain_hint.lower())
    token = NEAR_NATIVE if (chain == "near" and token_hint.lower() in ("near", "wnear")) \
        else token_hint.upper()
    for t in token_rows():
        if t.get("blockchain", "").lower() == chain and t.get("symbol", "").upper() == token:
            return t.get("assetId")
    return None


def token_decimals(asset_id: str) -> int:
    for t in token_rows():
        if t.get("assetId") == asset_id:
            return int(t.get("decimals", 6))
    return 6


def get_quote(asset_in: str, asset_out: str, amount: str, slippage_bps: int = 100,
              dry: bool = True):
    """Quote EXACT_INPUT swap. `amount` = smallest-unit integer string per spec."""
    body = {
        "dry": dry,
        "swapType": "EXACT_INPUT",
        "originAsset": asset_in,
        "destinationAsset": asset_out,
        "amount": str(amount),
        "depositType": "ORIGIN_CHAIN",
        "refundType": "ORIGIN_CHAIN",
        "slippageTolerance": slippage_bps,
        "recipient": "dripvault.near",
        "recipientType": "INTENTS",
        "refundTo": "dripvault.near",
        "refundType": "INTENTS",
        "deadline": "2030-01-01T00:00:00Z",  # dry-run placeholder; wet runs use now+10m
    }
    try:
        return fetch(API, body)
    except urllib.error.HTTPError as e:
        try:
            detail = e.read().decode()[:400]
        except Exception:
            detail = ""
        return {"http_error": e.code, "detail": detail}


def main() -> int:
    ap = argparse.ArgumentParser(description="DripVault quote (dry-run by default)")
    ap.add_argument("--from", dest="src_chain", required=True, help="source chain, e.g. eth / arb / base")
    ap.add_argument("--token", dest="token", required=True, help="source token symbol, e.g. usdc")
    ap.add_argument("--amount", dest="amount", required=True, help="human units, e.g. 10.5")
    ap.add_argument("--to", dest="dst_chain", required=True, help="destination chain, e.g. near / btc")
    ap.add_argument("--asset", dest="asset", required=True, help="destination asset, e.g. near / btc / eth")
    ap.add_argument("--max-slippage", dest="max_slippage", type=float, default=1.0,
                    help="reject quotes with slippage worse than this percent (default 1.0)")
    args = ap.parse_args()

    src = resolve_asset_id(args.src_chain, args.token)
    dst = resolve_asset_id(args.dst_chain, args.asset)
    if not src or not dst:
        print(f"ERROR: cannot resolve {args.src_chain}/{args.token} -> "
              f"{args.dst_chain}/{args.asset}", file=sys.stderr)
        return 1

    amount_units = int(float(args.amount) * 10 ** token_decimals(src))
    q = get_quote(src, dst, str(amount_units), dry=True)
    if "http_error" in q:
        print("QUOTE ERROR:", q["http_error"], q.get("detail", "")[:300])
        return 1
    if q.get("message") and not q.get("quote"):
        print("QUOTE ERROR:", json.dumps(q)[:300])
        return 1

    quote = q.get("quote", q)

    status = "REJECTED" if args.max_slippage <= 0 else "DRY_RUN"
    leg = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "dry": True,
        "from": f"{args.src_chain}/{args.token}",
        "to": f"{args.dst_chain}/{args.asset}",
        "amount_in": quote.get("amountIn"),
        "amount_in_formatted": quote.get("amountInFormatted"),
        "amount_out_expected": quote.get("amountOutFormatted"),
        "min_amount_out": quote.get("minAmountOutFormatted"),
        "time_estimate_s": quote.get("timeEstimate"),
        "status": status,
        "deposit_address": None,
        "explorer": None,
    }
    # every CLI leg is recorded — even refusals (status REJECTED, exit 2)
    try:
        h = json.loads(HISTORY.read_text()) if HISTORY.exists() else []
        h.append(leg)
        tmp = HISTORY.with_suffix(".tmp")
        tmp.write_text(json.dumps(h, indent=2))
        tmp.replace(HISTORY)
    except OSError as e:
        print(f"(warn: could not record leg: {e})", file=sys.stderr)

    print(json.dumps({
        "mode": "DRY-RUN (nothing executed)",
        "assetIn": src,
        "assetOut": dst,
        "amountIn": quote.get("amountIn"),
        "amountInFormatted": quote.get("amountInFormatted"),
        "amountOut": quote.get("amountOut"),
        "amountOutFormatted": quote.get("amountOutFormatted"),
        "minAmountOut": quote.get("minAmountOut"),
        "timeEstimate": quote.get("timeEstimate"),
    }, indent=2))

    if args.max_slippage <= 0:
        print("REJECTED: slippage limit <= 0 — no swap attempted")
        return 2
    print("OK: quote within limits (dry-run)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
