# DripVault

Cross-chain dollar-cost-averaging (DCA) vault built on **NEAR Intents (1Click API)**.

Deposit USDC from any supported chain, pick a target asset (BTC, ETH or NEAR) and a schedule.
On every interval the vault requests a quote from the 1Click API, rejects quotes that beat the
user's slippage limit, and executes the swap so the asset lands **in the user's own address**
on the target chain. No custodian holds your funds at rest — between drips the money sits in
the user's wallet, not in a vault contract.

Built for **NEAR HACKATHON** (2026-11-01 → 2026-11-14), track `defi` (Unstoppable DeFi).

## What it does

- **Deposit from any chain** — NEAR Intents moves USDC into the working account.
- **Scheduled drips** — every N hours/days the vault fetches a live 1Click quote.
- **Slippage guard** — quotes worse than the user's limit are rejected and logged, never executed.
- **Status tracking** — every executed (or failed/refunded) leg is recorded with an explorer link.
- **Dry-run mode** — fetches real quotes without executing, so anyone (including judges) can try
  the full flow with **zero funds**.

## How NEAR fits

- **NEAR Intents / 1Click API** does the cross-chain settlement: quotes, deposit addresses,
  execution and status — no bridges, no custodian.
- Between scheduled drips, funds stay in the user-controlled NEAR account — the app holds
  nothing.
- (Stretch, in progress) a NEAR smart contract owns the vault and uses **Chain Signatures**
  to derive target-chain addresses directly.

## Try it (dry-run, no funds needed)

Prerequisites: Node 20+, a terminal.

```bash
git clone https://github.com/Azet17/dripvault.git
cd dripvault
npm install
```

1. **See a live cross-chain quote without spending anything:**
   `npm run quote -- --from 1 --token usdc --amount 10 --to near --asset near`
   → *See* a JSON quote with `amountIn`, `amountOut`, `deadline`, and a `timeEstimate`.

2. **See the slippage guard reject a bad trade:**
   `npm run quote -- --from 1 --token usdc --amount 10 --to near --asset near --max-slippage 0.000001`
   → *See* `REJECTED: quote slippage X% exceeds limit 0.000001%` (exit code 2).

3. **See the schedule planner:**
   `npm run plan -- --total 300 --drips 10 --interval 24h --slippage 1.0`
   → *See* a table of 10 planned drips with timestamps and per-drip amounts.

4. **(Wet run, needs a funded NEAR testnet/intents account)** set `.env` per `.env.example`,
   then `npm run drip -- --dry=false` → *See* the executed deposit + swap leg and an explorer
   link in `history.json`.

## Status

- [x] 1Click quote client (dry-run safe)
- [ ] Slippage guard wired into scheduler
- [ ] Status polling + REFUNDED/FAILED handling
- [ ] Web UI (deposit / schedule / history)
- [ ] Stretch: contract-owned vault via Chain Signatures

## License

MIT
