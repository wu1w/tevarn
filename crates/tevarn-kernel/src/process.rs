//! AgentProcess — kernel-managed execution entity.

use std::collections::BTreeMap;
use std::time::{SystemTime, UNIX_EPOCH};

use serde::{Deserialize, Serialize};
use serde_json::{json, Value};

use crate::capability::CapabilityToken;
use crate::error::{KernelError, KernelResult};

fn now_secs() -> f64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs_f64())
        .unwrap_or(0.0)
}

fn short_id() -> String {
    uuid::Uuid::new_v4().simple().to_string()[..16].to_string()
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ProcessState {
    Created,
    Running,
    Suspended,
    Completed,
    Failed,
    Killed,
}

impl ProcessState {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Created => "created",
            Self::Running => "running",
            Self::Suspended => "suspended",
            Self::Completed => "completed",
            Self::Failed => "failed",
            Self::Killed => "killed",
        }
    }

    pub fn is_terminal(self) -> bool {
        matches!(self, Self::Completed | Self::Failed | Self::Killed)
    }

    pub fn parse(s: &str) -> Self {
        match s {
            "running" => Self::Running,
            "suspended" => Self::Suspended,
            "completed" => Self::Completed,
            "failed" => Self::Failed,
            "killed" => Self::Killed,
            _ => Self::Created,
        }
    }
}

impl std::fmt::Display for ProcessState {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(self.as_str())
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AgentProcess {
    pub id: String,
    pub identity: String,
    pub session_id: Option<String>,
    pub parent_id: Option<String>,
    /// None = compat mode (full allow at capability layer)
    pub capabilities: Option<Vec<String>>,
    pub token_budget: Option<i64>,
    pub tokens_used: i64,
    pub state: ProcessState,
    pub created_at: f64,
    pub started_at: Option<f64>,
    pub ended_at: Option<f64>,
    pub exit_reason: Option<String>,
    pub meta: BTreeMap<String, Value>,
    pub token: Option<CapabilityToken>,
}

impl AgentProcess {
    pub fn new(
        identity: impl Into<String>,
        session_id: Option<String>,
        parent_id: Option<String>,
        capabilities: Option<Vec<String>>,
        token_budget: Option<i64>,
        meta: BTreeMap<String, Value>,
    ) -> Self {
        Self {
            id: short_id(),
            identity: identity.into(),
            session_id,
            parent_id,
            capabilities,
            token_budget,
            tokens_used: 0,
            state: ProcessState::Created,
            created_at: now_secs(),
            started_at: None,
            ended_at: None,
            exit_reason: None,
            meta,
            token: None,
        }
    }

    pub fn is_terminal(&self) -> bool {
        self.state.is_terminal()
    }

    pub fn budget_remaining(&self) -> Option<i64> {
        self.token_budget
            .map(|b| (b - self.tokens_used).max(0))
    }

    pub fn has_capability(&self, cap: &str) -> bool {
        match &self.capabilities {
            None => true,
            Some(caps) => {
                use std::collections::BTreeSet;
                use crate::tool_catalog::capability_matches;
                let set: BTreeSet<String> = caps.iter().cloned().collect();
                capability_matches(cap, &set)
            }
        }
    }

    pub fn charge_tokens(&mut self, amount: i64) -> KernelResult<Option<i64>> {
        if amount > 0 {
            if let Some(budget) = self.token_budget {
                if self.tokens_used + amount > budget {
                    return Err(KernelError::Invalid(format!(
                        "charge {amount} would exceed budget ({} / {budget})",
                        self.tokens_used
                    )));
                }
            }
            self.tokens_used += amount;
        }
        Ok(self.budget_remaining())
    }

    pub fn suspend(&mut self) -> KernelResult<()> {
        if self.is_terminal() {
            return Err(KernelError::Invalid(format!(
                "进程 {} 已终止（{}），不可挂起",
                self.id, self.state
            )));
        }
        if self.state != ProcessState::Suspended {
            self.state = ProcessState::Suspended;
        }
        Ok(())
    }

    pub fn resume(&mut self) {
        if self.state == ProcessState::Suspended {
            self.state = ProcessState::Running;
        }
    }

    pub fn to_dict(&self) -> Value {
        let token = self.token.as_ref().map(|t| t.to_dict(None));
        json!({
            "id": self.id,
            "identity": self.identity,
            "session_id": self.session_id,
            "parent_id": self.parent_id,
            "capabilities": self.capabilities,
            "token_budget": self.token_budget,
            "tokens_used": self.tokens_used,
            "budget_remaining": self.budget_remaining(),
            "state": self.state.as_str(),
            "created_at": self.created_at,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "exit_reason": self.exit_reason,
            "meta": self.meta,
            "token": token,
        })
    }

    pub fn from_dict(data: &Value) -> Self {
        let meta = data
            .get("meta")
            .and_then(|m| m.as_object())
            .map(|o| o.iter().map(|(k, v)| (k.clone(), v.clone())).collect())
            .unwrap_or_default();
        let caps = data.get("capabilities").and_then(|c| {
            if c.is_null() {
                None
            } else {
                c.as_array().map(|arr| {
                    arr.iter()
                        .filter_map(|x| x.as_str().map(|s| s.to_string()))
                        .collect()
                })
            }
        });
        let token = data
            .get("token")
            .filter(|t| t.is_object())
            .and_then(|t| CapabilityToken::from_dict(t, None).ok());
        Self {
            id: data
                .get("id")
                .and_then(|v| v.as_str())
                .map(|s| s.to_string())
                .filter(|s| !s.is_empty())
                .unwrap_or_else(short_id),
            identity: data
                .get("identity")
                .and_then(|v| v.as_str())
                .unwrap_or("main")
                .to_string(),
            session_id: data
                .get("session_id")
                .and_then(|v| v.as_str())
                .map(|s| s.to_string()),
            parent_id: data
                .get("parent_id")
                .and_then(|v| v.as_str())
                .map(|s| s.to_string()),
            capabilities: caps,
            token_budget: data.get("token_budget").and_then(|v| v.as_i64()),
            tokens_used: data
                .get("tokens_used")
                .and_then(|v| v.as_i64())
                .unwrap_or(0),
            state: ProcessState::parse(
                data.get("state").and_then(|v| v.as_str()).unwrap_or("created"),
            ),
            created_at: data
                .get("created_at")
                .and_then(|v| v.as_f64())
                .unwrap_or_else(now_secs),
            started_at: data.get("started_at").and_then(|v| v.as_f64()),
            ended_at: data.get("ended_at").and_then(|v| v.as_f64()),
            exit_reason: data
                .get("exit_reason")
                .and_then(|v| v.as_str())
                .map(|s| s.to_string()),
            meta,
            token,
        }
    }
}
