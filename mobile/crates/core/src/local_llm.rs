//! Direct OpenAI-compatible chat when not using PC agent mode.
//! Also handles phone-local ChatGPT OAuth (Codex responses) and Grok OAuth.

use crate::error::{Error, Result};
use crate::storage::Store;
use futures_util::StreamExt;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;

const PROFILE_FILE: &str = "local_llm_profile.json";
const HISTORY_FILE: &str = "local_chat_history.json";

/// Virtual base used after ChatGPT OAuth (token is Codex subscription, not platform API key).
pub const CHATGPT_OAUTH_BASE: &str = "codex-oauth://chatgpt";
const CODEX_UPSTREAM: &str = "https://chatgpt.com/backend-api/codex/responses";

const CHATGPT_OAUTH_MODELS: &[&str] = &[
    "gpt-5.6-luna",
    "gpt-5.6-terra",
    "gpt-5.6-sol",
    "gpt-5.4",
    "gpt-5.3-codex",
    "gpt-5.2-codex",
    "gpt-5.1-codex",
    "o3",
    "o4-mini",
    "gpt-4.1",
    "gpt-4o",
];

const XAI_OAUTH_MODELS: &[&str] = &[
    "grok-4",
    "grok-3",
    "grok-3-mini",
    "grok-3-fast",
    "grok-2",
    "grok-2-vision-1212",
];

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct LocalLlmProfile {
    /// e.g. `https://api.openai.com/v1`, `https://api.x.ai/v1`, or `codex-oauth://chatgpt`
    pub base_url: String,
    pub api_key: String,
    pub model: String,
    #[serde(default)]
    pub provider_label: String,
    /// Optional custom path; default `/chat/completions`
    #[serde(default = "default_chat_path")]
    pub chat_path: String,
    /// Optional ChatGPT account id for Codex headers
    #[serde(default)]
    pub account_id: String,
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
            account_id: String::new(),
        }
    }
}

impl LocalLlmProfile {
    pub fn is_chatgpt_oauth(&self) -> bool {
        let label = self.provider_label.to_lowercase();
        let base = self.base_url.to_lowercase();
        label.contains("chatgpt")
            || base.starts_with("codex-oauth")
            || base.contains("backend-api/codex")
    }

    pub fn is_xai_oauth(&self) -> bool {
        let label = self.provider_label.to_lowercase();
        let base = self.base_url.to_lowercase();
        (label.contains("grok") || label.contains("xai")) && base.contains("x.ai")
    }

    pub fn is_ready(&self) -> bool {
        if self.api_key.trim().is_empty() || self.model.trim().is_empty() {
            return false;
        }
        if self.is_chatgpt_oauth() {
            return true;
        }
        !self.base_url.trim().is_empty()
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
            "auth_mode": if self.is_chatgpt_oauth() {
                "oauth_chatgpt"
            } else if self.is_xai_oauth() {
                "oauth_xai"
            } else {
                "api_key"
            },
        })
    }

    fn completions_url(&self) -> Result<String> {
        if self.is_chatgpt_oauth() {
            return Ok(CODEX_UPSTREAM.into());
        }
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
        Ok(format!("{base}{path}"))
    }

    /// Public for agent loop
    pub fn completions_url_pub(&self) -> Result<String> {
        self.completions_url()
    }
}

/// Inline image for multimodal user turns (base64, no data: prefix).
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct LocalImagePart {
    #[serde(default = "default_image_mime")]
    pub mime: String,
    /// Raw standard base64 (not URL-safe).
    pub data_b64: String,
}

fn default_image_mime() -> String {
    "image/jpeg".into()
}

impl LocalImagePart {
    pub fn data_url(&self) -> String {
        let mime = if self.mime.trim().is_empty() {
            "image/jpeg"
        } else {
            self.mime.trim()
        };
        format!("data:{mime};base64,{}", self.data_b64.trim())
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct LocalChatMessage {
    pub role: String,
    #[serde(default)]
    pub content: String,
    /// Multimodal images for this turn (user). Not re-sent from disk history after strip.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub images: Option<Vec<LocalImagePart>>,
    /// OpenAI tool_calls payload (assistant)
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub tool_calls: Option<Value>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub tool_call_id: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub name: Option<String>,
}

impl LocalChatMessage {
    pub fn to_openai_json(&self) -> Value {
        let mut m = json!({
            "role": self.role,
        });
        if self.role == "assistant" {
            if let Some(tcs) = &self.tool_calls {
                // Expand to OpenAI shape if compact
                if let Some(arr) = tcs.as_array() {
                    let mapped: Vec<Value> = arr
                        .iter()
                        .map(|tc| {
                            if tc.get("function").is_some() {
                                tc.clone()
                            } else {
                                json!({
                                    "id": tc.get("id").and_then(|x| x.as_str()).unwrap_or("call"),
                                    "type": "function",
                                    "function": {
                                        "name": tc.get("name").and_then(|x| x.as_str()).unwrap_or(""),
                                        "arguments": tc.get("arguments").and_then(|x| x.as_str()).unwrap_or("{}"),
                                    }
                                })
                            }
                        })
                        .collect();
                    m["tool_calls"] = json!(mapped);
                    if self.content.is_empty() {
                        m["content"] = Value::Null;
                    } else {
                        m["content"] = json!(self.content);
                    }
                    return m;
                }
            }
        }
        if self.role == "tool" {
            if let Some(id) = &self.tool_call_id {
                m["tool_call_id"] = json!(id);
            }
            if let Some(n) = &self.name {
                m["name"] = json!(n);
            }
        }
        // Multimodal user content: text + image_url parts (OpenAI / Grok / compatible)
        if let Some(imgs) = &self.images {
            if !imgs.is_empty() && (self.role == "user" || self.role == "developer") {
                let mut parts: Vec<Value> = Vec::new();
                let text = if self.content.trim().is_empty() {
                    "（请结合图片回答）"
                } else {
                    self.content.as_str()
                };
                parts.push(json!({"type": "text", "text": text}));
                for img in imgs.iter().take(6) {
                    if img.data_b64.trim().is_empty() {
                        continue;
                    }
                    parts.push(json!({
                        "type": "image_url",
                        "image_url": {
                            "url": img.data_url(),
                            "detail": "auto"
                        }
                    }));
                }
                m["content"] = json!(parts);
                return m;
            }
        }
        m["content"] = json!(self.content);
        m
    }

    /// Drop heavy base64 so history JSON stays small (keep a text marker).
    pub fn strip_inline_images(&mut self) {
        if let Some(imgs) = self.images.take() {
            if !imgs.is_empty() {
                let n = imgs.len();
                if !self.content.contains("[已附") {
                    if self.content.trim().is_empty() {
                        self.content = format!("（已附 {n} 张图片）");
                    } else {
                        self.content = format!("{}
[已附 {n} 张图片 · 像素仅当轮发送]", self.content);
                    }
                }
            }
        }
    }
}

/// Heuristic: model can accept image_url / input_image parts.
pub fn model_supports_vision(profile: &LocalLlmProfile) -> bool {
    if profile.is_chatgpt_oauth() {
        return true; // ChatGPT subscription multimodal models
    }
    let m = profile.model.to_lowercase();
    let label = profile.provider_label.to_lowercase();
    // Explicit blind models (need OCR)
    if m.contains("deepseek")
        || m.contains("glm-4")
        || m.contains("glm4")
        || (m.contains("qwen") && !m.contains("vl") && !m.contains("vision"))
        || m.contains("yi-")
        || m.contains("moonshot")
    {
        return false;
    }
    if m.contains("vision")
        || m.contains("gpt-4o")
        || m.contains("gpt-4.1")
        || m.contains("gpt-4-turbo")
        || m.contains("gpt-5")
        || m.contains("luna")
        || m.contains("terra")
        || m.contains("sol")
        || m.contains("o3")
        || m.contains("o4")
        || m.contains("gemini")
        || m.contains("claude")
        || m.contains("grok-2")
        || m.contains("grok-4")
        || m.contains("grok-3")
        || label.contains("openai")
        || label.contains("chatgpt")
        || label.contains("xai")
        || label.contains("grok")
    {
        return true;
    }
    // Default: try vision for OpenAI-compatible bases
    let base = profile.base_url.to_lowercase();
    base.contains("openai.com") || base.contains("x.ai") || base.contains("openrouter")
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
        let mut p: LocalLlmProfile = self
            .store
            .load_json(PROFILE_FILE)
            .ok()
            .flatten()
            .unwrap_or_default();
        // Plain gpt-5.6 is not available on ChatGPT OAuth; migrate to luna.
        if p.is_chatgpt_oauth() && p.model.trim() == "gpt-5.6" {
            p.model = "gpt-5.6-luna".into();
            let _ = self.save_profile(&p);
        }
        p
    }

    pub fn clear_profile(&self) -> Result<()> {
        self.store
            .save_json(PROFILE_FILE, &LocalLlmProfile::default())?;
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

    pub fn is_cancelled(&self) -> bool {
        self.cancel.load(Ordering::SeqCst)
    }

    pub fn http(&self) -> &reqwest::Client {
        &self.http
    }

    /// Non-stream probe: list models or tiny completion.
    pub async fn test_connection(&self, profile: &LocalLlmProfile) -> Result<Value> {
        // ChatGPT OAuth → static Codex model list (no platform /models).
        if profile.is_chatgpt_oauth() {
            if profile.api_key.trim().is_empty() {
                return Err(Error::Msg("ChatGPT OAuth 未登录或令牌丢失，请重新登录".into()));
            }
            let ids: Vec<String> = CHATGPT_OAUTH_MODELS.iter().map(|s| (*s).to_string()).collect();
            return Ok(json!({
                "ok": true,
                "via": "chatgpt_oauth_catalog",
                "models": ids,
                "message": format!("ChatGPT OAuth 已授权 · {} 个订阅模型可选", ids.len()),
                "auth_ok": true,
            }));
        }

        if profile.base_url.trim().is_empty() {
            return Err(Error::Msg("请填写 Base URL".into()));
        }
        if profile.api_key.trim().is_empty() {
            return Err(Error::Msg("缺少 API Key / OAuth 令牌，请先登录或填写密钥".into()));
        }

        // Try models endpoint first (OpenAI-compatible / xAI)
        let base = profile.base_url.trim().trim_end_matches('/');
        let models_url = format!("{base}/models");
        let mut req = self.http.get(&models_url);
        req = req.bearer_auth(profile.api_key.trim());
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
                if ids.is_empty() && profile.is_xai_oauth() {
                    ids = XAI_OAUTH_MODELS.iter().map(|s| (*s).to_string()).collect();
                }
                return Ok(json!({
                    "ok": true,
                    "via": "models",
                    "models": ids,
                    "message": format!("连接成功 · 拉取 {} 个模型", ids.len()),
                    "auth_ok": true,
                }));
            }
            Ok(resp) => {
                let status = resp.status().as_u16();
                let text = resp.text().await.unwrap_or_default();
                if status == 401 || status == 403 {
                    // Fall back to curated list for known OAuth if auth weirdness
                    if profile.is_xai_oauth() {
                        let ids: Vec<String> =
                            XAI_OAUTH_MODELS.iter().map(|s| (*s).to_string()).collect();
                        return Ok(json!({
                            "ok": true,
                            "via": "xai_oauth_fallback",
                            "models": ids,
                            "message": format!(
                                "令牌已保存；/models 返回 {status}，已提供常用 Grok 模型列表"
                            ),
                            "auth_ok": true,
                            "detail": text.chars().take(200).collect::<String>(),
                        }));
                    }
                    return Err(Error::Msg(format!(
                        "认证失败 (HTTP {status})：令牌无效或过期，请重新 OAuth 登录"
                    )));
                }
            }
            Err(e) => {
                if profile.is_xai_oauth() {
                    let ids: Vec<String> =
                        XAI_OAUTH_MODELS.iter().map(|s| (*s).to_string()).collect();
                    return Ok(json!({
                        "ok": true,
                        "via": "xai_oauth_offline",
                        "models": ids,
                        "message": format!("网络拉取失败，已提供常用 Grok 列表 · {e}"),
                        "auth_ok": true,
                    }));
                }
            }
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
            "auth_ok": true,
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
        // Cancel flag is owned by the caller (agent run / host stop).
        // Never clear it here — multi-turn loops must honor mid-run stop.

        if profile.is_chatgpt_oauth() {
            return self.stream_chatgpt_codex(profile, messages, on_delta).await;
        }

        let url = profile.completions_url()?;
        let msgs: Vec<Value> = messages.iter().map(|m| m.to_openai_json()).collect();
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
            if status.as_u16() == 401 || status.as_u16() == 403 {
                return Err(Error::Msg(format!(
                    "认证失败 (HTTP {})：请重新 OAuth 登录或检查 API Key",
                    status.as_u16()
                )));
            }
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
                    let delta = v
                        .pointer("/choices/0/delta/content")
                        .and_then(|c| c.as_str())
                        .or_else(|| {
                            v.pointer("/choices/0/message/content")
                                .and_then(|c| c.as_str())
                        })
                        .or_else(|| v.get("response").and_then(|c| c.as_str()))
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

    /// ChatGPT subscription path via Codex Responses API.
    /// Payload shape mirrors desktop `openai_codex_proxy.build_codex_oauth_payload`
    /// / `_messages_to_input` (assistant → output_text, user → input_text).
    async fn stream_chatgpt_codex<F>(
        &self,
        profile: &LocalLlmProfile,
        messages: &[LocalChatMessage],
        mut on_delta: F,
    ) -> Result<String>
    where
        F: FnMut(&str),
    {
        let model = if profile.model.trim().is_empty() {
            "gpt-5.6-luna"
        } else {
            profile.model.trim()
        };

        // system → instructions; rest → Responses `input` items
        let mut instructions = String::new();
        let mut input: Vec<Value> = Vec::new();
        for m in messages {
            let role = m.role.as_str();
            let content = m.content.as_str();
            if role == "system" {
                if !instructions.is_empty() {
                    instructions.push_str("\n\n");
                }
                instructions.push_str(content);
                continue;
            }
            if role == "assistant" {
                // Assistant history MUST use output_text (not input_text) — Codex 400 otherwise.
                input.push(json!({
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": content}],
                }));
                continue;
            }
            if role == "tool" {
                input.push(json!({
                    "type": "function_call_output",
                    "call_id": "call_unknown",
                    "output": content,
                }));
                continue;
            }
            // user / developer / other — include input_image for multimodal
            let r = if role == "developer" { "developer" } else { "user" };
            let mut parts: Vec<Value> = Vec::new();
            let text = if content.trim().is_empty() && m.images.as_ref().map(|i| !i.is_empty()).unwrap_or(false) {
                "（请结合图片回答）"
            } else {
                content
            };
            if !text.is_empty() {
                parts.push(json!({"type": "input_text", "text": text}));
            }
            if let Some(imgs) = &m.images {
                for img in imgs.iter().take(6) {
                    if img.data_b64.trim().is_empty() {
                        continue;
                    }
                    parts.push(json!({
                        "type": "input_image",
                        "image_url": img.data_url(),
                        "detail": "auto"
                    }));
                }
            }
            if parts.is_empty() {
                parts.push(json!({"type": "input_text", "text": "(empty)"}));
            }
            input.push(json!({
                "type": "message",
                "role": r,
                "content": parts,
            }));
        }

        let mut payload = json!({
            "model": model,
            "input": input,
            "stream": true,
            "store": false,
        });
        if !instructions.is_empty() {
            payload["instructions"] = json!(instructions);
        }

        let mut req = self
            .http
            .post(CODEX_UPSTREAM)
            .bearer_auth(profile.api_key.trim())
            .header("Content-Type", "application/json")
            .header("Accept", "text/event-stream")
            .header("OpenAI-Beta", "responses=experimental");
        if !profile.account_id.trim().is_empty() {
            req = req.header("ChatGPT-Account-Id", profile.account_id.trim());
        }
        let resp = req
            .json(&payload)
            .send()
            .await
            .map_err(|e| Error::Network(e.to_string()))?;
        let status = resp.status();
        if !status.is_success() {
            let text = resp.text().await.unwrap_or_default();
            if status.as_u16() == 401 || status.as_u16() == 403 {
                return Err(Error::Msg(
                    "ChatGPT OAuth 令牌无效或过期，请重新登录".into(),
                ));
            }
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
                let Ok(v) = serde_json::from_str::<Value>(data) else {
                    continue;
                };
                // Prefer desktop-compatible event types only for text deltas
                let et = v.get("type").and_then(|t| t.as_str()).unwrap_or("");
                let delta = if et == "response.output_text.delta"
                    || et == "response.output_text.delta.event"
                {
                    v.get("delta").and_then(|d| d.as_str()).unwrap_or("")
                } else if et == "response.failed" || et == "error" {
                    let msg = v
                        .pointer("/error/message")
                        .and_then(|m| m.as_str())
                        .or_else(|| v.get("message").and_then(|m| m.as_str()))
                        .unwrap_or("Codex 响应失败");
                    return Err(Error::Msg(msg.to_string()));
                } else {
                    // Fallback: some gateways omit type and only send delta
                    if et.is_empty() {
                        v.get("delta").and_then(|d| d.as_str()).unwrap_or("")
                    } else {
                        ""
                    }
                };
                if !delta.is_empty() {
                    full.push_str(delta);
                    on_delta(delta);
                }
            }
        }
        Ok(full)
    }
}
