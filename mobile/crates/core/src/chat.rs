//! WebSocket chat session against Takton `WS /api/ws/{session_id}`.
//!
//! Robustness notes:
//! - Writer and reader both signal Closed/Error so the host cache can drop dead sockets.
//! - Binary frames are accepted (VPS relay historically lost text opcode).
//! - App-level `{"type":"ping"}` → PC replies `{"type":"pong"}` (see backend websocket.py).
//! - Half-open detection: if a ping is not answered by pong within PONG_TIMEOUT_SECS,
//!   the socket is marked dead (true round-trip, not mere local mpsc health).

use crate::client::TaktonClient;
use crate::error::{Error, Result};
use futures_util::{SinkExt, StreamExt};
use serde_json::{json, Value};
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::Arc;
use std::time::{SystemTime, UNIX_EPOCH};
use tokio::sync::{mpsc, oneshot, Mutex};
use tokio_tungstenite::{connect_async, tungstenite::Message};

/// How often we send an app-level ping.
const HEARTBEAT_SECS: u64 = 15;
/// Max time to wait for a pong after sending a ping before declaring half-open.
const PONG_TIMEOUT_SECS: u64 = 20;
/// Absolute inbound silence (any frame) — secondary guard.
const IDLE_DEAD_SECS: u64 = 60;

fn now_secs() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0)
}

fn is_pong(v: &Value) -> bool {
    v.get("type")
        .and_then(|t| t.as_str())
        .map(|s| s.eq_ignore_ascii_case("pong"))
        .unwrap_or(false)
}

#[derive(Debug, Clone)]
pub enum ChatEvent {
    Json(Value),
    Closed(String),
    Error(String),
}

pub struct ChatConnection {
    tx: mpsc::UnboundedSender<String>,
    alive: Arc<AtomicBool>,
    last_rx: Arc<AtomicU64>,
    last_pong: Arc<AtomicU64>,
    /// Unix secs when the outstanding ping was sent; 0 = none awaiting.
    pending_ping_at: Arc<AtomicU64>,
    _close: Mutex<Option<oneshot::Sender<()>>>,
}

impl ChatConnection {
    pub async fn connect(
        client: &TaktonClient,
        session_id: &str,
        on_event: mpsc::UnboundedSender<ChatEvent>,
    ) -> Result<Arc<Self>> {
        let url = client.ws_chat_url(session_id)?;
        let (ws, _) = connect_async(&url)
            .await
            .map_err(|e| Error::Ws(format!("connect {url}: {e}")))?;
        let (mut sink, mut stream) = ws.split();
        let (out_tx, mut out_rx) = mpsc::unbounded_channel::<String>();
        let (close_tx, mut close_rx) = oneshot::channel::<()>();
        let alive = Arc::new(AtomicBool::new(true));
        let ended = Arc::new(AtomicBool::new(false));
        let t0 = now_secs();
        let last_rx = Arc::new(AtomicU64::new(t0));
        let last_pong = Arc::new(AtomicU64::new(t0));
        let pending_ping_at = Arc::new(AtomicU64::new(0));

        // writer
        let alive_w = alive.clone();
        let ended_w = ended.clone();
        let event_w = on_event.clone();
        tokio::spawn(async move {
            loop {
                tokio::select! {
                    msg = out_rx.recv() => {
                        match msg {
                            Some(s) => {
                                if sink.send(Message::Text(s.into())).await.is_err() {
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
                let _ = event_w.send(ChatEvent::Closed("writer end".into()));
            }
        });

        // reader
        let alive_r = alive.clone();
        let ended_r = ended.clone();
        let last_rx_r = last_rx.clone();
        let last_pong_r = last_pong.clone();
        let pending_r = pending_ping_at.clone();
        let event_tx = on_event.clone();
        tokio::spawn(async move {
            let mut terminal_sent = false;
            while let Some(item) = stream.next().await {
                last_rx_r.store(now_secs(), Ordering::SeqCst);
                match item {
                    Ok(Message::Text(t)) => match serde_json::from_str::<Value>(&t) {
                        Ok(v) => {
                            if is_pong(&v) {
                                let n = now_secs();
                                last_pong_r.store(n, Ordering::SeqCst);
                                pending_r.store(0, Ordering::SeqCst);
                                // Do not fan-out pure keepalive pongs to Flutter UI.
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
                            if is_pong(&v) {
                                let n = now_secs();
                                last_pong_r.store(n, Ordering::SeqCst);
                                pending_r.store(0, Ordering::SeqCst);
                                continue;
                            }
                            let _ = event_tx.send(ChatEvent::Json(v));
                        }
                        Err(e) => {
                            let _ = event_tx.send(ChatEvent::Error(format!("binary frame: {e}")));
                        }
                    },
                    // Protocol-level WS ping/pong also counts as liveness.
                    Ok(Message::Ping(_)) => {
                        last_pong_r.store(now_secs(), Ordering::SeqCst);
                        pending_r.store(0, Ordering::SeqCst);
                    }
                    Ok(Message::Pong(_)) => {
                        last_pong_r.store(now_secs(), Ordering::SeqCst);
                        pending_r.store(0, Ordering::SeqCst);
                    }
                    Ok(Message::Close(_)) => {
                        if !ended_r.swap(true, Ordering::SeqCst) {
                            let _ = event_tx.send(ChatEvent::Closed("close frame".into()));
                            terminal_sent = true;
                        }
                        break;
                    }
                    Ok(_) => {}
                    Err(e) => {
                        if !ended_r.swap(true, Ordering::SeqCst) {
                            let _ = event_tx.send(ChatEvent::Error(e.to_string()));
                            terminal_sent = true;
                        }
                        break;
                    }
                }
            }
            alive_r.store(false, Ordering::SeqCst);
            if !terminal_sent && !ended_r.swap(true, Ordering::SeqCst) {
                let _ = event_tx.send(ChatEvent::Closed("stream end".into()));
            }
        });

        if let Some(token) = client.token() {
            let _ = out_tx.send(json!({ "type": "auth", "token": token }).to_string());
        }
        let _ = out_tx.send(json!({ "type": "sync" }).to_string());

        // Heartbeat: true round-trip — ping must be answered by pong.
        let alive_h = alive.clone();
        let ended_h = ended.clone();
        let last_rx_h = last_rx.clone();
        let last_pong_h = last_pong.clone();
        let pending_h = pending_ping_at.clone();
        let out_tx_h = out_tx.clone();
        let event_h = on_event;
        tokio::spawn(async move {
            let mut interval =
                tokio::time::interval(std::time::Duration::from_secs(HEARTBEAT_SECS));
            interval.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Delay);
            interval.tick().await;
            loop {
                interval.tick().await;
                if !alive_h.load(Ordering::SeqCst) {
                    break;
                }
                let now = now_secs();

                // Outstanding ping never got a pong?
                let pending = pending_h.load(Ordering::SeqCst);
                if pending > 0 && now.saturating_sub(pending) > PONG_TIMEOUT_SECS {
                    alive_h.store(false, Ordering::SeqCst);
                    if !ended_h.swap(true, Ordering::SeqCst) {
                        let _ = event_h.send(ChatEvent::Closed(format!(
                            "pong timeout {}s (half-open)",
                            now.saturating_sub(pending)
                        )));
                    }
                    break;
                }

                // Secondary: no inbound traffic at all (including pongs)
                let idle = now.saturating_sub(last_rx_h.load(Ordering::SeqCst));
                if idle > IDLE_DEAD_SECS {
                    alive_h.store(false, Ordering::SeqCst);
                    if !ended_h.swap(true, Ordering::SeqCst) {
                        let _ = event_h.send(ChatEvent::Closed(format!(
                            "idle timeout {idle}s (half-open?)"
                        )));
                    }
                    break;
                }

                // Send next app-level ping (PC responds with type=pong)
                let sent_at = now_secs();
                pending_h.store(sent_at, Ordering::SeqCst);
                if out_tx_h
                    .send(
                        json!({
                            "type": "ping",
                            "ts": sent_at,
                        })
                        .to_string(),
                    )
                    .is_err()
                {
                    alive_h.store(false, Ordering::SeqCst);
                    if !ended_h.swap(true, Ordering::SeqCst) {
                        let _ =
                            event_h.send(ChatEvent::Closed("heartbeat channel closed".into()));
                    }
                    break;
                }
                // Touch last_pong expectation: if last_pong is ancient and we never
                // got any pong after first few pings, next loop catches via pending.
                let _ = last_pong_h.load(Ordering::SeqCst);
            }
        });

        Ok(Arc::new(Self {
            tx: out_tx,
            alive,
            last_rx,
            last_pong,
            pending_ping_at,
            _close: Mutex::new(Some(close_tx)),
        }))
    }

    /// False when closed, write channel dead, pong overdue, or idle too long.
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
        if now.saturating_sub(last_rx) > IDLE_DEAD_SECS + HEARTBEAT_SECS {
            return false;
        }
        true
    }

    pub fn send_json(&self, v: Value) -> Result<()> {
        if !self.is_alive() {
            return Err(Error::Ws("chat channel closed".into()));
        }
        self.tx
            .send(v.to_string())
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
        self.pending_ping_at.store(sent_at, Ordering::SeqCst);
        self.send_json(json!({ "type": "ping", "ts": sent_at }))
    }

    pub async fn close(&self) {
        self.alive.store(false, Ordering::SeqCst);
        if let Some(tx) = self._close.lock().await.take() {
            let _ = tx.send(());
        }
    }
}
