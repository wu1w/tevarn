//! LLM admission: global slots · owner reserve · fairness · daily quota (P0-C).

use std::collections::HashMap;
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

/// Priority bands (higher = more important). Aligns with Python Priority IntEnum.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum LlmPriority {
    OwnerChat = 100,
    Interactive = 80,
    WorkforceHigh = 50,
    WorkforceNormal = 30,
    WorkforceLow = 10,
    Background = 5,
}

impl LlmPriority {
    pub fn from_i32(v: i32) -> Self {
        match v {
            x if x >= 100 => Self::OwnerChat,
            x if x >= 80 => Self::Interactive,
            x if x >= 50 => Self::WorkforceHigh,
            x if x >= 30 => Self::WorkforceNormal,
            x if x >= 10 => Self::WorkforceLow,
            _ => Self::Background,
        }
    }

    pub fn as_i32(self) -> i32 {
        self as i32
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct LlmLeaseRequest {
    pub request_id: String,
    pub source: String,
    pub identity_id: Option<String>,
    pub session_id: Option<String>,
    pub process_id: Option<String>,
    pub inbox_item_id: Option<String>,
    pub priority: i32,
    pub enqueued_at: f64,
    pub estimated_tokens: i64,
    pub wait_boost: f64,
}

impl LlmLeaseRequest {
    pub fn new(
        source: impl Into<String>,
        priority: i32,
        identity_id: Option<String>,
        process_id: Option<String>,
    ) -> Self {
        Self {
            request_id: short_id(),
            source: source.into(),
            identity_id,
            session_id: None,
            process_id,
            inbox_item_id: None,
            priority,
            enqueued_at: now_secs(),
            estimated_tokens: 0,
            wait_boost: 0.0,
        }
    }

    pub fn is_owner(&self) -> bool {
        self.source == "chat"
            || self.source == "interactive"
            || self.priority >= LlmPriority::Interactive.as_i32()
    }

    pub fn from_dict(v: &Value) -> Self {
        Self {
            request_id: v
                .get("request_id")
                .and_then(|x| x.as_str())
                .map(|s| s.to_string())
                .filter(|s| !s.is_empty())
                .unwrap_or_else(short_id),
            source: v
                .get("source")
                .and_then(|x| x.as_str())
                .unwrap_or("chat")
                .to_string(),
            identity_id: v
                .get("identity_id")
                .and_then(|x| x.as_str())
                .map(|s| s.to_string()),
            session_id: v
                .get("session_id")
                .and_then(|x| x.as_str())
                .map(|s| s.to_string()),
            process_id: v
                .get("process_id")
                .and_then(|x| x.as_str())
                .map(|s| s.to_string()),
            inbox_item_id: v
                .get("inbox_item_id")
                .and_then(|x| x.as_str())
                .map(|s| s.to_string()),
            priority: v
                .get("priority")
                .and_then(|x| x.as_i64())
                .unwrap_or(100) as i32,
            enqueued_at: v
                .get("enqueued_at")
                .and_then(|x| x.as_f64())
                .unwrap_or_else(now_secs),
            estimated_tokens: v
                .get("estimated_tokens")
                .and_then(|x| x.as_i64())
                .unwrap_or(0),
            wait_boost: v
                .get("wait_boost")
                .and_then(|x| x.as_f64())
                .unwrap_or(0.0),
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct LlmLease {
    pub request_id: String,
    pub granted_at: f64,
    pub source: String,
    pub identity_id: Option<String>,
    pub process_id: Option<String>,
    pub priority: i32,
    pub is_owner: bool,
}

impl LlmLease {
    pub fn to_dict(&self) -> Value {
        json!({
            "request_id": self.request_id,
            "granted_at": self.granted_at,
            "source": self.source,
            "identity_id": self.identity_id,
            "process_id": self.process_id,
            "priority": self.priority,
            "is_owner": self.is_owner,
        })
    }
}

#[derive(Debug, Clone)]
pub struct LlmAdmissionConfig {
    pub max_in_flight: usize,
    pub max_per_identity: usize,
    pub owner_reserve: usize,
    pub queue_max: usize,
    pub fairness_wait_weight: f64,
    pub daily_global: i64,
    pub daily_identity: i64,
    pub grant_timeout_secs: f64,
}

impl Default for LlmAdmissionConfig {
    fn default() -> Self {
        Self {
            max_in_flight: 4,
            max_per_identity: 1,
            owner_reserve: 1,
            queue_max: 64,
            fairness_wait_weight: 1.0,
            daily_global: 0,
            daily_identity: 0,
            grant_timeout_secs: 300.0,
        }
    }
}

#[derive(Default)]
struct DailyQuota {
    day: String,
    global_used: i64,
    by_identity: HashMap<String, i64>,
}

impl DailyQuota {
    fn roll(&mut self) {
        let day = chrono_day();
        if day != self.day {
            self.day = day;
            self.global_used = 0;
            self.by_identity.clear();
        }
    }

    fn would_exceed(
        &mut self,
        identity_id: Option<&str>,
        global_limit: i64,
        per_identity: i64,
        estimated: i64,
    ) -> Option<&'static str> {
        self.roll();
        let est = estimated.max(0);
        if global_limit > 0
            && (self.global_used >= global_limit || self.global_used + est > global_limit)
        {
            return Some("global_daily_quota");
        }
        if per_identity > 0 {
            if let Some(iid) = identity_id {
                let used = *self.by_identity.get(iid).unwrap_or(&0);
                if used >= per_identity || used + est > per_identity {
                    return Some("identity_daily_quota");
                }
            }
        }
        None
    }

    fn charge(&mut self, identity_id: Option<&str>, amount: i64) {
        if amount <= 0 {
            return;
        }
        self.roll();
        self.global_used += amount;
        if let Some(iid) = identity_id {
            *self.by_identity.entry(iid.to_string()).or_insert(0) += amount;
        }
    }

    fn snapshot(&mut self, global_limit: i64, per_identity: i64) -> Value {
        self.roll();
        let by: Vec<_> = self
            .by_identity
            .iter()
            .map(|(iid, used)| {
                json!({
                    "identity_id": iid,
                    "used": used,
                    "limit": if per_identity > 0 { Value::from(per_identity) } else { Value::Null },
                })
            })
            .collect();
        json!({
            "day": self.day,
            "global_used_today": self.global_used,
            "global_limit": if global_limit > 0 { Value::from(global_limit) } else { Value::Null },
            "per_identity_limit": if per_identity > 0 { Value::from(per_identity) } else { Value::Null },
            "by_identity": by,
        })
    }
}

fn chrono_day() -> String {
    let secs = now_secs() as i64;
    let days = secs / 86400;
    format!("day-{days}")
}

/// Result of try_acquire / poll.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(tag = "status", rename_all = "snake_case")]
pub enum LlmAcquireResult {
    Granted { lease: LlmLease },
    Queued { request_id: String, reason: String, queue_len: usize },
    Rejected { request_id: String, reason: String, code: String },
}

pub struct LlmAdmissionController {
    cfg: LlmAdmissionConfig,
    in_flight: HashMap<String, LlmLease>,
    queued: HashMap<String, LlmLeaseRequest>,
    /// request_id → granted lease waiting to be polled
    pending_grants: HashMap<String, LlmLease>,
    rejected: HashMap<String, (String, String)>, // code, reason
    quota: DailyQuota,
}

impl Default for LlmAdmissionController {
    fn default() -> Self {
        Self::new(LlmAdmissionConfig::default())
    }
}

impl LlmAdmissionController {
    pub fn new(cfg: LlmAdmissionConfig) -> Self {
        Self {
            cfg,
            in_flight: HashMap::new(),
            queued: HashMap::new(),
            pending_grants: HashMap::new(),
            rejected: HashMap::new(),
            quota: DailyQuota::default(),
        }
    }

    pub fn set_config(&mut self, cfg: LlmAdmissionConfig) {
        self.cfg = cfg;
    }

    pub fn config(&self) -> &LlmAdmissionConfig {
        &self.cfg
    }

    fn score(&self, req: &LlmLeaseRequest) -> f64 {
        let wait = (now_secs() - req.enqueued_at).max(0.0);
        let wait_term = self.cfg.fairness_wait_weight * wait.min(300.0) / 10.0;
        let mut id_penalty = 0.0;
        if let Some(ref iid) = req.identity_id {
            let n = self
                .in_flight
                .values()
                .filter(|l| l.identity_id.as_deref() == Some(iid.as_str()))
                .count();
            if n > 0 {
                id_penalty = 1000.0;
            }
        }
        req.priority as f64 + wait_term + req.wait_boost - id_penalty
    }

    fn identity_inflight(&self, identity_id: Option<&str>) -> usize {
        let Some(iid) = identity_id else {
            return 0;
        };
        self.in_flight
            .values()
            .filter(|l| l.identity_id.as_deref() == Some(iid))
            .count()
    }

    fn can_grant_now(&mut self, req: &LlmLeaseRequest) -> Option<String> {
        if let Some(qerr) = self.quota.would_exceed(
            req.identity_id.as_deref(),
            self.cfg.daily_global,
            self.cfg.daily_identity,
            req.estimated_tokens,
        ) {
            return Some(format!("reject:{qerr}"));
        }
        if self.in_flight.len() >= self.cfg.max_in_flight {
            return Some("queue:full".into());
        }
        if let Some(ref iid) = req.identity_id {
            if self.identity_inflight(Some(iid)) >= self.cfg.max_per_identity {
                return Some("queue:identity".into());
            }
        }
        let free = self.cfg.max_in_flight.saturating_sub(self.in_flight.len());
        if self.cfg.owner_reserve > 0 && free <= self.cfg.owner_reserve && !req.is_owner() {
            return Some("queue:owner_reserve".into());
        }
        None
    }

    fn grant_locked(&mut self, req: &LlmLeaseRequest) -> LlmLease {
        let lease = LlmLease {
            request_id: req.request_id.clone(),
            granted_at: now_secs(),
            source: req.source.clone(),
            identity_id: req.identity_id.clone(),
            process_id: req.process_id.clone(),
            priority: req.priority,
            is_owner: req.is_owner(),
        };
        self.in_flight
            .insert(lease.request_id.clone(), lease.clone());
        self.queued.remove(&req.request_id);
        lease
    }

    fn wake_best(&mut self) {
        let mut best: Option<(f64, String)> = None;
        let ids: Vec<_> = self.queued.keys().cloned().collect();
        for id in ids {
            let req = match self.queued.get(&id) {
                Some(r) => r.clone(),
                None => continue,
            };
            if self.can_grant_now(&req).is_none() {
                let s = self.score(&req);
                if best.as_ref().map(|(bs, _)| s > *bs).unwrap_or(true) {
                    best = Some((s, id));
                }
            }
        }
        if let Some((_, id)) = best {
            if let Some(req) = self.queued.remove(&id) {
                let lease = self.grant_locked(&req);
                self.pending_grants
                    .insert(lease.request_id.clone(), lease);
            }
        }
    }

    /// Try immediate grant or enqueue. Does not block.
    pub fn try_acquire(&mut self, mut req: LlmLeaseRequest) -> LlmAcquireResult {
        req.enqueued_at = now_secs();
        if let Some(qerr) = self.quota.would_exceed(
            req.identity_id.as_deref(),
            self.cfg.daily_global,
            self.cfg.daily_identity,
            req.estimated_tokens,
        ) {
            return LlmAcquireResult::Rejected {
                request_id: req.request_id,
                reason: format!("LLM 日配额已用尽（{qerr}）"),
                code: qerr.into(),
            };
        }
        let reason = self.can_grant_now(&req);
        if reason.is_none() && self.queued.is_empty() {
            let lease = self.grant_locked(&req);
            return LlmAcquireResult::Granted { lease };
        }
        if self.queued.len() >= self.cfg.queue_max {
            return LlmAcquireResult::Rejected {
                request_id: req.request_id,
                reason: "LLM 排队已满".into(),
                code: "queue_full".into(),
            };
        }
        let rid = req.request_id.clone();
        let qreason = reason.unwrap_or_else(|| "queued".into());
        self.queued.insert(rid.clone(), req);
        self.wake_best();
        // might have been granted immediately by wake
        if let Some(lease) = self.pending_grants.remove(&rid) {
            return LlmAcquireResult::Granted { lease };
        }
        if self.in_flight.contains_key(&rid) {
            if let Some(lease) = self.in_flight.get(&rid).cloned() {
                return LlmAcquireResult::Granted { lease };
            }
        }
        LlmAcquireResult::Queued {
            request_id: rid,
            reason: qreason,
            queue_len: self.queued.len(),
        }
    }

    /// Poll a queued request.
    pub fn poll(&mut self, request_id: &str) -> LlmAcquireResult {
        if let Some(lease) = self.pending_grants.remove(request_id) {
            return LlmAcquireResult::Granted { lease };
        }
        if let Some(lease) = self.in_flight.get(request_id).cloned() {
            return LlmAcquireResult::Granted { lease };
        }
        if let Some((code, reason)) = self.rejected.remove(request_id) {
            return LlmAcquireResult::Rejected {
                request_id: request_id.to_string(),
                reason,
                code,
            };
        }
        if let Some(req) = self.queued.get(request_id) {
            // timeout check
            if now_secs() - req.enqueued_at > self.cfg.grant_timeout_secs {
                self.queued.remove(request_id);
                return LlmAcquireResult::Rejected {
                    request_id: request_id.to_string(),
                    reason: "等待 LLM 槽位超时".into(),
                    code: "wait_timeout".into(),
                };
            }
            self.wake_best();
            if let Some(lease) = self.pending_grants.remove(request_id) {
                return LlmAcquireResult::Granted { lease };
            }
            return LlmAcquireResult::Queued {
                request_id: request_id.to_string(),
                reason: "queued".into(),
                queue_len: self.queued.len(),
            };
        }
        LlmAcquireResult::Rejected {
            request_id: request_id.to_string(),
            reason: "unknown request".into(),
            code: "not_found".into(),
        }
    }

    pub fn release(&mut self, request_id: &str) -> bool {
        let gone = self.in_flight.remove(request_id).is_some();
        self.pending_grants.remove(request_id);
        if gone {
            self.wake_best();
        }
        gone
    }

    pub fn cancel_wait(&mut self, request_id: &str) -> bool {
        let q = self.queued.remove(request_id).is_some();
        self.pending_grants.remove(request_id);
        q
    }

    pub fn charge_quota(&mut self, identity_id: Option<&str>, amount: i64) {
        self.quota.charge(identity_id, amount);
    }

    pub fn status(&mut self) -> Value {
        let now = now_secs();
        let in_flight: Vec<_> = self
            .in_flight
            .values()
            .map(|l| {
                json!({
                    "request_id": l.request_id,
                    "source": l.source,
                    "identity_id": l.identity_id,
                    "process_id": l.process_id,
                    "priority": l.priority,
                    "is_owner": l.is_owner,
                    "held_ms": ((now - l.granted_at).max(0.0) * 1000.0) as i64,
                })
            })
            .collect();
        let mut queued_reqs: Vec<_> = self.queued.values().cloned().collect();
        queued_reqs.sort_by(|a, b| {
            self.score(b)
                .partial_cmp(&self.score(a))
                .unwrap_or(std::cmp::Ordering::Equal)
                .then(
                    a.enqueued_at
                        .partial_cmp(&b.enqueued_at)
                        .unwrap_or(std::cmp::Ordering::Equal),
                )
        });
        let queued: Vec<_> = queued_reqs
            .iter()
            .map(|r| {
                json!({
                    "request_id": r.request_id,
                    "source": r.source,
                    "identity_id": r.identity_id,
                    "priority": r.priority,
                    "wait_ms": ((now - r.enqueued_at).max(0.0) * 1000.0) as i64,
                    "score": self.score(r),
                })
            })
            .collect();
        let quota = self
            .quota
            .snapshot(self.cfg.daily_global, self.cfg.daily_identity);
        json!({
            "in_flight": in_flight,
            "queued": queued,
            "config": {
                "llm_max_in_flight": self.cfg.max_in_flight,
                "llm_max_in_flight_per_identity": self.cfg.max_per_identity,
                "llm_owner_reserve_slots": self.cfg.owner_reserve,
                "llm_queue_max": self.cfg.queue_max,
                "llm_fairness_wait_weight": self.cfg.fairness_wait_weight,
                "llm_daily_token_budget_global": self.cfg.daily_global,
                "llm_daily_token_budget_per_identity": self.cfg.daily_identity,
            },
            "quota": quota,
            "counts": {
                "in_flight": in_flight.len(),
                "queued": queued.len(),
            },
            "backend": "rust",
        })
    }
}

impl LlmAcquireResult {
    pub fn to_dict(&self) -> Value {
        match self {
            Self::Granted { lease } => json!({
                "status": "granted",
                "lease": lease.to_dict(),
            }),
            Self::Queued {
                request_id,
                reason,
                queue_len,
            } => json!({
                "status": "queued",
                "request_id": request_id,
                "reason": reason,
                "queue_len": queue_len,
            }),
            Self::Rejected {
                request_id,
                reason,
                code,
            } => json!({
                "status": "rejected",
                "request_id": request_id,
                "reason": reason,
                "code": code,
            }),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn owner_gets_slot_before_worker_when_reserve() {
        let mut c = LlmAdmissionController::new(LlmAdmissionConfig {
            max_in_flight: 2,
            owner_reserve: 1,
            max_per_identity: 4,
            ..Default::default()
        });
        let w1 = LlmLeaseRequest::new("workforce", 30, Some("a".into()), None);
        let r1 = c.try_acquire(w1);
        assert!(matches!(r1, LlmAcquireResult::Granted { .. }));
        // one free but reserved for owner → workforce queues
        let w2 = LlmLeaseRequest::new("workforce", 30, Some("b".into()), None);
        let r2 = c.try_acquire(w2);
        assert!(matches!(r2, LlmAcquireResult::Queued { .. }));
        // owner can still get the reserved slot
        let o = LlmLeaseRequest::new("chat", 100, None, None);
        let r3 = c.try_acquire(o);
        assert!(matches!(r3, LlmAcquireResult::Granted { .. }));
    }

    #[test]
    fn release_wakes_queued() {
        let mut c = LlmAdmissionController::new(LlmAdmissionConfig {
            max_in_flight: 1,
            owner_reserve: 0,
            ..Default::default()
        });
        let a = LlmLeaseRequest::new("chat", 100, None, None);
        let ga = c.try_acquire(a);
        let LlmAcquireResult::Granted { lease } = ga else {
            panic!("expected grant");
        };
        let b = LlmLeaseRequest::new("chat", 100, None, None);
        let bid = b.request_id.clone();
        let qb = c.try_acquire(b);
        assert!(matches!(qb, LlmAcquireResult::Queued { .. }));
        c.release(&lease.request_id);
        let polled = c.poll(&bid);
        assert!(matches!(polled, LlmAcquireResult::Granted { .. }));
    }
}
