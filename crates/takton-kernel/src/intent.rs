//! Intent declaration → minimal capability synthesis (P0-B).

use std::collections::{BTreeMap, BTreeSet};
use std::time::{SystemTime, UNIX_EPOCH};

use serde::{Deserialize, Serialize};
use serde_json::Value;

use crate::capability::CapabilityToken;
use crate::error::{KernelError, KernelResult};

pub static DEFAULT_GRANTABLE: &[&str] = &[
    "file_read",
    "grep",
    "glob",
    "web_search",
    "web_extract",
    "session_search",
    "memory",
    "knowledge_search",
    "wiki_search",
    // Main-chat orchestration (tool packs always inject these; token must match)
    "crew_steward",
    "clarify",
    "use_tool_pack",
    "current_time",
];

pub static RISKY_CAPABILITIES: &[&str] = &[
    "terminal",
    "file_write",
    "file_edit",
    "browser",
    "computer",
    "delegate_task",
    "cronjob",
    "send_message",
    // abstract risky crew caps
    "command",
    "file_rw",
];

/// P1 IPC / multi-agent caps — grantable when explicitly requested (not in readonly default).
pub static IPC_CAPABILITIES: &[&str] = &[
    "ipc",
    "ipc_send",
    "ipc_recv",
    "agent_comm",
];

fn now_secs() -> f64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs_f64())
        .unwrap_or(0.0)
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct IntentDeclaration {
    pub goal: String,
    pub capabilities: Vec<String>,
    pub constraints: BTreeMap<String, Value>,
}

impl IntentDeclaration {
    /// Safe default: read-only exploration (grantable set).
    pub fn default_readonly(goal: &str) -> Self {
        Self {
            goal: if goal.trim().is_empty() {
                "general assistance (read-only defaults)".into()
            } else {
                goal.to_string()
            },
            capabilities: vec![], // empty → full DEFAULT_GRANTABLE
            constraints: BTreeMap::new(),
        }
    }

    pub fn from_dict(data: &Value) -> KernelResult<Self> {
        let obj = data
            .as_object()
            .ok_or_else(|| KernelError::Invalid("intent declaration 必须是 dict".into()))?;
        let goal = obj
            .get("goal")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .trim()
            .to_string();
        if goal.is_empty() {
            return Err(KernelError::Invalid("intent declaration 缺少 goal".into()));
        }
        let capabilities = obj
            .get("capabilities")
            .and_then(|v| v.as_array())
            .map(|arr| {
                arr.iter()
                    .filter_map(|x| x.as_str().map(|s| s.to_string()))
                    .collect()
            })
            .unwrap_or_default();
        let constraints = obj
            .get("constraints")
            .and_then(|v| v.as_object())
            .map(|m| m.iter().map(|(k, v)| (k.clone(), v.clone())).collect())
            .unwrap_or_default();
        Ok(Self {
            goal,
            capabilities,
            constraints,
        })
    }

    pub fn to_dict(&self) -> Value {
        serde_json::json!({
            "goal": self.goal,
            "capabilities": self.capabilities,
            "constraints": self.constraints,
        })
    }

    pub fn allow_risky(&self) -> bool {
        self.constraints
            .get("allow_risky")
            .and_then(|v| v.as_bool())
            .unwrap_or(false)
    }

    pub fn token_budget_hint(&self) -> Option<i64> {
        self.constraints
            .get("token_budget")
            .and_then(|v| v.as_i64())
            .or_else(|| {
                self.constraints
                    .get("token_budget")
                    .and_then(|v| v.as_f64().map(|f| f as i64))
            })
    }
}

pub fn synthesize_capabilities(intent: &IntentDeclaration) -> (Vec<String>, Vec<String>) {
    let grantable: BTreeSet<&str> = DEFAULT_GRANTABLE.iter().copied().collect();
    let risky: BTreeSet<&str> = RISKY_CAPABILITIES.iter().copied().collect();
    let ipc: BTreeSet<&str> = IPC_CAPABILITIES.iter().copied().collect();
    let allow_risky = intent.allow_risky();

    let requested: Vec<String> = if intent.capabilities.is_empty() {
        let mut g: Vec<_> = grantable.iter().map(|s| (*s).to_string()).collect();
        g.sort();
        g
    } else {
        intent.capabilities.clone()
    };

    let mut granted = Vec::new();
    let mut dropped = Vec::new();
    for cap in requested {
        if grantable.contains(cap.as_str()) {
            granted.push(cap);
        } else if ipc.contains(cap.as_str()) {
            // explicit request only (not in empty-default readonly set)
            granted.push(cap);
        } else if risky.contains(cap.as_str()) {
            if allow_risky {
                granted.push(cap);
            } else {
                dropped.push(cap);
            }
        } else {
            // Unknown: whitelist only — drop
            dropped.push(cap);
        }
    }
    (granted, dropped)
}

pub fn synthesize_token(
    intent: &IntentDeclaration,
    process_id: &str,
    parent: Option<&CapabilityToken>,
) -> KernelResult<(CapabilityToken, Vec<String>)> {
    let (mut granted, mut dropped) = synthesize_capabilities(intent);

    // Parent filter before narrow (avoid CapabilityEscalation panic path)
    if let Some(parent) = parent {
        if !parent.capabilities.contains("*") {
            let mut still = Vec::new();
            for cap in granted {
                if parent.allows(&cap) || parent.capabilities.contains(&cap) {
                    still.push(cap);
                } else {
                    dropped.push(cap);
                }
            }
            granted = still;
        }
    }

    let ttl = intent
        .constraints
        .get("ttl_seconds")
        .and_then(|v| v.as_f64())
        .or_else(|| {
            intent
                .constraints
                .get("ttl_seconds")
                .and_then(|v| v.as_i64().map(|i| i as f64))
        });
    let expires_at = ttl.map(|t| now_secs() + t);

    let token = if let Some(parent) = parent {
        parent.narrow(granted.iter().cloned(), process_id, expires_at)?
    } else {
        CapabilityToken::new(granted.iter().cloned(), process_id, expires_at)
    };
    Ok((token, dropped))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn default_empty_caps_is_grantable() {
        let i = IntentDeclaration::default_readonly("explore");
        let (g, d) = synthesize_capabilities(&i);
        assert!(d.is_empty());
        assert!(g.contains(&"file_read".into()));
        assert!(!g.contains(&"terminal".into()));
    }

    #[test]
    fn risky_dropped_without_flag() {
        let mut i = IntentDeclaration::default_readonly("x");
        i.capabilities = vec!["file_read".into(), "terminal".into()];
        let (g, d) = synthesize_capabilities(&i);
        assert!(g.contains(&"file_read".into()));
        assert!(d.contains(&"terminal".into()));
    }

    #[test]
    fn risky_granted_with_flag() {
        let mut i = IntentDeclaration::default_readonly("x");
        i.capabilities = vec!["terminal".into()];
        i.constraints
            .insert("allow_risky".into(), serde_json::json!(true));
        let (g, d) = synthesize_capabilities(&i);
        assert!(g.contains(&"terminal".into()));
        assert!(d.is_empty());
    }

    #[test]
    fn parent_narrows_token() {
        let parent = CapabilityToken::new(["file_read", "grep"], "p", None);
        let mut i = IntentDeclaration::default_readonly("x");
        i.capabilities = vec!["file_read".into(), "glob".into()];
        let (tok, dropped) = synthesize_token(&i, "c", Some(&parent)).unwrap();
        assert!(tok.allows("file_read"));
        assert!(!tok.allows("glob"));
        assert!(dropped.contains(&"glob".into()));
    }
}
