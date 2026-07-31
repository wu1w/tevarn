//! Evolution control plane in Rust (authority).
//!
//! Proposals are draft-only until human confirm + skill gate.
//! `auto_apply` is hard-false (align skill_gate::EVOLUTION_AUTO_APPLY).

use std::collections::HashMap;
use std::time::{SystemTime, UNIX_EPOCH};

use serde::{Deserialize, Serialize};
use serde_json::{json, Value};

use crate::skill_gate::EVOLUTION_AUTO_APPLY;

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
pub struct EvolutionProposal {
    pub id: String,
    pub kind: String, // skill | policy | caps | prompt
    pub title: String,
    pub body: String,
    pub identity: Option<String>,
    pub status: String, // draft | reviewing | approved | rejected | applied | failed
    pub score: f64,
    pub created_at: f64,
    pub resolved_at: Option<f64>,
    pub resolved_by: Option<String>,
    pub apply_log: Vec<String>,
    pub meta: Value,
}

pub struct EvolutionGate {
    proposals: HashMap<String, EvolutionProposal>,
    rejected: u64,
    approved: u64,
    applied: u64,
    blocked_auto: u64,
}

impl Default for EvolutionGate {
    fn default() -> Self {
        Self {
            proposals: HashMap::new(),
            rejected: 0,
            approved: 0,
            applied: 0,
            blocked_auto: 0,
        }
    }
}

impl EvolutionGate {
    pub fn policy() -> Value {
        json!({
            "auto_apply": EVOLUTION_AUTO_APPLY,
            "auto_apply_live_caps": false,
            "require_skill_gate": true,
            "require_human_confirm": true,
            "authority": "rust",
            "python_adapter_only": true,
        })
    }

    pub fn submit(
        &mut self,
        kind: &str,
        title: &str,
        body: &str,
        identity: Option<&str>,
        score: f64,
        meta: Value,
    ) -> EvolutionProposal {
        let p = EvolutionProposal {
            id: short_id(),
            kind: kind.to_string(),
            title: title.chars().take(200).collect(),
            body: body.chars().take(8000).collect(),
            identity: identity.map(|s| s.to_string()),
            status: "draft".into(),
            score: score.clamp(0.0, 1.0),
            created_at: now_secs(),
            resolved_at: None,
            resolved_by: None,
            apply_log: vec!["submitted".into()],
            meta,
        };
        self.proposals.insert(p.id.clone(), p.clone());
        p
    }

    pub fn get(&self, id: &str) -> Option<&EvolutionProposal> {
        self.proposals.get(id)
    }

    pub fn list(&self, status: Option<&str>, limit: usize) -> Vec<EvolutionProposal> {
        let mut v: Vec<_> = self
            .proposals
            .values()
            .filter(|p| status.map(|s| p.status == s).unwrap_or(true))
            .cloned()
            .collect();
        v.sort_by(|a, b| b.created_at.partial_cmp(&a.created_at).unwrap_or(std::cmp::Ordering::Equal));
        v.into_iter().take(limit.max(1)).collect()
    }

    pub fn approve(&mut self, id: &str, by: &str) -> Result<EvolutionProposal, String> {
        let p = self
            .proposals
            .get_mut(id)
            .ok_or_else(|| format!("unknown proposal {id}"))?;
        if p.status != "draft" && p.status != "reviewing" {
            return Err(format!("cannot approve status={}", p.status));
        }
        p.status = "approved".into();
        p.resolved_at = Some(now_secs());
        p.resolved_by = Some(by.to_string());
        p.apply_log.push(format!("approved_by:{by}"));
        self.approved = self.approved.saturating_add(1);
        Ok(p.clone())
    }

    pub fn reject(&mut self, id: &str, by: &str, reason: &str) -> Result<EvolutionProposal, String> {
        let p = self
            .proposals
            .get_mut(id)
            .ok_or_else(|| format!("unknown proposal {id}"))?;
        if p.status == "applied" {
            return Err("already applied".into());
        }
        p.status = "rejected".into();
        p.resolved_at = Some(now_secs());
        p.resolved_by = Some(by.to_string());
        p.apply_log.push(format!("rejected_by:{by}:{reason}"));
        self.rejected = self.rejected.saturating_add(1);
        Ok(p.clone())
    }

    /// Apply is never automatic. Caller must pass approved + skill verified.
    pub fn try_apply(
        &mut self,
        id: &str,
        by: &str,
        skill_loadable: bool,
    ) -> Result<EvolutionProposal, String> {
        if EVOLUTION_AUTO_APPLY {
            // hard invariant — should never be true
            self.blocked_auto = self.blocked_auto.saturating_add(1);
            return Err("EVOLUTION_AUTO_APPLY must be false".into());
        }
        let p = self
            .proposals
            .get_mut(id)
            .ok_or_else(|| format!("unknown proposal {id}"))?;
        if p.status != "approved" {
            return Err(format!("not approved (status={})", p.status));
        }
        if p.kind == "skill" && !skill_loadable {
            p.apply_log
                .push("blocked:skill_not_loadable".into());
            return Err("skill gate: package not verified+active".into());
        }
        p.status = "applied".into();
        p.resolved_at = Some(now_secs());
        p.resolved_by = Some(by.to_string());
        p.apply_log.push(format!("applied_by:{by}"));
        self.applied = self.applied.saturating_add(1);
        Ok(p.clone())
    }

    /// Block any path that tries to auto-apply without human.
    pub fn block_auto_apply(&mut self, reason: &str) -> Value {
        self.blocked_auto = self.blocked_auto.saturating_add(1);
        json!({
            "ok": false,
            "blocked": true,
            "reason": reason,
            "auto_apply": false,
            "blocked_total": self.blocked_auto,
        })
    }

    pub fn status(&self) -> Value {
        json!({
            "proposals": self.proposals.len(),
            "draft": self.proposals.values().filter(|p| p.status == "draft").count(),
            "approved": self.approved,
            "rejected": self.rejected,
            "applied": self.applied,
            "blocked_auto": self.blocked_auto,
            "policy": Self::policy(),
            "authority": "rust",
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn no_auto_apply() {
        let mut g = EvolutionGate::default();
        let p = g.submit("skill", "t", "body", None, 0.8, json!({}));
        assert!(g.try_apply(&p.id, "sys", true).is_err());
        g.approve(&p.id, "human").unwrap();
        assert!(g.try_apply(&p.id, "human", false).is_err());
        let a = g.try_apply(&p.id, "human", true).unwrap();
        assert_eq!(a.status, "applied");
        assert_eq!(EVOLUTION_AUTO_APPLY, false);
    }
}
