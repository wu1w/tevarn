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

    /// Business analysis fully in Rust (replaces Python rule engine).
    ///
    /// Input snapshot (from SQL feeder or pure kernel stats):
    /// ```json
    /// {
    ///   "identity": "...",
    ///   "capabilities": ["file_read", ...],
    ///   "done": 10, "failed": 2,
    ///   "recent_done": ["instr", ...],
    ///   "recent_errors": ["err", ...],
    ///   "approved_caps": {"terminal": 3},
    ///   "tool_attempts": {"command": 20},
    ///   "tool_denials": {"command": 12},
    ///   "thresholds": { "min_samples": 5, "deprecate_rate": 0.5, ... }
    /// }
    /// ```
    pub fn analyze(&mut self, snapshot: &Value) -> Vec<EvolutionProposal> {
        let identity = snapshot
            .get("identity")
            .and_then(|v| v.as_str())
            .unwrap_or("unknown");
        let caps: Vec<String> = snapshot
            .get("capabilities")
            .and_then(|v| v.as_array())
            .map(|a| {
                a.iter()
                    .filter_map(|x| x.as_str().map(|s| s.to_string()))
                    .collect()
            })
            .unwrap_or_default();
        let done = snapshot.get("done").and_then(|v| v.as_u64()).unwrap_or(0);
        let failed = snapshot.get("failed").and_then(|v| v.as_u64()).unwrap_or(0);
        let total = done + failed;
        let success_rate = if total > 0 {
            done as f64 / total as f64
        } else {
            0.0
        };
        let thr = snapshot.get("thresholds").cloned().unwrap_or(json!({}));
        let min_samples = thr.get("min_samples").and_then(|v| v.as_u64()).unwrap_or(5);
        let deprecate_rate = thr
            .get("deprecate_rate")
            .and_then(|v| v.as_f64())
            .unwrap_or(0.5);
        let caps_adjust_n = thr
            .get("caps_adjust_approvals")
            .and_then(|v| v.as_u64())
            .unwrap_or(2);
        let distill_min = thr
            .get("distill_min_done")
            .and_then(|v| v.as_u64())
            .unwrap_or(5);
        let distill_success = thr
            .get("distill_min_success")
            .and_then(|v| v.as_f64())
            .unwrap_or(0.8);
        let planner_fail = thr
            .get("planner_tune_fail_rate")
            .and_then(|v| v.as_f64())
            .unwrap_or(0.3);

        let pending_kinds: std::collections::HashSet<String> = self
            .proposals
            .values()
            .filter(|p| {
                p.status == "draft"
                    && p.identity.as_deref() == Some(identity)
            })
            .map(|p| p.kind.clone())
            .collect();

        let mut out = Vec::new();

        // Rule 1: memory_distill
        if done >= distill_min && success_rate >= distill_success && !pending_kinds.contains("memory_distill")
        {
            let recent: Vec<String> = snapshot
                .get("recent_done")
                .and_then(|v| v.as_array())
                .map(|a| {
                    a.iter()
                        .filter_map(|x| x.as_str().map(|s| s.chars().take(80).collect()))
                        .take(5)
                        .collect()
                })
                .unwrap_or_default();
            let pct = (success_rate * 100.0).round() as i64;
            let title = format!("沉淀工作方法论（{done} 单，成功率 {pct}%）");
            let body = format!(
                "完成 {done} 单 / 失败 {failed}，成功率 {pct}%。近期：{}",
                recent.join("；")
            );
            let p = self.submit(
                "memory_distill",
                &title,
                &body,
                Some(identity),
                success_rate,
                json!({
                    "memory_kind": "methodology",
                    "stats": {"done": done, "failed": failed, "success_rate": success_rate},
                    "recent": recent,
                    "source": "rust_analyze",
                }),
            );
            out.push(p);
        }

        // Rule 2: planner_tune on high fail rate
        if total >= distill_min
            && success_rate < (1.0 - planner_fail)
            && !pending_kinds.contains("planner_tune")
        {
            let errors: Vec<String> = snapshot
                .get("recent_errors")
                .and_then(|v| v.as_array())
                .map(|a| {
                    a.iter()
                        .filter_map(|x| x.as_str().map(|s| s.chars().take(80).collect()))
                        .take(3)
                        .collect()
                })
                .unwrap_or_default();
            let fail_rate = 1.0 - success_rate;
            let fpct = (fail_rate * 100.0).round() as i64;
            let title = format!("工作方式检讨（失败率 {fpct}%）");
            let body = format!(
                "完成 {done} / 失败 {failed}，失败率 {fpct}%。错误：{}",
                errors.join("；")
            );
            let p = self.submit(
                "planner_tune",
                &title,
                &body,
                Some(identity),
                fail_rate,
                json!({
                    "planner_prefs": {"max_task_scope": "narrow", "verify_steps": true},
                    "stats": {"done": done, "failed": failed},
                    "source": "rust_analyze",
                }),
            );
            out.push(p);
        }

        // Rule 3: caps_adjust from repeated approvals
        if let Some(obj) = snapshot.get("approved_caps").and_then(|v| v.as_object()) {
            for (cap, cnt_v) in obj {
                let count = cnt_v.as_u64().unwrap_or(0);
                if count >= caps_adjust_n && !caps.iter().any(|c| c == cap) {
                    let kind_key = format!("caps_adjust:{cap}");
                    if pending_kinds.iter().any(|k| k.starts_with("caps_adjust")) {
                        continue;
                    }
                    let title = format!("能力「{cap}」并入编制（已获批 {count} 次）");
                    let body = format!(
                        "能力 {cap} 提权获批 {count} 次，建议并入身份权限档案。"
                    );
                    let p = self.submit(
                        "caps_adjust",
                        &title,
                        &body,
                        Some(identity),
                        0.7,
                        json!({
                            "add_capabilities": [cap],
                            "before": {"capabilities": caps},
                            "source": "rust_analyze",
                            "kind_key": kind_key,
                        }),
                    );
                    out.push(p);
                }
            }
        }

        // Rule 4: tool_deprecate high denial rate
        let attempts = snapshot
            .get("tool_attempts")
            .and_then(|v| v.as_object())
            .cloned()
            .unwrap_or_default();
        let denials = snapshot
            .get("tool_denials")
            .and_then(|v| v.as_object())
            .cloned()
            .unwrap_or_default();
        for (target, n_v) in &attempts {
            let n = n_v.as_u64().unwrap_or(0);
            if n < min_samples || !caps.iter().any(|c| c == target) {
                continue;
            }
            let d = denials.get(target).and_then(|v| v.as_u64()).unwrap_or(0);
            let rate = d as f64 / n as f64;
            if rate >= deprecate_rate && !pending_kinds.contains("tool_deprecate") {
                let rpct = (rate * 100.0).round() as i64;
                let title = format!("淘汰能力「{target}」（拒绝率 {rpct}%）");
                let body = format!("能力 {target} 共 {n} 次调用、{d} 次被拒（{rpct}%）。");
                let p = self.submit(
                    "tool_deprecate",
                    &title,
                    &body,
                    Some(identity),
                    rate,
                    json!({
                        "remove_capabilities": [target],
                        "before": {"capabilities": caps},
                        "stats": {"attempts": n, "denials": d},
                        "source": "rust_analyze",
                    }),
                );
                out.push(p);
                break; // one deprecate suggestion per analyze
            }
        }

        out
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
            "analyzer": "rust",
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

    #[test]
    fn analyze_distill_and_deprecate() {
        let mut g = EvolutionGate::default();
        let snap = json!({
            "identity": "alice",
            "capabilities": ["command", "file_read"],
            "done": 10,
            "failed": 1,
            "recent_done": ["fix a", "fix b"],
            "recent_errors": [],
            "approved_caps": {"terminal": 3},
            "tool_attempts": {"command": 20},
            "tool_denials": {"command": 15},
            "thresholds": {
                "min_samples": 5,
                "deprecate_rate": 0.5,
                "caps_adjust_approvals": 2,
                "distill_min_done": 5,
                "distill_min_success": 0.8,
                "planner_tune_fail_rate": 0.3
            }
        });
        let props = g.analyze(&snap);
        assert!(props.iter().any(|p| p.kind == "memory_distill"));
        assert!(props.iter().any(|p| p.kind == "tool_deprecate" || p.kind == "caps_adjust"));
    }
}
