//! Resource accounts — first-class OS-style resource management.

use std::collections::HashMap;
use std::time::{SystemTime, UNIX_EPOCH};

use serde::{Deserialize, Serialize};
use serde_json::{json, Value};

use crate::error::{KernelError, KernelResult};

fn now_secs() -> f64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs_f64())
        .unwrap_or(0.0)
}

fn short_id() -> String {
    uuid::Uuid::new_v4().simple().to_string()[..12].to_string()
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ResourceKind {
    /// Token budget (mirrored from process ledger for unified view)
    TokenBudget,
    /// Logical memory bytes (context / tool result cache)
    MemoryBytes,
    /// Concurrent run slots under an identity
    ConcurrencySlots,
    /// Child OS process count
    ChildProc,
    /// Tool invocation count (rate window handled by caller)
    ToolCalls,
    /// Bytes written via tools
    IoWriteBytes,
    /// Bytes read via tools
    IoReadBytes,
}

impl ResourceKind {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::TokenBudget => "token_budget",
            Self::MemoryBytes => "memory_bytes",
            Self::ConcurrencySlots => "concurrency_slots",
            Self::ChildProc => "child_proc",
            Self::ToolCalls => "tool_calls",
            Self::IoWriteBytes => "io_write_bytes",
            Self::IoReadBytes => "io_read_bytes",
        }
    }

    pub fn parse(s: &str) -> Option<Self> {
        match s {
            "token_budget" => Some(Self::TokenBudget),
            "memory_bytes" => Some(Self::MemoryBytes),
            "concurrency_slots" => Some(Self::ConcurrencySlots),
            "child_proc" => Some(Self::ChildProc),
            "tool_calls" => Some(Self::ToolCalls),
            "io_write_bytes" => Some(Self::IoWriteBytes),
            "io_read_bytes" => Some(Self::IoReadBytes),
            _ => None,
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ResourceAccount {
    pub kind: ResourceKind,
    pub limit: Option<i64>,
    pub used: i64,
}

impl ResourceAccount {
    pub fn remaining(&self) -> Option<i64> {
        self.limit.map(|l| (l - self.used).max(0))
    }

    pub fn can_charge(&self, amount: i64) -> bool {
        if amount <= 0 {
            return true;
        }
        match self.limit {
            None => true,
            Some(l) => self.used + amount <= l,
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ResourceHandle {
    pub id: String,
    pub process_id: String,
    pub kind: ResourceKind,
    pub amount: i64,
    pub allocated_at: f64,
}

#[derive(Default)]
pub struct ResourceManager {
    /// process_id -> kind -> account
    accounts: HashMap<String, HashMap<ResourceKind, ResourceAccount>>,
    handles: HashMap<String, ResourceHandle>,
    /// global defaults applied on process create
    defaults: HashMap<ResourceKind, Option<i64>>,
}

impl ResourceManager {
    pub fn new() -> Self {
        let mut defaults = HashMap::new();
        defaults.insert(ResourceKind::ConcurrencySlots, Some(4));
        defaults.insert(ResourceKind::ChildProc, Some(16));
        defaults.insert(ResourceKind::MemoryBytes, Some(256 * 1024 * 1024)); // 256 MiB logical
        defaults.insert(ResourceKind::ToolCalls, None);
        defaults.insert(ResourceKind::IoWriteBytes, None);
        defaults.insert(ResourceKind::IoReadBytes, None);
        Self {
            accounts: HashMap::new(),
            handles: HashMap::new(),
            defaults,
        }
    }

    pub fn set_default(&mut self, kind: ResourceKind, limit: Option<i64>) {
        self.defaults.insert(kind, limit);
    }

    pub fn ensure_process(&mut self, process_id: &str, token_budget: Option<i64>) {
        let entry = self.accounts.entry(process_id.to_string()).or_default();
        for (kind, lim) in &self.defaults {
            entry.entry(*kind).or_insert(ResourceAccount {
                kind: *kind,
                limit: *lim,
                used: 0,
            });
        }
        entry.insert(
            ResourceKind::TokenBudget,
            ResourceAccount {
                kind: ResourceKind::TokenBudget,
                limit: token_budget,
                used: 0,
            },
        );
    }

    pub fn drop_process(&mut self, process_id: &str) {
        self.accounts.remove(process_id);
        self.handles.retain(|_, h| h.process_id != process_id);
    }

    pub fn charge(&mut self, process_id: &str, kind: ResourceKind, amount: i64) -> KernelResult<i64> {
        let acct = self
            .accounts
            .get_mut(process_id)
            .and_then(|m| m.get_mut(&kind))
            .ok_or_else(|| KernelError::NotFound(format!("no resource account {process_id}/{kind:?}")))?;
        if !acct.can_charge(amount) {
            return Err(KernelError::BudgetExceeded(format!(
                "resource {:?} exceeded for {process_id}: used={} limit={:?} charge={amount}",
                kind, acct.used, acct.limit
            )));
        }
        if amount > 0 {
            acct.used += amount;
        }
        Ok(acct.remaining().unwrap_or(i64::MAX))
    }

    pub fn release_amount(&mut self, process_id: &str, kind: ResourceKind, amount: i64) {
        if let Some(acct) = self
            .accounts
            .get_mut(process_id)
            .and_then(|m| m.get_mut(&kind))
        {
            acct.used = (acct.used - amount).max(0);
        }
    }

    pub fn alloc(
        &mut self,
        process_id: &str,
        kind: ResourceKind,
        amount: i64,
    ) -> KernelResult<ResourceHandle> {
        self.charge(process_id, kind, amount)?;
        let h = ResourceHandle {
            id: short_id(),
            process_id: process_id.to_string(),
            kind,
            amount,
            allocated_at: now_secs(),
        };
        self.handles.insert(h.id.clone(), h.clone());
        Ok(h)
    }

    pub fn release(&mut self, handle_id: &str) -> KernelResult<()> {
        let h = self
            .handles
            .remove(handle_id)
            .ok_or_else(|| KernelError::NotFound(format!("unknown handle {handle_id}")))?;
        self.release_amount(&h.process_id, h.kind, h.amount);
        Ok(())
    }

    pub fn usage(&self, process_id: &str) -> Value {
        let Some(map) = self.accounts.get(process_id) else {
            return json!({});
        };
        let mut out = serde_json::Map::new();
        for (kind, acct) in map {
            out.insert(
                kind.as_str().to_string(),
                json!({
                    "limit": acct.limit,
                    "used": acct.used,
                    "remaining": acct.remaining(),
                }),
            );
        }
        Value::Object(out)
    }

    pub fn sync_token_used(&mut self, process_id: &str, used: i64, budget: Option<i64>) {
        if let Some(acct) = self
            .accounts
            .get_mut(process_id)
            .and_then(|m| m.get_mut(&ResourceKind::TokenBudget))
        {
            acct.used = used;
            acct.limit = budget;
        }
    }

    /// OS RSS report: raise memory_bytes.used to at least rss (never decreases here).
    /// Returns (used, limit, over_limit).
    pub fn report_rss(
        &mut self,
        process_id: &str,
        rss_bytes: i64,
    ) -> KernelResult<(i64, Option<i64>, bool)> {
        if rss_bytes < 0 {
            return Err(KernelError::Invalid("rss_bytes must be >= 0".into()));
        }
        self.ensure_process(process_id, None);
        let acct = self
            .accounts
            .get_mut(process_id)
            .and_then(|m| m.get_mut(&ResourceKind::MemoryBytes))
            .ok_or_else(|| {
                KernelError::NotFound(format!("no memory account {process_id}"))
            })?;
        if rss_bytes > acct.used {
            acct.used = rss_bytes;
        }
        let over = match acct.limit {
            Some(l) => acct.used > l,
            None => false,
        };
        Ok((acct.used, acct.limit, over))
    }
}
