# DripVault

Cross-chain dollar-cost-averaging (DCA) on **NEAR Intents (1Click API)** — currently a
**dry-run quote and planning CLI**; the executing scheduler is not built yet.

Planned final shape: deposit USDC from any supported chain, pick a target asset (BTC, ETH or
NEAR) and a schedule. On every interval the scheduler requests a quote from the 1Click API,
rejects quotes that beat the user's slippage limit, and executes the swap so the asset lands
in the user's own address on the target chain. Self-custodial by design: the signing key
stays on the user's machine (in their own `.env`, never in this repo) and funds rest in the
user's own account between drips.

Built for **NEAR HACKATHON** (2026-11-01 → 2026-11-14), track `defi` (Unstoppable DeFi).

## What works today (verified)

- **Live cross-chain quotes** — resolves asset ids against the live `/v0/tokens` list
  (39 chains) and fetches real quotes from the 1Click API with `dry=true`
  (no deposit address generated, nothing executes, no funds involved).
- **Slippage guard reject path** — the quote command exits with code 2 when the user's
  slippage limit is below the minimum, before anything could execute.
- **Schedule planner** — pure planning of the drip schedule (amounts + timestamps), no API calls.
- **Drip leg state machine** (dry-run) — `scripts/scheduler.py` fetches a real quote, records
  the leg in `history.json`, and refuses wet runs without dedicated signing credentials.

## What is NOT built yet

- **Wet-run deposit path** — implemented (NEAR CLI signing per Opsi A: `near send` to the
  1Click depositAddress, tx hash parsed, `/v0/deposit/submit` accelerator, then status
  polling to a terminal state). End-to-end wet run pending a funded mainnet dev account.
- **Interval loop** — `--interval 24` loops drips every 24h (`--max-legs N` caps it).
- **Web UI**. Not implemented.
- Stretch: a NEAR contract owning the vault via **Chain Signatures**. Not started.

## Custody model (plain terms)

Today nothing holds funds: the dry-run client touches no keys and no money. In the planned
final version, a **NEAR account the user controls** (its private key lives in the user's own
`.env`, never in this repo) signs each drip. The app itself holds nothing between drips —
funds sit in that user-controlled account, not in a DripVault contract. This is
key-held-by-user, not smart-contract-custodial; it is the honest scope for this build window
(see Status), with the Chain Signatures contract as the stretch goal.

Note: NEAR Intents has **no testnet** (per the hackathon FAQ Q-007). Wet runs would use
mainnet with a dedicated dev account and tiny amounts — which is also why they are deferred
until the scheduler exists.

## How NEAR fits

- **NEAR Intents / 1Click API** does the cross-chain settlement: quotes, deposit addresses,
  execution and status — no bridges, no custodian.
- Between scheduled drips, funds stay in the user-controlled NEAR account.

## Try it (dry-run, no funds needed)

Prerequisites: **Python 3.10+** only. No third-party packages — the scripts use the standard
library, so there is nothing to install.

```bash
git clone https://github.com/Azet17/dripvault.git
cd dripvault
```

1. **See a live cross-chain quote without spending anything:**
   `python3 scripts/quote.py --from eth --token usdc --amount 10 --to near --asset near`
   → *See* JSON with `amountInFormatted: "10.0"`, a live `amountOutFormatted` around 2.6x
   wNEAR, `minAmountOut`, and a `timeEstimate` (~45 seconds). The output includes
   `"mode": "DRY-RUN (nothing executed)"`.

   (`npm run quote` also works if you have Node/npm installed; the npm script just wraps the
   Python command.)

2. **See the slippage guard reject:**
   `python3 scripts/quote.py --from eth --token usdc --amount 10 --to near --asset near --max-slippage 0`
   → *See* `REJECTED: slippage limit <= 0 (slippage guard demonstration)` and exit code `2`
   (`echo $?` prints `2`).

3. **See a real API error surfaced honestly (example output — may differ by day):**
   `python3 scripts/quote.py --from base --token usdc --amount 25 --to btc --asset btc`
   → *Captured 2026-11-06:* `QUOTE ERROR: 400 ... "No liquidity available"`, exit code `1`.
   The exact message depends on that day's liquidity — if the route has liquidity when you
   run it, you will get a normal quote (exit code `0`) instead; that is also a correct
   outcome. Error handling is separately exercised by step 2 (deterministic reject path).

4. **See the schedule planner:**
   `python3 scripts/plan.py --total 300 --drips 10 --interval 24h --slippage 1.0`
   → *See* a JSON schedule of 10 drips (30.0 each) with UTC timestamps 24h apart.

5. **See a full dry-run drip leg recorded in history:**
   `python3 scripts/scheduler.py --from eth --token usdc --amount 10 --to near --asset near`
   → *See* the leg printed with real quote numbers and `status: DRY_RUN`, then check
   `history.json` — the leg is appended (amounts, expected out, min out, time estimate).
   Nothing was executed; no keys were read.

   Now try wet mode with no credentials present:
   `python3 scripts/scheduler.py --from eth --token usdc --amount 10 --to near --asset near --wet`
   → *See* `WET RUN REFUSED: set NEAR_MAIN_ACCOUNT + NEAR_MAIN_KEY in .env` and exit code `6`.
   The leg is recorded as `WET_RUN_REFUSED_NO_CREDENTIALS` — the scheduler never fabricates
   a wet run.

Requires network access to `1click.chaindefuser.com`. No API key needed for quotes.

6. **See the interval loop execute two capped dry legs:**
   `python3 scripts/scheduler.py --from eth --token usdc --amount 5 --to near --asset near --interval 0.0003 --max-legs 2`
   → *See* `--- drip leg 1 ---` … `Next drip in 0.0003h` … `--- drip leg 2 ---` …
   `loop: max_legs=2 reached, stopping` — two legs appended to `history.json`.
   (0.0003h ≈ 1 second, used here so the demo finishes; real schedules use e.g. `--interval 24`.)

Requires network access to `1click.chaindefuser.com`. No API key needed for quotes.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | SUCCESS, or dry-run leg completed |
| 1 | Quote error (e.g. no liquidity for that route/size) |
| 2 | Slippage guard rejected the quote |
| 3 | Leg ended REFUNDED (funds returned by 1Click) |
| 4 | Leg ended FAILED |
| 5 | Status polling TIMEOUT |
| 6 | Wet run refused: signing credentials missing, or not yet implemented |

## Status

- [x] 1Click quote client (dry-run safe, live-verified against eth→near and arb→eth)
- [x] Slippage guard reject path (exit code 2)
- [x] Schedule planner (pure, no API)
- [x] Per-leg scheduler with dry-run state machine (`scripts/scheduler.py`)
- [x] Status polling logic (`/v0/status?depositAddress=...`, PENDING_DEPOSIT→terminal states)
- [x] Wet-run deposit signing (NEAR CLI `near send`, Opsi A) — tx hash → explorer link →
      `/v0/deposit/submit` accelerator → poll to terminal state
- [x] Interval loop (`--interval 24 --max-legs N`)
- [ ] End-to-end wet run on mainnet (awaits funded dedicated dev account)
- [ ] Web UI (deposit / schedule / history)
- [ ] Stretch: contract-owned vault via Chain Signatures

## License

MIT
