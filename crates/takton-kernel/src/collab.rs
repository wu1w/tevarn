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
            .map(|s| {
                s.interrupted
                    || s.pending_approvals.iter().any(|a| {
                        a.status == "pending" && (a.kind == "write" || a.kind == "command")
                    })
            })
            .unwrap_or(false)
    }

    /// Why mediate should deny a write/command for this process (first-class collab gate).
    pub fn block_reason(&self, process_id: &str, action: &str, target: &str) -> Option<String> {
        let s = self.sessions.get(process_id)?;
        if s.interrupted {
            return Some(format!(
                "collab:interrupted:{}",
                s.interrupt_reason.as_deref().unwrap_or("user")
            ));
        }
        let kind_needed = collab_kind_for(action, target);
        if kind_needed.is_empty() {
            return None;
        }
        if s.pending_approvals
            .iter()
            .any(|a| a.status == "pending" && (a.kind == kind_needed || a.kind == "write" || a.kind == "command"))
        {
            return Some(format!("collab:pending_approval:{kind_needed}"));
        }
        None
    }

    /// True when this action/target is subject to collab write/command gating.
    pub fn is_gated_action(action: &str, target: &str) -> bool {
        !collab_kind_for(action, target).is_empty()
    }

    pub fn drop_process(&mut self, process_id: &str) {
        self.sessions.remove(process_id);
    }

    pub fn status(&self) -> Value {
        let pending: usize = self
            .sessions
            .values()
            .map(|s| s.pending_approvals.iter().filter(|a| a.status == "pending").count())
            .sum();
        let interrupted = self.sessions.values().filter(|s| s.interrupted).count();
        json!({
            "sessions": self.sessions.len(),
            "interrupted": interrupted,
            "pending_approvals": pending,
            "first_class_gate": true,
            "gated_kinds": ["write", "command"],
        })
    }
}

/// Map mediate action/target → collab approval kind (empty = not gated).
fn collab_kind_for(action: &str, target: &str) -> &'static str {
    let a = action.to_lowercase();
    let t = target.to_lowercase();
    let write_tools = [
        "file_write",
        "edit",
        "apply_patch",
        "write",
        "file_edit",
        "delete",
        "file_delete",
    ];
    let cmd_tools = ["command", "terminal", "shell", "python", "bash", "powershell"];
    if a.contains("write")
        || a == "tool_call" && write_tools.iter().any(|w| t == *w || t.contains(w))
        || write_tools.iter().any(|w| t == *w)
    {
        return "write";
    }
    if a.contains("command")
        || a.contains("exec")
        || a == "tool_call" && cmd_tools.iter().any(|w| t == *w || t.contains(w))
        || cmd_tools.iter().any(|w| t == *w)
    {
        return "command";
    }
    ""
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
        assert!(h
            .block_reason("p1", "tool_call", "file_write")
            .unwrap()
            .contains("interrupted"));
        h.resume_collab("p1");
        let a = h.request_approval("p1", "write", "edit file", json!({}));
        assert!(h.is_blocked("p1"));
        assert!(CollabHub::is_gated_action("tool_call", "file_write"));
        h.resolve_approval("p1", &a.id, true).unwrap();
        assert_eq!(
            h.get("p1").unwrap().pending_approvals[0].status,
            "approved"
        );
        assert!(!h.is_blocked("p1"));
    }
}
