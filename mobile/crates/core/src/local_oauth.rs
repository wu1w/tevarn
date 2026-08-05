//! Phone-local OAuth (no PC required).
//! Pending PKCE verifiers are **persisted to disk** so paste-callback still works
//! after the app was backgrounded / process restarted.

use base64::Engine;
use parking_lot::Mutex;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::collections::HashMap;
use std::sync::Arc;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};
use uuid::Uuid;

use crate::local_llm::CHATGPT_OAUTH_BASE;
use crate::storage::Store;


const OPENAI_CLIENT_ID: &str = "app_EMoamEEZ73f0CkXaXp7hrann";
const OPENAI_AUTH_URL: &str = "https://auth.openai.com/oauth/authorize";
const OPENAI_TOKEN_URL: &str = "https://auth.openai.com/oauth/token";
const OPENAI_REDIRECT: &str = "http://localhost:1455/auth/callback";
const OPENAI_SCOPE: &str = "openid email profile offline_access";

const XAI_CLIENT_ID: &str = "b1a00492-073a-47ea-816f-4c329264a828";
const XAI_DEVICE_URL: &str = "https://auth.x.ai/oauth2/device/code";
const XAI_TOKEN_URL: &str = "https://auth.x.ai/oauth2/token";
const XAI_SCOPE: &str = "openid profile email offline_access grok-cli:access api:access";
const XAI_BASE: &str = "https://api.x.ai/v1";

const PENDING_FILE: &str = "oauth_pending.json";

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
struct Persisted {
    #[serde(default)]
    openai: HashMap<String, OpenaiPendingSer>,
    #[serde(default)]
    xai: HashMap<String, XaiPendingSer>,
    #[serde(default)]
    results: HashMap<String, ResultSer>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
struct OpenaiPendingSer {
    verifier: String,
    created_unix: u64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
struct XaiPendingSer {
    created_unix: u64,
    expires_in: u64,
    interval: u64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
struct ResultSer {
    access_token: String,
    refresh_token: String,
    created_unix: u64,
    base_url: String,
    provider_label: String,
    #[serde(default)]
    account_id: String,
}

struct Inner {
    data: Persisted,
    /// In-memory Instant anchors for age checks (rebuilt from unix on load).
    boot: Instant,
    boot_unix: u64,
}

#[derive(Clone)]
pub struct LocalOauth {
    inner: Arc<Mutex<Inner>>,
    store: Store,
    http: reqwest::Client,
}

impl LocalOauth {
    pub fn open(store: Store) -> Self {
        let boot_unix = now_unix();
        let mut data: Persisted = store
            .load_json(PENDING_FILE)
            .ok()
            .flatten()
            .unwrap_or_default();
        // drop expired
        data.openai
            .retain(|_, v| boot_unix.saturating_sub(v.created_unix) < 900);
        data.xai.retain(|_, v| {
            boot_unix.saturating_sub(v.created_unix) < v.expires_in.saturating_add(60)
        });
        data.results
            .retain(|_, v| boot_unix.saturating_sub(v.created_unix) < 900);
        Self {
            inner: Arc::new(Mutex::new(Inner {
                data,
                boot: Instant::now(),
                boot_unix,
            })),
            store,
            http: reqwest::Client::builder()
                .timeout(Duration::from_secs(30))
                .build()
                .unwrap_or_else(|_| reqwest::Client::new()),
        }
    }

    fn persist(&self, data: &Persisted) {
        let _ = self.store.save_json(PENDING_FILE, data);
    }

    fn b64url(data: &[u8]) -> String {
        base64::engine::general_purpose::URL_SAFE_NO_PAD.encode(data)
    }

    fn pkce() -> (String, String) {
        let mut raw = Uuid::new_v4().as_bytes().to_vec();
        raw.extend_from_slice(Uuid::new_v4().as_bytes());
        let verifier = Self::b64url(&raw);
        let mut hasher = Sha256::new();
        hasher.update(verifier.as_bytes());
        let challenge = Self::b64url(&hasher.finalize());
        (verifier, challenge)
    }

    fn random_state() -> String {
        Self::b64url(Uuid::new_v4().as_bytes())
    }

    fn age_secs(inner: &Inner, created_unix: u64) -> u64 {
        let wall = now_unix().saturating_sub(created_unix);
        // also consider process uptime as fallback
        let mem = inner.boot.elapsed().as_secs()
            + created_unix.saturating_sub(inner.boot_unix).min(0);
        wall.max(mem)
    }

    fn cleanup_locked(inner: &mut Inner) {
        let now = now_unix();
        inner
            .data
            .openai
            .retain(|_, v| now.saturating_sub(v.created_unix) < 900);
        inner.data.xai.retain(|_, v| {
            now.saturating_sub(v.created_unix) < v.expires_in.saturating_add(60)
        });
        inner
            .data
            .results
            .retain(|_, v| now.saturating_sub(v.created_unix) < 900);
    }

    pub fn openai_start(&self) -> Value {
        let state = Self::random_state();
        let (verifier, challenge) = Self::pkce();
        {
            let mut g = self.inner.lock();
            Self::cleanup_locked(&mut g);
            g.data.openai.insert(
                state.clone(),
                OpenaiPendingSer {
                    verifier,
                    created_unix: now_unix(),
                },
            );
            self.persist(&g.data);
        }
        let auth_url = format!(
            "{OPENAI_AUTH_URL}?response_type=code&client_id={OPENAI_CLIENT_ID}\
             &redirect_uri={}&scope={}&code_challenge={challenge}\
             &code_challenge_method=S256&state={state}\
             &id_token_add_organizations=true&codex_cli_simplified_flow=true",
            urlencoding::encode(OPENAI_REDIRECT),
            urlencoding::encode(OPENAI_SCOPE),
        );
        json!({
            "ok": true,
            "state": state,
            "authorization_url": auth_url,
            "redirect_uri": OPENAI_REDIRECT,
            "expires_in": 600,
            "callback_listening": false,
            "local": true,
            "message": "① 将打开/复制授权链接 → 浏览器登录\n② 登录后浏览器会跳到 localhost 并失败——这是正常的\n③ 复制地址栏完整 URL（含 code=）\n④ 回到本 App 粘贴，点「完成登录」\n⑤ 成功后留在本页，点「应用模型」",
        })
    }

    pub fn openai_poll(&self, state: Option<&str>) -> Value {
        let g = self.inner.lock();
        let key = state
            .map(str::to_string)
            .or_else(|| g.data.results.keys().next().cloned());
        let Some(k) = key else {
            return json!({
                "ok": false,
                "status": "pending",
                "message": "等待授权…浏览器跳到 localhost 失败是正常的，请复制地址栏 URL 粘贴回来",
            });
        };
        if let Some(r) = g.data.results.get(&k) {
            return json!({
                "ok": true,
                "status": "authorized",
                "access_token": r.access_token,
                "refresh_token": r.refresh_token,
                "base_url": r.base_url,
                "provider_label": r.provider_label,
                "account_id": r.account_id,
                "local": true,
                "message": "ChatGPT 登录成功 · 令牌已写入本机",
            });
        }
        if g.data.openai.contains_key(&k) {
            return json!({
                "ok": false,
                "status": "pending",
                "message": "等待授权…完成后请粘贴回调 URL（不要离开 LLM 设置页）",
            });
        }
        json!({
            "ok": false,
            "status": "expired",
            "message": "登录会话已失效，请重新点「ChatGPT 登录」",
        })
    }

    pub async fn openai_complete(&self, callback_url: &str, state: Option<&str>) -> Value {
        let (code, st_from_url) = match parse_callback(callback_url) {
            Ok(v) => v,
            Err(e) => return json!({ "ok": false, "error": e, "message": e }),
        };
        let st = state
            .filter(|s| !s.is_empty())
            .map(|s| s.to_string())
            .or(st_from_url)
            .unwrap_or_default();
        if st.is_empty() {
            return json!({ "ok": false, "error": "缺少 state", "message": "回调 URL 缺少 state，请复制完整地址" });
        }

        // Reload from disk in case process restarted after start
        {
            let mut g = self.inner.lock();
            if let Ok(Some(disk)) = self.store.load_json::<Persisted>(PENDING_FILE) {
                for (k, v) in disk.openai {
                    g.data.openai.entry(k).or_insert(v);
                }
                for (k, v) in disk.results {
                    g.data.results.entry(k).or_insert(v);
                }
            }
            Self::cleanup_locked(&mut g);
        }

        let verifier = {
            let mut g = self.inner.lock();
            match g.data.openai.remove(&st) {
                Some(p) => {
                    self.persist(&g.data);
                    p.verifier
                }
                None => {
                    // Already completed? return existing result
                    if let Some(r) = g.data.results.get(&st) {
                        return json!({
                            "ok": true,
                            "status": "authorized",
                            "access_token": r.access_token,
                            "refresh_token": r.refresh_token,
                            "base_url": r.base_url,
                            "provider_label": r.provider_label,
                            "local": true,
                            "message": "ChatGPT 登录已完成（缓存）",
                        });
                    }
                    return json!({
                        "ok": false,
                        "error": "登录会话不存在或已过期",
                        "message": "登录会话不存在或已过期。请重新点「ChatGPT 登录」，再在浏览器授权后粘贴新的回调 URL。",
                    });
                }
            }
        };

        let form = [
            ("grant_type", "authorization_code"),
            ("client_id", OPENAI_CLIENT_ID),
            ("code", code.as_str()),
            ("redirect_uri", OPENAI_REDIRECT),
            ("code_verifier", verifier.as_str()),
        ];
        let resp = match self
            .http
            .post(OPENAI_TOKEN_URL)
            .header("Content-Type", "application/x-www-form-urlencoded")
            .form(&form)
            .send()
            .await
        {
            Ok(r) => r,
            Err(e) => {
                return json!({
                    "ok": false,
                    "error": format!("换 token 失败: {e}"),
                    "message": format!("换 token 失败: {e}"),
                })
            }
        };
        let status = resp.status();
        let body: Value = resp.json().await.unwrap_or(json!({}));
        if !status.is_success() {
            let err = body
                .get("error_description")
                .or_else(|| body.get("error"))
                .and_then(|v| v.as_str())
                .map(|s| s.to_string())
                .unwrap_or_else(|| format!("HTTP {status}"));
            // put verifier back so user can retry same session if code still valid? codes are one-shot
            return json!({ "ok": false, "error": err, "message": err, "detail": body });
        }
        let access = body
            .get("access_token")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_string();
        if access.is_empty() {
            return json!({ "ok": false, "error": "响应无 access_token", "message": "响应无 access_token" });
        }
        let refresh = body
            .get("refresh_token")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_string();
        let account_id = extract_chatgpt_account_id(&body, &access);
        let result = ResultSer {
            access_token: access.clone(),
            refresh_token: refresh.clone(),
            created_unix: now_unix(),
            base_url: CHATGPT_OAUTH_BASE.into(),
            provider_label: "ChatGPT OAuth".into(),
            account_id: account_id.clone(),
        };
        {
            let mut g = self.inner.lock();
            g.data.results.insert(st.clone(), result.clone());
            self.persist(&g.data);
        }
        json!({
            "ok": true,
            "status": "authorized",
            "access_token": access,
            "refresh_token": refresh,
            "base_url": CHATGPT_OAUTH_BASE,
            "provider_label": "ChatGPT OAuth",
            "provider_id": "openai-chatgpt-oauth",
            "account_id": account_id,
            "local": true,
            "state": st,
            "message": "ChatGPT 登录成功 · 令牌已写入本机（订阅 Codex 路径）。请拉取模型后点「应用模型」。",
        })
    }

    pub async fn xai_start(&self) -> Value {
        let resp = match self
            .http
            .post(XAI_DEVICE_URL)
            .header("Content-Type", "application/x-www-form-urlencoded")
            .header("Accept", "application/json")
            .form(&[("client_id", XAI_CLIENT_ID), ("scope", XAI_SCOPE)])
            .send()
            .await
        {
            Ok(r) => r,
            Err(e) => {
                return json!({
                    "ok": false,
                    "error": format!("申请设备码失败: {e}"),
                    "message": format!("申请设备码失败: {e}"),
                })
            }
        };
        let status = resp.status();
        let body: Value = resp.json().await.unwrap_or(json!({}));
        if !status.is_success() {
            return json!({
                "ok": false,
                "error": format!("申请设备码失败 (HTTP {status})"),
                "message": format!("申请设备码失败 (HTTP {status})"),
                "detail": body,
            });
        }
        let device_code = body
            .get("device_code")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_string();
        if device_code.is_empty() {
            return json!({ "ok": false, "error": "设备码响应缺少 device_code", "message": "设备码响应异常" });
        }
        let expires_in = body.get("expires_in").and_then(|v| v.as_u64()).unwrap_or(900);
        let interval = body
            .get("interval")
            .and_then(|v| v.as_u64())
            .unwrap_or(5)
            .max(1);
        {
            let mut g = self.inner.lock();
            Self::cleanup_locked(&mut g);
            g.data.xai.insert(
                device_code.clone(),
                XaiPendingSer {
                    created_unix: now_unix(),
                    expires_in,
                    interval,
                },
            );
            self.persist(&g.data);
        }
        json!({
            "ok": true,
            "device_code": device_code,
            "user_code": body.get("user_code").cloned().unwrap_or(json!("")),
            "verification_uri": body.get("verification_uri").cloned().unwrap_or(json!("https://accounts.x.ai")),
            "verification_uri_complete": body.get("verification_uri_complete").cloned().unwrap_or(json!("")),
            "expires_in": expires_in,
            "interval": interval,
            "local": true,
            "message": "请在浏览器打开验证链接并输入代码。完成后回到本页等待（不会强制跳转）。",
        })
    }

    pub async fn xai_poll(&self, device_code: &str) -> Value {
        if device_code.is_empty() {
            return json!({ "ok": false, "status": "error", "error": "缺少 device_code" });
        }
        // merge disk
        {
            let mut g = self.inner.lock();
            if let Ok(Some(disk)) = self.store.load_json::<Persisted>(PENDING_FILE) {
                for (k, v) in disk.xai {
                    g.data.xai.entry(k).or_insert(v);
                }
            }
        }
        let meta = {
            let g = self.inner.lock();
            g.data
                .xai
                .get(device_code)
                .map(|m| (m.created_unix, m.expires_in, m.interval))
        };
        let Some((created_unix, expires_in, interval)) = meta else {
            return json!({
                "ok": false,
                "status": "expired",
                "message": "登录会话已失效，请重新发起 OAuth",
            });
        };
        if now_unix().saturating_sub(created_unix) > expires_in {
            let mut g = self.inner.lock();
            g.data.xai.remove(device_code);
            self.persist(&g.data);
            return json!({
                "ok": false,
                "status": "expired",
                "message": "授权超时，请重新点击登录",
            });
        }

        let resp = match self
            .http
            .post(XAI_TOKEN_URL)
            .header("Content-Type", "application/x-www-form-urlencoded")
            .header("Accept", "application/json")
            .form(&[
                ("grant_type", "urn:ietf:params:oauth:grant-type:device_code"),
                ("client_id", XAI_CLIENT_ID),
                ("device_code", device_code),
            ])
            .send()
            .await
        {
            Ok(r) => r,
            Err(e) => {
                return json!({
                    "ok": false,
                    "status": "pending",
                    "message": format!("轮询网络错误: {e}"),
                    "interval": interval,
                })
            }
        };
        let body: Value = resp.json().await.unwrap_or(json!({}));
        if let Some(access) = body.get("access_token").and_then(|v| v.as_str()) {
            let mut g = self.inner.lock();
            g.data.xai.remove(device_code);
            self.persist(&g.data);
            return json!({
                "ok": true,
                "status": "authorized",
                "access_token": access,
                "refresh_token": body.get("refresh_token").cloned().unwrap_or(json!("")),
                "expires_in": body.get("expires_in").cloned().unwrap_or(json!(3600)),
                "base_url": XAI_BASE,
                "provider_label": "Grok OAuth",
                "provider_id": "xai-oauth",
                "local": true,
                "message": "Grok 登录成功 · 令牌已写入本机。请点「应用模型」。",
            });
        }
        let err = body.get("error").and_then(|v| v.as_str()).unwrap_or("");
        match err {
            "authorization_pending" => json!({
                "ok": false,
                "status": "pending",
                "message": "等待浏览器中完成授权…",
                "interval": interval,
            }),
            "slow_down" => json!({
                "ok": false,
                "status": "pending",
                "message": "请稍候…",
                "interval": (interval + 2).min(15),
            }),
            "expired_token" => {
                let mut g = self.inner.lock();
                g.data.xai.remove(device_code);
                self.persist(&g.data);
                json!({ "ok": false, "status": "expired", "message": "设备码已过期，请重新登录" })
            }
            "access_denied" => {
                let mut g = self.inner.lock();
                g.data.xai.remove(device_code);
                self.persist(&g.data);
                json!({ "ok": false, "status": "denied", "message": "用户拒绝了授权" })
            }
            _ => json!({
                "ok": false,
                "status": "pending",
                "message": "等待授权…",
                "interval": interval,
            }),
        }
    }
}

fn now_unix() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0)
}

/// Pull ChatGPT account id from token JSON or JWT claims (desktop parity).
fn extract_chatgpt_account_id(payload: &Value, access_token: &str) -> String {
    for key in [
        "chatgpt_account_id",
        "account_id",
        "https://api.openai.com/auth.chatgpt_account_id",
    ] {
        if let Some(v) = payload.get(key).and_then(|x| x.as_str()) {
            if !v.is_empty() {
                return v.to_string();
            }
        }
    }
    // JWT payload heuristic
    let parts: Vec<&str> = access_token.split('.').collect();
    if parts.len() >= 2 {
        let mut b64 = parts[1].replace('-', "+").replace('_', "/");
        while b64.len() % 4 != 0 {
            b64.push('=');
        }
        if let Ok(bytes) = base64::Engine::decode(
            &base64::engine::general_purpose::STANDARD,
            b64.as_bytes(),
        ) {
            if let Ok(claims) = serde_json::from_slice::<Value>(&bytes) {
                if let Some(auth) = claims.get("https://api.openai.com/auth") {
                    if let Some(aid) = auth
                        .get("chatgpt_account_id")
                        .or_else(|| auth.get("account_id"))
                        .and_then(|x| x.as_str())
                    {
                        if !aid.is_empty() {
                            return aid.to_string();
                        }
                    }
                }
                for k in ["chatgpt_account_id", "account_id", "org_id"] {
                    if let Some(v) = claims.get(k).and_then(|x| x.as_str()) {
                        if !v.is_empty() {
                            return v.to_string();
                        }
                    }
                }
            }
        }
    }
    String::new()
}

fn parse_callback(raw: &str) -> Result<(String, Option<String>), String> {
    let s = raw.trim();
    if s.is_empty() {
        return Err("回调地址为空".into());
    }
    // Also accept multiline paste / "地址无法访问" pages where user only copied query
    let q = if let Some(i) = s.find('?') {
        &s[i + 1..]
    } else if s.contains("code=") {
        s.trim_start_matches('?')
    } else {
        return Err("URL 中未找到 code= 参数。请复制浏览器地址栏完整链接（即使 localhost 打不开）。".into());
    };
    // strip fragment
    let q = q.split('#').next().unwrap_or(q);
    let mut code = None;
    let mut state = None;
    for part in q.split('&') {
        let mut kv = part.splitn(2, '=');
        let k = kv.next().unwrap_or("");
        let v = kv.next().unwrap_or("");
        let v = urlencoding::decode(v)
            .unwrap_or_else(|_| v.into())
            .into_owned();
        if k == "code" {
            code = Some(v);
        } else if k == "state" {
            state = Some(v);
        }
    }
    let code = code
        .filter(|c| !c.is_empty())
        .ok_or_else(|| "缺少 code".to_string())?;
    Ok((code, state))
}
