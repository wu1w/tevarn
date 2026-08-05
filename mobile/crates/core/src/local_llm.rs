//! Direct OpenAI-compatible chat when not using PC agent mode.

use crate::error::{Error, Result};
use crate::storage::Store;
use futures_util::StreamExt;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;

const PROFILE_FILE: &str = "local_llm_profile.json";
const HISTORY_FILE: &str = "local_chat_history.json";

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct LocalLlmProfile {
    /// e.g. `https://api.openai.com/v1` or any OpenAI-compatible root
    pub base_url: String,
    pub api_key: String,
    pub model: String,
    #[serde(default)]
    pub provider_label: String,
    /// Optional custom path; default `/chat/completions`
    #[serde(default = "default_chat_path")]
    pub chat_path: String,
}

fn default_chat_path() -> String {
    "/chat/completions".into()
}

impl Default for LocalLlmProfile {
    fn default() -> Self {
        Self {
            base_url: String::new(),
            api_key: String::new(),
            model: String::new(),
            provider_label: "OpenAI-compatible".into(),
            chat_path: default_chat_path(),
        }
    }
}

impl LocalLlmProfile {
    pub fn is_ready(&self) -> bool {
        !self.base_url.trim().is_empty()
            && !self.model.trim().is_empty()
            && !self.api_key.trim().is_empty()
    }

    pub fn masked(&self) -> Value {
        let key = self.api_key.trim();
        let masked = if key.is_empty() {
            String::new()
        } else if key.len() <= 8 {
            "••••".into()
        } else {
            format!("{}…{}", &key[..4], &key[key.len().saturating_sub(4)..])
        };
        json!({
            "base_url": self.base_url,
            "model": self.model,
            "provider_label": self.provider_label,
            "chat_path": self.chat_path,
            "api_key_masked": masked,
            "has_key": !key.is_empty(),
            "ready": self.is_ready(),
        })
    }

    fn completions_url(&self) -> Result<String> {
        let base = self.base_url.trim().trim_end_matches('/');
        if base.is_empty() {
            return Err(Error::Msg("local LLM base_url 未配置".into()));
        }
        let path = self.chat_path.trim();
        let path = if path.is_empty() {
            "/chat/completions"
        } else if path.starts_with('/') {
            path
        } else {
            return Ok(format!("{base}/{path}"));
        };
        // If user already included /v1 in base, just append path
        Ok(format!("{base}{path}"))
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct LocalChatMessage {
    pub role: String,
    pub content: String,
}

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct LocalChatHistory {
    pub messages: Vec<LocalChatMessage>,
}

pub struct LocalLlmService {
    store: Store,
    http: reqwest::Client,
    cancel: Arc<AtomicBool>,
}

impl LocalLlmService {
    pub fn new(store: Store) -> Self {
        Self {
            store,
            http: reqwest::Client::builder()
                .timeout(std::time::Duration::from_secs(300))
                .build()
                .unwrap_or_else(|_| reqwest::Client::new()),
            cancel: Arc::new(AtomicBool::new(false)),
        }
    }

    pub fn load_profile(&self) -> LocalLlmProfile {
        self.store
            .load_json(PROFILE_FILE)
            .ok()
            .flatten()
            .unwrap_or_default()
    }

    pub fn clear_profile(&self) -> Result<()> {
        self.store.write_json(PROFILE_FILE, &LocalLlmProfile::default())?;
        Ok(())
    }

    pub fn save_profile(&self, profile: &LocalLlmProfile) -> Result<()> {
        self.store.save_json(PROFILE_FILE, profile)
    }

    pub fn load_history(&self) -> LocalChatHistory {
        self.store
            .load_json(HISTORY_FILE)
            .ok()
            .flatten()
            .unwrap_or_default()
    }

    pub fn save_history(&self, hist: &LocalChatHistory) -> Result<()> {
        self.store.save_json(HISTORY_FILE, hist)
    }

    pub fn clear_history(&self) -> Result<()> {
        self.save_history(&LocalChatHistory::default())
    }

    pub fn request_stop(&self) {
        self.cancel.store(true, Ordering::SeqCst);
    }

    pub fn reset_cancel(&self) {
        self.cancel.store(false, Ordering::SeqCst);
    }

    /// Non-stream probe: list models or tiny completion.
    pub async fn test_connection(&self, profile: &LocalLlmProfile) -> Result<Value> {
        if profile.base_url.trim().is_empty() {
            return Err(Error::Msg("请填写 Base URL".into()));
        }
        // Try models endpoint first (OpenAI-compatible)
        let base = profile.base_url.trim().trim_end_matches('/');
        let models_url = format!("{base}/models");
        let mut req = self.http.get(&models_url);
        if !profile.api_key.trim().is_empty() {
            req = req.bearer_auth(profile.api_key.trim());
        }
        match req.send().await {
            Ok(resp) if resp.status().is_success() => {
                let v: Value = resp.json().await.unwrap_or(json!({}));
                let mut ids = Vec::new();
                if let Some(arr) = v.get("data").and_then(|d| d.as_array()) {
                    for m in arr {
                        if let Some(id) = m.get("id").and_then(|x| x.as_str()) {
                            ids.push(id.to_string());
                        }
                    }
                }
                return Ok(json!({
                    "ok": true,
                    "via": "models",
                    "models": ids,
                    "message": format!("连接成功 · 拉取 {} 个模型", ids.len()),
                }));
            }
            _ => {}
        }

        // Fallback: one-shot completion
        if !profile.is_ready() {
            return Err(Error::Msg(
                "模型列表不可用，请同时填写 model + API Key 再试".into(),
            ));
        }
        let url = profile.completions_url()?;
        let body = json!({
            "model": profile.model,
            "messages": [{"role":"user","content":"ping"}],
            "max_tokens": 8,
            "stream": false,
        });
        let resp = self
            .http
            .post(url)
            .bearer_auth(profile.api_key.trim())
            .json(&body)
            .send()
            .await
            .map_err(|e| Error::Network(e.to_string()))?;
        let status = resp.status();
        let text = resp.text().await.unwrap_or_default();
        if !status.is_success() {
            return Err(Error::http(status.as_u16(), text));
        }
        Ok(json!({
            "ok": true,
            "via": "chat",
            "message": "连接成功 · chat completion 可用",
            "raw_preview": text.chars().take(200).collect::<String>(),
        }))
    }

    /// Stream chat; calls `on_delta` with each text chunk. Returns full assistant text.
    pub async fn stream_chat<F>(
        &self,
        profile: &LocalLlmProfile,
        messages: &[LocalChatMessage],
        mut on_delta: F,
    ) -> Result<String>
    where
        F: FnMut(&str),
    {
        if !profile.is_ready() {
            return Err(Error::Msg(
                "本地 LLM 未配置完整（需要 base_url / api_key / model）".into(),
            ));
        }
        self.reset_cancel();
        let url = profile.completions_url()?;
        let msgs: Vec<Value> = messages
            .iter()
            .map(|m| json!({"role": m.role, "content": m.content}))
            .collect();
        let body = json!({
            "model": profile.model,
            "messages": msgs,
            "stream": true,
        });
        let resp = self
            .http
            .post(url)
            .bearer_auth(profile.api_key.trim())
            .header("Accept", "text/event-stream")
            .json(&body)
            .send()
            .await
            .map_err(|e| Error::Network(e.to_string()))?;
        let status = resp.status();
        if !status.is_success() {
            let text = resp.text().await.unwrap_or_default();
            return Err(Error::http(status.as_u16(), text));
        }

        let mut full = String::new();
        let mut stream = resp.bytes_stream();
        let mut buf = String::new();

        while let Some(item) = stream.next().await {
            if self.cancel.load(Ordering::SeqCst) {
                return Ok(full);
            }
            let chunk = item.map_err(|e| Error::Network(e.to_string()))?;
            buf.push_str(&String::from_utf8_lossy(&chunk));
            while let Some(pos) = buf.find('\n') {
                let line = buf[..pos].trim_end_matches('\r').to_string();
                buf = buf[pos + 1..].to_string();
                if line.is_empty() || line.starts_with(':') {
                    continue;
                }
                let data = if let Some(rest) = line.strip_prefix("data:") {
                    rest.trim()
                } else {
                    continue;
                };
                if data == "[DONE]" {
                    return Ok(full);
                }
                if let Ok(v) = serde_json::from_str::<Value>(data) {
                    // OpenAI style
                    let delta = v
                        .pointer("/choices/0/delta/content")
                        .and_then(|c| c.as_str())
                        .or_else(|| {
                            v.pointer("/choices/0/message/content")
                                .and_then(|c| c.as_str())
                        })
                        .or_else(|| v.get("response").and_then(|c| c.as_str())) // ollama-ish
                        .unwrap_or("");
                    if !delta.is_empty() {
                        full.push_str(delta);
                        on_delta(delta);
                    }
                }
            }
        }
        Ok(full)
    }
}
