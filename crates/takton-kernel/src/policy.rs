//! Run policy: iteration budget + doom-loop detection (P0.5 E3).
//!
//! Kernel authority for long-run safety; Python loop consults these RPCs.

use std::collections::HashMap;
use std::time::{SystemTime, UNIX_EPOCH};

use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};

fn now_secs() -> f64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs_f64())
        .unwrap_or(0.0)
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct IterationBudgetState {
    pub process_id: String,
    pub max_total: u32,
    pub used: u32,
}

impl IterationBudgetState {
    pub fn remaining(&self) -> u32 {
        self.max_total.saturating_sub(self.used)
    }

    pub fn to_dict(&self) -> Value {
        json!({
            "process_id": self.process_id,
            "max_total": self.max_total,
            "used": self.used,
            "remaining": self.remaining(),
            "exhausted": self.used >= self.max_total,
        })
    }
}

#[derive(Debug, Clone)]
struct DoomState {
    last_fingerprint: Option<String>,
    streak: u32,
    threshold: u32,
    tripped: bool,
    last_tool: Option<String>,
}

impl DoomState {
    fn new(threshold: u32) -> Self {
        Self {
            last_fingerprint: None,
            streak: 0,
            threshold: threshold.max(2),
            tripped: false,
            last_tool: None,
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(tag = "status", rename_all = "snake_case")]
pub enum PolicyDecision {
    Allow {
        process_id: String,
    },
    Exhausted {
        process_id: String,
        reason: String,
        code: String,
        action: String,
    },
    DoomLoop {
        process_id: String,
        tool: String,
        streak: u32,
        threshold: u32,
        reason: String,
        code: String,
        action: String,
    },
}

impl PolicyDecision {
    pub fn to_dict(&self) -> Value {
        match self {
            Self::Allow { process_id } => json!({
                "status": "allow",
                "process_id": process_id,
            }),
            Self::Exhausted {
                process_id,
                reason,
                code,
                action,
            } => json!({
                "status": "exhausted",
                "process_id": process_id,
                "reason": reason,
                "code": code,
                "action": action,
            }),
            Self::DoomLoop {
                process_id,
                tool,
                streak,
                threshold,
                reason,
                code,
                action,
            } => json!({
                "status": "doom_loop",
                "process_id": process_id,
                "tool": tool,
                "streak": streak,
                "threshold": threshold,
                "reason": reason,
                "code": code,
                "action": action,
            }),
        }
    }

    pub fn is_blocking(&self) -> bool {
        !matches!(self, Self::Allow { .. })
    }
}

pub struct PolicySupervisor {
    budgets: HashMap<String, IterationBudgetState>,
    doom: HashMap<String, DoomState>,
    default_max_iterations: u32,
    default_doom_threshold: u32,
}

impl Default for PolicySupervisor {
    fn default() -> Self {
        Self::new(25, 3)
    }
}

impl PolicySupervisor {
    pub fn new(default_max_iterations: u32, default_doom_threshold: u32) -> Self {
        Self {
            budgets: HashMap::new(),
            doom: HashMap::new(),
            default_max_iterations: default_max_iterations.max(1),
            default_doom_threshold: default_doom_threshold.max(2),
        }
    }

    pub fn set_iteration_budget(&mut self, process_id: &str, max_total: u32) {
        self.budgets.insert(
            process_id.to_string(),
            IterationBudgetState {
                process_id: process_id.to_string(),
                max_total: max_total.max(1),
                used: 0,
            },
        );
    }

    pub fn ensure_budget(&mut self, process_id: &str) -> &mut IterationBudgetState {
        if !self.budgets.contains_key(process_id) {
            self.set_iteration_budget(process_id, self.default_max_iterations);
        }
        self.budgets.get_mut(process_id).unwrap()
    }

    /// Consume one iteration. Blocking if exhausted.
    pub fn iteration_consume(&mut self, process_id: &str) -> PolicyDecision {
        let b = self.ensure_budget(process_id);
        if b.used >= b.max_total {
            return PolicyDecision::Exhausted {
                process_id: process_id.to_string(),
                reason: format!(
                    "iteration budget exhausted ({}/{})",
                    b.used, b.max_total
                ),
                code: "iteration_exhausted".into(),
                action: "suspend_or_end".into(),
            };
        }
        b.used += 1;
        PolicyDecision::Allow {
            process_id: process_id.to_string(),
        }
    }

    pub fn iteration_refund(&mut self, process_id: &str) -> bool {
        if let Some(b) = self.budgets.get_mut(process_id) {
            if b.used > 0 {
                b.used -= 1;
                return true;
            }
        }
        false
    }

    pub fn iteration_status(&self, process_id: &str) -> Value {
        self.budgets
            .get(process_id)
            .map(|b| b.to_dict())
            .unwrap_or_else(|| {
                json!({
                    "process_id": process_id,
                    "max_total": self.default_max_iterations,
                    "used": 0,
                    "remaining": self.default_max_iterations,
                    "exhausted": false,
                })
            })
    }

    pub fn fingerprint(tool: &str, args: &Value) -> String {
        let raw = serde_json::to_string(args).unwrap_or_default();
        let normalized: String = raw.split_whitespace().collect::<Vec<_>>().join(" ");
        let mut hasher = Sha256::new();
        hasher.update(tool.as_bytes());
        hasher.update(b"|");
        hasher.update(normalized.as_bytes());
        let hex = hex::encode(hasher.finalize());
        format!("{tool}:{}", &hex[..16.min(hex.len())])
    }

    /// Record tool call; returns DoomLoop decision if thrashing.
    pub fn doom_record(
        &mut self,
        process_id: &str,
        tool: &str,
        args: &Value,
    ) -> PolicyDecision {
        let fp = Self::fingerprint(tool, args);
        let st = self
            .doom
            .entry(process_id.to_string())
            .or_insert_with(|| DoomState::new(self.default_doom_threshold));
        if st.last_fingerprint.as_deref() == Some(fp.as_str()) {
            st.streak += 1;
        } else {
            st.last_fingerprint = Some(fp);
            st.streak = 1;
        }
        st.last_tool = Some(tool.to_string());
        if st.streak >= st.threshold {
            st.tripped = true;
            return PolicyDecision::DoomLoop {
                process_id: process_id.to_string(),
                tool: tool.to_string(),
                streak: st.streak,
                threshold: st.threshold,
                reason: format!(
                    "doom loop: tool {tool} repeated {} times (threshold {})",
                    st.streak, st.threshold
                ),
                code: "doom_loop".into(),
                action: "ask_or_stop".into(),
            };
        }
        PolicyDecision::Allow {
            process_id: process_id.to_string(),
        }
    }

    pub fn doom_reset(&mut self, process_id: &str) {
        self.doom.remove(process_id);
    }

    pub fn doom_status(&self, process_id: &str) -> Value {
        match self.doom.get(process_id) {
            Some(st) => json!({
                "process_id": process_id,
                "tripped": st.tripped,
                "streak": st.streak,
                "threshold": st.threshold,
                "last_tool": st.last_tool,
                "last_fingerprint": st.last_fingerprint,
            }),
            None => json!({
                "process_id": process_id,
                "tripped": false,
                "streak": 0,
                "threshold": self.default_doom_threshold,
            }),
        }
    }

    pub fn drop_process(&mut self, process_id: &str) {
        self.budgets.remove(process_id);
        self.doom.remove(process_id);
    }

    pub fn status(&self) -> Value {
        json!({
            "budgets": self.budgets.len(),
            "doom_tracked": self.doom.len(),
            "default_max_iterations": self.default_max_iterations,
            "default_doom_threshold": self.default_doom_threshold,
            "ts": now_secs(),
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn iteration_exhausts() {
        let mut p = PolicySupervisor::new(2, 3);
        assert!(!p.iteration_consume("p1").is_blocking());
        assert!(!p.iteration_consume("p1").is_blocking());
        assert!(p.iteration_consume("p1").is_blocking());
    }

    #[test]
    fn doom_trips_on_repeat() {
        let mut p = PolicySupervisor::new(25, 3);
        let args = json!({"x": 1});
        assert!(!p.doom_record("p1", "cmd", &args).is_blocking());
        assert!(!p.doom_record("p1", "cmd", &args).is_blocking());
        assert!(p.doom_record("p1", "cmd", &args).is_blocking());
    }
}
