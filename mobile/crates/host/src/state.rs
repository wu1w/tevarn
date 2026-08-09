use axum::extract::ws::Message;
use dashmap::DashMap;
use parking_lot::{Mutex, RwLock};
use serde_json::{json, Value};
use std::collections::VecDeque;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Arc;
use std::time::{Duration, Instant};
use tevarn_mobile_core::chat::ChatConnection;
use tevarn_mobile_core::local_llm::LocalLlmService;
use tevarn_mobile_core::local_agent::LocalAgent;
use tevarn_mobile_core::local_tools::ToolRuntime;
use tevarn_mobile_core::media::MediaStore;
use tevarn_mobile_core::mesh::MeshService;
use tevarn_mobile_core::pair::PairService;
use tevarn_mobile_core::path::PathService;
use tevarn_mobile_core::session_meta::SessionMetaStore;
use tevarn_mobile_core::storage::Store;
use tevarn_mobile_core::{AppConfig, LocalOauth, TevarnClient};

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

/// Global fan-out frames retained for gap fill (control + mixed sessions).
const EVENT_RING_CAP: usize = 512;
/// Per-session ring so one hot session cannot wipe another's gap-fill buffer.
const EVENT_RING_PER_SESSION: usize = 256;
/// Max concurrent session rings (LRU by last_access).
const EVENT_RING_MAX_SESSIONS: usize = 40;

/// Per-session event ring with LRU access tracking.
struct SessionEventRing {
    events: VecDeque<Value>,
    /// Monotonic access counter (higher = more recent).
    last_access: u64,
}

#[derive(Clone)]
pub struct AppState {
    pub client: TevarnClient,
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
    /// phone-local agent (tools + pruned loop)
    pub local_agent: Arc<LocalAgent>,
    /// local media captures (voice / camera)
    pub media: Arc<MediaStore>,
    /// titles + pin order
    pub meta_store: Arc<Store>,
    /// cached remote catalog/runtime probes
    pub remote_probe: Arc<RwLock<RemoteProbeCache>>,
    /// QR pairing (M1)
    pub pair: Arc<PairService>,
    /// Remote access / mesh (M2)
    pub mesh: Arc<MeshService>,
    /// Multi-endpoint path failover (M4)
    pub path: Arc<PathService>,
    /// Phone-local OAuth (no PC required)
    pub local_oauth: Arc<LocalOauth>,
    /// Per-session WS stream delta coalescers
    delta_coalesce: Arc<DashMap<String, Arc<Mutex<DeltaCoalesceInner>>>>,
    /// Monotonic event sequence for Flutter ordered merge / gap detect.
    event_seq: Arc<AtomicU64>,
    /// Global ring (control frames + fallback).
    event_ring: Arc<Mutex<VecDeque<Value>>>,
    /// Per-session rings for targeted after_seq gap fill (LRU eviction).
    event_ring_by_session: Arc<Mutex<std::collections::HashMap<String, SessionEventRing>>>,
    /// Monotonic counter for LRU session ring access.
    event_ring_access_tick: Arc<AtomicU64>,
    /// Per-session connect single-flight (prevents dual PC WS + dual fanout).
    pub chat_connect_locks: Arc<DashMap<String, Arc<tokio::sync::Mutex<()>>>>,
    /// Watchdog generation per session — bump cancels older watchdogs.
    pub watchdog_gen: Arc<DashMap<String, AtomicU64>>,
    /// Unix secs of last successful PC reachability probe (health / authed API).
    /// `pc_connected` requires auth + recent success (not JWT alone).
    pub last_pc_reachable_at: Arc<AtomicU64>,
}

impl AppState {
    pub const DELTA_COALESCE_MS: u64 = 40;

    pub fn new(client: TevarnClient, config: AppConfig) -> anyhow::Result<Self> {
        // Android: ensure writable data dir before opening stores (avoids panic → white screen).
        std::fs::create_dir_all(&config.data_dir).map_err(|e| {
            anyhow::anyhow!("create data_dir {:?}: {e}", config.data_dir)
        })?;
        let store = Store::open(&config.data_dir).map_err(|e| {
            anyhow::anyhow!("open data dir {:?}: {e}", config.data_dir)
        })?;
        let tools = ToolRuntime::new(store.clone());
        let local_llm = Arc::new(LocalLlmService::new(store.clone()));
        let local_agent = LocalAgent::new(local_llm.clone(), tools);
        let media = MediaStore::open(&config.data_dir)
            .map_err(|e| anyhow::anyhow!("open media store: {e}"))?;
        let meta_store =
            Store::open(&config.data_dir).map_err(|e| anyhow::anyhow!("open meta store: {e}"))?;
        let pair_store = Store::open(config.data_dir.join("pair"))
            .map_err(|e| anyhow::anyhow!("open pair store: {e}"))?;
        let mesh_store = Store::open(config.data_dir.join("mesh"))
            .map_err(|e| anyhow::anyhow!("open mesh store: {e}"))?;
        let path_store = Store::open(config.data_dir.join("path"))
            .map_err(|e| anyhow::anyhow!("open path store: {e}"))?;
        let pair = PairService::open(pair_store);
        let backend_port = config.backend_port();
        let mesh = MeshService::open(mesh_store, backend_port);
        let path = PathService::open(path_store);
        let oauth_dir = config.data_dir.join("oauth");
        std::fs::create_dir_all(&oauth_dir).ok();
        let oauth_store = Store::open(&oauth_dir)
            .or_else(|_| Store::open(&config.data_dir))
            .map_err(|e| anyhow::anyhow!("open oauth store: {e}"))?;
        Ok(Self {
            client,
            config: Arc::new(RwLock::new(config)),
            browser_subs: Arc::new(DashMap::new()),
            chats: Arc::new(DashMap::new()),
            active_session: Arc::new(RwLock::new(None)),
            notify_approvals: Arc::new(RwLock::new(true)),
            local_llm,
            local_agent: Arc::new(local_agent),
            media: Arc::new(media),
            meta_store: Arc::new(meta_store),
            remote_probe: Arc::new(RwLock::new(RemoteProbeCache::default())),
            pair: Arc::new(pair),
            mesh: Arc::new(mesh),
            path: Arc::new(path),
            local_oauth: Arc::new(LocalOauth::open(oauth_store)),
            delta_coalesce: Arc::new(DashMap::new()),
            event_seq: Arc::new(AtomicU64::new(0)),
            event_ring: Arc::new(Mutex::new(VecDeque::with_capacity(EVENT_RING_CAP))),
            event_ring_by_session: Arc::new(Mutex::new(std::collections::HashMap::new())),
            event_ring_access_tick: Arc::new(AtomicU64::new(1)),
            chat_connect_locks: Arc::new(DashMap::new()),
            watchdog_gen: Arc::new(DashMap::new()),
            last_pc_reachable_at: Arc::new(AtomicU64::new(0)),
        })
    }

    /// Stamp successful reachability (login, health, catalog probe).
    pub fn mark_pc_reachable(&self) {
        let now = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|d| d.as_secs())
            .unwrap_or(0);
        self.last_pc_reachable_at.store(now, Ordering::SeqCst);
    }

    /// True when JWT exists AND we reached PC within the last 45s.
    /// Prevents "已连接" while tunnel/PC is dead.
    pub fn pc_connected(&self) -> bool {
        if !self.client.is_authenticated() {
            return false;
        }
        let last = self.last_pc_reachable_at.load(Ordering::SeqCst);
        if last == 0 {
            // Just logged in this process but no probe yet — optimistic for 10s
            // after process start is handled by mark on login; if never marked, false.
            return false;
        }
        let now = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|d| d.as_secs())
            .unwrap_or(0);
        now.saturating_sub(last) <= 45
    }

    pub fn new_sub_id(&self) -> String {
        Uuid::new_v4().to_string()
    }

    /// Latest stamped seq (0 if none yet).
    pub fn latest_seq(&self) -> u64 {
        self.event_seq.load(Ordering::SeqCst)
    }

    /// Drop half-dead PC chat sockets (e.g. after path switch / VPS blip).
    /// Explicitly close so old fanout tasks shut down cleanly.
    pub fn prune_dead_chats(&self) -> usize {
        let mut dead: Vec<(String, Arc<ChatConnection>)> = Vec::new();
        for e in self.chats.iter() {
            if !e.value().is_alive() {
                dead.push((e.key().clone(), e.value().clone()));
            }
        }
        for (id, conn) in dead.iter() {
            // Only remove if still the same instance (avoid racing a fresh reconnect).
            let still = self
                .chats
                .get(id)
                .map(|c| c.conn_id() == conn.conn_id())
                .unwrap_or(false);
            if still {
                self.chats.remove(id);
                let c = conn.clone();
                tokio::spawn(async move {
                    c.close().await;
                });
            }
        }
        dead.len()
    }

    /// After path failover, old PC WS is almost certainly wrong path — drop all.
    pub fn drop_all_chats(&self) -> usize {
        let n = self.chats.len();
        let all: Vec<Arc<ChatConnection>> = self.chats.iter().map(|e| e.value().clone()).collect();
        self.chats.clear();
        for c in all {
            tokio::spawn(async move {
                c.close_path().await;
            });
        }
        n
    }

    /// Bump watchdog generation for session; returns the new generation to hold.
    pub fn next_watchdog_gen(&self, session_id: &str) -> u64 {
        let entry = self
            .watchdog_gen
            .entry(session_id.to_string())
            .or_insert_with(|| AtomicU64::new(0));
        entry.fetch_add(1, Ordering::SeqCst) + 1
    }

    pub fn current_watchdog_gen(&self, session_id: &str) -> u64 {
        self.watchdog_gen
            .get(session_id)
            .map(|g| g.load(Ordering::SeqCst))
            .unwrap_or(0)
    }

    /// Events with seq > after_seq (optionally filtered by session_id).
    /// Merges per-session ring ∪ global (unscoped control frames + older session frames
    /// still in the global buffer) so gap-fill is not capped solely at 160.
    pub fn events_after(
        &self,
        after_seq: u64,
        session_id: Option<&str>,
        limit: usize,
    ) -> Vec<Value> {
        let lim = limit.clamp(1, 256);
        let sid = session_id.map(str::trim).filter(|s| !s.is_empty());

        let mut by_seq: std::collections::BTreeMap<u64, Value> = std::collections::BTreeMap::new();

        // Per-session ring first (updates LRU access).
        if let Some(want) = sid {
            let mut by = self.event_ring_by_session.lock();
            if let Some(ring) = by.get_mut(want) {
                ring.last_access = self.event_ring_access_tick.fetch_add(1, Ordering::SeqCst) + 1;
                for v in ring.events.iter() {
                    let seq = v.get("seq").and_then(|x| x.as_u64()).unwrap_or(0);
                    if seq > after_seq {
                        by_seq.entry(seq).or_insert_with(|| v.clone());
                    }
                }
            }
        }

        // Always merge global: deeper history + unscoped control frames.
        {
            let ring = self.event_ring.lock();
            for v in ring.iter() {
                let seq = v.get("seq").and_then(|x| x.as_u64()).unwrap_or(0);
                if seq <= after_seq {
                    continue;
                }
                if let Some(want) = sid {
                    match v.get("session_id").and_then(|x| x.as_str()) {
                        Some(got) if !got.is_empty() => {
                            if got != want {
                                continue;
                            }
                        }
                        // Unscoped control frames (badge, path_status, …) included.
                        _ => {}
                    }
                }
                by_seq.entry(seq).or_insert_with(|| v.clone());
            }
        }

        by_seq.into_values().take(lim).collect()
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
        // Ensure session_id rides on the envelope for ring filter + Flutter merge.
        let mut owned = v.clone();
        if let Some(sid) = session_id.map(str::trim).filter(|s| !s.is_empty()) {
            if let Some(obj) = owned.as_object_mut() {
                // Fill missing or empty session_id from host scope (don't overwrite good ids).
                let need = match obj.get("session_id").and_then(|x| x.as_str()) {
                    None => true,
                    Some(s) => s.trim().is_empty(),
                };
                if need {
                    obj.insert("session_id".into(), json!(sid));
                }
            }
        }
        if is_stream_delta(&owned) {
            self.push_delta(key, &owned);
        } else {
            self.flush_deltas(key);
            self.broadcast_json_raw(&owned);
        }
    }

    /// Immediate fan-out path used by older call sites.
    pub fn broadcast_json(&self, v: &Value) {
        self.broadcast_event(v);
    }

    pub fn flush_session_deltas(&self, session_id: &str) {
        self.flush_deltas(session_id);
    }

    /// Flush every per-session coalesce buffer (path switch / teardown).
    pub fn flush_all_session_deltas(&self) {
        let keys: Vec<String> = self.delta_coalesce.iter().map(|e| e.key().clone()).collect();
        for k in keys {
            self.flush_deltas(&k);
        }
    }

    fn stamp_and_broadcast(&self, v: &Value) {
        // Read active session BEFORE ring locks to avoid lock-order deadlocks.
        let active = self.active_session.read().clone();
        // Hold BOTH ring locks across seq++ + push so concurrent producers cannot
        // reorder ring entries relative to seq numbers.
        let mut global = self.event_ring.lock();
        let mut by_session = self.event_ring_by_session.lock();
        let seq = self.event_seq.fetch_add(1, Ordering::SeqCst) + 1;
        let mut out = v.clone();
        if let Some(obj) = out.as_object_mut() {
            obj.insert("seq".into(), json!(seq));
            // Empty session_id is useless for gap fill — drop it.
            if let Some(sid_v) = obj.get("session_id") {
                if sid_v.as_str().map(|s| s.is_empty()).unwrap_or(false) {
                    obj.remove("session_id");
                }
            }
        } else {
            out = json!({ "type": "wrapped", "payload": v, "seq": seq });
        }
        let sid = out
            .get("session_id")
            .and_then(|x| x.as_str())
            .map(str::trim)
            .filter(|s| !s.is_empty())
            .map(|s| s.to_string());

        global.push_back(out.clone());
        while global.len() > EVENT_RING_CAP {
            global.pop_front();
        }

        if let Some(sid) = sid {
            let tick = self.event_ring_access_tick.fetch_add(1, Ordering::SeqCst) + 1;
            // Protect active session from LRU eviction
            let entry = by_session.entry(sid.clone()).or_insert_with(|| SessionEventRing {
                events: VecDeque::with_capacity(EVENT_RING_PER_SESSION),
                last_access: tick,
            });
            entry.last_access = tick;
            entry.events.push_back(out.clone());
            while entry.events.len() > EVENT_RING_PER_SESSION {
                entry.events.pop_front();
            }
            // Evict cold session rings; never drop the active session.
            if by_session.len() > EVENT_RING_MAX_SESSIONS {
                let active_key = active.clone().unwrap_or_default();
                let mut pairs: Vec<(String, u64)> = by_session
                    .iter()
                    .map(|(k, v)| (k.clone(), v.last_access))
                    .collect();
                pairs.sort_by_key(|(_, acc)| *acc); // oldest first
                let overflow = by_session.len().saturating_sub(EVENT_RING_MAX_SESSIONS);
                let mut removed = 0;
                for (k, _) in pairs {
                    if removed >= overflow {
                        break;
                    }
                    if !active_key.is_empty() && k == active_key {
                        continue; // current session never evicted
                    }
                    by_session.remove(&k);
                    removed += 1;
                }
            }
        }
        let text = out.to_string();
        drop(by_session);
        drop(global);

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

    fn broadcast_json_raw(&self, v: &Value) {
        self.stamp_and_broadcast(v);
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
            // PC may stream *cumulative* snapshots as type=token/stream with content/text
            // (not true incremental deltas). Concatenating those causes 复读
            // ("Hello" + "Hello world" → "HelloHello world"). Merge smartly:
            merge_stream_piece(&mut buf.pending, &piece);
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

    /// Probe Tevarn Rust kernel host (default 127.0.0.1:17890 relative to PC).
    pub fn probe_local_kernel() -> bool {
        use std::net::TcpStream;
        use std::time::Duration;
        let host = std::env::var("TEVARN_KERNEL_HOST").unwrap_or_else(|_| "127.0.0.1:17890".into());
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

    // Full-text snapshots (HTTP watchdog / final replace) must NOT be coalesced:
    // coalesce strips `replace` and would append a full answer as if it were a token.
    if v.get("replace").and_then(|x| x.as_bool()).unwrap_or(false) {
        return false;
    }
    let source = v
        .get("source")
        .and_then(|x| x.as_str())
        .unwrap_or("");
    if source == "http_watchdog" || source == "http_progress" {
        return false;
    }

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
            | "tool_event"
            | "mobile_tool"
            | "mobile_status"
            | "mobile_confirm"
            | "mobile_approval_badge"
            | "approval"
            | "escalation"
            | "status"
            | "assistant"
            | "message"
            | "assistant_message"
            | "final"
            | "complete"
            | "mobile_hello"
            | "run_event"
            | "confirm_request"
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

/// Merge a stream piece into the coalesce buffer without 复读.
///
/// - True incremental token: append.
/// - Cumulative snapshot (`pending` is a prefix of `piece`): replace with longer text.
/// - Duplicate / already-contained fragment: keep existing.
fn merge_stream_piece(pending: &mut String, piece: &str) {
    if piece.is_empty() {
        return;
    }
    if pending.is_empty() {
        pending.push_str(piece);
        return;
    }
    if piece == pending.as_str() {
        return;
    }
    // Cumulative full text (common for type=token with content=full_so_far).
    if piece.starts_with(pending.as_str()) {
        *pending = piece.to_string();
        return;
    }
    // Already absorbed (out-of-order or retransmit of a shorter prefix).
    if pending.starts_with(piece) {
        return;
    }
    // Exact suffix retransmit of last chunk.
    if pending.ends_with(piece) {
        return;
    }
    // True incremental token.
    pending.push_str(piece);
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

    #[test]
    fn rejects_replace_and_watchdog_full_text() {
        assert!(!is_stream_delta(&json!({
            "type": "stream_delta",
            "content": "full final answer",
            "replace": true,
        })));
        assert!(!is_stream_delta(&json!({
            "type": "stream_delta",
            "content": "full final answer",
            "source": "http_watchdog",
        })));
        assert!(!is_stream_delta(&json!({
            "type": "tool_event",
            "name": "shell",
            "phase": "start",
        })));
    }

    #[test]
    fn merge_stream_piece_handles_cumulative_snapshots() {
        let mut p = String::new();
        merge_stream_piece(&mut p, "你");
        merge_stream_piece(&mut p, "你好");
        merge_stream_piece(&mut p, "你好世界");
        assert_eq!(p, "你好世界");
    }

    #[test]
    fn merge_stream_piece_appends_true_tokens() {
        let mut p = String::new();
        merge_stream_piece(&mut p, "你");
        merge_stream_piece(&mut p, "好");
        merge_stream_piece(&mut p, "世界");
        assert_eq!(p, "你好世界");
    }

    #[test]
    fn merge_stream_piece_skips_duplicates() {
        let mut p = String::from("你好");
        merge_stream_piece(&mut p, "你好");
        merge_stream_piece(&mut p, "你");
        assert_eq!(p, "你好");
    }
}
