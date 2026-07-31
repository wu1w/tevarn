//! Coding Profile — engineering mode templates (P2 H1).

use serde::{Deserialize, Serialize};
use serde_json::{json, Value};

use crate::isolation::IsolationProfile;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CodingProfile {
    pub id: String,
    pub name: String,
    pub tools: Vec<String>,
    pub capabilities: Vec<String>,
    pub isolation: String,
    pub token_budget: i64,
    pub max_iterations: u32,
    pub allow_risky: bool,
    pub network: bool,
    pub description: String,
}

impl CodingProfile {
    pub fn engineering() -> Self {
        Self {
            id: "engineering".into(),
            name: "工程模式".into(),
            tools: vec![
                "file_read".into(),
                "file_write".into(),
                "edit".into(),
                "apply_patch".into(),
                "grep".into(),
                "glob".into(),
                "command".into(),
                "python".into(),
                "git".into(),
            ],
            capabilities: vec![
                "file_read".into(),
                "file_write".into(),
                "file_edit".into(),
                "terminal".into(),
                "command".into(),
                "file_rw".into(),
            ],
            isolation: IsolationProfile::Interactive.as_str().into(),
            token_budget: 200_000,
            max_iterations: 40,
            allow_risky: true,
            network: false,
            description: "日用编程：读写/补丁/命令，交互沙箱，治理不减".into(),
        }
    }

    pub fn review_only() -> Self {
        Self {
            id: "code_review".into(),
            name: "代码审阅".into(),
            tools: vec!["file_read".into(), "grep".into(), "glob".into()],
            capabilities: vec!["file_read".into(), "grep".into(), "glob".into()],
            isolation: IsolationProfile::ReadOnly.as_str().into(),
            token_budget: 80_000,
            max_iterations: 20,
            allow_risky: false,
            network: false,
            description: "只读审阅，禁止写与执行".into(),
        }
    }

    pub fn pair() -> Self {
        let mut p = Self::engineering();
        p.id = "pair".into();
        p.name = "结对编程".into();
        p.max_iterations = 60;
        p.description = "结对：可打断/改 plan/批准写操作".into();
        p
    }

    pub fn all() -> Vec<Self> {
        vec![Self::engineering(), Self::review_only(), Self::pair()]
    }

    pub fn get(id: &str) -> Option<Self> {
        Self::all().into_iter().find(|p| p.id == id || p.id.replace('_', "-") == id)
    }

    pub fn to_intent_dict(&self) -> Value {
        json!({
            "goal": format!("coding profile: {}", self.id),
            "capabilities": self.capabilities,
            "constraints": {
                "allow_risky": self.allow_risky,
                "token_budget": self.token_budget,
                "max_iterations": self.max_iterations,
                "profile": self.id,
            }
        })
    }

    pub fn to_dict(&self) -> Value {
        json!(self)
    }
}

pub struct CodingProfileRegistry;

impl CodingProfileRegistry {
    pub fn list() -> Value {
        json!({"profiles": CodingProfile::all()})
    }

    pub fn resolve(id: &str) -> Option<CodingProfile> {
        CodingProfile::get(id)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn engineering_has_write_tools() {
        let p = CodingProfile::engineering();
        assert!(p.tools.contains(&"file_write".into()) || p.tools.contains(&"edit".into()));
        assert!(p.allow_risky);
    }
}
