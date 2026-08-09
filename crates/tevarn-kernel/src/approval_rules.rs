//! Approval rules for escalations (R4) — pure policy, no DB.
//! Evolution never auto-applies (hard red line).

use std::collections::HashSet;

use serde_json::{json, Value};

const LOW_RISK: &[&str] = &[
    "web_search",
    "web_extract",
    "search",
    "read",
    "file_read",
    "glob",
    "grep",
    "list_dir",
    "memory_read",
    "wiki_read",
    "knowledge_query",
    "rag_query",
    "ipc_recv",
];

const DANGER: &[&str] = &[
    "command",
    "shell",
    "bash",
    "exec",
    "terminal",
    "file_rw",
    "file_write",
    "write",
    "delete",
    "rm",
    "sudo",
    "network",
    "egress",
    "browser",
    "http",
    "mcp",
    "subagent",
    "spawn",
];

#[derive(Debug, Clone)]
pub struct ApprovalRules {
    pub auto_low_risk: bool,
    pub review_high_risk: bool,
    pub review_capability_upgrade: bool,
    pub review_evolution: bool,
    pub auto_tighten_2x: bool,
}

impl Default for ApprovalRules {
    fn default() -> Self {
        Self {
            auto_low_risk: true,
            review_high_risk: true,
            review_capability_upgrade: true,
            review_evolution: true,
            auto_tighten_2x: true,
        }
    }
}

impl ApprovalRules {
    pub fn from_json_list(rules: &[Value]) -> Self {
        let mut r = Self::default();
        for item in rules {
            let key = item.get("key").and_then(|v| v.as_str()).unwrap_or("");
            let en = item
                .get("enabled")
                .and_then(|v| v.as_bool())
                .unwrap_or(true);
            match key {
                "auto_low_risk" => r.auto_low_risk = en,
                "review_high_risk" => r.review_high_risk = en,
                "review_capability_upgrade" => r.review_capability_upgrade = en,
                "review_evolution" => r.review_evolution = en,
                "auto_tighten_2x" => r.auto_tighten_2x = en,
                _ => {}
            }
        }
        r
    }

    pub fn to_json_list(&self) -> Value {
        json!([
            {"key": "auto_low_risk", "enabled": self.auto_low_risk},
            {"key": "review_high_risk", "enabled": self.review_high_risk, "warn": true},
            {"key": "review_capability_upgrade", "enabled": self.review_capability_upgrade, "warn": true},
            {"key": "review_evolution", "enabled": self.review_evolution, "warn": true},
            {"key": "auto_tighten_2x", "enabled": self.auto_tighten_2x},
        ])
    }
}

/// low | high | upgrade
pub fn classify_caps(capabilities: &[String]) -> &'static str {
    let caps: HashSet<String> = capabilities.iter().map(|c| c.to_lowercase()).collect();
    let danger: HashSet<&str> = DANGER.iter().copied().collect();
    let low: HashSet<&str> = LOW_RISK.iter().copied().collect();
    for c in &caps {
        if danger.contains(c.as_str()) || danger.iter().any(|d| c.contains(d)) {
            return "high";
        }
    }
    if !caps.is_empty() && caps.iter().all(|c| low.contains(c.as_str())) {
        return "low";
    }
    "upgrade"
}

pub fn should_auto_approve(rules: &ApprovalRules, capabilities: &[String]) -> bool {
    if !rules.auto_low_risk {
        return false;
    }
    let kind = classify_caps(capabilities);
    if kind == "high" && rules.review_high_risk {
        return false;
    }
    if kind == "upgrade" && rules.review_capability_upgrade {
        return false;
    }
    kind == "low"
}

/// Evolution always requires human review (double lock).
pub fn evolution_requires_review() -> bool {
    true
}

pub struct ApprovalPolicy {
    rules: ApprovalRules,
}

impl Default for ApprovalPolicy {
    fn default() -> Self {
        Self {
            rules: ApprovalRules::default(),
        }
    }
}

impl ApprovalPolicy {
    pub fn set_rules(&mut self, rules: ApprovalRules) {
        self.rules = rules;
    }

    pub fn set_from_json(&mut self, list: &[Value]) {
        self.rules = ApprovalRules::from_json_list(list);
    }

    pub fn rules_json(&self) -> Value {
        self.rules.to_json_list()
    }

    pub fn classify(&self, capabilities: &[String]) -> Value {
        json!({
            "kind": classify_caps(capabilities),
            "auto_approve": should_auto_approve(&self.rules, capabilities),
            "evolution_requires_review": evolution_requires_review(),
        })
    }

    pub fn should_auto_approve(&self, capabilities: &[String]) -> bool {
        should_auto_approve(&self.rules, capabilities)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn low_auto_high_not() {
        let r = ApprovalRules::default();
        assert!(should_auto_approve(
            &r,
            &["file_read".into(), "grep".into()]
        ));
        assert!(!should_auto_approve(&r, &["terminal".into()]));
        assert!(evolution_requires_review());
    }
}
