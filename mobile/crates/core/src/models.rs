use serde::{Deserialize, Serialize};
use serde_json::Value;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct UserInfo {
    pub id: String,
    pub email: String,
    pub username: String,
    #[serde(default)]
    pub display_name: Option<String>,
    #[serde(default)]
    pub is_superuser: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TokenResponse {
    pub access_token: String,
    #[serde(default)]
    pub token_type: String,
    #[serde(default)]
    pub expires_in: i64,
    pub user: UserInfo,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SessionInfo {
    pub id: String,
    #[serde(default)]
    pub user_id: Option<String>,
    #[serde(default)]
    pub status: Option<String>,
    #[serde(default)]
    pub title: Option<String>,
    #[serde(default)]
    pub config: Value,
    #[serde(default)]
    pub created_at: Option<String>,
    #[serde(default)]
    pub updated_at: Option<String>,
}

impl SessionInfo {
    pub fn display_title(&self) -> String {
        if let Some(t) = &self.title {
            if !t.is_empty() {
                return t.clone();
            }
        }
        if let Some(obj) = self.config.as_object() {
            for key in ["contact_agent", "title", "name"] {
                if let Some(v) = obj.get(key).and_then(|x| x.as_str()) {
                    if !v.is_empty() {
                        return v.to_string();
                    }
                }
            }
        }
        format!("会话 {}", &self.id[..8.min(self.id.len())])
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct MessageInfo {
    pub id: String,
    #[serde(default)]
    pub role: String,
    #[serde(default)]
    pub content: Value,
    #[serde(default)]
    pub created_at: Option<String>,
    #[serde(default)]
    pub metadata: Option<Value>,
    /// Present on intermediate assistant turns that invoke tools.
    #[serde(default)]
    pub tool_calls: Option<Value>,
}

impl MessageInfo {
    /// True when this assistant message still expects tool results (not final answer).
    pub fn is_tool_invocation(&self) -> bool {
        if self.role != "assistant" {
            return false;
        }
        match &self.tool_calls {
            Some(Value::Array(a)) => !a.is_empty(),
            Some(Value::Object(o)) => !o.is_empty(),
            Some(Value::Null) | None => false,
            Some(_) => true,
        }
    }

    pub fn text(&self) -> String {
        match &self.content {
            Value::String(s) => s.clone(),
            Value::Array(arr) => arr
                .iter()
                .filter_map(|p| {
                    p.get("text")
                        .and_then(|t| t.as_str())
                        .or_else(|| p.as_str())
                })
                .collect::<Vec<_>>()
                .join("\n"),
            other => other
                .as_str()
                .map(|s| s.to_string())
                .unwrap_or_else(|| other.to_string()),
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct DeviceInfo {
    pub id: String,
    pub name: String,
    #[serde(default)]
    pub device_type: String,
    #[serde(default)]
    pub status: String,
    #[serde(default)]
    pub capabilities: Vec<String>,
    #[serde(default)]
    pub config: Value,
    #[serde(default)]
    pub last_seen_at: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct RuntimeStatus {
    #[serde(default)]
    pub ok: bool,
    #[serde(default)]
    pub processes_live: Option<u64>,
    #[serde(default)]
    pub approvals_pending: Option<u64>,
    #[serde(default)]
    pub jobs_claimed: Option<u64>,
    #[serde(default)]
    pub jobs_pending: Option<u64>,
    #[serde(default)]
    pub badge: Option<u64>,
    #[serde(default)]
    pub product: Option<String>,
    #[serde(default)]
    pub aios_profile: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct ModelCatalog {
    #[serde(default)]
    pub active_provider_id: Option<String>,
    #[serde(default)]
    pub providers: Vec<Value>,
    #[serde(flatten)]
    pub extra: Value,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct HealthInfo {
    #[serde(default)]
    pub status: String,
    #[serde(default)]
    pub service: Option<String>,
}
