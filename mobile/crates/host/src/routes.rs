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
use takton_mobile_core::local_llm::LocalChatMessage;
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
}

#[derive(Debug, Deserialize)]
pub struct LocalChatBody {
    #[serde(default)]
    pub content: Option<String>,
    #[serde(default)]
    pub message: Option<String>,
    #[serde(default)]
    pub reset: Option<bool>,
}

impl LocalChatBody {
    fn text(&self) -> String {
        self.content
            .clone()
            .filter(|s| !s.trim().is_empty())
            .or_else(|| self.message.clone())
            .unwrap_or_default()
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
            .route("/local/test", post(local_test))
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
            let raw: Vec<Value> = msgs
                .into_iter()
                .map(|m| {
                    json!({
                        "id": m.id,
                        "role": m.role,
                        "content": m.text(),
                        "created_at": m.created_at,
                    })
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

async fn ensure_chat(st: AppState, session_id: String) -> Result<Arc<ChatConnection>, String> {
    if session_id.is_empty() || session_id == LOCAL_SESSION_ID {
        return Err("invalid remote session".into());
    }
    if let Some(c) = st.chats.get(&session_id) {
        return Ok(c.clone());
    }
    let (evt_tx, mut evt_rx) = mpsc::unbounded_channel::<ChatEvent>();
    let st_fan = st.clone();
    let sid_fan = session_id.clone();
    tokio::spawn(async move {
        while let Some(ev) = evt_rx.recv().await {
            match ev {
                ChatEvent::Json(v) => {
                    st_fan.broadcast_event_for_session(Some(&sid_fan), &v);
                }
                ChatEvent::Error(e) => {
                    st_fan.flush_session_deltas(&sid_fan);
                    st_fan.broadcast_event_for_session(
                        Some(&sid_fan),
                        &json!({"type":"error","error": e, "session_id": sid_fan}),
                    );
                }
                ChatEvent::Closed(r) => {
                    st_fan.flush_session_deltas(&sid_fan);
                    st_fan.broadcast_event_for_session(
                        Some(&sid_fan),
                        &json!({"type":"closed","reason": r, "session_id": sid_fan}),
                    );
                }
            }
        }
    });
    let conn = ChatConnection::connect(&st.client, &session_id, evt_tx)
        .await
        .map_err(|e| e.to_string())?;
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

async fn try_connect_best(
    st: &AppState,
    candidates: &[String],
    email: &str,
    password: &str,
) -> Result<Value, String> {
    use takton_mobile_core::path::{select_best, Endpoint, EndpointKind};

    let mut extra = candidates.to_vec();
    // Merge stored path profile
    for ep in st.path.candidate_urls(&extra) {
        if !extra.iter().any(|u| u.trim_end_matches('/') == ep.url.trim_end_matches('/')) {
            extra.push(ep.url.clone());
        }
    }
    if extra.is_empty() {
        extra.push(st.client.config().base_url);
    }

    let endpoints: Vec<Endpoint> = extra
        .iter()
        .filter_map(|u| Endpoint::from_url(u, EndpointKind::Unknown))
        .collect();
    let (best, probes) = select_best(&endpoints).await;

    let try_urls: Vec<String> = if let Some(b) = best {
        let mut v = vec![b.url.clone()];
        for e in &endpoints {
            if e.url != b.url {
                v.push(e.url.clone());
            }
        }
        v
    } else {
        endpoints.iter().map(|e| e.url.clone()).collect()
    };

    let mut last_err = "no reachable endpoint".to_string();
    for url in try_urls {
        st.client.set_base_url(url.clone());
        let login = if email.is_empty() {
            st.client.auto_login().await
        } else {
            st.client.login(email, password).await
        };
        match login {
            Ok(s) => {
                *st.remote_probe.write() = Default::default();
                let kind = takton_mobile_core::path::classify_host(
                    &takton_mobile_core::config::parse_base_url_parts(&url).1,
                );
                let _ = st.path.set_active(&url, kind);
                // Refresh dual paths from local mesh knowledge (LAN IP drift).
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
                    "base_url": base_url_of(st),
                    "user_email": s.user.email,
                    "path_kind": kind.as_str(),
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

        let client = reqwest::Client::builder()
            .timeout(std::time::Duration::from_secs(10))
            .build()
            .ok();

        if let Some(http) = client {
            'urls: for claim_url in &claim_url_list {
                for _attempt in 0..4 {
                    let remote = http
                        .post(claim_url)
                        .json(&json!({
                            "pair_id": payload.pair_id,
                            "code": payload.code,
                            "device_name": device_name,
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
                                        device_token = v
                                            .get("token")
                                            .or_else(|| v.pointer("/device/token"))
                                            .and_then(|x| x.as_str())
                                            .map(|s| s.to_string());
                                        claim_result = Some(v);
                                        last_err.clear();
                                        break 'urls;
                                    }
                                    last_err = v
                                        .get("error")
                                        .and_then(|x| x.as_str())
                                        .unwrap_or("claim failed")
                                        .to_string();
                                }
                            } else if let Ok(v) = serde_json::from_str::<Value>(&text) {
                                last_err = v
                                    .get("error")
                                    .and_then(|x| x.as_str())
                                    .unwrap_or("claim failed")
                                    .to_string();
                            } else {
                                last_err = format!("HTTP {status}");
                            }
                            if last_err.contains("waiting") || last_err.contains("confirm") {
                                tokio::time::sleep(std::time::Duration::from_millis(700)).await;
                                continue;
                            }
                            break;
                        }
                        Err(e) => {
                            last_err = e.to_string();
                            break;
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

    // Path select + login across all QR endpoints
    let candidates: Vec<String> = payload.endpoints().into_iter().map(|e| e.url).collect();
    let email = body.email.unwrap_or_default();
    let password = body.password.unwrap_or_default();

    match try_connect_best(&st, &candidates, &email, &password).await {
        Ok(mut v) => {
            if let Some(obj) = v.as_object_mut() {
                obj.insert("device_token".into(), json!(device_token));
                obj.insert("claim".into(), json!(claim_result));
                obj.insert("deferred_claim".into(), json!(deferred));
                obj.insert("pair_id".into(), json!(payload.pair_id));
                obj.insert("mesh".into(), json!(payload.mesh.as_str()));
                obj.insert("endpoints".into(), json!(candidates));
                obj.insert("mesh_join".into(), json!(mesh_join));
                obj.insert("seamless".into(), json!(payload.tsk.as_ref().map(|s| !s.is_empty()).unwrap_or(false)));
            }
            Json(v)
        }
        Err(e) => {
            // Pairing can succeed without immediate login (e.g. scan on 5G, claim deferred)
            Json(json!({
                "ok": true,
                "authenticated": false,
                "base_url": candidates.first().cloned().unwrap_or_else(|| payload.base_url()),
                "mesh": payload.mesh.as_str(),
                "device_token": device_token,
                "claim": claim_result,
                "deferred_claim": deferred,
                "pair_id": payload.pair_id,
                "endpoints": candidates,
                "login_error": e,
                "claim_error": if last_err.is_empty() { Value::Null } else { json!(last_err) },
                "mesh_join": mesh_join,
                "seamless": payload.tsk.as_ref().map(|s| !s.is_empty()).unwrap_or(false),
                "hint": if deferred {
                    "已保存连接信息 · 网络可用后将自动完成"
                } else {
                    "配对信息已记录 · 可稍后自动重连"
                },
                "path": st.path.profile_json(),
            }))
        }
    }
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
        Ok(v) => Json(json!({ "ok": true, "presets": v })),
        Err(e) => err_json(e),
    }
}

async fn oauth_openai_start(State(st): State<AppState>) -> Json<Value> {
    match st.client.openai_oauth_start().await {
        Ok(v) => Json(merge_ok(v)),
        Err(e) => err_json(e),
    }
}

async fn oauth_openai_poll(State(st): State<AppState>, Json(body): Json<Value>) -> Json<Value> {
    let state = body.get("state").and_then(|v| v.as_str());
    match st.client.openai_oauth_poll(state).await {
        Ok(v) => Json(merge_ok(v)),
        Err(e) => err_json(e),
    }
}

async fn oauth_openai_complete(State(st): State<AppState>, Json(body): Json<Value>) -> Json<Value> {
    let callback = body
        .get("callback_url")
        .and_then(|v| v.as_str())
        .unwrap_or("");
    let state = body.get("state").and_then(|v| v.as_str());
    match st.client.openai_oauth_complete(callback, state).await {
        Ok(v) => Json(merge_ok(v)),
        Err(e) => err_json(e),
    }
}

async fn oauth_xai_start(State(st): State<AppState>) -> Json<Value> {
    match st.client.xai_oauth_start().await {
        Ok(v) => Json(merge_ok(v)),
        Err(e) => err_json(e),
    }
}

async fn oauth_xai_poll(State(st): State<AppState>, Json(body): Json<Value>) -> Json<Value> {
    let code = body
        .get("device_code")
        .and_then(|v| v.as_str())
        .unwrap_or("");
    match st.client.xai_oauth_poll(code).await {
        Ok(v) => Json(merge_ok(v)),
        Err(e) => err_json(e),
    }
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
        Ok(v) => Json(json!({ "ok": true, "result": v })),
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
    match st.local_llm.save_profile(&profile) {
        Ok(()) => Json(json!({
            "ok": true,
            "ready": profile.is_ready(),
            "config": profile.masked(),
        })),
        Err(e) => err_json(e),
    }
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
    let reset = body.reset.unwrap_or(false);
    let (tx, rx) = mpsc::unbounded_channel::<Event>();

    tokio::spawn(async move {
        let send = |ev: Event| {
            let _ = tx.send(ev);
        };

        if content.is_empty() {
            send(
                Event::default()
                    .event("error")
                    .data(json!({"error": "empty content"}).to_string()),
            );
            return;
        }

        if reset {
            let _ = st.local_llm.clear_history();
        }

        let profile = st.local_llm.load_profile();
        let mut hist = st.local_llm.load_history();
        hist.messages.push(LocalChatMessage {
            role: "user".into(),
            content: content.clone(),
        });

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
            // skip first immediate tick
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

        let stream_result = st
            .local_llm
            .stream_chat(&profile, &hist.messages, |delta| {
                let _ = raw_tx.send(delta.to_string());
            })
            .await;
        drop(raw_tx);
        let _ = coalesce.await;

        match stream_result {
            Ok(full) => {
                hist.messages.push(LocalChatMessage {
                    role: "assistant".into(),
                    content: full.clone(),
                });
                let _ = st.local_llm.save_history(&hist);
                send(
                    Event::default()
                        .event("done")
                        .data(json!({"content": full}).to_string()),
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
                    if let Some(first) = list.first() {
                        session_id = Some(first.id.clone());
                    }
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
            let chat = ensure_chat(st.clone(), sid.clone()).await?;
            let attachments = v.get("attachments").cloned();
            chat.user_input(&content, None, attachments.as_ref())
                .map_err(|e| e.to_string())?;
            Ok(())
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
