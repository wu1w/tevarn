//! Pure-Rust dual-mode chat surface resolver (local LLM vs PC agent).
//! UI must call these rules — never invent silent fallbacks.
//! Also normalizes chat history into a compact UI payload so clients
//! don't re-parse heterogeneous backend shapes on the UI thread.

use serde::{Deserialize, Serialize};
use serde_json::{json, Value};

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ChatSurface {
    Local,
    Remote,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum SendPath {
    Local,
    Remote,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ModeSnapshot {
    pub surface: ChatSurface,
    pub pc_connected: bool,
    pub local_llm_ready: bool,
    pub kernel_ready: bool,
    pub can_send: bool,
    pub send_path: Option<SendPath>,
    pub reason: String,
    pub label: String,
    pub subtitle: String,
    pub placeholder: String,
    pub allow_attachments: bool,
    pub allow_camera: bool,
    pub allow_voice: bool,
    /// Actionable recovery: where the UI should guide the user.
    #[serde(default)]
    pub fix_hint: String,
    /// Tab key suggestion: "me" | "remote" | ""
    #[serde(default)]
    pub fix_tab: String,
}

impl ModeSnapshot {
    pub fn resolve(
        surface: ChatSurface,
        pc_connected: bool,
        local_llm_ready: bool,
        kernel_ready: bool,
        local_model: &str,
        pc_model: &str,
    ) -> Self {
        // NEVER silently rewrite the user's chosen surface.
        let (send_path, can_send, reason, fix_hint, fix_tab) = match surface {
            ChatSurface::Remote => {
                if !pc_connected {
                    (
                        None,
                        false,
                        "远端通道需先连接 PC".to_string(),
                        "前往「连接」登录 PC".to_string(),
                        "remote".to_string(),
                    )
                } else if !kernel_ready {
                    (
                        Some(SendPath::Remote),
                        true,
                        "PC 已连接 · Agent 运行时未就绪时可能无模型输出".to_string(),
                        String::new(),
                        String::new(),
                    )
                } else {
                    (
                        Some(SendPath::Remote),
                        true,
                        "远端 Agent 就绪".to_string(),
                        String::new(),
                        String::new(),
                    )
                }
            }
            ChatSurface::Local => {
                if local_llm_ready {
                    (
                        Some(SendPath::Local),
                        true,
                        "本机模型就绪".to_string(),
                        String::new(),
                        String::new(),
                    )
                } else {
                    (
                        None,
                        false,
                        "本机未配置 API Key 模型".to_string(),
                        "前往「我的 → LLM 设置」配置，或切换到远端 Agent".to_string(),
                        "me".to_string(),
                    )
                }
            }
        };

        let (label, subtitle, placeholder) = match surface {
            ChatSurface::Remote => (
                "远端 Agent".to_string(),
                if pc_connected {
                    format!(
                        "PC 工具链 · {}",
                        if pc_model.is_empty() { "—" } else { pc_model }
                    )
                } else {
                    "需先连接 PC".to_string()
                },
                "给 PC Agent 发消息…".to_string(),
            ),
            ChatSurface::Local => (
                "本机对话".to_string(),
                if local_llm_ready {
                    format!(
                        "直连模型 · {}",
                        if local_model.is_empty() {
                            "—"
                        } else {
                            local_model
                        }
                    )
                } else {
                    "我的 → LLM 设置（API Key 供应商）".to_string()
                },
                if local_llm_ready {
                    "本机模型对话…".to_string()
                } else {
                    "配置本机模型，或切换到远端…".to_string()
                },
            ),
        };

        let allow_attachments = true; // local + remote: real image bytes & previews
        let allow_camera = true;
        let allow_voice = true;

        Self {
            surface,
            pc_connected,
            local_llm_ready,
            kernel_ready,
            can_send,
            send_path,
            reason,
            label,
            subtitle,
            placeholder,
            allow_attachments,
            allow_camera,
            allow_voice,
            fix_hint,
            fix_tab,
        }
    }
}

/// Compact chat bubble payload for mobile UI (already normalized in Rust).
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct UiChatMessage {
    pub id: String,
    pub role: String,
    pub content: String,
    #[serde(default)]
    pub who: String,
    /// "plain" | "markdown" — decided in Rust so Flutter skips Markdown parser when plain.
    #[serde(default = "default_format")]
    pub format: String,
}

fn default_format() -> String {
    "plain".into()
}

impl UiChatMessage {
    pub fn to_value(&self) -> Value {
        json!({
            "id": self.id,
            "role": self.role,
            "content": self.content,
            "text": self.content,
            "who": self.who,
            "format": self.format,
        })
    }
}

/// Normalize heterogeneous history shapes into stable UI messages.
/// Heavy string/JSON work stays in Rust so Flutter only binds fields.
pub fn normalize_ui_messages(raw: &[Value], default_who: &str) -> Vec<UiChatMessage> {
    let mut out = Vec::with_capacity(raw.len());
    for (i, m) in raw.iter().enumerate() {
        let role = m
            .get("role")
            .and_then(|v| v.as_str())
            .unwrap_or("assistant")
            .to_string();
        // Hide tool protocol rows from mobile UI history.
        if role == "tool" || role == "function" {
            continue;
        }
        let content = extract_content(m);
        if role == "assistant" && content.trim().is_empty() {
            // Skip pure tool-call assistants with no visible text.
            let has_tc = m
                .get("tool_calls")
                .and_then(|v| v.as_array())
                .map(|a| !a.is_empty())
                .unwrap_or(false);
            if has_tc {
                continue;
            }
        }
        let id = m
            .get("id")
            .and_then(|v| v.as_str())
            .map(|s| s.to_string())
            .filter(|s| !s.is_empty())
            .unwrap_or_else(|| format!("m{i}"));
        let who = m
            .get("who")
            .and_then(|v| v.as_str())
            .map(|s| s.to_string())
            .filter(|s| !s.is_empty())
            .unwrap_or_else(|| {
                if role == "user" {
                    String::new()
                } else {
                    default_who.to_string()
                }
            });
        let format = if role == "user" || !looks_like_markdown(&content) {
            "plain".to_string()
        } else {
            "markdown".to_string()
        };
        out.push(UiChatMessage {
            id,
            role,
            content,
            who,
            format,
        });
    }
    out
}

/// Cheap heuristic — keep in Rust so Flutter doesn't re-scan on every rebuild.
fn looks_like_markdown(t: &str) -> bool {
    if t.len() < 4 {
        return false;
    }
    t.contains("```")
        || t.contains("**")
        || t.contains("\n- ")
        || t.contains("\n* ")
        || t.contains("\n#")
        || t.contains("](")
        || t.contains("\n> ")
        || t.contains("\n1. ")
}

fn extract_content(m: &Value) -> String {
    if let Some(s) = m.get("content").and_then(|v| v.as_str()) {
        return s.to_string();
    }
    if let Some(s) = m.get("text").and_then(|v| v.as_str()) {
        return s.to_string();
    }
    // OpenAI-style content parts
    if let Some(arr) = m.get("content").and_then(|v| v.as_array()) {
        let mut buf = String::new();
        for part in arr {
            if let Some(t) = part.get("text").and_then(|v| v.as_str()) {
                if !buf.is_empty() {
                    buf.push('\n');
                }
                buf.push_str(t);
            } else if let Some(t) = part.as_str() {
                if !buf.is_empty() {
                    buf.push('\n');
                }
                buf.push_str(t);
            }
        }
        if !buf.is_empty() {
            return buf;
        }
    }
    String::new()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn normalize_mixed_shapes() {
        let raw = vec![
            json!({"role":"user","content":"hi"}),
            json!({"role":"assistant","text":"yo","who":"bot"}),
            json!({"role":"assistant","content":[{"type":"text","text":"a"},{"text":"b"}]}),
            json!({"role":"assistant","content":"see **bold** and [x](y)"}),
        ];
        let msgs = normalize_ui_messages(&raw, "Agent");
        assert_eq!(msgs.len(), 4);
        assert_eq!(msgs[0].content, "hi");
        assert_eq!(msgs[0].format, "plain");
        assert_eq!(msgs[1].content, "yo");
        assert_eq!(msgs[1].who, "bot");
        assert_eq!(msgs[1].format, "plain");
        assert_eq!(msgs[2].content, "a\nb");
        assert_eq!(msgs[3].format, "markdown");
    }
}
