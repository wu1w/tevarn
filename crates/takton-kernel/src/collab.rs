//! Human-in-the-loop collaboration (P2 H2): interrupt, revise plan, approve, reject.

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

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PlanStep {
    pub id: String,
    pub text: String,
    pub status: String, // pending | active | done | skipped
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CollabSession {
    pub process_id: String,
    pub plan: Vec<PlanStep>,
    pub interrupted: bool,
    pub interrupt_reason: Option<String>,
    pub pending_approvals: Vec<ApprovalRequest>,
    pub revised_at: Option<f64>,
    pub updated_at: f64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ApprovalRequest {
    pub id: String,
    pub kind: String, // write | command | plan_change | other
    pub summary: String,
    pub detail: Value,
    pub status: String, // pending | approved | rejected
    pub created_at: f64,
    pub resolved_at: Option<f64>,
}

#[derive(Default)]
pub struct CollabHub {
    sessions: HashMap<String, CollabSession>,
}

impl CollabHub {
    pub fn ensure(&mut self, process_id: &str) -> &mut CollabSession {
        if !self.sessions.contains_key(process_id) {
            self.sessions.insert(
                process_id.to_string(),
                CollabSession {
                    process_id: process_id.to_string(),
                    plan: vec![],
                    interrupted: false,
                    interrupt_reason: None,
                    pending_approvals: vec![],
                    revised_at: None,
                    updated_at: now_secs(),
                },
            );
        }
        self.sessions.get_mut(process_id).unwrap()
    }

    pub fn set_plan(&mut self, process_id: &str, steps: Vec<String>) -> Value {
        let s = self.ensure(process_id);
        s.plan = steps
            .into_iter()
            .enumerate()
            .map(|(i, text)| PlanStep {
                id: format!("s{i}"),
                text,
                status: if i == 0 { "active" } else { "pending" }.into(),
            })
            .collect();
        s.revised_at = Some(now_secs());
        s.updated_at = now_secs();
        json!(s)
    }

    pub fn revise_plan(&mut self, process_id: &str, steps: Vec<String>) -> Value {
        let s = self.ensure(process_id);
        s.interrupted = false;
        s.interrupt_reason = None;
        self.set_plan(process_id, steps)
    }

    pub fn interrupt(&mut self, process_id: &str, reason: &str) -> Value {
        let s = self.ensure(process_id);
        s.interrupted = true;
        s.interrupt_reason = Some(reason.to_string());
        s.updated_at = now_secs();
        json!(s)
    }

    pub fn resume_collab(&mut self, process_id: &str) -> Value {
        let s = self.ensure(process_id);
        s.interrupted = false;
        s.interrupt_reason = None;
        s.updated_at = now_secs();
        json!(s)
    }

    pub fn request_approval(
        &mut self,
        process_id: &str,
        kind: &str,
        summary: &str,
        detail: Value,
    ) -> ApprovalRequest {
        let req = ApprovalRequest {
            id: short_id(),
            kind: kind.to_string(),
            summary: summary.chars().take(500).collect(),
            detail,
            status: "pending".into(),
            created_at: now_secs(),
            resolved_at: None,
        };
        let s = self.ensure(process_id);
        s.pending_approvals.push(req.clone());
        s.updated_at = now_secs();
        req
    }

    pub fn resolve_approval(
        &mut self,
        process_id: &str,
        approval_id: &str,
        approve: bool,
    ) -> Result<ApprovalRequest, String> {
        let s = self.ensure(process_id);
        let req = s
            .pending_approvals
            .iter_mut()
            .find(|a| a.id == approval_id)
            .ok_or_else(|| format!("unknown approval {approval_id}"))?;
        if req.status != "pending" {
            return Err(format!("approval already {}", req.status));
        }
        req.status = if approve { "approved" } else { "rejected" }.into();
        req.resolved_at = Some(now_secs());
        s.updated_at = now_secs();
        Ok(req.clone())
    }

    pub fn get(&self, process_id: &str) -> Option<&CollabSession> {
        self.sessions.get(process_id)
    }

    pub fn is_blocked(&self, process_id: &str) -> bool {
        self.sessions
            .get(process_id)
            .map(|s| s.interrupted || s.pending_approvals.iter().any(|a| a.status == "pending" && a.kind == "write"))
            .unwrap_or(false)
    }

    pub fn drop_process(&mut self, process_id: &str) {
        self.sessions.remove(process_id);
    }

    pub fn status(&self) -> Value {
        json!({
            "sessions": self.sessions.len(),
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn interrupt_and_approve() {
        let mut h = CollabHub::default();
        h.set_plan("p1", vec!["read".into(), "edit".into()]);
        h.interrupt("p1", "user pause");
        assert!(h.is_blocked("p1"));
        h.resume_collab("p1");
        let a = h.request_approval("p1", "write", "edit file", json!({}));
        h.resolve_approval("p1", &a.id, true).unwrap();
        assert_eq!(
            h.get("p1").unwrap().pending_approvals[0].status,
            "approved"
        );
    }
}
