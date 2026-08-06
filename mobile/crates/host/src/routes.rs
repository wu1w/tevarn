use axum::{
    extract::{
        multipart::Multipart,
        ws::{Message, WebSocket, WebSocketUpgrade},
        Path, Query, State,
    },
    response::{
        sse::{Event, KeepAlive, Sse},
        IntoResponse, Response,
    },
    routing::{get, post},
    Json, Router,
};
use futures_util::Stream;

use serde::Deserialize;
use serde_json::{json, Value};
use std::convert::Infallible;
use std::sync::Arc;
use std::time::Instant;
use takton_mobile_core::chat::{ChatConnection, ChatEvent};
use takton_mobile_core::models::SessionInfo;
use takton_mobile_core::{
    filter_catalog, normalize_ui_messages, ChatSurface, ModeSnapshot, MotionProfile,
    LOCAL_SESSION_ID,
};
use tokio::sync::mpsc;
use axum::http::header;

use crate::state::AppState;

// ── request bodies ──────────────────────────────────────────────────────────

#[derive(Debug, Deserialize)]
pub struct ConnectBody {
    #[serde(default)]
    pub base_url: Option<String>,
    /// Extra candidates to probe (LAN/TS/host). Prefer LAN when reachable.
    #[serde(default)]
    pub candidates: Option<Vec<String>>,
    #[serde(default)]
    pub email: Option<String>,
    #[serde(default)]
    pub password: Option<String>,
}

#[derive(Debug, Deserialize)]
pub struct RenameBody {
    pub title: String,
}

#[derive(Debug, Deserialize)]
pub struct PinBody {
    pub pinned: bool,
}

#[derive(Debug, Deserialize)]
pub struct DecideBody {
    pub approved: bool,
    #[serde(default)]
    pub scope: Option<String>,
    #[serde(default)]
    pub kind: Option<String>,
}

#[derive(Debug, Deserialize)]
pub struct PairBody {
    pub name: String,
    pub host: String,
    pub port: u16,
    pub token: String,
}

#[derive(Debug, Deserialize)]
pub struct PairStartBody {
    #[serde(default)]
    pub mesh: Option<String>,
    #[serde(default)]
    pub require_confirm: Option<bool>,
    #[serde(default)]
    pub host: Option<String>,
    #[serde(default)]
    pub port: Option<u16>,
    #[serde(default)]
    pub name: Option<String>,
}

#[derive(Debug, Deserialize)]
pub struct PairClaimBody {
    pub pair_id: String,
    pub code: String,
    #[serde(default)]
    pub device_name: Option<String>,
}

#[derive(Debug, Deserialize)]
pub struct PairApplyBody {
    pub qr: String,
    #[serde(default)]
    pub device_name: Option<String>,
    #[serde(default)]
    pub email: Option<String>,
    #[serde(default)]
    pub password: Option<String>,
    #[serde(default)]
    pub claim: Option<bool>,
}

#[derive(Debug, Deserialize)]
pub struct MeshSetBody {
    #[serde(default)]
    pub mode: Option<String>,
    #[serde(default)]
    pub hostname: Option<String>,
    #[serde(default)]
    pub require_pair_confirm: Option<bool>,
}

#[derive(Debug, Deserialize)]
pub struct MeshAuthBody {
    #[serde(default)]
    pub auth_key: Option<String>,
}

#[derive(Debug, Deserialize)]
pub struct MeshEmbedBody {
    #[serde(default)]
    pub role: Option<String>,
    #[serde(default)]
    pub hostname: Option<String>,
}

#[derive(Debug, Deserialize)]
pub struct MeshRuntimeBody {
    #[serde(default)]
    pub hostname: Option<String>,
    #[serde(default)]
    pub ifaces: Option<Vec<String>>,
    #[serde(default)]
    pub auth_key: Option<bool>,
}

#[derive(Debug, Deserialize)]
pub struct PathProbeBody {
    #[serde(default)]
    pub candidates: Option<Vec<String>>,
}

#[derive(Debug, Deserialize)]
pub struct PathReconnectBody {
    #[serde(default)]
    pub candidates: Option<Vec<String>>,
    #[serde(default)]
    pub email: Option<String>,
    #[serde(default)]
    pub password: Option<String>,
    #[serde(default)]
    pub claim: Option<bool>,
}

#[derive(Debug, Deserialize)]
pub struct SelectModelBody {
    pub provider_id: String,
    pub model: String,
    #[serde(default)]
    pub session_id: Option<String>,
}

#[derive(Debug, Deserialize)]
pub struct NotifyBody {
    pub enabled: bool,
}

#[derive(Debug, Deserialize)]
pub struct ModeBody {
    pub surface: String,
}

#[derive(Debug, Deserialize)]
pub struct SwitchSurfaceBody {
    pub surface: String,
    #[serde(default)]
    pub session_id: Option<String>,
    #[serde(default)]
    pub ensure_session: Option<bool>,
}

#[derive(Debug, Deserialize)]
pub struct LocalConfigBody {
    #[serde(default)]
    pub base_url: Option<String>,
    #[serde(default)]
    pub api_key: Option<String>,
    #[serde(default)]
    pub model: Option<String>,
    #[serde(default)]
    pub provider_label: Option<String>,
    #[serde(default)]
    pub chat_path: Option<String>,
    #[serde(default)]
    pub account_id: Option<String>,
}

#[derive(Debug, Deserialize, Clone)]
pub struct LocalChatImage {
    #[serde(default)]
    pub mime: Option<String>,
    /// raw base64 (no data: prefix) or full data URL
    #[serde(default)]
    pub data_b64: Option<String>,
    #[serde(default, alias = "b64")]
    pub base64: Option<String>,
}

#[derive(Debug, Deserialize)]
pub struct LocalChatBody {
    #[serde(default)]
    pub content: Option<String>,
    #[serde(default)]
    pub message: Option<String>,
    #[serde(default)]
    pub reset: Option<bool>,
    /// Multimodal images for vision models
    #[serde(default)]
    pub images: Option<Vec<LocalChatImage>>,
}

impl LocalChatBody {
    fn text(&self) -> String {
        self.content
            .clone()
            .filter(|s| !s.trim().is_empty())
            .or_else(|| self.message.clone())
            .unwrap_or_default()
    }

    fn image_parts(&self) -> Vec<takton_mobile_core::LocalImagePart> {
        let Some(imgs) = &self.images else {
            return Vec::new();
        };
        let mut out = Vec::new();
        for img in imgs.iter().take(6) {
            let mut raw = img
                .data_b64
                .clone()
                .or_else(|| img.base64.clone())
                .unwrap_or_default();
            raw = raw.trim().to_string();
            if raw.is_empty() {
                continue;
            }
            let mut mime = img
                .mime
                .clone()
                .unwrap_or_else(|| "image/jpeg".into());
            // Accept data:image/png;base64,XXXX
            if let Some(rest) = raw.strip_prefix("data:") {
                if let Some((meta, b64)) = rest.split_once(",") {
                    if let Some(mt) = meta.split(';').next() {
                        if mt.starts_with("image/") {
                            mime = mt.to_string();
                        }
                    }
                    raw = b64.trim().to_string();
                }
            }
            // strip whitespace/newlines from b64
            raw.retain(|c| !c.is_whitespace());
            if raw.is_empty() {
                continue;
            }
            // Cap ~4MB base64 (~3MB binary)
            if raw.len() > 5_500_000 {
                continue;
            }
            out.push(takton_mobile_core::LocalImagePart {
                mime,
                data_b64: raw,
            });
        }
        out
    }
}

#[derive(Debug, Deserialize)]
struct LimitQuery {
    limit: Option<u32>,
}

#[derive(Debug, Deserialize)]
struct CatalogQuery {
    refresh: Option<bool>,
    /// Free-text filter over provider name/id and model ids
    q: Option<String>,
    /// Restrict to one provider id
    provider: Option<String>,
    #[serde(alias = "provider_id")]
    provider_id: Option<String>,
}

#[derive(Debug, Deserialize)]
struct SessionsQuery {
    /// Free-text filter over session title/id (applied after pin sort)
    q: Option<String>,
}

// ── router ──────────────────────────────────────────────────────────────────

pub fn api_router() -> Router<AppState> {
    Router::new().nest(
        "/api/mobile",
        Router::new()
            .route("/health", get(healthz))
            .route("/state", get(app_state))
            .route("/connect", post(connect))
            .route("/disconnect", post(disconnect))
            .route("/auto-login", post(auto_login))
            .route("/sessions", get(list_sessions).post(create_session))
            .route("/sessions/{id}", get(get_session))
            .route("/sessions/{id}/messages", get(list_messages))
            .route("/sessions/{id}/turn_status", get(turn_status))
            .route("/sessions/{id}/open", post(open_session))
            .route("/sessions/{id}/pin", post(pin_session))
            .route("/sessions/{id}/rename", post(rename_session))
            .route("/sessions/{id}/delete", post(delete_session))
            .route("/sessions/{id}/stop", post(stop_session))

            .route("/session-meta", get(get_session_meta))
            .route("/approvals", get(list_approvals))
            .route("/approvals/summary", get(approvals_summary))
            .route("/approvals/{id}/decide", post(decide_approval))
            .route("/devices", get(list_devices))
            .route("/devices/pair", post(pair_device))
            .route("/devices/{id}/heartbeat", post(device_heartbeat))
            .route("/devices/{id}/ping", post(device_ping))
            // M1 QR pairing
            .route("/pair/start", post(pair_start))
            .route("/pair/status/{id}", get(pair_status))
            .route("/pair/confirm/{id}", post(pair_confirm))
            .route("/pair/cancel/{id}", post(pair_cancel))
            .route("/pair/claim", post(pair_claim))
            .route("/pair/apply", post(pair_apply))
            .route("/pair/devices", get(pair_devices))
            .route("/pair/revoke/{id}", post(pair_revoke))
            .route("/pair/pending", get(pair_pending))
            // M2 mesh
            .route("/mesh", get(mesh_status).post(mesh_set))
            .route("/mesh/up", post(mesh_up))
            .route("/mesh/down", post(mesh_down))
            .route("/mesh/ifaces", post(mesh_ifaces))
            .route("/mesh/auth", post(mesh_auth))
            .route("/mesh/embed/start", post(mesh_embed_start))
            .route("/mesh/embed/stop", post(mesh_embed_stop))
            .route("/mesh/embed", get(mesh_embed_status))
            // M4 multi-endpoint path
            .route("/path", get(path_status))
            .route("/path/probe", post(path_probe))
            .route("/path/reconnect", post(path_reconnect))
            .route("/path/refresh", post(path_refresh))
            .route("/catalog", get(catalog))
            .route("/catalog/select", post(select_model))
            .route("/catalog/register", post(register_provider))
            .route("/presets", get(list_presets))
            .route("/oauth/openai/start", post(oauth_openai_start))
            .route("/oauth/openai/poll", post(oauth_openai_poll))
            .route("/oauth/openai/complete", post(oauth_openai_complete))
            .route("/oauth/xai/start", post(oauth_xai_start))
            .route("/oauth/xai/poll", post(oauth_xai_poll))
            .route("/test-llm", post(test_llm))
            .route("/settings/apply", post(apply_settings))
            .route("/settings/credentials", post(set_credentials))
            .route("/upload", post(upload_file))
            .route("/runtime", get(runtime))
            .route("/processes", get(list_processes))
            .route("/processes/{id}/stop", post(stop_process))
            .route("/processes/{id}/resume", post(resume_process))
            .route("/notify", post(set_notify))
            .route("/local/config", get(local_config_get).post(local_config_set))
            .route("/local/config/clear", post(local_config_clear))
            .route("/local/test", post(local_test))
            .route("/local/tools", post(local_tools_run))
            .route("/local/skills", get(local_skills_list).post(local_skills_install))
            .route("/local/skills/pack", post(local_skills_install_pack))
            .route("/local/skills/uninstall", post(local_skills_uninstall))
            .route("/local/mcp", get(local_mcp_get).post(local_mcp_set))
            .route("/local/agent_config", get(local_agent_cfg_get).post(local_agent_cfg_set))
            .route("/local/history", get(local_history_get).post(local_history_clear))
            .route("/local/chat", post(local_chat_stream))
            .route("/local/stop", post(local_stop))
            .route("/kernel", get(kernel_status))
            .route("/motion", get(ui_motion))
            .route("/media", get(list_media).post(save_media))
            .route("/media/{id}", get(get_media))
            .route("/mode", post(resolve_mode))
            .route("/switch_surface", post(switch_surface))
            .route("/ws", get(ws_upgrade)),
    )
}

// ── helpers ─────────────────────────────────────────────────────────────────

fn err_json(e: impl ToString) -> Json<Value> {
    Json(json!({ "ok": false, "error": e.to_string() }))
}

fn mask_secret(s: &str) -> String {
    let s = s.trim();
    if s.is_empty() {
        return String::new();
    }
    if s.len() <= 8 {
        return "••••".into();
    }
    format!("{}…{}", &s[..4], &s[s.len().saturating_sub(4)..])
}

fn agent_config_public(cfg: &takton_mobile_core::AgentConfig) -> Value {
    json!({
        "max_iterations": cfg.max_iterations,
        "context_soft_tokens": cfg.context_soft_tokens,
        "context_hard_tokens": cfg.context_hard_tokens,
        "enable_skills": cfg.enable_skills,
        "enable_mcp": cfg.enable_mcp,
        "enable_text_tools": cfg.enable_text_tools,
        "azure_vision_endpoint": cfg.azure_vision_endpoint,
        "azure_speech_region": cfg.azure_speech_region,
        "tts_voice": cfg.tts_voice,
        // Secrets: masked only — UI must not overwrite with empty on save.
        "azure_vision_key": mask_secret(&cfg.azure_vision_key),
        "azure_speech_key": mask_secret(&cfg.azure_speech_key),
        "tavily_api_key": mask_secret(&cfg.tavily_api_key),
        "has_azure_vision_key": !cfg.azure_vision_key.trim().is_empty(),
        "has_azure_speech_key": !cfg.azure_speech_key.trim().is_empty(),
        "has_tavily_api_key": !cfg.tavily_api_key.trim().is_empty(),
    })
}

fn chrono_now() -> u64 {
    use std::time::{SystemTime, UNIX_EPOCH};
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0)
}

fn normalize_list(v: &Value) -> Vec<Value> {
    if let Some(arr) = v.as_array() {
        return arr.clone();
    }
    for key in ["items", "sessions", "data", "results", "proposals", "escalations", "processes"] {
        if let Some(arr) = v.get(key).and_then(|x| x.as_array()) {
            return arr.clone();
        }
    }
    vec![]
}

fn merge_ok(v: Value) -> Value {
    match v {
        Value::Object(mut m) => {
            m.entry("ok".to_string()).or_insert(json!(true));
            Value::Object(m)
        }
        other => json!({ "ok": true, "data": other }),
    }
}

fn base_url_of(st: &AppState) -> String {
    st.client.config().base_url
}

fn user_email_of(st: &AppState) -> String {
    st.client
        .session()
        .map(|s| s.user.email)
        .unwrap_or_default()
}

fn session_view(
    s: &SessionInfo,
    meta: &takton_mobile_core::session_meta::SessionMetaStore,
) -> Value {
    let id = s.id.clone();
    let title = meta
        .title_of(&id)
        .unwrap_or_else(|| s.display_title());
    json!({
        "id": id,
        "title": title,
        "pinned": meta.is_pinned(&id),
        "updated_at": s.updated_at,
    })
}

fn local_session_view(meta: &takton_mobile_core::session_meta::SessionMetaStore) -> Value {
    let title = meta
        .title_of(LOCAL_SESSION_ID)
        .unwrap_or_else(|| "本机对话".into());
    json!({
        "id": LOCAL_SESSION_ID,
        "title": title,
        "pinned": meta.is_pinned(LOCAL_SESSION_ID),
        "is_local": true,
    })
}

fn sort_session_views(mut sessions: Vec<Value>) -> Vec<Value> {
    sessions.sort_by(|a, b| {
        let ap = a.get("pinned").and_then(|v| v.as_bool()).unwrap_or(false);
        let bp = b.get("pinned").and_then(|v| v.as_bool()).unwrap_or(false);
        match (ap, bp) {
            (true, false) => std::cmp::Ordering::Less,
            (false, true) => std::cmp::Ordering::Greater,
            _ => {
                let at = a.get("updated_at").and_then(|v| v.as_str()).unwrap_or("");
                let bt = b.get("updated_at").and_then(|v| v.as_str()).unwrap_or("");
                bt.cmp(at)
            }
        }
    });
    sessions
}

async fn collect_session_views(st: &AppState) -> (Vec<Value>, Value) {
    let meta = st.load_meta();
    let local = local_session_view(&meta);
    if !st.client.is_authenticated() {
        return (vec![], local);
    }
    match st.client.list_sessions(None).await {
        Ok(list) => {
            let views: Vec<Value> = list.iter().map(|s| session_view(s, &meta)).collect();
            (sort_session_views(views), local)
        }
        Err(_) => (vec![], local),
    }
}

fn catalog_active_model(cat: &Value) -> Option<String> {
    cat.get("active_model")
        .and_then(|v| v.as_str())
        .map(|s| s.to_string())
        .or_else(|| {
            cat.get("active_provider_id").and_then(|_| {
                cat.get("providers")
                    .and_then(|p| p.as_array())
                    .and_then(|arr| {
                        arr.iter().find_map(|p| {
                            let active = p.get("active").and_then(|v| v.as_bool()) == Some(true)
                                || p.get("is_active").and_then(|v| v.as_bool()) == Some(true);
                            if active {
                                p.get("active_model")
                                    .or_else(|| p.get("model"))
                                    .and_then(|v| v.as_str())
                                    .map(|s| s.to_string())
                            } else {
                                None
                            }
                        })
                    })
            })
        })
        .or_else(|| {
            // flatten extra
            cat.get("extra")
                .and_then(|e| e.get("active_model"))
                .and_then(|v| v.as_str())
                .map(|s| s.to_string())
        })
}

fn resolve_surface(s: &str) -> ChatSurface {
    match s.to_ascii_lowercase().as_str() {
        "remote" | "pc" | "agent" => ChatSurface::Remote,
        _ => ChatSurface::Local,
    }
}

fn build_mode_snapshot(
    st: &AppState,
    surface: ChatSurface,
    pc_model: &str,
    runtime: Option<&takton_mobile_core::models::RuntimeStatus>,
    // When probe cache is fresh, pass stored kernel_ready so we don't
    // optimistically treat `runtime=None` as ready.
    cached_kernel_ready: Option<bool>,
) -> ModeSnapshot {
    let pc_connected = st.client.is_authenticated();
    let profile = st.local_llm.load_profile();
    let local_llm_ready = profile.is_ready();
    let kernel_ready = if let Some(k) = cached_kernel_ready {
        pc_connected && k
    } else {
        let kernel_local = AppState::probe_local_kernel();
        let runtime_ok = runtime
            .map(|rt| {
                rt.ok
                    || rt.processes_live.unwrap_or(0) > 0
                    || rt.jobs_claimed.unwrap_or(0) > 0
                    || rt.jobs_pending.unwrap_or(0) > 0
            })
            .unwrap_or(true);
        pc_connected && (kernel_local || runtime_ok)
    };
    ModeSnapshot::resolve(
        surface,
        pc_connected,
        local_llm_ready,
        kernel_ready,
        &profile.model,
        pc_model,
    )
}

/// Returns (pc_model, runtime_if_fetched, cached_kernel_ready_if_hit).
async fn remote_probe(
    st: &AppState,
) -> (
    String,
    Option<takton_mobile_core::models::RuntimeStatus>,
    Option<bool>,
) {
    {
        let cache = st.remote_probe.read();
        if cache.fresh() {
            return (cache.pc_model.clone(), None, Some(cache.kernel_ready));
        }
    }
    let mut pc_model = String::new();
    let mut runtime = None;
    if st.client.is_authenticated() {
        if let Ok(cat) = st.client.model_catalog(false).await {
            let v = serde_json::to_value(&cat).unwrap_or(json!({}));
            pc_model = catalog_active_model(&v).unwrap_or_default();
            if pc_model.is_empty() {
                if let Some(pid) = cat.active_provider_id.as_ref() {
                    for p in &cat.providers {
                        if p.get("id").and_then(|x| x.as_str()) == Some(pid.as_str()) {
                            if let Some(m) = p
                                .get("active_model")
                                .or_else(|| p.get("model"))
                                .and_then(|x| x.as_str())
                            {
                                pc_model = m.to_string();
                            }
                        }
                    }
                }
            }
        }
        runtime = st.client.runtime_status().await.ok();
        let kernel_ready = {
            let runtime_ok = runtime
                .as_ref()
                .map(|rt| {
                    rt.ok
                        || rt.processes_live.unwrap_or(0) > 0
                        || rt.jobs_claimed.unwrap_or(0) > 0
                        || rt.jobs_pending.unwrap_or(0) > 0
                })
                .unwrap_or(true);
            AppState::probe_local_kernel() || runtime_ok
        };
        let mut cache = st.remote_probe.write();
        cache.pc_model = pc_model.clone();
        cache.kernel_ready = kernel_ready;
        cache.at = Some(Instant::now());
    }
    (pc_model, runtime, None)
}

fn local_history_as_ui(st: &AppState) -> Vec<Value> {
    let hist = st.local_llm.load_history();
    let raw: Vec<Value> = hist
        .messages
        .into_iter()
        .enumerate()
        .map(|(i, m)| {
            json!({
                "id": format!("local-{i}"),
                "role": m.role,
                "content": m.content,
            })
        })
        .collect();
    normalize_ui_messages(&raw, "本机 · LLM")
        .into_iter()
        .map(|m| m.to_value())
        .collect()
}

async fn remote_messages_as_ui(st: &AppState, id: &str, limit: u32) -> Vec<Value> {
    if id.is_empty() || id == LOCAL_SESSION_ID {
        return vec![];
    }
    if !st.client.is_authenticated() {
        return vec![];
    }
    match st.client.list_messages(id, limit).await {
        Ok(msgs) => {
            // Keep tool_calls so Flutter poll / history can detect tool loops
            // and render Codex-like tool rows (was dropped → early "final" finish).
            let base = base_url_of(st);
            let raw: Vec<Value> = msgs
                .into_iter()
                .map(|m| {
                    let text = absolutize_content_media_links(&base, &m.text());
                    let mut obj = json!({
                        "id": m.id,
                        "role": m.role,
                        "content": text,
                        "created_at": m.created_at,
                    });
                    if let Some(tc) = m.tool_calls.clone() {
                        if !tc.is_null() {
                            obj.as_object_mut()
                                .unwrap()
                                .insert("tool_calls".into(), tc);
                        }
                    }
                    if let Some(meta) = m.metadata.clone() {
                        if let Some(name) = meta.get("name").and_then(|n| n.as_str()) {
                            obj.as_object_mut()
                                .unwrap()
                                .insert("name".into(), json!(name));
                        }
                    }
                    obj
                })
                .collect();
            normalize_ui_messages(&raw, "远端 Agent")
                .into_iter()
                .map(|m| m.to_value())
                .collect()
        }
        Err(_) => vec![],
    }
}

/// Evidence that a *new* turn started on this socket (ignore pre-chat idle/sync).
fn is_remote_turn_started(v: &Value) -> bool {
    let ty = v.get("type").and_then(|x| x.as_str()).unwrap_or("");
    if ty == "user_message_ack" || ty == "stream_delta" {
        return true;
    }
    if ty == "run_event" {
        let topic = v.get("topic").and_then(|x| x.as_str()).unwrap_or("");
        return topic.ends_with("created") || topic.contains("status_changed");
    }
    if ty == "status" {
        let state = v.get("state").and_then(|x| x.as_str()).unwrap_or("");
        return state == "thinking" || state == "running" || state == "tool";
    }
    false
}

fn emit_chat_done(st: &AppState, session_id: &str, reason: &str) {
    st.broadcast_event_for_session(
        Some(session_id),
        &json!({
            "type": "done",
            "session_id": session_id,
            "reason": reason,
        }),
    );
}

fn emit_chat_done_with_text(st: &AppState, session_id: &str, reason: &str, text: &str) {
    st.broadcast_event_for_session(
        Some(session_id),
        &json!({
            "type": "done",
            "session_id": session_id,
            "reason": reason,
            "content": text,
            "replace": true,
        }),
    );
}

/// Map PC agent events into mobile-friendly envelopes (Codex-like live status/tools).
/// Raw event is always forwarded; extras are additive.
fn mobile_live_overlays(v: &Value) -> Vec<Value> {
    let mut extra = Vec::new();
    let ty = v.get("type").and_then(|x| x.as_str()).unwrap_or("");
    match ty {
        "status" => {
            let state = v.get("state").and_then(|x| x.as_str()).unwrap_or("");
            let detail = v
                .get("detail")
                .and_then(|x| x.as_str())
                .unwrap_or("");
            // Prefer human detail; fall back to readable state labels.
            let label = if !detail.is_empty() {
                detail.to_string()
            } else {
                match state {
                    "thinking" => "思考中…".into(),
                    "tool" | "tool_executing" => "执行工具…".into(),
                    "running" => "运行中…".into(),
                    "idle" => "空闲".into(),
                    "error" => "出错".into(),
                    s if !s.is_empty() => s.to_string(),
                    _ => String::new(),
                }
            };
            if !label.is_empty() {
                extra.push(json!({
                    "type": "mobile_status",
                    "detail": label,
                    "state": state,
                }));
            }
        }
        "tool_event" | "tool" | "tool_call" | "tool_result" => {
            let phase = v
                .get("phase")
                .and_then(|x| x.as_str())
                .unwrap_or(if ty == "tool_result" { "end" } else { "start" });
            let name = v
                .get("name")
                .or_else(|| v.pointer("/function/name"))
                .or_else(|| v.pointer("/tool/name"))
                .and_then(|x| x.as_str())
                .unwrap_or("tool");
            let status = v
                .get("status")
                .and_then(|x| x.as_str())
                .unwrap_or(if phase == "start" || phase == "running" {
                    "running"
                } else {
                    "completed"
                });
            let ok = status != "failed" && status != "error";
            let preview = v
                .get("result")
                .or_else(|| v.get("output"))
                .or_else(|| v.get("content"))
                .map(|r| match r {
                    Value::String(s) => s.chars().take(160).collect::<String>(),
                    other => {
                        let s = other.to_string();
                        s.chars().take(160).collect()
                    }
                })
                .or_else(|| {
                    v.get("arguments").or_else(|| v.get("args")).map(|a| {
                        let s = a.to_string();
                        s.chars().take(100).collect()
                    })
                })
                .unwrap_or_default();
            let endish = phase == "end"
                || phase == "completed"
                || phase == "result"
                || ty == "tool_result"
                || status == "completed"
                || status == "failed"
                || status == "error";
            extra.push(json!({
                "type": "mobile_tool",
                "phase": if endish { "end" } else { "start" },
                "name": name,
                "ok": ok,
                "preview": preview,
            }));
            // Also bump island status so tools are never silent.
            extra.push(json!({
                "type": "mobile_status",
                "detail": if endish {
                    format!("工具 · {name} {}", if ok { "✓" } else { "✗" })
                } else {
                    format!("工具 · {name} …")
                },
                "state": if endish { "thinking" } else { "tool_executing" },
            }));
        }
        "run_event" => {
            let topic = v.get("topic").and_then(|x| x.as_str()).unwrap_or("");
            let detail = if topic.ends_with("created") {
                "任务已创建…"
            } else if topic.contains("status") {
                "状态更新…"
            } else if topic.ends_with("completed") {
                "任务完成"
            } else if topic.ends_with("failed") {
                "任务失败"
            } else if !topic.is_empty() {
                topic
            } else {
                ""
            };
            if !detail.is_empty() {
                extra.push(json!({
                    "type": "mobile_status",
                    "detail": detail,
                    "state": "running",
                }));
            }
        }
        _ => {}
    }
    extra
}

fn absolutize_media_url(base_url: &str, rel_or_abs: &str) -> String {
    let u = rel_or_abs.trim();
    if u.starts_with("http://") || u.starts_with("https://") {
        return u.to_string();
    }
    let base = base_url.trim().trim_end_matches('/');
    if u.starts_with('/') {
        format!("{base}{u}")
    } else {
        format!("{base}/{u}")
    }
}

/// Rewrite relative `/uploads/...` (and similar) markdown links so phone can open/download
/// through the VPS tunnel the same way Codex surfaces file links.
fn absolutize_content_media_links(base_url: &str, content: &str) -> String {
    if base_url.is_empty() || content.is_empty() {
        return content.to_string();
    }
    let mut out = content.to_string();
    // Markdown href: ](/uploads/...) → ](https://tunnel.../uploads/...)
    for prefix in ["/uploads/", "/api/uploads/", "/files/", "/api/files/", "/media/"] {
        let needle = format!("]({prefix}");
        if !out.contains(&needle) {
            continue;
        }
        let abs_prefix = absolutize_media_url(base_url, prefix);
        out = out.replace(&needle, &format!("]({abs_prefix}"));
    }
    out
}

fn message_text(content: &Value) -> String {
    match content {
        Value::String(s) => s.clone(),
        Value::Array(arr) => arr
            .iter()
            .filter_map(|p| {
                p.get("text")
                    .and_then(|t| t.as_str())
                    .or_else(|| p.as_str())
            })
            .collect::<Vec<_>>()
            .join(""),
        other => other
            .get("text")
            .and_then(|t| t.as_str())
            .unwrap_or("")
            .to_string(),
    }
}

/// Pick the **final** assistant text for a user turn.
/// Skips intermediate assistants that only fire tool_calls, and waits while
/// the last message is still `tool` (tool loop in progress).
fn final_assistant_after_user(
    msgs: &[takton_mobile_core::models::MessageInfo],
    user_idx: usize,
) -> Option<String> {
    let after: Vec<&takton_mobile_core::models::MessageInfo> =
        msgs.iter().skip(user_idx + 1).collect();
    if after.is_empty() {
        return None;
    }
    let last = *after.last()?;
    // Still waiting on tool results → not done.
    if last.role == "tool" || last.role == "function" {
        return None;
    }
    // Intermediate assistant that invoked tools → wait for final answer.
    if last.role == "assistant" && last.is_tool_invocation() {
        return None;
    }
    if last.role != "assistant" {
        return None;
    }
    let text = message_text(&last.content);
    if text.trim().is_empty() {
        return None;
    }
    // Prefer last final assistant; if earlier tool-invocation assistants exist,
    // still return this last final one (correct for multi-step tool turns).
    Some(text)
}

/// Emit Codex-like tool/status frames from HTTP history when WS tool_events were lost.
fn emit_http_tool_progress(
    st: &AppState,
    session_id: &str,
    msgs: &[takton_mobile_core::models::MessageInfo],
    user_idx: usize,
    seen_tools: &mut std::collections::HashSet<String>,
) {
    for m in msgs.iter().skip(user_idx + 1) {
        if m.role == "assistant" && m.is_tool_invocation() {
            if let Some(Value::Array(arr)) = &m.tool_calls {
                for tc in arr {
                    let id = tc
                        .get("id")
                        .and_then(|x| x.as_str())
                        .unwrap_or("")
                        .to_string();
                    let name = tc
                        .pointer("/function/name")
                        .or_else(|| tc.get("name"))
                        .and_then(|n| n.as_str())
                        .unwrap_or("tool");
                    let key = if id.is_empty() {
                        format!("start:{name}")
                    } else {
                        format!("start:{id}")
                    };
                    if seen_tools.insert(key) {
                        st.broadcast_event_for_session(
                            Some(session_id),
                            &json!({
                                "type": "mobile_tool",
                                "phase": "start",
                                "name": name,
                                "ok": true,
                                "preview": "",
                                "source": "http_progress",
                            }),
                        );
                        st.broadcast_event_for_session(
                            Some(session_id),
                            &json!({
                                "type": "mobile_status",
                                "detail": format!("工具 · {name} …"),
                                "state": "tool_executing",
                                "source": "http_progress",
                            }),
                        );
                    }
                }
            }
        } else if m.role == "tool" || m.role == "function" {
            let name = m
                .metadata
                .as_ref()
                .and_then(|meta| meta.get("name").and_then(|n| n.as_str()))
                .unwrap_or("tool");
            let preview: String = message_text(&m.content).chars().take(120).collect();
            let key = format!("end:{}:{}", m.id, name);
            if seen_tools.insert(key) {
                st.broadcast_event_for_session(
                    Some(session_id),
                    &json!({
                        "type": "mobile_tool",
                        "phase": "end",
                        "name": name,
                        "ok": true,
                        "preview": preview,
                        "source": "http_progress",
                    }),
                );
                st.broadcast_event_for_session(
                    Some(session_id),
                    &json!({
                        "type": "mobile_status",
                        "detail": format!("工具 · {name} ✓"),
                        "state": "thinking",
                        "source": "http_progress",
                    }),
                );
            }
        }
    }
}

/// Independent of PC event WS: poll HTTP messages until the **final** assistant
/// (after any tool loop) is stable, then push full text + done to Flutter.
fn spawn_turn_completion_watchdog(st: AppState, session_id: String, user_content: String) {
    tokio::spawn(async move {
        let needle = user_content.trim();
        if needle.is_empty() {
            return;
        }
        let needle_prefix: String = needle.chars().take(48).collect();
        let mut last_push = String::new();
        let mut stable: u8 = 0;
        let mut seen_tools: std::collections::HashSet<String> =
            std::collections::HashSet::new();
        let mut last_status = String::new();
        // ~5 minutes @ 1.2s — faster than before so finals appear without restart.
        for tick in 0..250u32 {
            tokio::time::sleep(std::time::Duration::from_millis(1200)).await;
            let msgs = match st.client.list_messages(&session_id, 80).await {
                Ok(m) => m,
                Err(_) => continue,
            };
            let mut user_idx: Option<usize> = None;
            for (i, m) in msgs.iter().enumerate().rev() {
                if m.role != "user" {
                    continue;
                }
                let t = message_text(&m.content);
                let tt = t.trim();
                if tt == needle
                    || tt.ends_with(needle)
                    || needle.ends_with(tt)
                    || (!needle_prefix.is_empty() && tt.contains(&needle_prefix))
                {
                    user_idx = Some(i);
                    break;
                }
            }
            let Some(ui) = user_idx else {
                continue;
            };

            // Always surface tool progress from HTTP (Codex-like even if WS lost).
            emit_http_tool_progress(&st, &session_id, &msgs, ui, &mut seen_tools);

            let Some(text) = final_assistant_after_user(&msgs, ui) else {
                // Tool loop / intermediate — keep waiting; pulse status occasionally.
                stable = 0;
                if tick % 3 == 0 {
                    let detail = "远端处理中…".to_string();
                    if detail != last_status {
                        last_status = detail.clone();
                        st.broadcast_event_for_session(
                            Some(&session_id),
                            &json!({
                                "type": "mobile_status",
                                "detail": detail,
                                "state": "running",
                                "source": "http_progress",
                            }),
                        );
                    }
                }
                continue;
            };
            // Push full text (replace) so lossy delta streams still show PC answer.
            if text != last_push {
                last_push = text.clone();
                stable = 0;
                st.flush_session_deltas(&session_id);
                st.broadcast_event_for_session(
                    Some(&session_id),
                    &json!({
                        "type": "stream_delta",
                        "content": text,
                        "delta": text,
                        "replace": true,
                        "session_id": session_id,
                        "source": "http_watchdog",
                    }),
                );
            } else {
                stable = stable.saturating_add(1);
            }
            // Same final assistant on 2 consecutive polls (~2.4s) → turn finished.
            // Need stable>=1 so a single snapshot right as tools end doesn't cut off.
            if stable >= 1 && !last_push.is_empty() {
                st.flush_session_deltas(&session_id);
                emit_chat_done_with_text(&st, &session_id, "http_watchdog", &last_push);
                tracing::info!(%session_id, len = last_push.len(), "turn watchdog completed via HTTP");
                return;
            }
        }
        // Always unblock UI eventually (include best text if any).
        if !last_push.is_empty() {
            emit_chat_done_with_text(&st, &session_id, "watchdog_timeout", &last_push);
        } else {
            emit_chat_done(&st, &session_id, "watchdog_timeout");
        }
        tracing::warn!(%session_id, "turn watchdog timeout");
    });
}

/// Prefer the boss-facing CEO/steward DM session over empty "helpful assistant"
/// shells created by pair probes / VPS tests.
fn pick_preferred_remote_session(list: &[takton_mobile_core::models::SessionInfo]) -> Option<String> {
    if list.is_empty() {
        return None;
    }
    let score = |s: &takton_mobile_core::models::SessionInfo| -> i32 {
        let cfg = &s.config;
        let contact = cfg
            .get("contact_agent")
            .and_then(|x| x.as_str())
            .unwrap_or("")
            .to_ascii_lowercase();
        let identity = cfg
            .get("identity")
            .and_then(|x| x.as_str())
            .unwrap_or("")
            .to_ascii_lowercase();
        let source = cfg
            .get("source")
            .and_then(|x| x.as_str())
            .unwrap_or("")
            .to_ascii_lowercase();
        let title = s.display_title().to_ascii_lowercase();
        let blob = format!("{contact} {identity} {title}");

        let mut sc = 0i32;
        // Primary product path: human DM with company steward / CEO
        if source == "human_dm" {
            sc += 50;
        }
        if blob.contains("steward")
            || blob.contains("ceo")
            || blob.contains("大管家")
            || contact.contains("小总")
            || title.contains("小总")
        {
            sc += 100;
        }
        // Named contact better than blank test shells
        if !contact.is_empty() {
            sc += 20;
        }
        // Workforce employee sessions are not the phone default
        if source == "workforce" {
            sc -= 40;
        }
        // Generic empty assistant (pair/VPS test debris)
        if identity.contains("helpful assistant") && contact.is_empty() {
            sc -= 80;
        }
        if identity.is_empty() && contact.is_empty() {
            sc -= 30;
        }
        sc
    };

    let mut ranked: Vec<_> = list.iter().collect();
    ranked.sort_by(|a, b| {
        score(b)
            .cmp(&score(a))
            .then_with(|| b.updated_at.cmp(&a.updated_at))
    });
    let best = ranked.first()?;
    // If everything is junk, still return the newest so UX isn't empty.
    Some(best.id.clone())
}

async fn ensure_chat(st: AppState, session_id: String) -> Result<Arc<ChatConnection>, String> {
    if session_id.is_empty() || session_id == LOCAL_SESSION_ID {
        return Err("invalid remote session".into());
    }
    // Drop dead cache: VPS WS can close after pair while UI still shows "已连接".
    // Reusing a dead channel yields "chat channel closed" on every send.
    if let Some(c) = st.chats.get(&session_id) {
        if c.is_alive() {
            return Ok(c.clone());
        }
        drop(c);
        st.chats.remove(&session_id);
        tracing::info!(%session_id, "dropped dead PC chat socket; reconnecting");
    }
    let (evt_tx, mut evt_rx) = mpsc::unbounded_channel::<ChatEvent>();
    let st_fan = st.clone();
    let sid_fan = session_id.clone();
    tokio::spawn(async move {
        // Only map idle→done after this socket has seen a real turn start.
        // Avoids pair/open sync "status:idle" ending a brand-new Flutter stream.
        let mut turn_active = false;
        let mut done_emitted = false;
        while let Some(ev) = evt_rx.recv().await {
            match ev {
                ChatEvent::Json(v) => {
                    if is_remote_turn_started(&v) {
                        turn_active = true;
                        done_emitted = false;
                    }
                    st_fan.broadcast_event_for_session(Some(&sid_fan), &v);
                    // Live tool/status overlays for Flutter (Codex-like).
                    for ov in mobile_live_overlays(&v) {
                        st_fan.broadcast_event_for_session(Some(&sid_fan), &ov);
                    }
                    // Do NOT finish on bare status=idle during tool loops — that
                    // caused "PC has final answer, phone stuck / only first msg".
                    // Only hard-finish on run.completed; HTTP watchdog covers the rest.
                    let ty = v.get("type").and_then(|x| x.as_str()).unwrap_or("");
                    let run_done = ty == "run_event"
                        && v.get("topic")
                            .and_then(|x| x.as_str())
                            .map(|t| {
                                t.ends_with("completed")
                                    || t.ends_with("failed")
                                    || t.ends_with("cancelled")
                            })
                            .unwrap_or(false);
                    if turn_active && !done_emitted && run_done {
                        done_emitted = true;
                        turn_active = false;
                        emit_chat_done(&st_fan, &sid_fan, "run_finished");
                    }
                }
                ChatEvent::Error(e) => {
                    st_fan.flush_session_deltas(&sid_fan);
                    st_fan.chats.remove(&sid_fan);
                    st_fan.broadcast_event_for_session(
                        Some(&sid_fan),
                        &json!({"type":"error","error": e, "session_id": sid_fan}),
                    );
                    // Unblock Flutter even if it only waits on done.
                    if !done_emitted {
                        done_emitted = true;
                        emit_chat_done(&st_fan, &sid_fan, "socket_error");
                    }
                    turn_active = false;
                }
                ChatEvent::Closed(r) => {
                    st_fan.flush_session_deltas(&sid_fan);
                    st_fan.chats.remove(&sid_fan);
                    st_fan.broadcast_event_for_session(
                        Some(&sid_fan),
                        &json!({"type":"closed","reason": r, "session_id": sid_fan}),
                    );
                    if !done_emitted {
                        done_emitted = true;
                        emit_chat_done(&st_fan, &sid_fan, "socket_closed");
                    }
                    turn_active = false;
                }
            }
        }
    });
    let conn = ChatConnection::connect(&st.client, &session_id, evt_tx)
        .await
        .map_err(|e| {
            format!(
                "PC WebSocket 连接失败（{e}）。请确认 VPS 隧道在线且 base_url 含 /t/{{id}}"
            )
        })?;
    st.chats.insert(session_id, conn.clone());
    Ok(conn)
}

// ── handlers ────────────────────────────────────────────────────────────────

async fn healthz(State(st): State<AppState>) -> Json<Value> {
    let authenticated = st.client.is_authenticated();
    let profile = st.local_llm.load_profile();
    let masked = profile.masked();
    Json(json!({
        "ok": true,
        "authenticated": authenticated,
        "base_url": base_url_of(&st),
        "local_llm": masked,
        "backend": if authenticated {
            json!({"service":"takton-backend","status":"ok"})
        } else {
            json!({"service":"takton-backend","status":"disconnected"})
        },
        "ts": chrono_now(),
    }))
}

async fn app_state(State(st): State<AppState>) -> Json<Value> {
    let authenticated = st.client.is_authenticated();
    let (sessions, local_session) = collect_session_views(&st).await;
    let active = st.active_session.read().clone();
    let profile = st.local_llm.load_profile();
    let mut active_model = String::new();
    if authenticated {
        let (m, _, _) = remote_probe(&st).await;
        active_model = m;
    }
    let approvals_pending = if authenticated {
        st.client
            .list_escalations(Some("pending"))
            .await
            .ok()
            .map(|v| normalize_list(&v).len())
            .unwrap_or(0)
    } else {
        0
    };
    Json(json!({
        "ok": true,
        "authenticated": authenticated,
        "base_url": base_url_of(&st),
        "user_email": user_email_of(&st),
        "active_session_id": active,
        "sessions": sessions,
        "local_session": local_session,
        "mode": if authenticated { "remote" } else { "local" },
        "local_llm": profile.masked(),
        "local_llm_ready": profile.is_ready(),
        "active_model": active_model,
        "approvals_pending": approvals_pending,
    }))
}

async fn connect(State(st): State<AppState>, Json(body): Json<ConnectBody>) -> Json<Value> {
    // Multi-endpoint: probe candidates (LAN first) then login.
    let mut extras = body.candidates.clone().unwrap_or_default();
    if let Some(b) = body.base_url.clone().filter(|s| !s.trim().is_empty()) {
        extras.insert(0, b);
    }
    let email = body.email.unwrap_or_default();
    let password = body.password.unwrap_or_default();
    match try_connect_best(&st, &extras, &email, &password).await {
        Ok(v) => Json(v),
        Err(e) => err_json(e),
    }
}

async fn disconnect(State(st): State<AppState>) -> Json<Value> {
    let _ = st.client.logout();
    *st.remote_probe.write() = Default::default();
    *st.active_session.write() = None;
    st.chats.clear();
    Json(json!({ "ok": true }))
}

async fn auto_login(State(st): State<AppState>) -> Json<Value> {
    match st.client.auto_login().await {
        Ok(s) => Json(json!({
            "ok": true,
            "authenticated": true,
            "user_email": s.user.email,
        })),
        Err(e) => err_json(e),
    }
}

async fn list_sessions(
    State(st): State<AppState>,
    Query(q): Query<SessionsQuery>,
) -> Json<Value> {
    let (mut sessions, local) = collect_session_views(&st).await;
    // Pin + updated_at sort already applied in collect_session_views.
    // Optional title/id filter — still server-side so Flutter only binds.
    if let Some(raw) = q.q.as_deref().map(str::trim).filter(|s| !s.is_empty()) {
        let ql = raw.to_lowercase();
        sessions.retain(|s| {
            let id = s.get("id").and_then(|v| v.as_str()).unwrap_or("").to_lowercase();
            let title = s
                .get("title")
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .to_lowercase();
            id.contains(&ql) || title.contains(&ql)
        });
    }
    Json(json!({
        "ok": true,
        "sessions": sessions,
        "local_session": local,
        "sorted": true,
        "sort": "pinned_desc,updated_at_desc",
    }))
}

async fn create_session(State(st): State<AppState>) -> Json<Value> {
    if !st.client.is_authenticated() {
        return err_json("not authenticated");
    }
    match st.client.create_session(None).await {
        Ok(s) => {
            let meta = st.load_meta();
            *st.active_session.write() = Some(s.id.clone());
            Json(json!({
                "ok": true,
                "session": session_view(&s, &meta),
            }))
        }
        Err(e) => err_json(e),
    }
}

async fn get_session(State(st): State<AppState>, Path(id): Path<String>) -> Json<Value> {
    if id == LOCAL_SESSION_ID {
        let meta = st.load_meta();
        return Json(json!({ "ok": true, "session": local_session_view(&meta) }));
    }
    if !st.client.is_authenticated() {
        return err_json("not authenticated");
    }
    match st.client.get_session(&id).await {
        Ok(s) => {
            let meta = st.load_meta();
            Json(json!({ "ok": true, "session": session_view(&s, &meta) }))
        }
        Err(e) => err_json(e),
    }
}

async fn list_messages(
    State(st): State<AppState>,
    Path(id): Path<String>,
    Query(q): Query<LimitQuery>,
) -> Json<Value> {
    let limit = q.limit.unwrap_or(100).min(500);
    if id == LOCAL_SESSION_ID {
        return Json(json!({ "ok": true, "messages": local_history_as_ui(&st) }));
    }
    if !st.client.is_authenticated() {
        return err_json("not authenticated");
    }
    let messages = remote_messages_as_ui(&st, &id, limit).await;
    Json(json!({ "ok": true, "messages": messages }))
}

#[derive(Debug, Deserialize)]
struct TurnStatusQuery {
    /// User message text of the current turn (matched from the end of history).
    #[serde(default)]
    user: Option<String>,
}

/// Tool-loop-aware turn probe for Flutter silence/reconcile polls.
/// `ready=true` only when a **final** assistant (no open tool_calls / trailing tool) exists.
async fn turn_status(
    State(st): State<AppState>,
    Path(id): Path<String>,
    Query(q): Query<TurnStatusQuery>,
) -> Json<Value> {
    if id.is_empty() || id == LOCAL_SESSION_ID {
        return Json(json!({ "ok": true, "ready": false, "text": "", "reason": "local" }));
    }
    if !st.client.is_authenticated() {
        return err_json("not authenticated");
    }
    let needle = q.user.unwrap_or_default();
    let needle_trim = needle.trim().to_string();
    let msgs = match st.client.list_messages(&id, 80).await {
        Ok(m) => m,
        Err(e) => {
            return Json(json!({
                "ok": false,
                "ready": false,
                "text": "",
                "error": e.to_string(),
            }));
        }
    };
    if msgs.is_empty() {
        return Json(json!({ "ok": true, "ready": false, "text": "", "reason": "empty" }));
    }

    let mut user_idx: Option<usize> = None;
    if !needle_trim.is_empty() {
        let prefix: String = needle_trim.chars().take(48).collect();
        for (i, m) in msgs.iter().enumerate().rev() {
            if m.role != "user" {
                continue;
            }
            let t = message_text(&m.content);
            let tt = t.trim();
            if tt == needle_trim
                || tt.ends_with(&needle_trim)
                || needle_trim.ends_with(tt)
                || (!prefix.is_empty() && tt.contains(&prefix))
            {
                user_idx = Some(i);
                break;
            }
        }
    } else {
        // No needle: use last user message.
        for (i, m) in msgs.iter().enumerate().rev() {
            if m.role == "user" {
                user_idx = Some(i);
                break;
            }
        }
    }
    let Some(ui) = user_idx else {
        return Json(json!({
            "ok": true,
            "ready": false,
            "text": "",
            "reason": "user_not_found",
        }));
    };

    // Count open tools after user for live UI.
    let mut tool_starts = 0u32;
    let mut tool_ends = 0u32;
    for m in msgs.iter().skip(ui + 1) {
        if m.is_tool_invocation() {
            tool_starts = tool_starts.saturating_add(1);
        } else if m.role == "tool" || m.role == "function" {
            tool_ends = tool_ends.saturating_add(1);
        }
    }

    match final_assistant_after_user(&msgs, ui) {
        Some(text) => Json(json!({
            "ok": true,
            "ready": true,
            "text": text,
            "tools_started": tool_starts,
            "tools_finished": tool_ends,
        })),
        None => Json(json!({
            "ok": true,
            "ready": false,
            "text": "",
            "reason": "tool_loop_or_pending",
            "tools_started": tool_starts,
            "tools_finished": tool_ends,
        })),
    }
}

async fn open_session(State(st): State<AppState>, Path(id): Path<String>) -> Json<Value> {
    *st.active_session.write() = Some(id.clone());
    if id != LOCAL_SESSION_ID && st.client.is_authenticated() {
        let _ = ensure_chat(st.clone(), id.clone()).await;
    }
    Json(json!({ "ok": true, "session_id": id }))
}

/// Cancel in-flight remote generation for a session (HTTP path for Flutter abort).
async fn stop_session(State(st): State<AppState>, Path(id): Path<String>) -> Json<Value> {
    if id.is_empty() || id == LOCAL_SESSION_ID {
        st.local_llm.request_stop();
        return Json(json!({ "ok": true, "id": id, "path": "local" }));
    }
    st.flush_session_deltas(&id);
    if let Some(c) = st.chats.get(&id) {
        match c.stop() {
            Ok(()) => Json(json!({ "ok": true, "id": id, "path": "remote" })),
            Err(e) => Json(json!({ "ok": false, "id": id, "error": e.to_string() })),
        }
    } else {
        // No live chat socket — still ok (idempotent stop).
        Json(json!({ "ok": true, "id": id, "path": "noop" }))
    }
}

async fn pin_session(
    State(st): State<AppState>,
    Path(id): Path<String>,
    Json(body): Json<PinBody>,
) -> Json<Value> {
    let mut meta = st.load_meta();
    meta.set_pinned(&id, body.pinned);
    st.save_meta(&meta);
    Json(json!({ "ok": true, "id": id, "pinned": body.pinned }))
}

async fn rename_session(
    State(st): State<AppState>,
    Path(id): Path<String>,
    Json(body): Json<RenameBody>,
) -> Json<Value> {
    let title = body.title.trim().to_string();
    if title.is_empty() {
        return err_json("empty title");
    }
    let mut meta = st.load_meta();
    meta.set_title(&id, &title);
    st.save_meta(&meta);
    if id != LOCAL_SESSION_ID && st.client.is_authenticated() {
        let _ = st.client.patch_session_title(&id, &title).await;
    }
    Json(json!({ "ok": true, "id": id, "title": title, "note": "已改名" }))
}

async fn delete_session(State(st): State<AppState>, Path(id): Path<String>) -> Json<Value> {
    if id == LOCAL_SESSION_ID {
        return match st.local_llm.clear_history() {
            Ok(()) => Json(json!({ "ok": true, "id": id })),
            Err(e) => err_json(e),
        };
    }
    if !st.client.is_authenticated() {
        return err_json("not authenticated");
    }
    match st.client.delete_session(&id, true).await {
        Ok(()) => {
            if st.active_session.read().as_deref() == Some(id.as_str()) {
                *st.active_session.write() = None;
            }
            st.chats.remove(&id);
            let mut meta = st.load_meta();
            meta.remove(&id);
            st.save_meta(&meta);
            Json(json!({ "ok": true, "id": id }))
        }
        Err(e) => err_json(e),
    }
}

async fn get_session_meta(State(st): State<AppState>) -> Json<Value> {
    let meta = st.load_meta();
    Json(json!({ "ok": true, "meta": meta }))
}

async fn list_approvals(State(st): State<AppState>) -> Json<Value> {
    // Backward-compatible thin wrapper over summary (no processes).
    let Json(v) = approvals_summary(State(st)).await;
    let mut out = v;
    if let Some(obj) = out.as_object_mut() {
        obj.remove("processes");
    }
    Json(out)
}

/// Single-shot approvals + processes + badge count for mobile shell.
async fn approvals_summary(State(st): State<AppState>) -> Json<Value> {
    if !st.client.is_authenticated() {
        return Json(json!({
            "ok": true,
            "escalations": [],
            "evolution": [],
            "processes": [],
            "pending": 0,
            "badge": 0,
        }));
    }

    let esc_f = st.client.list_escalations(Some("pending"));
    let evo_f = st.client.list_evolution_proposals();
    let proc_f = st.client.list_processes(false);
    let (esc_r, evo_r, proc_r) = tokio::join!(esc_f, evo_f, proc_f);

    let escalations = esc_r
        .map(|v| Value::Array(normalize_list(&v)))
        .unwrap_or_else(|_| json!([]));
    let evolution = evo_r
        .map(|v| Value::Array(normalize_list(&v)))
        .unwrap_or_else(|_| json!([]));
    let processes = proc_r
        .map(|v| Value::Array(normalize_list(&v)))
        .unwrap_or_else(|_| json!([]));

    let esc_n = escalations.as_array().map(|a| a.len()).unwrap_or(0);
    let evo_n = evolution.as_array().map(|a| a.len()).unwrap_or(0);
    let pending = esc_n + evo_n;
    Json(json!({
        "ok": true,
        "escalations": escalations,
        "evolution": evolution,
        "processes": processes,
        "pending": pending,
        "badge": pending,
        "escalations_count": esc_n,
        "evolution_count": evo_n,
    }))
}

async fn decide_approval(
    State(st): State<AppState>,
    Path(id): Path<String>,
    Json(body): Json<DecideBody>,
) -> Json<Value> {
    if !st.client.is_authenticated() {
        return err_json("not authenticated");
    }
    let kind = body.kind.as_deref().unwrap_or("escalation");
    let res = match kind {
        "evolution" => {
            if body.approved {
                st.client.approve_evolution(&id).await
            } else {
                st.client.reject_evolution(&id).await
            }
        }
        "confirm" => {
            let scope = body.scope.as_deref().unwrap_or("once");
            st.client.resolve_confirm(&id, body.approved, scope).await
        }
        _ => {
            if body.approved {
                st.client.approve_escalation(&id).await
            } else {
                st.client.deny_escalation(&id).await
            }
        }
    };
    match res {
        Ok(v) => Json(merge_ok(v)),
        Err(e) => err_json(e),
    }
}

async fn list_devices(State(st): State<AppState>) -> Json<Value> {
    if !st.client.is_authenticated() {
        return Json(json!({ "ok": true, "devices": [] }));
    }
    match st.client.list_devices().await {
        Ok(list) => {
            let devices: Vec<Value> = list
                .into_iter()
                .filter_map(|d| serde_json::to_value(d).ok())
                .collect();
            Json(json!({ "ok": true, "devices": devices }))
        }
        Err(e) => err_json(e),
    }
}

async fn pair_device(State(st): State<AppState>, Json(body): Json<PairBody>) -> Json<Value> {
    match st
        .client
        .pair_device(&body.name, &body.host, body.port, &body.token)
        .await
    {
        Ok(d) => Json(json!({ "ok": true, "device": d })),
        Err(e) => err_json(e),
    }
}

// ── path helpers ─────────────────────────────────────────────────────────────

/// Login to the first working base URL.
/// `preferred` is tried first (e.g. the URL that just won claim over VPS).
async fn try_connect_best(
    st: &AppState,
    candidates: &[String],
    email: &str,
    password: &str,
) -> Result<Value, String> {
    try_connect_best_pref(st, candidates, email, password, &[], None).await
}

async fn try_connect_best_pref(
    st: &AppState,
    candidates: &[String],
    email: &str,
    password: &str,
    preferred: &[String],
    device_token: Option<&str>,
) -> Result<Value, String> {
    use takton_mobile_core::path::{select_best, Endpoint, EndpointKind};

    let mut extra: Vec<String> = Vec::new();
    for p in preferred {
        let u = p.trim().trim_end_matches('/').to_string();
        if !u.is_empty() && !extra.iter().any(|x| x == &u) {
            extra.push(u);
        }
    }
    for c in candidates {
        let u = c.trim().trim_end_matches('/').to_string();
        if !u.is_empty() && !extra.iter().any(|x| x == &u) {
            extra.push(u);
        }
    }
    for ep in st.path.candidate_urls(&extra) {
        let u = ep.url.trim_end_matches('/').to_string();
        if !extra.iter().any(|x| x == &u) {
            extra.push(u);
        }
    }
    if extra.is_empty() {
        extra.push(st.client.config().base_url);
    }

    // Drop known-unreachable docker/link-local bases early (phone cannot use them).
    extra.retain(|u| !is_phone_unusable_base(u));

    // Prefer VPS-shaped bases after explicit preferred (claim winner).
    extra.sort_by_key(|u| {
        if preferred.iter().any(|p| p.trim_end_matches('/') == u.as_str()) {
            0u8
        } else if u.contains("/t/") {
            1
        } else {
            2
        }
    });

    // When we already know the claim winner (preferred), skip full probe mesh —
    // dead LAN hosts cost ~1.2s each and make scan feel frozen.
    let (best, probes) = if !preferred.is_empty() {
        (None, vec![])
    } else {
        let endpoints: Vec<Endpoint> = extra
            .iter()
            .filter_map(|u| Endpoint::from_url(u, EndpointKind::Unknown))
            .collect();
        select_best(&endpoints).await
    };

    let mut try_urls: Vec<String> = preferred
        .iter()
        .map(|s| s.trim().trim_end_matches('/').to_string())
        .filter(|s| !s.is_empty() && !is_phone_unusable_base(s))
        .collect();
    if let Some(b) = best {
        if !try_urls.iter().any(|u| u == &b.url) {
            try_urls.push(b.url.clone());
        }
    }
    for u in &extra {
        if !try_urls.iter().any(|x| x == u) {
            try_urls.push(u.clone());
        }
    }
    // Hard cap attempts so pair never freezes the UI for a minute.
    try_urls.truncate(4);

    let token = device_token
        .map(|s| s.trim())
        .filter(|s| !s.is_empty())
        .map(|s| s.to_string());

    let mut last_err = "no reachable endpoint".to_string();
    for url in try_urls {
        st.client.set_base_url(url.clone());
        // Auth order for phone:
        // 1) pair device token → JWT  (works LAN + VPS; no loopback required)
        // 2) email/password if user typed them
        // 3) auto-login last (loopback-only on PC; only works via tunnel spoof)
        let login_fut = async {
            if let Some(ref t) = token {
                match st.client.pair_session_login(t).await {
                    Ok(s) => return Ok(s),
                    Err(e) => {
                        // Fall through — token may be for a different PC path
                        tracing::debug!("pair_session fail on {url}: {e}");
                    }
                }
            }
            if !email.is_empty() {
                st.client.login(email, password).await
            } else {
                st.client.auto_login().await
            }
        };
        let login = match tokio::time::timeout(std::time::Duration::from_secs(6), login_fut).await {
            Ok(r) => r,
            Err(_) => {
                last_err = format!("login timeout · {url}");
                continue;
            }
        };
        match login {
            Ok(s) => {
                *st.remote_probe.write() = Default::default();
                let kind = path_kind_for_url(&url);
                let _ = st.path.set_active(&url, kind);
                let mesh = st.mesh.status();
                let port = takton_mobile_core::config::parse_base_url_parts(&url).2;
                let _ = st.path.refresh_candidates(
                    mesh.lan_ip.as_deref(),
                    mesh.tailscale_ip.as_deref(),
                    Some(st.mesh.config().hostname.as_str()),
                    port,
                    "http",
                );
                return Ok(json!({
                    "ok": true,
                    "authenticated": true,
                    "base_url": url,
                    "user_email": s.user.email,
                    "path_kind": kind.as_str(),
                    "auth_via": if token.is_some() { "pair_session" } else if email.is_empty() { "auto_login" } else { "password" },
                    "probes": probes,
                    "path": st.path.profile_json(),
                }));
            }
            Err(e) => {
                last_err = e.to_string();
            }
        }
    }
    Err(last_err)
}

fn is_phone_unusable_base(url: &str) -> bool {
    let host = takton_mobile_core::config::parse_base_url_parts(url).1;
    if host == "127.0.0.1" || host == "localhost" || host == "::1" {
        return true;
    }
    // Docker / compose bridges commonly seen on Windows dev PCs
    if let Ok(ip) = host.parse::<std::net::Ipv4Addr>() {
        let o = ip.octets();
        if o[0] == 172 && (17..=20).contains(&o[1]) {
            return true;
        }
        if o[0] == 169 && o[1] == 254 {
            return true;
        }
    }
    false
}

fn path_kind_for_url(url: &str) -> takton_mobile_core::path::EndpointKind {
    use takton_mobile_core::path::{classify_host, EndpointKind};
    let rest = url
        .trim()
        .trim_start_matches("https://")
        .trim_start_matches("http://");
    if rest.contains("/t/") {
        return EndpointKind::Vps;
    }
    let host = takton_mobile_core::config::parse_base_url_parts(url).1;
    classify_host(&host)
}

fn base_from_claim_url(claim_url: &str) -> String {
    // http://host/t/id/api/mobile/pair/claim → http://host/t/id
    let u = claim_url.trim().trim_end_matches('/');
    if let Some(idx) = u.find("/api/mobile/pair/claim") {
        return u[..idx].trim_end_matches('/').to_string();
    }
    if let Some(idx) = u.find("/api/") {
        return u[..idx].trim_end_matches('/').to_string();
    }
    u.to_string()
}

async fn try_deferred_claim(st: &AppState) -> Option<Value> {
    use takton_mobile_core::path::claim_urls;
    use takton_mobile_core::pair::PairPayload;

    let profile = st.path.profile();
    let d = profile.deferred_claim?;
    if d.exp > 0 {
        let now = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|d| d.as_secs() as i64)
            .unwrap_or(0);
        if now > d.exp {
            let _ = st.path.clear_deferred();
            return None;
        }
    }
    // Rebuild minimal payload for claim URL generation
    let payload = if !d.qr.is_empty() {
        PairPayload::parse_uri(&d.qr).ok()?
    } else {
        return None;
    };
    let shell_port = st.config.read().host_port;
    let urls = claim_urls(&payload, shell_port);
    let client = reqwest::Client::builder()
        .timeout(std::time::Duration::from_secs(8))
        .build()
        .ok()?;
    for claim_url in urls {
        if let Ok(res) = client
            .post(&claim_url)
            .json(&json!({
                "pair_id": d.pair_id,
                "code": d.code,
                "device_name": d.device_name,
            }))
            .send()
            .await
        {
            if res.status().is_success() {
                if let Ok(v) = res.json::<Value>().await {
                    if v.get("ok") == Some(&json!(true)) {
                        let token = v
                            .get("token")
                            .or_else(|| v.pointer("/device/token"))
                            .and_then(|x| x.as_str())
                            .map(|s| s.to_string());
                        let _ = st.path.merge_from_payload(&payload, token, None);
                        let _ = st.path.clear_deferred();
                        return Some(v);
                    }
                }
            }
        }
    }
    None
}

// ── M1 pairing ──────────────────────────────────────────────────────────────

async fn pair_start(State(st): State<AppState>, Json(body): Json<PairStartBody>) -> Json<Value> {
    use takton_mobile_core::config::parse_base_url_parts;
    use takton_mobile_core::mesh::parse_mode;
    use takton_mobile_core::pair::MeshMode;

    // Product default: auto dual-path, silent mesh bring-up for one-scan pairing.
    let (_h0, _p0, _s0, mesh_default, _hn0) = st.mesh.pair_endpoint();
    let mesh = body
        .mesh
        .as_deref()
        .and_then(|m| parse_mode(m).ok())
        .unwrap_or(if mesh_default == MeshMode::Off {
            MeshMode::Auto
        } else {
            mesh_default
        });

    let (pc_scheme, pc_host, pc_port) = parse_base_url_parts(&st.client.config().base_url);
    st.mesh.set_backend_port(pc_port);

    // Spawn PC tsnet if key present; collect dual paths + phone join key (tsk).
    let (lan_ip, ts_ip, mesh_hn, tsk) = st.mesh.prepare_for_pairing(mesh);
    let (adv_host, _mesh_port, _scheme0, _, hostname) = st.mesh.pair_endpoint();

    let host = body
        .host
        .clone()
        .filter(|h| !h.trim().is_empty())
        .unwrap_or_else(|| {
            if adv_host != "127.0.0.1" && adv_host != "localhost" {
                adv_host
            } else if let Some(ref l) = lan_ip {
                l.clone()
            } else {
                pc_host
            }
        });
    let port = body.port.unwrap_or(pc_port);
    let scheme = pc_scheme;
    let name = body
        .name
        .clone()
        .filter(|n| !n.trim().is_empty())
        .or(Some(hostname.clone()));
    let require = body
        .require_confirm
        .unwrap_or_else(|| st.mesh.config().require_pair_confirm);

    let lan = lan_ip.clone();
    let ts = ts_ip.clone();
    let hn = Some(mesh_hn).filter(|s| !s.is_empty()).or(name.clone());

    let (_pending, payload) = st.pair.start(
        &host,
        port,
        &scheme,
        mesh,
        name,
        require,
        lan,
        ts,
        hn,
        tsk.clone(),
    );
    // Redact tsk in JSON payload echo (QR string still needs it for the phone).
    let mut payload_public = serde_json::to_value(&payload).unwrap_or(json!({}));
    if let Some(obj) = payload_public.as_object_mut() {
        if obj.get("tsk").and_then(|v| v.as_str()).map(|s| !s.is_empty()).unwrap_or(false) {
            obj.insert("tsk".into(), json!("***"));
        }
    }
    let seamless = tsk.as_ref().map(|s| !s.is_empty()).unwrap_or(false);
    let hint = if seamless {
        "用手机扫码即可连接 · 局域网与外出自动切换"
    } else if ts_ip.is_some() {
        "用手机扫码即可连接"
    } else {
        "用手机扫码连接（当前局域网）。外出使用请在本机启用一次远程。"
    };
    Json(json!({
        "ok": true,
        "pair_id": payload.pair_id,
        "code": payload.code,
        "exp": payload.exp,
        "ttl_secs": takton_mobile_core::pair::PAIR_TTL_SECS,
        "qr": payload.to_uri(),
        "payload": payload_public,
        "require_confirm": require,
        "mesh": mesh.as_str(),
        "base_url": payload.base_url(),
        "endpoints": payload.endpoints().iter().map(|e| json!({
            "url": e.url, "kind": e.kind.as_str()
        })).collect::<Vec<_>>(),
        "lan": lan_ip,
        "ts": ts_ip,
        "seamless": seamless,
        "mesh_status": st.mesh.status_json(),
        "hint": hint,
    }))
}

async fn pair_status(State(st): State<AppState>, Path(id): Path<String>) -> Json<Value> {
    match st.pair.status(&id) {
        Some(v) => Json(json!({ "ok": true, "status": v })),
        None => err_json("pair session not found"),
    }
}

async fn pair_confirm(State(st): State<AppState>, Path(id): Path<String>) -> Json<Value> {
    match st.pair.confirm(&id) {
        Ok(()) => Json(json!({ "ok": true, "pair_id": id, "confirmed": true })),
        Err(e) => err_json(e),
    }
}

async fn pair_cancel(State(st): State<AppState>, Path(id): Path<String>) -> Json<Value> {
    st.pair.cancel(&id);
    Json(json!({ "ok": true, "pair_id": id, "cancelled": true }))
}

async fn pair_claim(State(st): State<AppState>, Json(body): Json<PairClaimBody>) -> Json<Value> {
    let name = body.device_name.unwrap_or_else(|| "Phone".into());
    match st.pair.claim(&body.pair_id, &body.code, &name) {
        Ok((dev, pending)) => {
            let payload = takton_mobile_core::pair::PairPayload {
                v: 2,
                pair_id: pending.pair_id.clone(),
                code: pending.code.clone(),
                host: pending.host.clone(),
                port: pending.port,
                exp: pending.exp,
                mesh: pending.mesh,
                scheme: pending.scheme.clone(),
                name: pending.name.clone(),
                lan: pending.lan.clone(),
                ts: pending.ts.clone(),
                hn: pending.hn.clone(),
                tsk: None,
                vps: None,
                vp: None,
                vps_path: None,
                vpt: None,
                vps_scheme: None,
            };
            Json(json!({
                "ok": true,
                "device": dev,
                "base_url": format!("{}://{}:{}", pending.scheme, pending.host, pending.port),
                "mesh": pending.mesh.as_str(),
                "token": dev.token,
                "endpoints": payload.endpoints().iter().map(|e| e.url.clone()).collect::<Vec<_>>(),
            }))
        }
        Err(e) => err_json(e),
    }
}

/// Phone-side: parse QR → multi-host claim → soft-defer if offline → path select + login.
async fn pair_apply(State(st): State<AppState>, Json(body): Json<PairApplyBody>) -> Json<Value> {
    use takton_mobile_core::pair::PairPayload;
    use takton_mobile_core::path::{claim_urls, DeferredClaim};

    let payload = match PairPayload::parse_uri(&body.qr) {
        Ok(p) => p,
        Err(e) => return err_json(e),
    };
    // Soft-pair: allow storing past soft expiry for reconnect, but hard reject very old codes
    let soft_expired = payload.is_expired();

    let device_name = body
        .device_name
        .clone()
        .filter(|s| !s.trim().is_empty())
        .unwrap_or_else(|| {
            std::env::var("TAKTON_DEVICE_NAME").unwrap_or_else(|_| "Takton Phone".into())
        });

    // Seamless mesh: if QR carries phone join key, embed tsnet before claim (no UI).
    let mut mesh_join: Option<Value> = None;
    if let Some(ref tsk) = payload.tsk {
        if !tsk.is_empty() {
            let hn = payload
                .hn
                .clone()
                .map(|_| format!("takton-phone"));
            match st.mesh.phone_join_from_pair_key(tsk, hn.as_deref()) {
                Ok(v) => {
                    mesh_join = Some(v.clone());
                    // Brief wait so tsnet can assign 100.x before claim probes.
                    if v.get("joined") == Some(&json!(true))
                        || v.get("tailscale_ip").and_then(|x| x.as_str()).is_some()
                    {
                        tokio::time::sleep(std::time::Duration::from_millis(400)).await;
                    } else {
                        // Embed starting / binary missing: short wait still helps LAN claim order.
                        tokio::time::sleep(std::time::Duration::from_millis(150)).await;
                    }
                    // Refresh path with live TS IP if embed came up
                    if let Some(ip) = st.mesh.status().tailscale_ip {
                        let _ = st.path.refresh_candidates(
                            payload.lan.as_deref(),
                            Some(ip.as_str()),
                            payload.hn.as_deref().or(payload.name.as_deref()),
                            payload.port,
                            &payload.scheme,
                        );
                    }
                }
                Err(e) => {
                    mesh_join = Some(json!({"ok": false, "error": e.to_string()}));
                }
            }
        }
    } else {
        // Still mark runtime up for path watch (LAN-only QR)
        let _ = st.mesh.runtime_up(Some("takton-phone"), None, false);
    }

    let do_claim = body.claim.unwrap_or(true);
    let mut claim_result: Option<Value> = None;
    let mut device_token: Option<String> = None;
    let mut deferred = false;
    let mut last_err = String::new();

    // Always persist multi-endpoint candidates from QR
    let _ = st.path.merge_from_payload(&payload, None, None);

    if do_claim && !soft_expired {
        let shell_port = st.config.read().host_port;
        let claim_url_list = claim_urls(&payload, shell_port);

        // Parallel race: dead LAN endpoints must not freeze the phone for 10s×N.
        // connect 1.5s + total 3.5s per URL; first ok wins.
        let client = reqwest::Client::builder()
            .timeout(std::time::Duration::from_millis(3500))
            .connect_timeout(std::time::Duration::from_millis(1500))
            .pool_max_idle_per_host(2)
            .build()
            .ok();

        if let Some(http) = client {
            use futures_util::future::FutureExt;
            let pair_id = payload.pair_id.clone();
            let code = payload.code.clone();
            let dname = device_name.clone();
            let futs: Vec<_> = claim_url_list
                .iter()
                .map(|claim_url| {
                    let http = http.clone();
                    let claim_url = claim_url.clone();
                    let pair_id = pair_id.clone();
                    let code = code.clone();
                    let dname = dname.clone();
                    async move {
                        let remote = http
                            .post(&claim_url)
                            .json(&json!({
                                "pair_id": pair_id,
                                "code": code,
                                "device_name": dname,
                            }))
                            .send()
                            .await;
                        match remote {
                            Ok(res) => {
                                let status = res.status();
                                let text = res.text().await.unwrap_or_default();
                                if status.is_success() {
                                    if let Ok(v) = serde_json::from_str::<Value>(&text) {
                                        if v.get("ok") == Some(&json!(true)) {
                                            return Ok::<(Value, String), String>((v, claim_url));
                                        }
                                        return Err(v
                                            .get("error")
                                            .and_then(|x| x.as_str())
                                            .unwrap_or("claim failed")
                                            .to_string());
                                    }
                                }
                                if let Ok(v) = serde_json::from_str::<Value>(&text) {
                                    return Err(v
                                        .get("error")
                                        .and_then(|x| x.as_str())
                                        .unwrap_or("claim failed")
                                        .to_string());
                                }
                                Err(format!("HTTP {status}"))
                            }
                            Err(e) => Err(e.to_string()),
                        }
                    }
                    .boxed()
                })
                .collect();

            // Prefer first success among concurrent claims (LAN may still win if home Wi‑Fi).
            let mut remaining = futs;
            while !remaining.is_empty() {
                let (result, _idx, rest) = futures_util::future::select_all(remaining).await;
                remaining = rest;
                match result {
                    Ok((v, via)) => {
                        device_token = v
                            .get("token")
                            .or_else(|| v.pointer("/device/token"))
                            .and_then(|x| x.as_str())
                            .map(|s| s.to_string());
                        let mut v = v;
                        if let Some(obj) = v.as_object_mut() {
                            obj.insert("claim_via".into(), json!(via));
                        }
                        claim_result = Some(v);
                        last_err.clear();
                        break;
                    }
                    Err(e) => {
                        // Keep last non-empty error for deferred hint; continue racing others.
                        if !e.is_empty() {
                            last_err = e;
                        }
                    }
                }
            }
        }

        if claim_result.is_none() {
            match st.pair.claim(&payload.pair_id, &payload.code, &device_name) {
                Ok((dev, _)) => {
                    device_token = Some(dev.token.clone());
                    claim_result = Some(json!({
                        "ok": true,
                        "device": dev,
                        "via": "local",
                    }));
                    last_err.clear();
                }
                Err(e) => {
                    if last_err.is_empty() {
                        last_err = e.to_string();
                    }
                }
            }
        }

        // Soft-defer: network unreachable / waiting confirm → keep QR for later claim
        if claim_result.is_none() {
            let networkish = last_err.contains("error sending")
                || last_err.contains("Connect")
                || last_err.contains("timed out")
                || last_err.contains("Connection")
                || last_err.contains("unreachable")
                || last_err.contains("dns")
                || last_err.contains("waiting")
                || last_err.contains("not found")
                || last_err.is_empty();
            if networkish {
                deferred = true;
                let _ = st.path.set_deferred(DeferredClaim {
                    pair_id: payload.pair_id.clone(),
                    code: payload.code.clone(),
                    exp: payload.exp,
                    device_name: device_name.clone(),
                    qr: body.qr.clone(),
                });
            } else if !last_err.is_empty() {
                return err_json(last_err);
            }
        } else if let Some(t) = device_token.clone() {
            let _ = st.path.merge_from_payload(&payload, Some(t), None);
        }
    } else if soft_expired && do_claim {
        // Expired QR: still keep endpoints for reconnect if previously claimed
        deferred = true;
        last_err = "二维码已过期 · 端点已保存，可稍后在可达网络重试 claim".into();
    }

    // ── Connect + open remote surface (ALL in Rust; Flutter only binds result) ──
    let candidates: Vec<String> = payload
        .endpoints()
        .into_iter()
        .map(|e| e.url)
        .filter(|u| !is_phone_unusable_base(u))
        .collect();
    let email = body.email.unwrap_or_default();
    let password = body.password.unwrap_or_default();

    // Prefer the base that just won claim (usually VPS when off LAN).
    let mut preferred: Vec<String> = Vec::new();
    if let Some(ref cr) = claim_result {
        if let Some(via) = cr.get("claim_via").and_then(|v| v.as_str()) {
            let base = base_from_claim_url(via);
            if !base.is_empty() && !is_phone_unusable_base(&base) {
                preferred.push(base);
            }
        }
    }
    // Prefer VPS-shaped bases next (phone off-LAN happy path)
    for c in &candidates {
        if c.contains("/t/") && !preferred.iter().any(|p| p == c) {
            preferred.push(c.clone());
        }
    }

    let seamless = payload.tsk.as_ref().map(|s| !s.is_empty()).unwrap_or(false)
        || preferred.iter().any(|u| u.contains("/t/"))
        || payload.vps.as_ref().map(|s| !s.is_empty()).unwrap_or(false);

    let connect = try_connect_best_pref(
        &st,
        &candidates,
        &email,
        &password,
        &preferred,
        device_token.as_deref(),
    )
    .await;

    match connect {
        Ok(mut v) => {
            // Bootstrap remote chat surface so Flutter does zero extra orchestration.
            let surface = pair_bootstrap_remote_surface(&st).await;
            if let Some(obj) = v.as_object_mut() {
                obj.insert("device_token".into(), json!(device_token));
                obj.insert("claim".into(), json!(claim_result));
                obj.insert("claim_ok".into(), json!(claim_result.is_some()));
                obj.insert("deferred_claim".into(), json!(false));
                obj.insert("pair_id".into(), json!(payload.pair_id));
                obj.insert("mesh".into(), json!(payload.mesh.as_str()));
                obj.insert("endpoints".into(), json!(candidates));
                obj.insert("mesh_join".into(), json!(mesh_join));
                obj.insert("seamless".into(), json!(seamless));
                obj.insert("surface".into(), json!("remote"));
                obj.insert("chat_mode".into(), json!("remote"));
                obj.insert("toast".into(), json!(if seamless {
                    "已连接 · 外出自动切换"
                } else {
                    "配对成功 · 已连接 PC"
                }));
                obj.insert("ui_title".into(), json!("已连接 PC"));
                obj.insert("ui_body".into(), json!(obj
                    .get("path_kind")
                    .and_then(|x| x.as_str())
                    .map(|k| format!("路径 {k}"))
                    .unwrap_or_else(|| "远端 Agent 可用".into())));
                // Flatten surface fields for Flutter bind
                if let Some(sobj) = surface.as_object() {
                    for (k, val) in sobj {
                        if k != "ok" {
                            obj.insert(k.clone(), val.clone());
                        }
                    }
                }
                obj.insert("path".into(), st.path.profile_json());
                obj.insert("mesh_status".into(), st.mesh.status_json());
            }
            Json(v)
        }
        Err(e) => {
            let claim_ok = claim_result.is_some();
            let base = preferred
                .first()
                .cloned()
                .or_else(|| candidates.first().cloned())
                .unwrap_or_else(|| payload.base_url());
            // Persist best base even if login failed so reconnect can retry without re-scan.
            if !base.is_empty() && !is_phone_unusable_base(&base) {
                st.client.set_base_url(base.clone());
            }
            let (toast, title, body, needs_login) = if deferred {
                (
                    "已保存 · 网络可用后自动完成",
                    "配对已保存",
                    "回到同一 Wi‑Fi 或待 VPS 隧道在线后自动完成",
                    false,
                )
            } else if claim_ok {
                (
                    "配对成功 · 请稍后自动重连或手动登录",
                    "配对完成",
                    "设备已登记，登录未完成，可点「立即重试」",
                    email.is_empty(),
                )
            } else {
                (
                    "暂时连不上 PC · 已保存端点",
                    "连接未完成",
                    "请确认 PC 在线且 VPS 中继已启用",
                    false,
                )
            };
            Json(json!({
                "ok": true,
                "authenticated": false,
                "base_url": base,
                "mesh": payload.mesh.as_str(),
                "device_token": device_token,
                "claim": claim_result,
                "claim_ok": claim_ok,
                "deferred_claim": deferred,
                "pair_id": payload.pair_id,
                "endpoints": candidates,
                "login_error": e,
                "claim_error": if last_err.is_empty() { Value::Null } else { json!(last_err) },
                "mesh_join": mesh_join,
                "seamless": seamless,
                "needs_manual_login": needs_login,
                "surface": "local",
                "chat_mode": "local",
                "toast": toast,
                "ui_title": title,
                "ui_body": body,
                "session_id": Value::Null,
                "messages": [],
                "path": st.path.profile_json(),
                "mesh_status": st.mesh.status_json(),
                "hint": toast,
            }))
        }
    }
}

/// After PC auth: open remote surface (session + messages + mode) entirely in Rust.
async fn pair_bootstrap_remote_surface(st: &AppState) -> Value {
    let t0 = Instant::now();
    if !st.client.is_authenticated() {
        return json!({
            "ok": false,
            "surface": "remote",
            "session_id": Value::Null,
            "messages": [],
            "mode": Value::Null,
        });
    }

    let (pc_model, runtime, cached_kr) = remote_probe(st).await;
    let mode = build_mode_snapshot(
        st,
        ChatSurface::Remote,
        &pc_model,
        runtime.as_ref(),
        cached_kr,
    );

    let mut session_id = st
        .active_session
        .read()
        .clone()
        .filter(|s| s != LOCAL_SESSION_ID);

    if session_id.is_none() {
        if let Ok(Ok(list)) = tokio::time::timeout(
            std::time::Duration::from_secs(4),
            st.client.list_sessions(None),
        )
        .await
        {
            session_id = pick_preferred_remote_session(&list);
        }
    }

    // Do NOT create a blank "helpful assistant" session on pair — that stole
    // the default away from the CEO/steward DM. Only create if PC has zero sessions.
    if session_id.is_none() {
        if let Ok(Ok(list)) = tokio::time::timeout(
            std::time::Duration::from_secs(3),
            st.client.list_sessions(None),
        )
        .await
        {
            if list.is_empty() {
                if let Ok(Ok(s)) = tokio::time::timeout(
                    std::time::Duration::from_secs(4),
                    st.client.create_session(None),
                )
                .await
                {
                    session_id = Some(s.id);
                }
            }
        }
    }

    let mut messages: Vec<Value> = Vec::new();
    if let Some(ref sid) = session_id {
        *st.active_session.write() = Some(sid.clone());
        let _ = tokio::time::timeout(
            std::time::Duration::from_secs(3),
            ensure_chat(st.clone(), sid.clone()),
        )
        .await;
        messages = match tokio::time::timeout(
            std::time::Duration::from_secs(4),
            remote_messages_as_ui(st, sid, 80),
        )
        .await
        {
            Ok(m) => m,
            Err(_) => Vec::new(),
        };
    }

    if messages.is_empty() {
        let text = if session_id.is_some() {
            "远端会话已打开。消息将经 PC Agent 处理。"
        } else {
            "远端 Agent 已连接。发送消息将自动新建会话。"
        };
        messages.push(json!({
            "id": "w-remote-pair",
            "role": "assistant",
            "content": text,
            "text": text,
            "who": "远端 Agent",
            "format": "plain",
        }));
    }

    json!({
        "ok": true,
        "surface": "remote",
        "session_id": session_id,
        "messages": messages,
        "mode": mode,
        "elapsed_ms": t0.elapsed().as_millis() as u64,
        "active_session_id": st.active_session.read().clone(),
        "authenticated": true,
        "user_email": user_email_of(st),
    })
}

async fn pair_devices(State(st): State<AppState>) -> Json<Value> {
    let devices = st.pair.list_paired();
    Json(json!({ "ok": true, "devices": devices }))
}

async fn pair_revoke(State(st): State<AppState>, Path(id): Path<String>) -> Json<Value> {
    match st.pair.revoke(&id) {
        Ok(()) => Json(json!({ "ok": true, "id": id })),
        Err(e) => err_json(e),
    }
}

async fn pair_pending(State(st): State<AppState>) -> Json<Value> {
    Json(json!({
        "ok": true,
        "pending": st.pair.pending_snapshot(),
    }))
}

// ── M2 mesh ─────────────────────────────────────────────────────────────────

async fn mesh_status(State(st): State<AppState>) -> Json<Value> {
    let pc_port = takton_mobile_core::config::parse_base_url_parts(&st.client.config().base_url).2;
    st.mesh.set_backend_port(pc_port);
    Json(st.mesh.status_json())
}

async fn mesh_set(State(st): State<AppState>, Json(body): Json<MeshSetBody>) -> Json<Value> {
    use takton_mobile_core::mesh::parse_mode;
    if let Some(m) = body.mode.as_deref() {
        match parse_mode(m) {
            Ok(mode) => {
                if let Err(e) = st.mesh.set_mode(mode) {
                    return err_json(e);
                }
            }
            Err(e) => return err_json(e),
        }
    }
    if let Some(h) = body.hostname.as_deref() {
        if let Err(e) = st.mesh.set_hostname(h) {
            return err_json(e);
        }
    }
    if let Some(v) = body.require_pair_confirm {
        if let Err(e) = st.mesh.set_require_confirm(v) {
            return err_json(e);
        }
    }
    let pc_port = takton_mobile_core::config::parse_base_url_parts(&st.client.config().base_url).2;
    st.mesh.set_backend_port(pc_port);
    Json(st.mesh.status_json())
}

async fn mesh_up(State(st): State<AppState>, Json(body): Json<MeshRuntimeBody>) -> Json<Value> {
    match st.mesh.runtime_up(
        body.hostname.as_deref(),
        body.ifaces,
        body.auth_key.unwrap_or(false),
    ) {
        Ok(rt) => Json(json!({
            "ok": true,
            "up": rt.up,
            "hostname": rt.hostname,
            "backend": rt.backend,
            "detail": rt.detail,
            "fingerprint": rt.fingerprint,
            "ifaces": rt.ifaces,
            "mesh": st.mesh.status_json(),
        })),
        Err(e) => err_json(e),
    }
}

async fn mesh_down(State(st): State<AppState>) -> Json<Value> {
    match st.mesh.runtime_down() {
        Ok(rt) => Json(json!({ "ok": true, "up": rt.up, "detail": rt.detail })),
        Err(e) => err_json(e),
    }
}

async fn mesh_ifaces(State(st): State<AppState>, Json(body): Json<MeshRuntimeBody>) -> Json<Value> {
    let ifaces = body.ifaces.unwrap_or_default();
    match st.mesh.report_ifaces(ifaces) {
        Ok((changed, rt)) => {
            // On network change, refresh path candidates from mesh + re-probe best path
            if changed {
                let mesh = st.mesh.status();
                let port = st.mesh.backend_port();
                let _ = st.path.refresh_candidates(
                    mesh.lan_ip.as_deref(),
                    mesh.tailscale_ip.as_deref(),
                    Some(st.mesh.config().hostname.as_str()),
                    port,
                    "http",
                );
            }
            Json(json!({
                "ok": true,
                "changed": changed,
                "fingerprint": rt.fingerprint,
                "ifaces": rt.ifaces,
                "last_change_at": rt.last_change_at,
                "detail": rt.detail,
            }))
        }
        Err(e) => err_json(e),
    }
}

// ── Mesh embed / one-time auth ───────────────────────────────────────────────

async fn mesh_auth(State(st): State<AppState>, Json(body): Json<MeshAuthBody>) -> Json<Value> {
    let key = body.auth_key.unwrap_or_default();
    if key.trim().is_empty() {
        // clear
        return match st.mesh.clear_auth_key() {
            Ok(()) => Json(json!({"ok": true, "auth_key_set": false, "detail": "已清除"})),
            Err(e) => err_json(e),
        };
    }
    match st.mesh.set_auth_key(key.trim()) {
        Ok(v) => Json(v),
        Err(e) => err_json(e),
    }
}

async fn mesh_embed_start(State(st): State<AppState>, Json(body): Json<MeshEmbedBody>) -> Json<Value> {
    if let Some(role) = body.role.as_deref() {
        let r = takton_mobile_core::TsnetRole::parse(role);
        let _ = st.mesh.embed().set_role(r);
    }
    if let Some(h) = body.hostname.as_deref() {
        let _ = st.mesh.set_hostname(h);
    }
    match st.mesh.start_embed() {
        Ok(v) => Json(json!({"ok": true, "embed": v, "mesh": st.mesh.status_json()})),
        Err(e) => err_json(e),
    }
}

async fn mesh_embed_stop(State(st): State<AppState>) -> Json<Value> {
    match st.mesh.stop_embed() {
        Ok(v) => Json(json!({"ok": true, "embed": v})),
        Err(e) => err_json(e),
    }
}

async fn mesh_embed_status(State(st): State<AppState>) -> Json<Value> {
    Json(st.mesh.embed().status_json())
}

// ── M4 multi-endpoint path ───────────────────────────────────────────────────

async fn path_status(State(st): State<AppState>) -> Json<Value> {
    Json(st.path.profile_json())
}

async fn path_probe(State(st): State<AppState>, Json(body): Json<PathProbeBody>) -> Json<Value> {
    use takton_mobile_core::path::{select_best, Endpoint, EndpointKind};
    let extras = body.candidates.unwrap_or_default();
    let endpoints = st.path.candidate_urls(&extras);
    let eps: Vec<Endpoint> = if endpoints.is_empty() {
        extras
            .iter()
            .filter_map(|u| Endpoint::from_url(u, EndpointKind::Unknown))
            .collect()
    } else {
        endpoints
    };
    let (best, probes) = select_best(&eps).await;
    Json(json!({
        "ok": true,
        "best": best.as_ref().map(|e| json!({"url": e.url, "kind": e.kind.as_str()})),
        "probes": probes,
        "path": st.path.profile_json(),
    }))
}

async fn path_reconnect(
    State(st): State<AppState>,
    Json(body): Json<PathReconnectBody>,
) -> Json<Value> {
    // Retry deferred claim first (scan on 5G, claim when home / on TS)
    let claim_res = if body.claim.unwrap_or(true) {
        try_deferred_claim(&st).await
    } else {
        None
    };

    let extras = body.candidates.unwrap_or_default();
    let email = body.email.unwrap_or_default();
    let password = body.password.unwrap_or_default();

    // Refresh candidates from local mesh knowledge (DHCP drift)
    let mesh = st.mesh.status();
    let port = st.mesh.backend_port();
    let _ = st.path.refresh_candidates(
        mesh.lan_ip.as_deref(),
        mesh.tailscale_ip.as_deref(),
        Some(st.mesh.config().hostname.as_str()),
        port,
        "http",
    );

    match try_connect_best(&st, &extras, &email, &password).await {
        Ok(mut v) => {
            if let Some(obj) = v.as_object_mut() {
                obj.insert("deferred_claim_result".into(), json!(claim_res));
                obj.insert("reconnected".into(), json!(true));
            }
            Json(v)
        }
        Err(e) => Json(json!({
            "ok": false,
            "error": e,
            "deferred_claim_result": claim_res,
            "path": st.path.profile_json(),
            "probes_hint": "all candidates unreachable",
        })),
    }
}

async fn path_refresh(State(st): State<AppState>, Json(body): Json<PathProbeBody>) -> Json<Value> {
    let mesh = st.mesh.status();
    let port = st.mesh.backend_port();
    let _ = st.path.refresh_candidates(
        mesh.lan_ip.as_deref(),
        mesh.tailscale_ip.as_deref(),
        Some(st.mesh.config().hostname.as_str()),
        port,
        "http",
    );
    if let Some(cands) = body.candidates {
        let _ = st.path.merge_urls(&cands, takton_mobile_core::path::EndpointKind::Unknown);
    }
    Json(st.path.profile_json())
}

async fn device_heartbeat(State(st): State<AppState>, Path(id): Path<String>) -> Json<Value> {
    match st.client.device_heartbeat(&id).await {
        Ok(v) => Json(merge_ok(v)),
        Err(e) => err_json(e),
    }
}

async fn device_ping(State(st): State<AppState>, Path(id): Path<String>) -> Json<Value> {
    match st.client.device_ping(&id).await {
        Ok(v) => Json(merge_ok(v)),
        Err(e) => err_json(e),
    }
}

async fn catalog(State(st): State<AppState>, Query(q): Query<CatalogQuery>) -> Json<Value> {
    let refresh = q.refresh.unwrap_or(false);
    if refresh {
        *st.remote_probe.write() = Default::default();
    }
    match st.client.model_catalog(refresh).await {
        Ok(cat) => {
            let mut v = serde_json::to_value(&cat).unwrap_or(json!({}));
            if let Some(m) = catalog_active_model(&v) {
                let mut cache = st.remote_probe.write();
                cache.pc_model = m;
                cache.at = Some(Instant::now());
            }
            let provider = q
                .provider
                .as_deref()
                .or(q.provider_id.as_deref());
            // Server-side filter — Flutter only binds the returned lists.
            v = filter_catalog(v, q.q.as_deref(), provider);
            let models = v.get("models").cloned().unwrap_or(json!([]));
            let match_count = v.get("match_count").cloned().unwrap_or(json!(0));
            Json(json!({
                "ok": true,
                "catalog": v,
                "models": models,
                "match_count": match_count,
            }))
        }
        Err(e) => err_json(e),
    }
}

async fn select_model(
    State(st): State<AppState>,
    Json(body): Json<SelectModelBody>,
) -> Json<Value> {
    match st
        .client
        .select_model(&body.provider_id, &body.model, body.session_id.as_deref())
        .await
    {
        Ok(v) => {
            let mut cache = st.remote_probe.write();
            cache.pc_model = body.model.clone();
            cache.at = Some(Instant::now());
            Json(merge_ok(v))
        }
        Err(e) => err_json(e),
    }
}

async fn register_provider(State(st): State<AppState>, Json(body): Json<Value>) -> Json<Value> {
    match st.client.register_provider(body).await {
        Ok(v) => Json(merge_ok(v)),
        Err(e) => err_json(e),
    }
}

async fn list_presets(State(st): State<AppState>) -> Json<Value> {
    match st.client.list_presets().await {
        Ok(v) => {
            // PC may return a bare array or {presets:[...]}/{items:[...]}
            let list = if let Some(arr) = v.as_array() {
                Value::Array(arr.clone())
            } else if let Some(arr) = v.get("presets").and_then(|x| x.as_array()) {
                Value::Array(arr.clone())
            } else if let Some(arr) = v.get("items").and_then(|x| x.as_array()) {
                Value::Array(arr.clone())
            } else if let Some(arr) = v.get("data").and_then(|x| x.as_array()) {
                Value::Array(arr.clone())
            } else {
                Value::Array(vec![])
            };
            Json(json!({ "ok": true, "presets": list }))
        }
        // Don't hard-fail UI: empty list lets Flutter show offline templates
        Err(e) => Json(json!({
            "ok": true,
            "presets": [],
            "hint": e.to_string(),
        })),
    }
}

async fn oauth_openai_start(State(st): State<AppState>) -> Json<Value> {
    // Prefer PC when connected; otherwise phone-local OAuth.
    if st.client.is_authenticated() {
        match st.client.openai_oauth_start().await {
            Ok(v) => return Json(merge_ok(v)),
            Err(e) => {
                tracing::warn!("PC OAuth start failed, fallback local: {e}");
            }
        }
    }
    Json(st.local_oauth.openai_start())
}

async fn oauth_openai_poll(State(st): State<AppState>, Json(body): Json<Value>) -> Json<Value> {
    let state = body.get("state").and_then(|v| v.as_str());
    if st.client.is_authenticated() {
        match st.client.openai_oauth_poll(state).await {
            Ok(v) => return Json(merge_ok(v)),
            Err(e) => {
                tracing::warn!("PC OAuth poll failed, try local: {e}");
            }
        }
    }
    let v = st.local_oauth.openai_poll(state);
    if v.get("status").and_then(|x| x.as_str()) == Some("authorized") {
        apply_local_oauth_token(&st, &v);
    }
    Json(v)
}

async fn oauth_openai_complete(State(st): State<AppState>, Json(body): Json<Value>) -> Json<Value> {
    let callback = body
        .get("callback_url")
        .and_then(|v| v.as_str())
        .unwrap_or("");
    let state = body.get("state").and_then(|v| v.as_str());
    if st.client.is_authenticated() {
        match st.client.openai_oauth_complete(callback, state).await {
            Ok(v) => return Json(merge_ok(v)),
            Err(e) => {
                tracing::warn!("PC OAuth complete failed, try local: {e}");
            }
        }
    }
    let v = st.local_oauth.openai_complete(callback, state).await;
    if v.get("ok") == Some(&json!(true)) {
        apply_local_oauth_token(&st, &v);
    }
    Json(v)
}

async fn oauth_xai_start(State(st): State<AppState>) -> Json<Value> {
    if st.client.is_authenticated() {
        match st.client.xai_oauth_start().await {
            Ok(v) => return Json(merge_ok(v)),
            Err(e) => {
                tracing::warn!("PC xAI OAuth start failed, fallback local: {e}");
            }
        }
    }
    Json(st.local_oauth.xai_start().await)
}

async fn oauth_xai_poll(State(st): State<AppState>, Json(body): Json<Value>) -> Json<Value> {
    let code = body
        .get("device_code")
        .and_then(|v| v.as_str())
        .unwrap_or("");
    if st.client.is_authenticated() {
        match st.client.xai_oauth_poll(code).await {
            Ok(v) => return Json(merge_ok(v)),
            Err(e) => {
                tracing::warn!("PC xAI OAuth poll failed, try local: {e}");
            }
        }
    }
    let v = st.local_oauth.xai_poll(code).await;
    if v.get("status").and_then(|x| x.as_str()) == Some("authorized") {
        apply_local_oauth_token(&st, &v);
    }
    Json(v)
}

/// Persist OAuth access_token into local LLM profile for phone-only chat.
fn apply_local_oauth_token(st: &AppState, v: &Value) {
    let token = v
        .get("access_token")
        .and_then(|x| x.as_str())
        .unwrap_or("")
        .trim();
    if token.is_empty() {
        return;
    }
    let label = v
        .get("provider_label")
        .and_then(|x| x.as_str())
        .unwrap_or("OAuth");
    let is_chatgpt = label.to_lowercase().contains("chatgpt")
        || v.get("provider_id")
            .and_then(|x| x.as_str())
            .unwrap_or("")
            .contains("chatgpt");
    let base = if is_chatgpt {
        takton_mobile_core::local_llm::CHATGPT_OAUTH_BASE.to_string()
    } else {
        v.get("base_url")
            .and_then(|x| x.as_str())
            .unwrap_or("https://api.x.ai/v1")
            .to_string()
    };
    let mut profile = st.local_llm.load_profile();
    profile.api_key = token.to_string();
    profile.base_url = base.clone();
    profile.provider_label = label.to_string();
    if let Some(aid) = v.get("account_id").and_then(|x| x.as_str()) {
        if !aid.is_empty() {
            profile.account_id = aid.to_string();
        }
    }
    if profile.model.trim().is_empty() {
        if is_chatgpt {
            profile.model = "gpt-5.6-luna".into();
        } else if base.contains("x.ai") {
            profile.model = "grok-3".into();
        } else {
            profile.model = "gpt-4o".into();
        }
    }
    let _ = st.local_llm.save_profile(&profile);
}

async fn test_llm(State(st): State<AppState>, Json(body): Json<Value>) -> Json<Value> {
    match st.client.test_llm(body).await {
        Ok(v) => Json(merge_ok(v)),
        Err(e) => err_json(e),
    }
}

async fn apply_settings(State(st): State<AppState>, Json(body): Json<Value>) -> Json<Value> {
    match st.client.apply_settings(body).await {
        Ok(v) => Json(merge_ok(v)),
        Err(e) => err_json(e),
    }
}

async fn set_credentials(State(st): State<AppState>, Json(body): Json<Value>) -> Json<Value> {
    match st.client.set_catalog_credentials(body).await {
        Ok(v) => Json(merge_ok(v)),
        Err(e) => err_json(e),
    }
}

async fn upload_file(State(st): State<AppState>, mut multipart: Multipart) -> Json<Value> {
    let mut name = String::from("upload.bin");
    let mut bytes = Vec::new();
    let mut content_type = String::from("application/octet-stream");
    while let Ok(Some(field)) = multipart.next_field().await {
        let fname = field.file_name().map(|s| s.to_string());
        let ctype = field.content_type().map(|s| s.to_string());
        if let Ok(data) = field.bytes().await {
            if let Some(n) = fname {
                name = n;
            }
            if let Some(c) = ctype {
                content_type = c;
            }
            bytes = data.to_vec();
        }
    }
    if bytes.is_empty() {
        return err_json("empty file");
    }
    match st
        .client
        .upload_file(&name, bytes, Some(&content_type))
        .await
    {
        Ok(mut v) => {
            // Rewrite relative /uploads/... to tunnel-public absolute URL so
            // phone can open/download the same way Codex surfaces attachments.
            let base = st.client.config().base_url.clone();
            if let Some(obj) = v.as_object_mut() {
                for key in ["url", "public_url", "path", "download_url", "href"] {
                    if let Some(url) = obj.get(key).and_then(|u| u.as_str()).map(|s| s.to_string())
                    {
                        if url.starts_with('/') || url.starts_with("uploads/") {
                            let abs = absolutize_media_url(&base, &url);
                            obj.insert(key.into(), json!(abs));
                        }
                    }
                }
                // Always expose public_url for clients that prefer it.
                if let Some(url) = obj
                    .get("url")
                    .and_then(|u| u.as_str())
                    .map(|s| s.to_string())
                {
                    let abs = absolutize_media_url(&base, &url);
                    obj.insert("url".into(), json!(abs));
                    obj.insert("public_url".into(), json!(abs));
                }
            }
            Json(json!({ "ok": true, "result": v }))
        }
        Err(e) => err_json(e),
    }
}

async fn runtime(State(st): State<AppState>) -> Json<Value> {
    match st.client.runtime_status().await {
        Ok(rt) => Json(json!({ "ok": true, "runtime": rt })),
        Err(e) => err_json(e),
    }
}

async fn list_processes(State(st): State<AppState>) -> Json<Value> {
    match st.client.list_processes(false).await {
        Ok(v) => Json(json!({ "ok": true, "processes": normalize_list(&v) })),
        Err(e) => err_json(e),
    }
}

async fn stop_process(State(st): State<AppState>, Path(id): Path<String>) -> Json<Value> {
    match st.client.stop_process(&id).await {
        Ok(v) => Json(merge_ok(v)),
        Err(e) => err_json(e),
    }
}

async fn resume_process(State(st): State<AppState>, Path(id): Path<String>) -> Json<Value> {
    match st.client.resume_process(&id).await {
        Ok(v) => Json(merge_ok(v)),
        Err(e) => err_json(e),
    }
}

async fn set_notify(State(st): State<AppState>, Json(body): Json<NotifyBody>) -> Json<Value> {
    *st.notify_approvals.write() = body.enabled;
    Json(json!({ "ok": true, "enabled": body.enabled }))
}


async fn local_config_clear(State(st): State<AppState>) -> Json<Value> {
    match st.local_llm.clear_profile() {
        Ok(()) => {
            let profile = st.local_llm.load_profile();
            Json(json!({
                "ok": true,
                "ready": false,
                "config": profile.masked(),
            }))
        }
        Err(e) => err_json(e),
    }
}

async fn local_config_get(State(st): State<AppState>) -> Json<Value> {
    let profile = st.local_llm.load_profile();
    Json(json!({ "ok": true, "config": profile.masked() }))
}

async fn local_config_set(
    State(st): State<AppState>,
    Json(body): Json<LocalConfigBody>,
) -> Json<Value> {
    let mut profile = st.local_llm.load_profile();
    if let Some(v) = body.base_url {
        profile.base_url = v;
    }
    if let Some(v) = body.api_key {
        if !v.is_empty() {
            profile.api_key = v;
        }
    }
    if let Some(v) = body.model {
        profile.model = v;
    }
    if let Some(v) = body.provider_label {
        profile.provider_label = v;
    }
    if let Some(v) = body.chat_path {
        profile.chat_path = v;
    }
    if let Some(v) = body.account_id {
        if !v.is_empty() {
            profile.account_id = v;
        }
    }
    match st.local_llm.save_profile(&profile) {
        Ok(()) => Json(json!({
            "ok": true,
            "ready": profile.is_ready(),
            "config": profile.masked(),
        })),
        Err(e) => err_json(e),
    }
}



async fn local_skills_list(State(st): State<AppState>) -> Json<Value> {
    Json(json!({ "ok": true, "skills": st.local_agent.tools().skills().list_json() }))
}

async fn local_skills_install(State(st): State<AppState>, Json(body): Json<Value>) -> Json<Value> {
    let id = body
        .get("id")
        .or_else(|| body.get("name"))
        .and_then(|x| x.as_str())
        .unwrap_or("")
        .trim();
    let content = body
        .get("content")
        .and_then(|x| x.as_str())
        .unwrap_or("")
        .to_string();
    if id.is_empty() || content.trim().is_empty() {
        return Json(json!({"ok": false, "error": "id and content required"}));
    }
    match st.local_agent.tools().skills().install_content(id, &content) {
        Ok(path) => Json(json!({"ok": true, "path": path, "id": id})),
        Err(e) => Json(json!({"ok": false, "error": e.to_string()})),
    }
}

async fn local_skills_uninstall(State(st): State<AppState>, Json(body): Json<Value>) -> Json<Value> {
    let id = body.get("id").and_then(|x| x.as_str()).unwrap_or("").trim();
    if id.is_empty() {
        return Json(json!({"ok": false, "error": "id required"}));
    }
    match st.local_agent.tools().skills().uninstall(id) {
        Ok(removed) => Json(json!({"ok": true, "removed": removed})),
        Err(e) => Json(json!({"ok": false, "error": e.to_string()})),
    }
}

/// Install Matt Pocock mobile pack (and optional full productivity) from GitHub raw.
async fn local_skills_install_pack(
    State(st): State<AppState>,
    Json(body): Json<Value>,
) -> Json<Value> {
    let pack = body
        .get("pack_id")
        .or_else(|| body.get("pack"))
        .and_then(|x| x.as_str())
        .unwrap_or("mattpocock-mobile")
        .trim()
        .to_string();
    let force = body.get("force").and_then(|x| x.as_bool()).unwrap_or(false);

    let entries: Vec<(String, String)> = match pack.as_str() {
        "mattpocock-mobile" | "mattpocock" | "mobile" => takton_mobile_core::skills::mattpocock_mobile_pack()
            .iter()
            .map(|(id, path)| (id.to_string(), path.to_string()))
            .collect(),
        _ => {
            return Json(json!({
                "ok": false,
                "error": format!("unknown pack_id: {pack} (use mattpocock-mobile)")
            }));
        }
    };

    let client = match reqwest::Client::builder()
        .timeout(std::time::Duration::from_secs(25))
        .user_agent("Takton-Mobile/0.4")
        .build()
    {
        Ok(c) => c,
        Err(e) => return Json(json!({"ok": false, "error": e.to_string()})),
    };

    let mut installed = 0u32;
    let mut skipped = 0u32;
    let mut failed = 0u32;
    let mut items = Vec::new();
    let store = st.local_agent.tools().skills();

    for (id, cat_path) in entries {
        // skip if exists and !force
        if !force {
            if store.get(&id).is_ok() {
                skipped += 1;
                items.push(json!({"skill_id": id, "success": true, "error": "already installed"}));
                continue;
            }
        }
        let url = takton_mobile_core::skills::mattpocock_raw_url(&cat_path);
        match client.get(&url).send().await {
            Ok(resp) if resp.status().is_success() => {
                match resp.text().await {
                    Ok(content) if content.contains("name:") || content.starts_with('#') => {
                        match store.install_content(&id, &content) {
                            Ok(path) => {
                                installed += 1;
                                items.push(json!({"skill_id": id, "success": true, "path": path}));
                            }
                            Err(e) => {
                                failed += 1;
                                items.push(json!({"skill_id": id, "success": false, "error": e.to_string()}));
                            }
                        }
                    }
                    Ok(_) => {
                        failed += 1;
                        items.push(json!({"skill_id": id, "success": false, "error": "empty or invalid SKILL.md"}));
                    }
                    Err(e) => {
                        failed += 1;
                        items.push(json!({"skill_id": id, "success": false, "error": e.to_string()}));
                    }
                }
            }
            Ok(resp) => {
                failed += 1;
                items.push(json!({
                    "skill_id": id,
                    "success": false,
                    "error": format!("HTTP {}", resp.status())
                }));
            }
            Err(e) => {
                failed += 1;
                items.push(json!({"skill_id": id, "success": false, "error": e.to_string()}));
            }
        }
    }

    Json(json!({
        "ok": failed == 0 || installed > 0,
        "pack_id": pack,
        "installed": installed,
        "skipped": skipped,
        "failed": failed,
        "items": items,
        "message": format!("技能包 {pack}：成功 {installed}，跳过 {skipped}，失败 {failed}"),
    }))
}

async fn local_mcp_get(State(st): State<AppState>) -> Json<Value> {
    let cfg = st.local_agent.tools().mcp().load_config();
    Json(json!({ "ok": true, "config": cfg }))
}

async fn local_mcp_set(State(st): State<AppState>, Json(body): Json<Value>) -> Json<Value> {
    let cfg: takton_mobile_core::mcp_client::McpConfigFile =
        match serde_json::from_value(body.get("config").cloned().unwrap_or(body.clone())) {
            Ok(c) => c,
            Err(e) => return Json(json!({"ok": false, "error": e.to_string()})),
        };
    match st.local_agent.tools().mcp().save_config(&cfg) {
        Ok(()) => Json(json!({"ok": true})),
        Err(e) => Json(json!({"ok": false, "error": e.to_string()})),
    }
}

async fn local_agent_cfg_get(State(st): State<AppState>) -> Json<Value> {
    let cfg = st.local_agent.tools().load_config();
    Json(json!({ "ok": true, "config": agent_config_public(&cfg) }))
}

async fn local_agent_cfg_set(State(st): State<AppState>, Json(body): Json<Value>) -> Json<Value> {
    let mut cfg = st.local_agent.tools().load_config();
    if let Some(v) = body.get("max_iterations").and_then(|x| x.as_u64()) {
        cfg.max_iterations = v.clamp(2, 16) as u32;
    }
    if let Some(v) = body.get("context_soft_tokens").and_then(|x| x.as_u64()) {
        cfg.context_soft_tokens = v.clamp(4000, 100_000) as u32;
    }
    if let Some(v) = body.get("context_hard_tokens").and_then(|x| x.as_u64()) {
        cfg.context_hard_tokens = v.clamp(6000, 120_000) as u32;
    }
    if let Some(v) = body.get("enable_skills").and_then(|x| x.as_bool()) {
        cfg.enable_skills = v;
    }
    if let Some(v) = body.get("enable_mcp").and_then(|x| x.as_bool()) {
        cfg.enable_mcp = v;
    }
    if let Some(v) = body.get("enable_text_tools").and_then(|x| x.as_bool()) {
        cfg.enable_text_tools = v;
    }
    if let Some(v) = body.get("tavily_api_key").and_then(|x| x.as_str()) {
        let v = v.trim();
        // Empty or masked placeholder → keep existing secret
        if !v.is_empty() && !v.contains('…') && v != "••••" {
            cfg.tavily_api_key = v.to_string();
        }
    }
    if let Some(v) = body.get("azure_vision_key").and_then(|x| x.as_str()) {
        let v = v.trim();
        if !v.is_empty() && !v.contains('…') && v != "••••" {
            cfg.azure_vision_key = v.to_string();
        }
    }
    if let Some(v) = body.get("azure_vision_endpoint").and_then(|x| x.as_str()) {
        cfg.azure_vision_endpoint = v.to_string();
    }
    if let Some(v) = body.get("azure_speech_key").and_then(|x| x.as_str()) {
        let v = v.trim();
        if !v.is_empty() && !v.contains('…') && v != "••••" {
            cfg.azure_speech_key = v.to_string();
        }
    }
    if let Some(v) = body.get("azure_speech_region").and_then(|x| x.as_str()) {
        if !v.trim().is_empty() {
            cfg.azure_speech_region = v.to_string();
        }
    }
    if let Some(v) = body.get("tts_voice").and_then(|x| x.as_str()) {
        if !v.trim().is_empty() {
            cfg.tts_voice = v.to_string();
        }
    }
    match st.local_agent.tools().save_config(&cfg) {
        Ok(()) => Json(json!({"ok": true, "config": agent_config_public(&cfg)})),
        Err(e) => Json(json!({"ok": false, "error": e.to_string()})),
    }
}

/// Direct tool invoke for QA (no LLM). body: { "name": "web_search", "args": { "query": "..." } }
async fn local_tools_run(State(st): State<AppState>, Json(body): Json<Value>) -> Json<Value> {
    let name = body
        .get("name")
        .and_then(|x| x.as_str())
        .unwrap_or("")
        .trim();
    if name.is_empty() {
        return Json(json!({ "ok": false, "error": "name required" }));
    }
    let args = body.get("args").cloned().unwrap_or(json!({}));
    let started = std::time::Instant::now();
    let result = st.local_agent.tools().dispatch(name, &args).await;
    let ms = started.elapsed().as_millis();
    let ok = !result.starts_with("[tool_error]") && !result.contains("(no results)");
    Json(json!({
        "ok": ok,
        "name": name,
        "ms": ms,
        "result": result,
        "preview": result.chars().take(400).collect::<String>(),
    }))
}

async fn local_test(State(st): State<AppState>, Json(body): Json<Value>) -> Json<Value> {
    let mut profile = st.local_llm.load_profile();
    if let Some(b) = body.get("base_url").and_then(|x| x.as_str()) {
        profile.base_url = b.to_string();
    }
    if let Some(m) = body.get("model").and_then(|x| x.as_str()) {
        profile.model = m.to_string();
    }
    if let Some(k) = body.get("api_key").and_then(|x| x.as_str()) {
        if !k.is_empty() {
            profile.api_key = k.to_string();
        }
    }
    match st.local_llm.test_connection(&profile).await {
        Ok(v) => Json(json!({ "ok": true, "result": v })),
        Err(e) => err_json(e),
    }
}

async fn local_history_get(State(st): State<AppState>) -> Json<Value> {
    Json(json!({
        "ok": true,
        "messages": local_history_as_ui(&st),
    }))
}

async fn local_history_clear(State(st): State<AppState>) -> Json<Value> {
    match st.local_llm.clear_history() {
        Ok(()) => Json(json!({ "ok": true })),
        Err(e) => err_json(e),
    }
}

async fn local_stop(State(st): State<AppState>) -> Json<Value> {
    st.local_llm.request_stop();
    Json(json!({ "ok": true }))
}

async fn local_chat_stream(
    State(st): State<AppState>,
    Json(body): Json<LocalChatBody>,
) -> Sse<impl Stream<Item = Result<Event, Infallible>>> {
    let content = body.text().trim().to_string();
    let images = body.image_parts();
    let reset = body.reset.unwrap_or(false);
    let (tx, rx) = mpsc::unbounded_channel::<Event>();

    tokio::spawn(async move {
        let send = |ev: Event| {
            let _ = tx.send(ev);
        };

        if content.is_empty() && images.is_empty() {
            send(
                Event::default()
                    .event("error")
                    .data(json!({"error": "empty content"}).to_string()),
            );
            return;
        }
        let content = if content.is_empty() {
            "（见图片）".to_string()
        } else {
            content
        };

        if reset {
            let _ = st.local_llm.clear_history();
        }

        let profile = st.local_llm.load_profile();
        let mut hist = st.local_llm.load_history();

        // Coalesce token deltas ~40ms before SSE fanout (mirrors WS path).
        let (raw_tx, mut raw_rx) = mpsc::unbounded_channel::<String>();
        let tx_delta = tx.clone();
        let coalesce = tokio::spawn(async move {
            use std::time::Duration;
            let mut acc = String::new();
            let mut dirty = false;
            let period = Duration::from_millis(AppState::DELTA_COALESCE_MS);
            let mut ticker = tokio::time::interval(period);
            ticker.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Delay);
            ticker.tick().await;
            loop {
                tokio::select! {
                    item = raw_rx.recv() => {
                        match item {
                            Some(d) => {
                                acc.push_str(&d);
                                dirty = true;
                            }
                            None => {
                                if dirty && !acc.is_empty() {
                                    let chunk = std::mem::take(&mut acc);
                                    let _ = tx_delta.send(
                                        Event::default()
                                            .event("delta")
                                            .data(json!({
                                                "delta": chunk,
                                                "text": chunk,
                                                "coalesced": true,
                                                "coalesce_ms": AppState::DELTA_COALESCE_MS,
                                            }).to_string()),
                                    );
                                }
                                break;
                            }
                        }
                    }
                    _ = ticker.tick(), if dirty => {
                        if !acc.is_empty() {
                            let chunk = std::mem::take(&mut acc);
                            dirty = false;
                            let _ = tx_delta.send(
                                Event::default()
                                    .event("delta")
                                    .data(json!({
                                        "delta": chunk,
                                        "text": chunk,
                                        "coalesced": true,
                                        "coalesce_ms": AppState::DELTA_COALESCE_MS,
                                    }).to_string()),
                            );
                        } else {
                            dirty = false;
                        }
                    }
                }
            }
        });

        // Pruned PC-style agent loop (tools + doom guard)
        let tx_ev = tx.clone();
        let stream_result = st
            .local_agent
            .run(&profile, &mut hist, &content, &images, |ev| {
                use takton_mobile_core::AgentEvent;
                match ev {
                    AgentEvent::Delta { text } => {
                        let _ = raw_tx.send(text);
                    }
                    AgentEvent::Status { detail } => {
                        let _ = tx_ev.send(
                            Event::default()
                                .event("status")
                                .data(json!({ "detail": detail }).to_string()),
                        );
                    }
                    AgentEvent::ToolStart { id, name, args } => {
                        let _ = tx_ev.send(
                            Event::default().event("tool").data(
                                json!({
                                    "phase": "start",
                                    "id": id,
                                    "name": name,
                                    "args": args,
                                })
                                .to_string(),
                            ),
                        );
                    }
                    AgentEvent::ToolEnd {
                        id,
                        name,
                        preview,
                        ok,
                    } => {
                        let _ = tx_ev.send(
                            Event::default().event("tool").data(
                                json!({
                                    "phase": "end",
                                    "id": id,
                                    "name": name,
                                    "preview": preview,
                                    "ok": ok,
                                })
                                .to_string(),
                            ),
                        );
                    }
                    AgentEvent::Compress { report } => {
                        let _ = tx_ev.send(
                            Event::default()
                                .event("status")
                                .data(json!({ "detail": report, "compress": true }).to_string()),
                        );
                    }
                    AgentEvent::Done { .. } => {}
                    AgentEvent::Error { message } => {
                        let _ = tx_ev.send(
                            Event::default()
                                .event("error")
                                .data(json!({"error": message}).to_string()),
                        );
                    }
                }
            })
            .await;
        drop(raw_tx);
        let _ = coalesce.await;

        match stream_result {
            Ok(full) => {
                if full.trim().is_empty() {
                    send(
                        Event::default()
                            .event("error")
                            .data(json!({
                                "error": "模型返回空内容 · 请确认已配置模型/OAuth 并点应用"
                            }).to_string()),
                    );
                    return;
                }
                send(
                    Event::default()
                        .event("done")
                        .data(json!({
                            "content": full,
                            "text": full,
                            "done": true,
                        }).to_string()),
                );
            }
            Err(e) => {
                send(
                    Event::default()
                        .event("error")
                        .data(json!({"error": e.to_string()}).to_string()),
                );
            }
        }
    });

    let stream = futures_util::stream::unfold(rx, |mut rx| async move {
        match rx.recv().await {
            Some(ev) => Some((Ok::<Event, Infallible>(ev), rx)),
            None => None,
        }
    });
    Sse::new(stream).keep_alive(KeepAlive::default())
}

async fn kernel_status(State(st): State<AppState>) -> Json<Value> {
    let local = AppState::probe_local_kernel();
    let mut runtime = None;
    if st.client.is_authenticated() {
        runtime = st.client.runtime_status().await.ok();
    }
    Json(json!({
        "ok": true,
        "local_kernel": local,
        "runtime": runtime,
    }))
}

async fn ui_motion() -> Json<Value> {
    let m = MotionProfile::default();
    Json(json!({
        "ok": true,
        "motion": m.as_json(),
        "css": m.css_vars(),
    }))
}

async fn list_media(State(st): State<AppState>) -> Json<Value> {
    match st.media.list() {
        Ok(items) => Json(json!({ "ok": true, "media": items })),
        Err(e) => err_json(e),
    }
}

async fn save_media(State(st): State<AppState>, mut multipart: Multipart) -> Json<Value> {
    let mut name = String::from("capture.bin");
    let mut bytes = Vec::new();
    let mut kind = String::from("file");
    let mut mime = String::from("application/octet-stream");
    while let Ok(Some(field)) = multipart.next_field().await {
        let fname = field.file_name().map(|s| s.to_string());
        let field_name = field.name().map(|s| s.to_string());
        let ctype = field.content_type().map(|s| s.to_string());
        if let Ok(data) = field.bytes().await {
            if field_name.as_deref() == Some("kind") {
                kind = String::from_utf8_lossy(&data).to_string();
            } else {
                if let Some(n) = fname {
                    name = n;
                }
                if let Some(c) = ctype {
                    mime = c;
                }
                bytes = data.to_vec();
            }
        }
    }
    match st.media.save(&kind, &name, &mime, &bytes) {
        Ok(item) => Json(json!({ "ok": true, "media": item })),
        Err(e) => err_json(e),
    }
}

async fn get_media(State(st): State<AppState>, Path(id): Path<String>) -> Response {
    match st.media.get_bytes(&id) {
        Ok((meta, bytes)) => (
            [(header::CONTENT_TYPE, meta.mime)],
            bytes,
        )
            .into_response(),
        Err(e) => {
            let body = json!({"ok": false, "error": e.to_string()}).to_string();
            (
                axum::http::StatusCode::NOT_FOUND,
                [(header::CONTENT_TYPE, "application/json")],
                body,
            )
                .into_response()
        }
    }
}

async fn resolve_mode(State(st): State<AppState>, Json(body): Json<ModeBody>) -> Json<Value> {
    let surface = resolve_surface(&body.surface);
    let (pc_model, runtime, cached_kr) =
        if surface == ChatSurface::Remote && st.client.is_authenticated() {
            remote_probe(&st).await
        } else {
            (String::new(), None, None)
        };
    let snap = build_mode_snapshot(&st, surface, &pc_model, runtime.as_ref(), cached_kr);
    Json(json!({ "ok": true, "mode": snap }))
}

/// One-shot surface switch: mode + normalized messages + optional session open/create.
async fn switch_surface(
    State(st): State<AppState>,
    Json(body): Json<SwitchSurfaceBody>,
) -> Json<Value> {
    let t0 = Instant::now();
    let surface = resolve_surface(&body.surface);
    let ensure = body.ensure_session.unwrap_or(true);

    let (pc_model, runtime, cached_kr) =
        if surface == ChatSurface::Remote && st.client.is_authenticated() {
            remote_probe(&st).await
        } else {
            (String::new(), None, None)
        };
    let mode = build_mode_snapshot(&st, surface, &pc_model, runtime.as_ref(), cached_kr);

    let mut session_id = body
        .session_id
        .as_deref()
        .map(|s| s.trim().to_string())
        .filter(|s| !s.is_empty() && s != LOCAL_SESSION_ID)
        .or_else(|| {
            st.active_session
                .read()
                .clone()
                .filter(|s| s != LOCAL_SESSION_ID)
        });

    let mut messages: Vec<Value> = Vec::new();
    let mut note = String::new();

    match surface {
        ChatSurface::Local => {
            *st.active_session.write() = Some(LOCAL_SESSION_ID.to_string());
            messages = local_history_as_ui(&st);
            if messages.is_empty() {
                messages.push(json!({
                    "id": "w-local",
                    "role": "assistant",
                    "content": "你好。本机对话已就绪。在「我的 → LLM 设置」配置 API Key 后即可流式聊天；ChatGPT OAuth 请用顶栏「远端 Agent」。",
                    "text": "你好。本机对话已就绪。在「我的 → LLM 设置」配置 API Key 后即可流式聊天；ChatGPT OAuth 请用顶栏「远端 Agent」。",
                    "who": "本机 · LLM",
                    "format": "plain",
                }));
            }
            session_id = Some(LOCAL_SESSION_ID.to_string());
        }
        ChatSurface::Remote => {
            if !st.client.is_authenticated() {
                return Json(json!({
                    "ok": false,
                    "error": "远端 Agent 需先连接 PC",
                    "mode": mode,
                    "surface": "remote",
                    "messages": [],
                    "session_id": Value::Null,
                    "elapsed_ms": t0.elapsed().as_millis() as u64,
                }));
            }

            if session_id.is_none() {
                if let Ok(list) = st.client.list_sessions(None).await {
                    session_id = pick_preferred_remote_session(&list);
                }
            }

            if session_id.is_none() && ensure {
                match st.client.create_session(None).await {
                    Ok(s) => {
                        session_id = Some(s.id.clone());
                        note = "created".into();
                    }
                    Err(e) => {
                        note = format!("create_failed: {e}");
                    }
                }
            }

            if let Some(ref sid) = session_id {
                *st.active_session.write() = Some(sid.clone());
                let _ = ensure_chat(st.clone(), sid.clone()).await;
                messages = remote_messages_as_ui(&st, sid, 100).await;
            }

            if messages.is_empty() {
                let text = if session_id.is_some() {
                    "远端会话已打开。消息将经 PC Agent 工具链处理。"
                } else {
                    "远端 Agent 已连接。发送消息将自动新建会话，或在侧栏点 +。"
                };
                messages.push(json!({
                    "id": "w-remote",
                    "role": "assistant",
                    "content": text,
                    "text": text,
                    "who": "远端 Agent",
                    "format": "plain",
                }));
            }
        }
    }

    Json(json!({
        "ok": true,
        "surface": match surface {
            ChatSurface::Local => "local",
            ChatSurface::Remote => "remote",
        },
        "mode": mode,
        "messages": messages,
        "session_id": session_id,
        "note": note,
        "elapsed_ms": t0.elapsed().as_millis() as u64,
    }))
}

// ── WebSocket ───────────────────────────────────────────────────────────────

async fn ws_upgrade(ws: WebSocketUpgrade, State(st): State<AppState>) -> impl IntoResponse {
    ws.on_upgrade(move |socket| handle_browser_ws(socket, st))
}

async fn handle_browser_ws(mut socket: WebSocket, st: AppState) {
    let sub_id = st.new_sub_id();
    let (tx, mut rx) = mpsc::unbounded_channel::<Message>();
    st.browser_subs.insert(sub_id.clone(), tx);

    let _ = socket
        .send(Message::Text(
            json!({ "type": "mobile_hello", "ok": true })
                .to_string()
                .into(),
        ))
        .await;

    loop {
        tokio::select! {
            msg = socket.recv() => {
                match msg {
                    Some(Ok(Message::Text(t))) => {
                        if let Ok(v) = serde_json::from_str::<Value>(&t) {
                            if let Err(e) = handle_ws_client_msg(&st, &v).await {
                                let _ = socket.send(Message::Text(
                                    json!({"type":"error","error": e}).to_string().into()
                                )).await;
                            }
                        }
                    }
                    Some(Ok(Message::Close(_))) | None => break,
                    Some(Ok(Message::Ping(p))) => {
                        let _ = socket.send(Message::Pong(p)).await;
                    }
                    _ => {}
                }
            }
            fan = rx.recv() => {
                match fan {
                    Some(m) => {
                        if socket.send(m).await.is_err() {
                            break;
                        }
                    }
                    None => break,
                }
            }
        }
    }
    st.browser_subs.remove(&sub_id);
}

async fn handle_ws_client_msg(st: &AppState, v: &Value) -> Result<(), String> {
    let ty = v.get("type").and_then(|x| x.as_str()).unwrap_or("");
    match ty {
        "chat" | "message" | "user_message" | "user_input" => {
            let content = v
                .get("content")
                .or_else(|| v.get("text"))
                .or_else(|| v.get("message"))
                .and_then(|x| x.as_str())
                .unwrap_or("")
                .to_string();
            let sid = v
                .get("session_id")
                .and_then(|x| x.as_str())
                .map(|s| s.to_string())
                .or_else(|| st.active_session.read().clone())
                .unwrap_or_default();
            if sid.is_empty() || sid == LOCAL_SESSION_ID {
                return Err("use /api/mobile/local/chat for local sessions".into());
            }
            if content.trim().is_empty() {
                return Err("empty content".into());
            }
            let attachments = v.get("attachments").cloned();
            // One retry: if cached socket died mid-flight, drop + reconnect.
            let mut last_err = String::new();
            for attempt in 0..2 {
                let chat = match ensure_chat(st.clone(), sid.clone()).await {
                    Ok(c) => c,
                    Err(e) => {
                        last_err = e;
                        break;
                    }
                };
                match chat.user_input(&content, None, attachments.as_ref()) {
                    Ok(()) => {
                        // Backbone: finish the turn via HTTP even if PC event WS dies.
                        spawn_turn_completion_watchdog(st.clone(), sid.clone(), content.clone());
                        return Ok(());
                    }
                    Err(e) => {
                        last_err = e.to_string();
                        let dead = last_err.contains("channel closed")
                            || last_err.contains("closed")
                            || last_err.contains("WebSocket");
                        st.chats.remove(&sid);
                        if !dead || attempt == 1 {
                            break;
                        }
                        tracing::info!(%sid, attempt, "chat send failed; retry after reconnect");
                    }
                }
            }
            Err(last_err)
        }
        "stop" | "cancel" | "abort" => {
            let sid = v
                .get("session_id")
                .and_then(|x| x.as_str())
                .map(|s| s.to_string())
                .or_else(|| st.active_session.read().clone())
                .unwrap_or_default();
            if !sid.is_empty() {
                st.flush_session_deltas(&sid);
                if let Some(c) = st.chats.get(&sid) {
                    let _ = c.stop();
                }
            }
            Ok(())
        }
        "ping" => {
            st.broadcast_json(&json!({"type":"pong"}));
            Ok(())
        }
        _ => Ok(()),
    }
}
