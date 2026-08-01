//! Kernel-owned inbox claim queue (P1-A F7) — dual worker safe.

use std::collections::{HashMap, VecDeque};
use std::time::{SystemTime, UNIX_EPOCH};

use serde::{Deserialize, Serialize};
use serde_json::{json, Value};

fn now_secs() -> f64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs_f64())
        .unwrap_or(0.0)
}

fn short_id() -> String {
    uuid::Uuid::new_v4().simple().to_string()[..12].to_string()
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct InboxItem {
    pub id: String,
    pub identity: String,
    pub instruction: String,
    pub status: String, // pending | claimed | done | failed | cancelled
    pub priority: i32,
    pub claim_token: Option<String>,
    pub claimed_by: Option<String>,
    pub claimed_at: Option<f64>,
    pub process_id: Option<String>,
    pub result: Option<String>,
    pub created_at: f64,
    pub meta: Value,
}

pub struct InboxQueue {
    pending: VecDeque<String>,
    items: HashMap<String, InboxItem>,
    claim_timeout_secs: f64,
    /// max pending items (overflow drops lowest priority)
    max_pending: usize,
    /// claimed-but-not-done per identity
    claimed_by_identity: HashMap<String, u32>,
    max_claimed_per_identity: u32,
    dead: Vec<String>,
    overflow_drops: u64,
}

impl Default for InboxQueue {
    fn default() -> Self {
        // Align with Python agent_inbox_item_timeout (600s) + grace so long
        // jobs are not reclaimed while workers still run (was 300s → sticky fail).
        Self::new(900.0)
    }
}

impl InboxQueue {
    pub fn new(claim_timeout_secs: f64) -> Self {
        Self {
            pending: VecDeque::new(),
            items: HashMap::new(),
            claim_timeout_secs: claim_timeout_secs.max(1.0),
            max_pending: 500,
            claimed_by_identity: HashMap::new(),
            // Product serial-1: match agent_dispatcher_max_identity_concurrent default
            max_claimed_per_identity: 1,
            dead: Vec::new(),
            overflow_drops: 0,
        }
    }

    pub fn set_limits(&mut self, max_pending: usize, max_claimed_per_identity: u32) {
        self.max_pending = max_pending.max(8);
        self.max_claimed_per_identity = max_claimed_per_identity.max(1);
    }

    pub fn set_claim_timeout(&mut self, secs: f64) {
        self.claim_timeout_secs = secs.max(30.0);
    }

    /// Heartbeat: refresh claimed_at for sticky leases (by SQL db_item_id in meta).
    pub fn touch_by_db_id(&mut self, db_item_id: &str) -> bool {
        let now = now_secs();
        for item in self.items.values_mut() {
            if item.status != "claimed" {
                continue;
            }
            let mid = item
                .meta
                .get("db_item_id")
                .and_then(|v| v.as_str())
                .unwrap_or("");
            if mid == db_item_id {
                item.claimed_at = Some(now);
                return true;
            }
        }
        false
    }

    pub fn submit(
        &mut self,
        identity: &str,
        instruction: &str,
        priority: i32,
        meta: Option<Value>,
    ) -> InboxItem {
        let item = InboxItem {
            id: short_id(),
            identity: identity.to_string(),
            instruction: instruction.chars().take(8000).collect(),
            status: "pending".into(),
            priority,
            claim_token: None,
            claimed_by: None,
            claimed_at: None,
            process_id: None,
            result: None,
            created_at: now_secs(),
            meta: meta.unwrap_or_else(|| json!({})),
        };
        // higher priority (lower number) closer to front
        let mut inserted = false;
        for (i, id) in self.pending.iter().enumerate() {
            if let Some(other) = self.items.get(id) {
                if priority < other.priority {
                    self.pending.insert(i, item.id.clone());
                    inserted = true;
                    break;
                }
            }
        }
        if !inserted {
            self.pending.push_back(item.id.clone());
        }
        self.items.insert(item.id.clone(), item.clone());
        self.enforce_pending_cap();
        item
    }

    fn enforce_pending_cap(&mut self) {
        while self.pending.len() > self.max_pending {
            // drop from back (lowest priority / newest low-prio)
            if let Some(id) = self.pending.pop_back() {
                if let Some(mut it) = self.items.remove(&id) {
                    it.status = "cancelled".into();
                    it.result = Some("overflow_drop".into());
                    self.dead.push(id);
                    self.overflow_drops = self.overflow_drops.saturating_add(1);
                }
            } else {
                break;
            }
        }
    }

    /// Atomic claim for identity (or any if identity empty).
    pub fn claim(
        &mut self,
        worker_id: &str,
        identity: Option<&str>,
    ) -> Option<InboxItem> {
        self.reclaim_stale();
        let mut idx = None;
        for (i, id) in self.pending.iter().enumerate() {
            if let Some(it) = self.items.get(id) {
                if it.status != "pending" {
                    continue;
                }
                if let Some(want) = identity {
                    if !want.is_empty() && it.identity != want {
                        continue;
                    }
                }
                // per-identity concurrency
                let live = *self.claimed_by_identity.get(&it.identity).unwrap_or(&0);
                if live >= self.max_claimed_per_identity {
                    continue;
                }
                idx = Some(i);
                break;
            }
        }
        let i = idx?;
        let id = self.pending.remove(i)?;
        let item = self.items.get_mut(&id)?;
        item.status = "claimed".into();
        item.claim_token = Some(short_id());
        item.claimed_by = Some(worker_id.to_string());
        item.claimed_at = Some(now_secs());
        let ident = item.identity.clone();
        *self.claimed_by_identity.entry(ident).or_insert(0) += 1;
        Some(item.clone())
    }

    fn release_claim_count(&mut self, identity: &str) {
        if let Some(n) = self.claimed_by_identity.get_mut(identity) {
            *n = n.saturating_sub(1);
        }
    }

    pub fn complete(
        &mut self,
        item_id: &str,
        claim_token: &str,
        result: &str,
        process_id: Option<&str>,
    ) -> Result<InboxItem, String> {
        let item = self
            .items
            .get_mut(item_id)
            .ok_or_else(|| format!("unknown item {item_id}"))?;
        if item.status != "claimed" {
            return Err(format!("item not claimed (status={})", item.status));
        }
        if item.claim_token.as_deref() != Some(claim_token) {
            return Err("invalid claim_token".into());
        }
        let ident = item.identity.clone();
        item.status = "done".into();
        item.result = Some(result.chars().take(20000).collect());
        item.process_id = process_id.map(|s| s.to_string());
        item.claim_token = None;
        let out = item.clone();
        self.release_claim_count(&ident);
        Ok(out)
    }

    pub fn fail(
        &mut self,
        item_id: &str,
        claim_token: &str,
        reason: &str,
    ) -> Result<InboxItem, String> {
        let item = self
            .items
            .get_mut(item_id)
            .ok_or_else(|| format!("unknown item {item_id}"))?;
        if item.claim_token.as_deref() != Some(claim_token) {
            return Err("invalid claim_token".into());
        }
        let ident = item.identity.clone();
        item.status = "failed".into();
        item.result = Some(reason.to_string());
        item.claim_token = None;
        let out = item.clone();
        self.release_claim_count(&ident);
        Ok(out)
    }

    pub fn release_to_pending(
        &mut self,
        item_id: &str,
        claim_token: &str,
    ) -> Result<InboxItem, String> {
        let item = self
            .items
            .get_mut(item_id)
            .ok_or_else(|| format!("unknown item {item_id}"))?;
        if item.claim_token.as_deref() != Some(claim_token) {
            return Err("invalid claim_token".into());
        }
        let ident = item.identity.clone();
        item.status = "pending".into();
        item.claim_token = None;
        item.claimed_by = None;
        item.claimed_at = None;
        self.pending.push_front(item.id.clone());
        let out = item.clone();
        self.release_claim_count(&ident);
        Ok(out)
    }

    pub fn reclaim_stale(&mut self) -> usize {
        let now = now_secs();
        let timeout = self.claim_timeout_secs;
        let mut n = 0;
        let mut back = Vec::new();
        let mut release_idents = Vec::new();
        for item in self.items.values_mut() {
            if item.status == "claimed" {
                if let Some(at) = item.claimed_at {
                    if now - at > timeout {
                        release_idents.push(item.identity.clone());
                        item.status = "pending".into();
                        item.claim_token = None;
                        item.claimed_by = None;
                        item.claimed_at = None;
                        back.push(item.id.clone());
                        n += 1;
                    }
                }
            }
        }
        for id in release_idents {
            self.release_claim_count(&id);
        }
        for id in back {
            if !self.pending.contains(&id) {
                self.pending.push_front(id);
            }
        }
        n
    }

    pub fn get(&self, id: &str) -> Option<&InboxItem> {
        self.items.get(id)
    }

    pub fn list(&self, status: Option<&str>, limit: usize) -> Vec<InboxItem> {
        let lim = limit.max(1).min(200);
        let mut v: Vec<_> = self
            .items
            .values()
            .filter(|i| status.map(|s| i.status == s).unwrap_or(true))
            .cloned()
            .collect();
        v.sort_by(|a, b| b.created_at.partial_cmp(&a.created_at).unwrap());
        v.truncate(lim);
        v
    }

    pub fn status(&self) -> Value {
        let mut counts = HashMap::new();
        for i in self.items.values() {
            *counts.entry(i.status.clone()).or_insert(0u64) += 1;
        }
        json!({
            "pending_queue": self.pending.len(),
            "total": self.items.len(),
            "counts": counts,
            "claim_timeout_secs": self.claim_timeout_secs,
            "max_pending": self.max_pending,
            "max_claimed_per_identity": self.max_claimed_per_identity,
            "claimed_by_identity": self.claimed_by_identity,
            "overflow_drops": self.overflow_drops,
            "dead_letter": self.dead.len(),
            "authority": "rust",
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn dual_claim_not_double() {
        let mut q = InboxQueue::new(60.0);
        q.submit("alice", "do thing", 10, None);
        let a = q.claim("w1", Some("alice")).unwrap();
        assert!(q.claim("w2", Some("alice")).is_none());
        q.complete(&a.id, a.claim_token.as_deref().unwrap(), "ok", None)
            .unwrap();
        assert_eq!(q.get(&a.id).unwrap().status, "done");
    }
}
