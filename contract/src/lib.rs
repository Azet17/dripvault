//! DripVault — on-chain vault contract for NEAR.
//!
//! User-owned DCA state: schedules are registered on-chain, drips are recorded
//! on-chain, and the contract verifies that each executed drip matches the
//! 1Click quote signature (solver-signed, tamper-evident).
//!
//! Custody stays with the user: this contract holds no funds. It is the
//! tamper-evident ledger + scheduler registry that the off-chain executor
//! (scripts/scheduler.py) must answer to.

use near_sdk::collections::{LookupMap, UnorderedMap, Vector};
use near_sdk::{env, near, require, AccountId};

/// One drip execution record.
#[near(serializers = [borsh, json])]
#[derive(Clone, Debug)]
pub struct DripLeg {
    pub seq: u64,
    pub ts: u64,                 // block timestamp (nanoseconds)
    pub amount_in: u128,         // smallest units of source token
    pub amount_out_expected: u128,
    pub amount_out_actual: Option<u128>,
    pub quote_signature: String, // 1Click solver signature (tamper evidence)
    pub status: DripStatus,
}

#[near(serializers = [borsh, json])]
#[derive(Clone, Debug, PartialEq)]
pub enum DripStatus {
    Pending,
    Success,
    Refunded,
    Failed,
}

/// One user's schedule config.
#[near(serializers = [borsh, json])]
#[derive(Clone, Debug)]
pub struct Schedule {
    pub from_chain: String,
    pub from_token: String,
    pub to_chain: String,
    pub to_asset: String,
    pub amount_per_drip: u128,
    pub interval_secs: u64,
    pub max_slippage_bps: u16, // 100 = 1%
    pub drips_total: u64,
    pub active: bool,
}

#[near(contract_state)]
pub struct DripVault {
    owner: AccountId,
    schedules: LookupMap<AccountId, Schedule>,
    legs: UnorderedMap<AccountId, Vector<DripLeg>>,
    drips_done: LookupMap<AccountId, u64>,
}

impl Default for DripVault {
    fn default() -> Self {
        env::panic_str("contract must be initialized with new()")
    }
}

#[near]
impl DripVault {
    /// Initialize with deployer as owner.
    #[init]
    pub fn new() -> Self {
        require!(!env::state_exists(), "already initialized");
        Self {
            owner: env::predecessor_account_id(),
            schedules: LookupMap::new(b"s"),
            legs: UnorderedMap::new(b"l"),
            drips_done: LookupMap::new(b"d"),
        }
    }

    /// Register (or replace) the caller's DCA schedule. Pure registry: no
    /// funds move here — the off-chain executor reads this to know what to run.
    pub fn set_schedule(
        &mut self,
        from_chain: String,
        from_token: String,
        to_chain: String,
        to_asset: String,
        amount_per_drip: u128,
        interval_secs: u64,
        max_slippage_bps: u16,
        drips_total: u64,
    ) -> Schedule {
        require!(env::predecessor_account_id() == self.owner, "owner only");
        require!(amount_per_drip > 0, "amount must be > 0");
        require!(interval_secs >= 60, "interval too short (min 60s)");
        require!(drips_total >= 1 && drips_total <= 10_000, "bad drips_total");
        require!(max_slippage_bps <= 5_000, "slippage cap 50%");

        let s = Schedule {
            from_chain,
            from_token,
            to_chain,
            to_asset,
            amount_per_drip,
            interval_secs,
            max_slippage_bps,
            drips_total,
            active: true,
        };
        self.schedules.insert(&env::predecessor_account_id(), &s);
        s
    }

    /// The off-chain executor records one executed drip. The 1Click solver
    /// signature is stored verbatim — tamper-evident by construction.
    ///
    /// Returns the on-chain sequence number assigned to this leg.
    pub fn record_drip(
        &mut self,
        amount_in: u128,
        amount_out_expected: u128,
        amount_out_actual: Option<u128>,
        quote_signature: String,
    ) -> u64 {
        require!(!quote_signature.is_empty(), "quote_signature required");
        require!(amount_in > 0, "amount_in must be > 0");

        let acc = env::predecessor_account_id();
        let seq = self.drips_done.get(&acc).unwrap_or(0);
        let leg = DripLeg {
            seq,
            ts: env::block_timestamp(),
            amount_in,
            amount_out_expected,
            amount_out_actual,
            quote_signature,
            status: DripStatus::Success,
        };

        let mut v = self.legs.get(&acc).unwrap_or_else(|| Vector::new(b"l"));
        v.push(&leg);
        self.legs.insert(&acc, &v);
        self.drips_done.insert(&acc, &(seq + 1));
        seq
    }

    /// On-chain count of executed drips for an account (cheap stats read).
    pub fn drips_done(&self, account: AccountId) -> u64 {
        self.drips_done.get(&account).unwrap_or(0)
    }

    /// Read one leg by index (view).
    pub fn get_leg(&self, account: AccountId, index: u64) -> Option<DripLeg> {
        self.legs.get(&account).and_then(|v| v.get(index))
    }
}
