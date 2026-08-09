//! WebSocket chat session against Tevarn `WS /api/ws/{session_id}`.
//!
//! Half-open detection (true RTT):
//! - Send app-level `{"type":"ping"}`; PC replies `{"type":"pong"}` (websocket.py).
//! - Do **not** re-arm a new ping while one is outstanding.
//! - If no app pong within PONG_TIMEOUT_SECS → Closed (half-open) + explicit close.
//! - Protocol WS Ping/Pong only updates last_rx (does not clear app pending).
//! - Each connection has a unique `conn_id` so host fanout never removes a newer socket.

use crate::client::TevarnClient;
use crate::error::{Error, Result};
use futures_util::{SinkExt, StreamExt};
use serde_json::{json, Value};
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::Arc;
use std::time::{SystemTime, UNIX_EPOCH};
use tokio::sync::{mpsc, oneshot, Mutex};
use tokio_tungstenite::{connect_async, tungstenite::Message};

const HEARTBEAT_SECS: u64 = 12;
const PONG_TIMEOUT_SECS: u64 = 18;
/// Secondary guard; app ping catches half-open sooner. Long tools may idle on WS.
const IDLE_DEAD_SECS: u64 = 180;

static NEXT_CONN_ID: AtomicU64 = AtomicU64::new(1);

fn now_secs() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0)
}

fn is_app_pong(v: &Value) -> bool {
    v.get("type")
        .and_then(|t| t.as_str())
        .map(|s| s.eq_ignore_ascii_case("pong"))
        .unwrap_or(false)
}

enum OutFrame {
    Text(String),
    Pong(Vec<u8>),
}

#[derive(Debug, Clone)]
pub enum ChatEvent {
    Json(Value),
    Closed(String),
    Error(String),
}

pub struct ChatConnection {
    conn_id: u64,
    tx: mpsc::UnboundedSender<OutFrame>,
    alive: Arc<AtomicBool>,
    last_rx: Arc<AtomicU64>,
    pending_ping_at: Arc<AtomicU64>,
    close_reason: Arc<Mutex<Option<String>>>,
    /// When true, reader/writer terminal reasons are rewritten as path_switch (recoverable).
    path_closing: Arc<AtomicBool>,
    /// Shared with heartbeat so timeout can close the sink.
    close_tx: Arc<Mutex<Option<oneshot::Sender<()>>>>,
}

impl ChatConnection {
    pub fn conn_id(&self) -> u64 {
        self.conn_id
    }

    pub async fn connect(
        client: &TevarnClient,
        session_id: &str,
        on_event: mpsc::UnboundedSender<ChatEvent>,
    ) -> Result<Arc<Self>> {
        let url = client.ws_chat_url(session_id)?;
        let (ws, _) = connect_async(&url)
            .await
            .map_err(|e| Error::Ws(format!("connect {url}: {e}")))?;
        let (mut sink, mut stream) = ws.split();
        let (out_tx, mut out_rx) = mpsc::unbounded_channel::<OutFrame>();
        let (close_tx_raw, mut close_rx) = oneshot::channel::<()>();
        let alive = Arc::new(AtomicBool::new(true));
        let ended = Arc::new(AtomicBool::new(false));
        let t0 = now_secs();
        let last_rx = Arc::new(AtomicU64::new(t0));
        let pending_ping_at = Arc::new(AtomicU64::new(0));
        let close_reason = Arc::new(Mutex::new(None));
        let path_closing = Arc::new(AtomicBool::new(false));
        let close_tx = Arc::new(Mutex::new(Some(close_tx_raw)));
        let conn_id = NEXT_CONN_ID.fetch_add(1, Ordering::SeqCst);

        let alive_w = alive.clone();
        let ended_w = ended.clone();
        let event_w = on_event.clone();
        let close_reason_w = close_reason.clone();
        let path_closing_w = path_closing.clone();
        tokio::spawn(async move {
            loop {
                tokio::select! {
                    msg = out_rx.recv() => {
                        match msg {
                            Some(OutFrame::Text(s)) => {
                                if sink.send(Message::Text(s.into())).await.is_err() {
                                    break;
                                }
                            }
                            Some(OutFrame::Pong(p)) => {
                                if sink.send(Message::Pong(p.into())).await.is_err() {
                                    break;
                                }
                            }
                            None => break,
                        }
                    }
                    _ = &mut close_rx => {
                        let _ = sink.close().await;
                        break;
                    }
                }
            }
            alive_w.store(false, Ordering::SeqCst);
            if !ended_w.swap(true, Ordering::SeqCst) {
                let reason = if path_closing_w.load(Ordering::SeqCst) {
                    "path_switch".into()
                } else {
                    close_reason_w
                        .lock()
                        .await
                        .take()
                        .unwrap_or_else(|| "writer end".into())
                };
                let _ = event_w.send(ChatEvent::Closed(reason));
            }
        });

        let alive_r = alive.clone();
        let ended_r = ended.clone();
        let last_rx_r = last_rx.clone();
        let pending_r = pending_ping_at.clone();
        let event_tx = on_event.clone();
        let out_tx_r = out_tx.clone();
        let path_closing_r = path_closing.clone();
        tokio::spawn(async move {
            let mut terminal_sent = false;
            let terminal_reason = |default: &str| -> String {
                if path_closing_r.load(Ordering::SeqCst) {
                    "path_switch".into()
                } else {
                    default.into()
                }
            };
            while let Some(item) = stream.next().await {
                last_rx_r.store(now_secs(), Ordering::SeqCst);
                match item {
                    Ok(Message::Text(t)) => match serde_json::from_str::<Value>(&t) {
                        Ok(v) => {
                            if is_app_pong(&v) {
                                pending_r.store(0, Ordering::SeqCst);
                                continue;
                            }
                            let _ = event_tx.send(ChatEvent::Json(v));
                        }
                        Err(e) => {
                            let _ = event_tx.send(ChatEvent::Error(e.to_string()));
                        }
                    },
                    Ok(Message::Binary(b)) => match serde_json::from_slice::<Value>(&b) {
                        Ok(v) => {
                            if is_app_pong(&v) {
                                pending_r.store(0, Ordering::SeqCst);
                                continue;
                            }
                            let _ = event_tx.send(ChatEvent::Json(v));
                        }
                        Err(e) => {
                            let _ = event_tx.send(ChatEvent::Error(format!("binary frame: {e}")));
                        }
                    },
                    Ok(Message::Ping(p)) => {
                        let _ = out_tx_r.send(OutFrame::Pong(p.to_vec()));
                    }
                    Ok(Message::Pong(_)) => {}
                    Ok(Message::Close(_)) => {
                        if !ended_r.swap(true, Ordering::SeqCst) {
                            let _ = event_tx
                                .send(ChatEvent::Closed(terminal_reason("close frame")));
                            terminal_sent = true;
                        }
                        break;
                    }
                    Ok(_) => {}
                    Err(e) => {
                        if !ended_r.swap(true, Ordering::SeqCst) {
                            if path_closing_r.load(Ordering::SeqCst) {
                                let _ = event_tx.send(ChatEvent::Closed("path_switch".into()));
                            } else {
                                let _ = event_tx.send(ChatEvent::Error(e.to_string()));
                            }
                            terminal_sent = true;
                        }
                        break;
                    }
                }
            }
            alive_r.store(false, Ordering::SeqCst);
            if !terminal_sent && !ended_r.swap(true, Ordering::SeqCst) {
                let _ = event_tx.send(ChatEvent::Closed(terminal_reason("stream end")));
            }
        });

        if let Some(token) = client.token() {
            let _ = out_tx.send(OutFrame::Text(
                json!({ "type": "auth", "token": token }).to_string(),
            ));
        }
        let _ = out_tx.send(OutFrame::Text(json!({ "type": "sync" }).to_string()));

        let alive_h = alive.clone();
        let ended_h = ended.clone();
        let last_rx_h = last_rx.clone();
        let pending_h = pending_ping_at.clone();
        let out_tx_h = out_tx.clone();
        let event_h = on_event;
        let close_tx_h = close_tx.clone();
        tokio::spawn(async move {
            let mut interval = tokio::time::interval(std::time::Duration::from_secs(3));
            interval.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Delay);
            interval.tick().await;
            let mut last_ping_sent: u64 = now_secs();
            loop {
                interval.tick().await;
                if !alive_h.load(Ordering::SeqCst) {
                    break;
                }
                let now = now_secs();

                let pending = pending_h.load(Ordering::SeqCst);
                if pending > 0 && now.saturating_sub(pending) > PONG_TIMEOUT_SECS {
                    alive_h.store(false, Ordering::SeqCst);
                    // Prefer closing sink; writer emits Closed with reason if we set it first.
                    // Emit Closed here only if writer never runs (ended swap wins once).
                    if !ended_h.swap(true, Ordering::SeqCst) {
                        let _ = event_h.send(ChatEvent::Closed(format!(
                            "pong timeout {}s (half-open)",
                            now.saturating_sub(pending)
                        )));
                    }
                    if let Some(tx) = close_tx_h.lock().await.take() {
                        let _ = tx.send(());
                    }
                    break;
                }

                let idle = now.saturating_sub(last_rx_h.load(Ordering::SeqCst));
                if idle > IDLE_DEAD_SECS {
                    alive_h.store(false, Ordering::SeqCst);
                    if !ended_h.swap(true, Ordering::SeqCst) {
                        let _ = event_h.send(ChatEvent::Closed(format!(
                            "idle timeout {idle}s (half-open?)"
                        )));
                    }
                    if let Some(tx) = close_tx_h.lock().await.take() {
                        let _ = tx.send(());
                    }
                    break;
                }

                if pending == 0 && now.saturating_sub(last_ping_sent) >= HEARTBEAT_SECS {
                    let sent_at = now_secs();
                    if pending_h
                        .compare_exchange(0, sent_at, Ordering::SeqCst, Ordering::SeqCst)
                        .is_ok()
                    {
                        last_ping_sent = sent_at;
                        if out_tx_h
                            .send(OutFrame::Text(
                                json!({ "type": "ping", "ts": sent_at }).to_string(),
                            ))
                            .is_err()
                        {
                            pending_h.store(0, Ordering::SeqCst);
                            alive_h.store(false, Ordering::SeqCst);
                            if !ended_h.swap(true, Ordering::SeqCst) {
                                let _ = event_h
                                    .send(ChatEvent::Closed("heartbeat channel closed".into()));
                            }
                            break;
                        }
                    }
                }
            }
        });

        Ok(Arc::new(Self {
            conn_id,
            tx: out_tx,
            alive,
            last_rx,
            pending_ping_at,
            close_reason,
            path_closing,
            close_tx,
        }))
    }

    pub fn is_alive(&self) -> bool {
        if !self.alive.load(Ordering::SeqCst) || self.tx.is_closed() {
            return false;
        }
        let now = now_secs();
        let pending = self.pending_ping_at.load(Ordering::SeqCst);
        if pending > 0 && now.saturating_sub(pending) > PONG_TIMEOUT_SECS {
            return false;
        }
        let last_rx = self.last_rx.load(Ordering::SeqCst);
        if now.saturating_sub(last_rx) > IDLE_DEAD_SECS {
            return false;
        }
        true
    }

    pub fn send_json(&self, v: Value) -> Result<()> {
        if !self.is_alive() {
            return Err(Error::Ws("chat channel closed".into()));
        }
        self.tx
            .send(OutFrame::Text(v.to_string()))
            .map_err(|_| Error::Ws("chat channel closed".into()))
    }

    pub fn user_input(
        &self,
        content: &str,
        mode: Option<&str>,
        attachments: Option<&Value>,
    ) -> Result<()> {
        let atts = attachments.cloned().unwrap_or_else(|| json!([]));
        self.send_json(json!({
            "type": "user_input",
            "content": content,
            "attachments": atts,
            "mode": mode.unwrap_or("default"),
            "sub_agent_ids": [],
        }))
    }

    pub fn stop(&self) -> Result<()> {
        self.send_json(json!({ "type": "stop" }))
    }

    pub fn confirm(&self, confirm_id: &str, approved: bool, scope: &str) -> Result<()> {
        self.send_json(json!({
            "type": "confirm_response",
            "confirm_id": confirm_id,
            "approved": approved,
            "scope": scope,
        }))
    }

    pub fn ping(&self) -> Result<()> {
        let sent_at = now_secs();
        if self
            .pending_ping_at
            .compare_exchange(0, sent_at, Ordering::SeqCst, Ordering::SeqCst)
            .is_err()
        {
            return Ok(());
        }
        match self.send_json(json!({ "type": "ping", "ts": sent_at })) {
            Ok(()) => Ok(()),
            Err(e) => {
                self.pending_ping_at.store(0, Ordering::SeqCst);
                Err(e)
            }
        }
    }

    pub async fn close(&self) {
        self.alive.store(false, Ordering::SeqCst);
        if let Some(tx) = self.close_tx.lock().await.take() {
            let _ = tx.send(());
        }
    }

    /// Path switch — any terminal reason becomes path_switch (recoverable for fanout).
    pub async fn close_path(&self) {
        self.path_closing.store(true, Ordering::SeqCst);
        {
            let mut g = self.close_reason.lock().await;
            *g = Some("path_switch".into());
        }
        self.alive.store(false, Ordering::SeqCst);
        if let Some(tx) = self.close_tx.lock().await.take() {
            let _ = tx.send(());
        }
    }
}
