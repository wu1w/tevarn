use serde::{Deserialize, Serialize};
use serde_json::Value;

/// Single local chat thread id — must match `takton_mobile_core::LOCAL_SESSION_ID`.
pub const LOCAL_SESSION_ID: &str = "__local__";

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct AppStateDto {
    pub ok: bool,
    #[serde(default)]
    pub authenticated: bool,
    #[serde(default)]
    pub mode: String,
    #[serde(default)]
    pub base_url: String,
    pub user: Option<Value>,
    pub active_session_id: Option<String>,
    #[serde(default)]
    pub sessions: Vec<Value>,
    #[serde(default)]
    pub local_session: Option<Value>,
    #[serde(default)]
    pub session_meta: Option<Value>,
    #[serde(default)]
    pub devices: Vec<Value>,
    #[serde(default)]
    pub runtime: Value,
    #[serde(default)]
    pub approvals_pending: u64,
    #[serde(default)]
    pub notify_approvals: bool,
    pub backend_health: Option<Value>,
    #[serde(default)]
    pub platform: String,
    pub catalog: Option<Value>,
    pub active_model: Option<String>,
    pub local_llm: Option<Value>,
    #[serde(default)]
    pub local_llm_ready: bool,
    #[serde(default)]
    pub kernel_ready: bool,
    #[serde(default)]
    pub kernel_local: bool,
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct ModeSnap {
    pub surface: String,
    #[serde(default)]
    pub pc_connected: bool,
    #[serde(default)]
    pub local_llm_ready: bool,
    #[serde(default)]
    pub kernel_ready: bool,
    #[serde(default)]
    pub can_send: bool,
    pub send_path: Option<String>,
    #[serde(default)]
    pub reason: String,
    #[serde(default)]
    pub label: String,
    #[serde(default)]
    pub subtitle: String,
    #[serde(default)]
    pub placeholder: String,
    #[serde(default)]
    pub allow_attachments: bool,
    #[serde(default)]
    pub allow_camera: bool,
    #[serde(default)]
    pub allow_voice: bool,
    #[serde(default)]
    pub fix_hint: String,
    #[serde(default)]
    pub fix_tab: String,
}

#[derive(Debug, Clone)]
pub struct ChatMsg {
    pub id: String,
    pub role: String, // user | assistant | system
    pub html: String, // pre-escaped/rendered
    pub who: String,
    pub streaming: bool,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Tab {
    Chat,
    Approve,
    Remote,
    Me,
}

impl Tab {
    pub fn as_str(&self) -> &'static str {
        match self {
            Tab::Chat => "chat",
            Tab::Approve => "approve",
            Tab::Remote => "remote",
            Tab::Me => "me",
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ChatSurface {
    Local,
    Remote,
}

impl ChatSurface {
    pub fn as_str(&self) -> &'static str {
        match self {
            ChatSurface::Local => "local",
            ChatSurface::Remote => "remote",
        }
    }
}
