//! HTTP client for the Takton PC backend (`/api/*`).

use crate::auth::AuthSession;
use crate::config::AppConfig;
use crate::error::{Error, Result};
use crate::models::{
    DeviceInfo, HealthInfo, MessageInfo, ModelCatalog, RuntimeStatus, SessionInfo, TokenResponse,
};
use crate::storage::Store;
use parking_lot::RwLock;
use reqwest::{Client, Method, StatusCode};
use serde::de::DeserializeOwned;
use serde_json::{json, Value};
use std::sync::Arc;
use std::time::Duration;

const SESSION_FILE: &str = "auth_session.json";

#[derive(Clone)]
pub struct TaktonClient {
    http: Client,
    config: Arc<RwLock<AppConfig>>,
    session: Arc<RwLock<Option<AuthSession>>>,
    store: Store,
}

impl TaktonClient {
    pub fn new(config: AppConfig) -> Result<Self> {
        let store = Store::open(&config.data_dir)?;
        let session = store.load_json::<AuthSession>(SESSION_FILE)?.filter(|s| {
            s.base_url.trim_end_matches('/') == config.base_url.trim_end_matches('/')
                || s.base_url.is_empty()
        });
        let http = Client::builder()
            .timeout(Duration::from_secs(60))
            .connect_timeout(Duration::from_secs(8))
            .pool_idle_timeout(Duration::from_secs(30))
            .build()
            .map_err(|e| Error::Network(e.to_string()))?;
        Ok(Self {
            http,
            config: Arc::new(RwLock::new(config)),
            session: Arc::new(RwLock::new(session)),
            store,
        })
    }

    pub fn config(&self) -> AppConfig {
        self.config.read().clone()
    }

    pub fn set_base_url(&self, base_url: String) {
        let mut cfg = self.config.write();
        let prev = cfg.base_url.clone();
        cfg.base_url = base_url;
        let changed = prev.trim_end_matches('/') != cfg.base_url.trim_end_matches('/');
        drop(cfg);
        if changed {
            *self.session.write() = None;
            let _ = self.persist_session(None);
        }
    }

    pub fn session(&self) -> Option<AuthSession> {
        self.session.read().clone()
    }

    pub fn is_authenticated(&self) -> bool {
        self.session.read().is_some()
    }

    pub fn token(&self) -> Option<String> {
        self.session
            .read()
            .as_ref()
            .map(|s| s.access_token.clone())
    }

    fn persist_session(&self, s: Option<&AuthSession>) -> Result<()> {
        match s {
            Some(s) => self.store.save_json(SESSION_FILE, s)?,
            None => {
                self.store.delete(SESSION_FILE)?;
            }
        }
        Ok(())
    }

    pub fn logout(&self) -> Result<()> {
        *self.session.write() = None;
        self.persist_session(None)?;
        Ok(())
    }

    fn api_root(&self) -> String {
        self.config.read().api_root()
    }

    async fn request_raw(
        &self,
        method: Method,
        path: &str,
        body: Option<Value>,
        auth: bool,
    ) -> Result<(StatusCode, String)> {
        let url = format!("{}{}", self.api_root(), path);
        let mut req = self.http.request(method, &url);
        if auth {
            let token = self.token().ok_or(Error::NotAuthenticated)?;
            req = req.header("Authorization", format!("Bearer {token}"));
        }
        req = req.header("Accept", "application/json");
        if let Some(b) = body {
            req = req.header("Content-Type", "application/json").json(&b);
        }
        let res = req
            .send()
            .await
            .map_err(|e| Error::Network(e.to_string()))?;
        let status = res.status();
        let text = res.text().await.map_err(|e| Error::Network(e.to_string()))?;
        Ok((status, text))
    }

    async fn request_json<T: DeserializeOwned>(
        &self,
        method: Method,
        path: &str,
        body: Option<Value>,
        auth: bool,
    ) -> Result<T> {
        let (status, text) = self.request_raw(method, path, body, auth).await?;
        if !status.is_success() {
            // Prefer structured {message|detail|error} over raw HTML/500 body.
            let friendly = serde_json::from_str::<Value>(&text)
                .ok()
                .and_then(|v| {
                    v.get("message")
                        .or_else(|| v.get("detail"))
                        .or_else(|| v.get("error"))
                        .and_then(|x| x.as_str())
                        .map(|s| s.to_string())
                })
                .unwrap_or_else(|| trunc(&text, 240));
            return Err(Error::http(status.as_u16(), friendly));
        }
        serde_json::from_str(&text).map_err(|e| {
            Error::Msg(format!(
                "json decode {path}: {e}; body={}",
                trunc(&text, 240)
            ))
        })
    }

    pub async fn health(&self) -> Result<HealthInfo> {
        self.request_json(Method::GET, "/health", None, false).await
    }

    pub async fn login(&self, email: &str, password: &str) -> Result<AuthSession> {
        let tr: TokenResponse = self
            .request_json(
                Method::POST,
                "/auth/login",
                Some(json!({ "email": email, "password": password })),
                false,
            )
            .await?;
        let base_url = self.config.read().base_url.clone();
        let session = AuthSession::from_token_response(base_url, tr);
        *self.session.write() = Some(session.clone());
        self.persist_session(Some(&session))?;
        Ok(session)
    }

    pub async fn auto_login(&self) -> Result<AuthSession> {
        let tr: TokenResponse = self
            .request_json(Method::POST, "/auth/auto-login", Some(json!({})), false)
            .await?;
        let base_url = self.config.read().base_url.clone();
        let session = AuthSession::from_token_response(base_url, tr);
        *self.session.write() = Some(session.clone());
        self.persist_session(Some(&session))?;
        Ok(session)
    }

    /// Exchange pair claim device token for a JWT session.
    ///
    /// This is the correct phone auth path: `/auth/auto-login` is loopback-only
    /// under single_user_mode and will 403 from LAN/VPS unless the tunnel
    /// spoofs 127.0.0.1. Device-token session works on every path.
    pub async fn pair_session_login(&self, device_token: &str) -> Result<AuthSession> {
        let tr: TokenResponse = self
            .request_json(
                Method::POST,
                "/mobile/pair/session",
                Some(json!({ "token": device_token })),
                false,
            )
            .await?;
        let base_url = self.config.read().base_url.clone();
        let session = AuthSession::from_token_response(base_url, tr);
        *self.session.write() = Some(session.clone());
        self.persist_session(Some(&session))?;
        Ok(session)
    }

    pub async fn list_sessions(&self, kind: Option<&str>) -> Result<Vec<SessionInfo>> {
        let path = match kind {
            Some(k) => format!("/sessions/my?kind={k}"),
            None => "/sessions/my".into(),
        };
        self.request_json(Method::GET, &path, None, true).await
    }

    pub async fn create_session(&self, title: Option<&str>) -> Result<SessionInfo> {
        let mut body = json!({ "config": {} });
        if let Some(t) = title {
            body["title"] = json!(t);
        }
        self.request_json(Method::POST, "/sessions", Some(body), true)
            .await
    }

    pub async fn get_session(&self, id: &str) -> Result<SessionInfo> {
        self.request_json(Method::GET, &format!("/sessions/{id}"), None, true)
            .await
    }

    pub async fn delete_session(&self, id: &str, force: bool) -> Result<()> {
        let path = format!("/sessions/{id}?force={force}");
        let (status, text) = self.request_raw(Method::DELETE, &path, None, true).await?;
        if !status.is_success() {
            return Err(Error::http(status.as_u16(), text));
        }
        Ok(())
    }

    /// Best-effort title sync to PC via SessionConfig.contact_agent (known field).
    /// Phone meta remains the source of truth for the mobile list.
    pub async fn patch_session_title(&self, id: &str, title: &str) -> Result<Value> {
        let path = format!("/sessions/{id}/config");
        let body = json!({
            "config": {
                "contact_agent": title,
            }
        });
        self.request_json(Method::PUT, &path, Some(body), true).await
    }

    pub async fn list_messages(&self, session_id: &str, limit: u32) -> Result<Vec<MessageInfo>> {
        let path = format!("/sessions/{session_id}/messages?limit={limit}");
        self.request_json(Method::GET, &path, None, true).await
    }

    pub async fn active_session_ids(&self) -> Result<Vec<String>> {
        self.request_json(Method::GET, "/sessions/active-ids", None, true)
            .await
    }

    pub async fn list_devices(&self) -> Result<Vec<DeviceInfo>> {
        self.request_json(Method::GET, "/devices", None, true).await
    }

    pub async fn pair_device(
        &self,
        name: &str,
        host: &str,
        port: u16,
        token: &str,
    ) -> Result<DeviceInfo> {
        self.request_json(
            Method::POST,
            "/devices/pair",
            Some(json!({
                "name": name,
                "host": host,
                "port": port,
                "token": token,
            })),
            true,
        )
        .await
    }

    pub async fn device_heartbeat(&self, id: &str) -> Result<Value> {
        self.request_json(
            Method::POST,
            &format!("/devices/{id}/heartbeat"),
            Some(json!({})),
            true,
        )
        .await
    }

    pub async fn device_ping(&self, id: &str) -> Result<Value> {
        self.request_json(
            Method::POST,
            &format!("/devices/{id}/remote/ping"),
            Some(json!({})),
            true,
        )
        .await
    }

    pub async fn list_escalations(&self, status: Option<&str>) -> Result<Value> {
        let path = match status {
            Some(s) => format!("/kernel/escalations?status={s}"),
            None => "/kernel/escalations".into(),
        };
        self.request_json(Method::GET, &path, None, true).await
    }

    pub async fn approve_escalation(&self, id: &str) -> Result<Value> {
        self.request_json(
            Method::POST,
            &format!("/kernel/escalations/{id}/approve"),
            Some(json!({})),
            true,
        )
        .await
    }

    pub async fn deny_escalation(&self, id: &str) -> Result<Value> {
        self.request_json(
            Method::POST,
            &format!("/kernel/escalations/{id}/deny"),
            Some(json!({})),
            true,
        )
        .await
    }

    pub async fn resolve_confirm(
        &self,
        confirm_id: &str,
        approved: bool,
        scope: &str,
    ) -> Result<Value> {
        self.request_json(
            Method::POST,
            &format!("/kernel/confirm/{confirm_id}"),
            Some(json!({ "approved": approved, "scope": scope })),
            true,
        )
        .await
    }

    pub async fn list_evolution_proposals(&self) -> Result<Value> {
        self.request_json(Method::GET, "/kernel/evolution/proposals", None, true)
            .await
    }

    pub async fn approve_evolution(&self, id: &str) -> Result<Value> {
        match self
            .request_json::<Value>(
                Method::POST,
                &format!("/kernel/evolution/proposals/{id}/approve"),
                Some(json!({})),
                true,
            )
            .await
        {
            Ok(v) => Ok(v),
            Err(_) => {
                self.request_json(
                    Method::POST,
                    &format!("/evolution/proposals/{id}/approve"),
                    Some(json!({})),
                    true,
                )
                .await
            }
        }
    }

    pub async fn reject_evolution(&self, id: &str) -> Result<Value> {
        match self
            .request_json::<Value>(
                Method::POST,
                &format!("/kernel/evolution/proposals/{id}/reject"),
                Some(json!({})),
                true,
            )
            .await
        {
            Ok(v) => Ok(v),
            Err(_) => {
                self.request_json(
                    Method::POST,
                    &format!("/evolution/proposals/{id}/reject"),
                    Some(json!({})),
                    true,
                )
                .await
            }
        }
    }

    pub async fn list_processes(&self, all: bool) -> Result<Value> {
        let path = if all {
            "/kernel/processes/?all=true"
        } else {
            "/kernel/processes/"
        };
        self.request_json(Method::GET, path, None, true).await
    }

    pub async fn stop_process(&self, id: &str) -> Result<Value> {
        self.request_json(
            Method::POST,
            "/kernel/jobs/stop",
            Some(json!({ "id": id, "process_id": id })),
            true,
        )
        .await
    }

    pub async fn resume_process(&self, id: &str) -> Result<Value> {
        self.request_json(
            Method::POST,
            &format!("/kernel/processes/{id}/resume"),
            Some(json!({})),
            true,
        )
        .await
    }

    pub async fn openai_oauth_start(&self) -> Result<Value> {
        self.request_json(
            Method::POST,
            "/settings/oauth/openai/start",
            Some(json!({})),
            true,
        )
        .await
    }

    pub async fn openai_oauth_poll(&self, state: Option<&str>) -> Result<Value> {
        let body = match state {
            Some(s) => json!({ "state": s }),
            None => json!({}),
        };
        self.request_json(Method::POST, "/settings/oauth/openai/poll", Some(body), true)
            .await
    }

    pub async fn openai_oauth_complete(
        &self,
        callback_url: &str,
        state: Option<&str>,
    ) -> Result<Value> {
        let mut body = json!({ "callback_url": callback_url });
        if let Some(s) = state {
            body["state"] = json!(s);
        }
        self.request_json(
            Method::POST,
            "/settings/oauth/openai/complete",
            Some(body),
            true,
        )
        .await
    }

    pub async fn xai_oauth_start(&self) -> Result<Value> {
        self.request_json(
            Method::POST,
            "/settings/oauth/xai/start",
            Some(json!({})),
            true,
        )
        .await
    }

    pub async fn xai_oauth_poll(&self, device_code: &str) -> Result<Value> {
        self.request_json(
            Method::POST,
            "/settings/oauth/xai/poll",
            Some(json!({ "device_code": device_code })),
            true,
        )
        .await
    }

    pub async fn list_presets(&self) -> Result<Value> {
        self.request_json(Method::GET, "/settings/presets", None, true)
            .await
    }

    pub async fn register_provider(&self, body: Value) -> Result<Value> {
        self.request_json(Method::POST, "/settings/providers", Some(body), true)
            .await
    }

    pub async fn model_catalog(&self, refresh: bool) -> Result<ModelCatalog> {
        let path = if refresh {
            "/settings/model-catalog?refresh=true"
        } else {
            "/settings/model-catalog"
        };
        match self
            .request_json::<ModelCatalog>(Method::GET, path, None, true)
            .await
        {
            Ok(c) => Ok(c),
            Err(_) => {
                let v: Value = self
                    .request_json(Method::GET, "/models", None, true)
                    .await
                    .unwrap_or(json!({}));
                Ok(ModelCatalog {
                    active_provider_id: None,
                    providers: v.as_array().cloned().unwrap_or_else(|| {
                        v.get("providers")
                            .and_then(|p| p.as_array())
                            .cloned()
                            .unwrap_or_default()
                    }),
                    extra: v,
                })
            }
        }
    }

    pub async fn select_model(
        &self,
        provider_id: &str,
        model: &str,
        session_id: Option<&str>,
    ) -> Result<Value> {
        let mut body = json!({
            "provider_id": provider_id,
            "model": model,
        });
        if let Some(sid) = session_id {
            body["session_id"] = json!(sid);
        }
        self.request_json(
            Method::POST,
            "/settings/model-catalog/select",
            Some(body),
            true,
        )
        .await
    }

    pub async fn test_llm(&self, body: Value) -> Result<Value> {
        self.request_json(Method::POST, "/settings/test-llm", Some(body), true)
            .await
    }

    pub async fn apply_settings(&self, patch: Value) -> Result<Value> {
        self.request_json(Method::POST, "/settings/apply", Some(patch), true)
            .await
    }

    pub async fn set_catalog_credentials(&self, body: Value) -> Result<Value> {
        self.request_json(
            Method::POST,
            "/settings/model-catalog/credentials",
            Some(body),
            true,
        )
        .await
    }

    pub async fn runtime_status(&self) -> Result<RuntimeStatus> {
        self.request_json(Method::GET, "/runtime/status", None, true)
            .await
    }

    pub async fn upload_file(
        &self,
        filename: &str,
        bytes: Vec<u8>,
        content_type: Option<&str>,
    ) -> Result<Value> {
        let url = format!("{}/upload", self.api_root());
        let token = self.token().ok_or(Error::NotAuthenticated)?;
        let part = reqwest::multipart::Part::bytes(bytes)
            .file_name(filename.to_string())
            .mime_str(content_type.unwrap_or("application/octet-stream"))
            .map_err(|e| Error::Msg(e.to_string()))?;
        let form = reqwest::multipart::Form::new().part("file", part);
        let res = self
            .http
            .post(&url)
            .header("Authorization", format!("Bearer {token}"))
            .multipart(form)
            .send()
            .await
            .map_err(|e| Error::Network(e.to_string()))?;
        let status = res.status();
        let text = res.text().await.map_err(|e| Error::Network(e.to_string()))?;
        if !status.is_success() {
            return Err(Error::http(status.as_u16(), text));
        }
        serde_json::from_str(&text).map_err(|e| Error::Msg(format!("upload json: {e}")))
    }

    pub fn ws_chat_url(&self, session_id: &str) -> Result<String> {
        let cfg = self.config.read();
        let token = self.token().ok_or(Error::NotAuthenticated)?;
        let root = cfg.ws_root();
        Ok(format!(
            "{root}/ws/{session_id}?token={}",
            urlencoding_lite(&token)
        ))
    }
}

fn trunc(s: &str, n: usize) -> String {
    let mut it = s.chars();
    let head: String = it.by_ref().take(n).collect();
    if it.next().is_some() {
        format!("{head}…")
    } else {
        head
    }
}

fn urlencoding_lite(s: &str) -> String {
    let mut out = String::with_capacity(s.len());
    for b in s.bytes() {
        match b {
            b'A'..=b'Z' | b'a'..=b'z' | b'0'..=b'9' | b'-' | b'_' | b'.' | b'~' => {
                out.push(b as char)
            }
            _ => out.push_str(&format!("%{b:02X}")),
        }
    }
    out
}
