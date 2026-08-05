use axum::extract::ws::Message;
use dashmap::DashMap;
use parking_lot::{Mutex, RwLock};
use serde_json::{json, Value};
use std::sync::Arc;
use std::time::{Duration, Instant};
use takton_mobile_core::chat::ChatConnection;
use takton_mobile_core::local_llm::LocalLlmService;
use takton_mobile_core::media::MediaStore;
use takton_mobile_core::session_meta::SessionMetaStore;
use takton_mobile_core::storage::Store;
use takton_mobile_core::{AppConfig, TaktonClient};
use tokio::sync::mpsc;
use uuid::Uuid;

/// Short-lived cache for expensive remote probes (catalog / runtime).
#[derive(Debug, Clone, Default)]
pub struct RemoteProbeCache {
    pub pc_model: String,
    pub kernel_ready: bool,
    pub at: Option<Instant>,
}

impl RemoteProbeCache {
    pub const TTL: Duration = Duration::from_secs(8);

    pub fn fresh(&self) -> bool {
        self.at
            .map(|t| t.elapsed() < Self::TTL)
            .unwrap_or(false)
    }
}

/// Per-session coalesce buffer (never mix two sessions' tokens).
#[derive(Default)]
struct DeltaCoalesceInner {
    pending: String,
    /// Field name that carried the text: "delta" | "content" | "text"
    field: String,
    /// Extra envelope fields from first delta of a batch
    template: Option<Value>,
    flush_scheduled: bool,
}

const COALESCE_FALLBACK_KEY: &str = "__global__";

#[derive(Clone)]
pub struct AppState {
    pub client: TaktonClient,
    pub config: Arc<RwLock<AppConfig>>,
    /// browser_ws_id → fanout of chat events
    pub browser_subs: Arc<DashMap<String, mpsc::UnboundedSender<Message>>>,
    /// active backend chat per session
    pub chats: Arc<DashMap<String, Arc<ChatConnection>>>,
    /// session currently bound for mobile shell
    pub active_session: Arc<RwLock<Option<String>>>,
    /// notify preference
    pub notify_approvals: Arc<RwLock<bool>>,
    /// local LLM profile for offline direct chat
    pub local_llm: Arc<LocalLlmService>,
    /// local media captures (voice / camera)
    pub media: Arc<MediaStore>,
    /// titles + pin order
    pub meta_store: Arc<Store>,
    /// cached remote catalog/runtime probes
    pub remote_probe: Arc<RwLock<RemoteProbeCache>>,
    /// Per-session WS stream delta coalescers
    delta_coalesce: Arc<DashMap<String, Arc<Mutex<DeltaCoalesceInner>>>>,
}

impl AppState {
    pub const DELTA_COALESCE_MS: u64 = 40;

    pub fn new(client: TaktonClient, config: AppConfig) -> Self {
        let store = takton_mobile_core::storage::Store::open(&config.data_dir)
            .expect("open data dir");
        let local_llm = LocalLlmService::new(store);
        let media = MediaStore::open(&config.data_dir).expect("open media store");
        let meta_store = Store::open(&config.data_dir).expect("open meta store");
        Self {
            client,
            config: Arc::new(RwLock::new(config)),
            browser_subs: Arc::new(DashMap::new()),
            chats: Arc::new(DashMap::new()),
            active_session: Arc::new(RwLock::new(None)),
            notify_approvals: Arc::new(RwLock::new(true)),
            local_llm: Arc::new(local_llm),
            media: Arc::new(media),
            meta_store: Arc::new(meta_store),
            remote_probe: Arc::new(RwLock::new(RemoteProbeCache::default())),
            delta_coalesce: Arc::new(DashMap::new()),
        }
    }

    pub fn new_sub_id(&self) -> String {
        Uuid::new_v4().to_string()
    }

    /// Fan-out without session affinity (control / unknown).
    pub fn broadcast_event(&self, v: &Value) {
        self.broadcast_event_for_session(None, v);
    }

    /// Fan-out a chat event scoped to a session: deltas coalesced ~40ms per session.
    pub fn broadcast_event_for_session(&self, session_id: Option<&str>, v: &Value) {
        let key = session_id
            .map(str::trim)
            .filter(|s| !s.is_empty())
            .unwrap_or(COALESCE_FALLBACK_KEY);
        if is_stream_delta(v) {
            self.push_delta(key, v);
        } else {
            self.flush_deltas(key);
            self.broadcast_json_raw(v);
        }
    }

    /// Immediate fan-out path used by older call sites.
    pub fn broadcast_json(&self, v: &Value) {
        self.broadcast_event(v);
    }

    pub fn flush_session_deltas(&self, session_id: &str) {
        self.flush_deltas(session_id);
    }

    fn broadcast_json_raw(&self, v: &Value) {
        let text = v.to_string();
        let mut dead = Vec::new();
        for entry in self.browser_subs.iter() {
            if entry
                .value()
                .send(Message::Text(text.clone().into()))
                .is_err()
            {
                dead.push(entry.key().clone());
            }
        }
        for id in dead {
            self.browser_subs.remove(&id);
        }
    }

    fn buf_for(&self, session_key: &str) -> Arc<Mutex<DeltaCoalesceInner>> {
        self.delta_coalesce
            .entry(session_key.to_string())
            .or_insert_with(|| Arc::new(Mutex::new(DeltaCoalesceInner::default())))
            .clone()
    }

    fn push_delta(&self, session_key: &str, v: &Value) {
        let (field, piece) = extract_delta_piece(v);
        if piece.is_empty() {
            return;
        }
        let schedule;
        {
            let slot = self.buf_for(session_key);
            let mut buf = slot.lock();
            if buf.template.is_none() {
                buf.template = Some(v.clone());
                buf.field = field;
            }
            buf.pending.push_str(&piece);
            schedule = !buf.flush_scheduled;
            if schedule {
                buf.flush_scheduled = true;
            }
        }
        if schedule {
            let st = self.clone();
            let key = session_key.to_string();
            tokio::spawn(async move {
                tokio::time::sleep(Duration::from_millis(Self::DELTA_COALESCE_MS)).await;
                st.flush_deltas(&key);
            });
        }
    }

    fn flush_deltas(&self, session_key: &str) {
        let slot = match self.delta_coalesce.get(session_key) {
            Some(s) => s.clone(),
            None => return,
        };
        let (text, field, template) = {
            let mut buf = slot.lock();
            buf.flush_scheduled = false;
            if buf.pending.is_empty() {
                buf.template = None;
                return;
            }
            (
                std::mem::take(&mut buf.pending),
                std::mem::take(&mut buf.field),
                buf.template.take(),
            )
        };
        let mut out = template.unwrap_or_else(|| json!({"type": "delta"}));
        let key = if field.is_empty() { "delta" } else { field.as_str() };
        if let Some(obj) = out.as_object_mut() {
            // Only keep the active text carrier so client doesn't double-read.
            for alt in ["delta", "content", "text"] {
                if alt != key {
                    obj.remove(alt);
                }
            }
            obj.insert(key.into(), json!(text));
            obj.insert("coalesced".into(), json!(true));
            obj.insert("coalesce_ms".into(), json!(Self::DELTA_COALESCE_MS));
            if session_key != COALESCE_FALLBACK_KEY {
                obj.entry("session_id")
                    .or_insert_with(|| json!(session_key));
            }
        }
        self.broadcast_json_raw(&out);
    }

    /// Probe Takton Rust kernel host (default 127.0.0.1:17890 relative to PC).
    pub fn probe_local_kernel() -> bool {
        use std::net::TcpStream;
        use std::time::Duration;
        let host = std::env::var("TAKTON_KERNEL_HOST").unwrap_or_else(|_| "127.0.0.1:17890".into());
        let addr = if host.contains(':') {
            host
        } else {
            format!("{host}:17890")
        };
        TcpStream::connect_timeout(
            &addr.parse().unwrap_or_else(|_| "127.0.0.1:17890".parse().unwrap()),
            Duration::from_millis(250),
        )
        .is_ok()
    }
}

/// Only true for **incremental** token packages — never full assistant messages.
fn is_stream_delta(v: &Value) -> bool {
    let ty = v
        .get("type")
        .and_then(|t| t.as_str())
        .unwrap_or("")
        .to_ascii_lowercase();

    // Control / tool / full-message envelopes: always flush + pass through.
    if matches!(
        ty.as_str(),
        "error"
            | "done"
            | "chat_done"
            | "closed"
            | "pong"
            | "ping"
            | "connected"
            | "session"
            | "tool"
            | "tool_call"
            | "tool_result"
            | "approval"
            | "escalation"
            | "status"
            | "assistant"
            | "message"
            | "assistant_message"
            | "final"
            | "complete"
            | "mobile_hello"
    ) {
        return false;
    }

    // Explicit delta field is the strongest signal (token stream).
    if let Some(s) = v.get("delta").and_then(|x| x.as_str()) {
        return !s.is_empty();
    }

    // Named incremental types may carry text/content instead of delta.
    if ty.contains("delta") || ty.contains("chunk") || ty == "token" || ty == "stream" {
        let (_, piece) = extract_delta_piece(v);
        return !piece.is_empty();
    }

    // Do NOT coalesce bare content/text without delta — those are often full turns.
    false
}

fn extract_delta_piece(v: &Value) -> (String, String) {
    // Prefer explicit delta field first.
    for key in ["delta", "content", "text"] {
        if let Some(s) = v.get(key).and_then(|x| x.as_str()) {
            if !s.is_empty() {
                return (key.to_string(), s.to_string());
            }
        }
    }
    (String::new(), String::new())
}

impl AppState {
    pub fn load_meta(&self) -> SessionMetaStore {
        SessionMetaStore::load(&self.meta_store).unwrap_or_default()
    }

    pub fn save_meta(&self, meta: &SessionMetaStore) {
        let _ = meta.save(&self.meta_store);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn rejects_full_assistant_messages() {
        assert!(!is_stream_delta(&json!({"type":"assistant","content":"hello world"})));
        assert!(!is_stream_delta(&json!({"type":"message","text":"full"})));
        assert!(!is_stream_delta(&json!({"type":"done","content":"x"})));
    }

    #[test]
    fn accepts_explicit_deltas() {
        assert!(is_stream_delta(&json!({"delta":"hel"})));
        assert!(is_stream_delta(&json!({"type":"delta","delta":"lo"})));
        assert!(is_stream_delta(&json!({"type":"assistant_delta","content":"x"})));
        assert!(is_stream_delta(&json!({"type":"token","text":"y"})));
    }

    #[test]
    fn rejects_bare_content_without_delta_type() {
        assert!(!is_stream_delta(&json!({"content":"full reply"})));
        assert!(!is_stream_delta(&json!({"text":"full reply"})));
    }
}
