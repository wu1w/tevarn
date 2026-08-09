//! Product-level domain event bus (R4) — seq + recent ring + kind map.
//! Complements hash-chain audit; used by UI/CLI subscribe paths.

use std::collections::VecDeque;
use std::time::{SystemTime, UNIX_EPOCH};

use serde::{Deserialize, Serialize};
use serde_json::{json, Value};

fn now_secs() -> f64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs_f64())
        .unwrap_or(0.0)
}

const RECENT_MAX: usize = 500;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct DomainEvent {
    pub seq: u64,
    pub topic: String,
    pub ts: f64,
    pub payload: Value,
}

pub struct DomainEventBus {
    recent: VecDeque<DomainEvent>,
    seq: u64,
}

impl Default for DomainEventBus {
    fn default() -> Self {
        Self {
            recent: VecDeque::with_capacity(RECENT_MAX),
            seq: 0,
        }
    }
}

impl DomainEventBus {
    pub fn map_kernel_kind(kind: &str) -> Option<String> {
        let mapped = match kind {
            "inbox_enqueued" => "job.enqueued",
            "inbox_claimed" => "job.claimed",
            "inbox_done" => "job.done",
            "inbox_dead" => "job.dead",
            "inbox_retry" => "job.retry",
            "inbox_cancelled" => "job.cancelled",
            "inbox_requeued" => "job.requeued",
            "inbox_discarded" => "job.discarded",
            "inbox_reclaimed" => "job.reclaimed",
            "inbox_dropped" => "job.dropped",
            "inbox_overflow_drop" => "job.overflow",
            "process_created" => "process.created",
            "process_ended" => "process.ended",
            "process_suspended" => "process.suspended",
            "process_resumed" => "process.resumed",
            "policy.decision" => "policy.decision",
            "mediation" => "policy.mediation",
            "escalate" | "escalation_requested" => "approval.pending",
            "escalation_approved" | "escalation_denied" => "approval.resolved",
            "scheduler.queued" => "scheduler.queued",
            "scheduler.granted" => "scheduler.granted",
            "scheduler.released" => "scheduler.released",
            "scheduler.rejected" => "scheduler.rejected",
            other if other.starts_with("inbox_") => {
                return Some(format!("job.{}", &other[6..]));
            }
            other if other.starts_with("process_") => {
                return Some(format!("process.{}", &other[8..]));
            }
            _ => return None,
        };
        Some(mapped.into())
    }

    pub fn publish(&mut self, topic: &str, payload: Value) -> DomainEvent {
        self.seq = self.seq.saturating_add(1);
        let evt = DomainEvent {
            seq: self.seq,
            topic: topic.to_string(),
            ts: now_secs(),
            payload,
        };
        self.recent.push_back(evt.clone());
        while self.recent.len() > RECENT_MAX {
            self.recent.pop_front();
        }
        evt
    }

    pub fn publish_from_kernel(&mut self, kind: &str, process_id: &str, detail: Value) -> Option<DomainEvent> {
        let topic = Self::map_kernel_kind(kind)?;
        Some(self.publish(
            &topic,
            json!({
                "kind": kind,
                "process_id": process_id,
                "detail": detail,
            }),
        ))
    }

    pub fn current_seq(&self) -> u64 {
        self.seq
    }

    pub fn recent(
        &self,
        limit: usize,
        prefix: Option<&str>,
        since_ts: Option<f64>,
        after_seq: Option<u64>,
    ) -> Vec<DomainEvent> {
        let lim = limit.max(1).min(500);
        self.recent
            .iter()
            .filter(|e| {
                if let Some(st) = since_ts {
                    if e.ts <= st {
                        return false;
                    }
                }
                if let Some(asq) = after_seq {
                    if e.seq <= asq {
                        return false;
                    }
                }
                if let Some(p) = prefix {
                    if !e.topic.starts_with(p) {
                        return false;
                    }
                }
                true
            })
            .cloned()
            .rev()
            .take(lim)
            .collect::<Vec<_>>()
            .into_iter()
            .rev()
            .collect()
    }

    pub fn status(&self) -> Value {
        json!({
            "seq": self.seq,
            "recent_len": self.recent.len(),
            "max": RECENT_MAX,
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn map_and_recent() {
        let mut b = DomainEventBus::default();
        b.publish_from_kernel("inbox_claimed", "p1", json!({"x": 1}))
            .unwrap();
        let r = b.recent(10, Some("job."), None, None);
        assert_eq!(r.len(), 1);
        assert_eq!(r[0].topic, "job.claimed");
    }
}
