//! Global run admission gate (P0 gap fill).
//!
//! Caps concurrent agent runs across sessions. Queues waiters by priority class
//! (lower priority number = higher priority). Replaces "everyone races past
//! schedule_run" with real wait/grant semantics.

use std::collections::{HashMap, VecDeque};
use std::time::{SystemTime, UNIX_EPOCH};

use serde::{Deserialize, Serialize};
use serde_json::{json, Value};

use crate::scheduler::PriorityClass;

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
pub struct RunLease {
    pub lease_id: String,
    pub process_id: String,
    pub granted_at: f64,
    pub priority: i32,
}

#[derive(Debug, Clone)]
struct Waiter {
    process_id: String,
    priority: i32,
    enqueued_at: f64,
    request_id: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(tag = "status", rename_all = "snake_case")]
pub enum RunGateResult {
    Granted { lease: RunLease },
    Queued {
        request_id: String,
        process_id: String,
        queue_len: usize,
        priority: i32,
    },
    Rejected {
        request_id: String,
        reason: String,
        code: String,
    },
}

impl RunGateResult {
    pub fn to_dict(&self) -> Value {
        match self {
            Self::Granted { lease } => json!({
                "status": "granted",
                "lease": lease,
            }),
            Self::Queued {
                request_id,
                process_id,
                queue_len,
                priority,
            } => json!({
                "status": "queued",
                "request_id": request_id,
                "process_id": process_id,
                "queue_len": queue_len,
                "priority": priority,
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

pub struct RunGate {
    max_concurrent: usize,
    /// process_id -> lease
    in_flight: HashMap<String, RunLease>,
    /// FIFO within same priority; we store all and pick min priority + earliest
    waiters: VecDeque<Waiter>,
    /// granted leases waiting for poll (request_id -> lease)
    pending: HashMap<String, RunLease>,
    rejected: HashMap<String, (String, String)>,
    grant_timeout_secs: f64,
}

impl Default for RunGate {
    fn default() -> Self {
        Self::new(4)
    }
}

impl RunGate {
    pub fn new(max_concurrent: usize) -> Self {
        Self {
            max_concurrent: max_concurrent.max(1),
            in_flight: HashMap::new(),
            waiters: VecDeque::new(),
            pending: HashMap::new(),
            rejected: HashMap::new(),
            grant_timeout_secs: 300.0,
        }
    }

    pub fn set_max_concurrent(&mut self, n: usize) {
        self.max_concurrent = n.max(1);
    }

    fn grant(&mut self, process_id: &str, priority: i32) -> RunLease {
        let lease = RunLease {
            lease_id: short_id(),
            process_id: process_id.to_string(),
            granted_at: now_secs(),
            priority,
        };
        self.in_flight
            .insert(process_id.to_string(), lease.clone());
        lease
    }

    fn pick_best_waiter_index(&self) -> Option<usize> {
        if self.waiters.is_empty() {
            return None;
        }
        let mut best_i = 0usize;
        for (i, w) in self.waiters.iter().enumerate().skip(1) {
            let b = &self.waiters[best_i];
            if w.priority < b.priority
                || (w.priority == b.priority && w.enqueued_at < b.enqueued_at)
            {
                best_i = i;
            }
        }
        Some(best_i)
    }

    fn wake_one(&mut self) {
        if self.in_flight.len() >= self.max_concurrent {
            return;
        }
        let Some(i) = self.pick_best_waiter_index() else {
            return;
        };
        if let Some(w) = self.waiters.remove(i) {
            // skip if process already flying
            if self.in_flight.contains_key(&w.process_id) {
                self.wake_one();
                return;
            }
            let lease = self.grant(&w.process_id, w.priority);
            self.pending.insert(w.request_id, lease);
        }
    }

    /// Try grant or enqueue. One active lease per process_id.
    pub fn try_acquire(
        &mut self,
        process_id: &str,
        priority_class: Option<&str>,
        priority: Option<i32>,
    ) -> RunGateResult {
        let prio = priority.unwrap_or_else(|| {
            PriorityClass::parse(priority_class.unwrap_or("workforce")).as_i32()
        });
        // already holding
        if let Some(lease) = self.in_flight.get(process_id) {
            return RunGateResult::Granted {
                lease: lease.clone(),
            };
        }
        // already waiting
        if let Some(w) = self.waiters.iter().find(|w| w.process_id == process_id) {
            return RunGateResult::Queued {
                request_id: w.request_id.clone(),
                process_id: process_id.to_string(),
                queue_len: self.waiters.len(),
                priority: w.priority,
            };
        }

        if self.in_flight.len() < self.max_concurrent {
            let lease = self.grant(process_id, prio);
            return RunGateResult::Granted { lease };
        }

        let request_id = short_id();
        self.waiters.push_back(Waiter {
            process_id: process_id.to_string(),
            priority: prio,
            enqueued_at: now_secs(),
            request_id: request_id.clone(),
        });
        RunGateResult::Queued {
            request_id,
            process_id: process_id.to_string(),
            queue_len: self.waiters.len(),
            priority: prio,
        }
    }

    pub fn poll(&mut self, request_id: &str) -> RunGateResult {
        if let Some(lease) = self.pending.remove(request_id) {
            return RunGateResult::Granted { lease };
        }
        // Snapshot waiter fields first to avoid borrow conflicts with wake_one.
        let waiter_snap = self
            .waiters
            .iter()
            .find(|w| w.request_id == request_id)
            .map(|w| {
                (
                    w.process_id.clone(),
                    w.priority,
                    w.enqueued_at,
                )
            });
        if let Some((pid, priority, enqueued_at)) = waiter_snap {
            if now_secs() - enqueued_at > self.grant_timeout_secs {
                self.waiters.retain(|x| x.request_id != request_id);
                return RunGateResult::Rejected {
                    request_id: request_id.to_string(),
                    reason: format!("run gate wait timeout for {pid}"),
                    code: "wait_timeout".into(),
                };
            }
            self.wake_one();
            if let Some(lease) = self.pending.remove(request_id) {
                return RunGateResult::Granted { lease };
            }
            return RunGateResult::Queued {
                request_id: request_id.to_string(),
                process_id: pid,
                queue_len: self.waiters.len(),
                priority,
            };
        }
        if let Some((code, reason)) = self.rejected.remove(request_id) {
            return RunGateResult::Rejected {
                request_id: request_id.to_string(),
                reason,
                code,
            };
        }
        // maybe already in flight under process (direct grant without queue id)
        RunGateResult::Rejected {
            request_id: request_id.to_string(),
            reason: "unknown run gate request".into(),
            code: "not_found".into(),
        }
    }

    pub fn release(&mut self, process_id: &str) -> bool {
        let gone = self.in_flight.remove(process_id).is_some();
        // drop pending for same process
        self.pending.retain(|_, l| l.process_id != process_id);
        self.waiters.retain(|w| w.process_id != process_id);
        if gone {
            self.wake_one();
        }
        gone
    }

    pub fn status(&self) -> Value {
        json!({
            "max_concurrent": self.max_concurrent,
            "in_flight": self.in_flight.values().cloned().collect::<Vec<_>>(),
            "queued": self.waiters.iter().map(|w| json!({
                "request_id": w.request_id,
                "process_id": w.process_id,
                "priority": w.priority,
                "wait_ms": ((now_secs() - w.enqueued_at).max(0.0) * 1000.0) as i64,
            })).collect::<Vec<_>>(),
            "counts": {
                "in_flight": self.in_flight.len(),
                "queued": self.waiters.len(),
            },
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn queues_when_full_and_wakes_by_priority() {
        let mut g = RunGate::new(1);
        let a = g.try_acquire("p1", Some("background"), None);
        assert!(matches!(a, RunGateResult::Granted { .. }));
        let b = g.try_acquire("p2", Some("foreground"), None);
        let RunGateResult::Queued { request_id, .. } = b else {
            panic!("expected queue");
        };
        g.release("p1");
        let polled = g.poll(&request_id);
        assert!(matches!(polled, RunGateResult::Granted { .. }));
    }
}
