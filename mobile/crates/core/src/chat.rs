//! WebSocket chat session against Takton `WS /api/ws/{session_id}`.
//!
//! Robustness notes:
//! - Writer and reader both signal Closed/Error so the host cache can drop dead sockets.
//! - Binary frames are accepted (VPS relay historically lost text opcode).
//! - `is_alive()` lets callers avoid reusing a half-dead channel.

use crate::client::TaktonClient;
use crate::error::{Error, Result};
use futures_util::{SinkExt, StreamExt};
use serde_json::{json, Value};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use tokio::sync::{mpsc, oneshot, Mutex};
use tokio_tungstenite::{connect_async, tungstenite::Message};

#[derive(Debug, Clone)]
pub enum ChatEvent {
    Json(Value),
    Closed(String),
    Error(String),
}

pub struct ChatConnection {
    tx: mpsc::UnboundedSender<String>,
    alive: Arc<AtomicBool>,
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
        // Only the first of reader/writer/heartbeat may emit terminal Closed/Error.
        let ended = Arc::new(AtomicBool::new(false));

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

        // reader (clone sender before move — heartbeat also needs a clone)
        let alive_r = alive.clone();
        let ended_r = ended.clone();
        let event_tx = on_event.clone();
        tokio::spawn(async move {
            let mut terminal_sent = false;
            while let Some(item) = stream.next().await {
                match item {
                    Ok(Message::Text(t)) => match serde_json::from_str::<Value>(&t) {
                        Ok(v) => {
                            let _ = event_tx.send(ChatEvent::Json(v));
                        }
                        Err(e) => {
                            let _ = event_tx.send(ChatEvent::Error(e.to_string()));
                        }
                    },
                    // VPS relay may deliver JSON as binary frames.
                    Ok(Message::Binary(b)) => match serde_json::from_slice::<Value>(&b) {
                        Ok(v) => {
                            let _ = event_tx.send(ChatEvent::Json(v));
                        }
                        Err(e) => {
                            let _ = event_tx.send(ChatEvent::Error(format!("binary frame: {e}")));
                        }
                    },
                    Ok(Message::Ping(_)) | Ok(Message::Pong(_)) => {}
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

        // auth + sync (belt+suspenders if query token ignored)
        if let Some(token) = client.token() {
            let _ = out_tx.send(json!({ "type": "auth", "token": token }).to_string());
        }
        let _ = out_tx.send(json!({ "type": "sync" }).to_string());

        // App-level keepalive ping on the write channel. Marks dead only if
        // the local mpsc is closed (writer already gone) — not a half-open probe.
        let alive_h = alive.clone();
        let ended_h = ended.clone();
        let out_tx_h = out_tx.clone();
        let event_h = on_event;
        tokio::spawn(async move {
            let mut interval = tokio::time::interval(std::time::Duration::from_secs(20));
            interval.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Delay);
            // Skip the immediate first tick.
            interval.tick().await;
            loop {
                interval.tick().await;
                if !alive_h.load(Ordering::SeqCst) {
                    break;
                }
                if out_tx_h
                    .send(json!({ "type": "ping" }).to_string())
                    .is_err()
                {
                    alive_h.store(false, Ordering::SeqCst);
                    if !ended_h.swap(true, Ordering::SeqCst) {
                        let _ =
                            event_h.send(ChatEvent::Closed("heartbeat channel closed".into()));
                    }
                    break;
                }
            }
        });

        Ok(Arc::new(Self {
            tx: out_tx,
            alive,
            _close: Mutex::new(Some(close_tx)),
        }))
    }

    /// False when writer/reader ended — cache must reconnect.
    pub fn is_alive(&self) -> bool {
        self.alive.load(Ordering::SeqCst) && !self.tx.is_closed()
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
        self.send_json(json!({ "type": "ping" }))
    }

    pub async fn close(&self) {
        self.alive.store(false, Ordering::SeqCst);
        if let Some(tx) = self._close.lock().await.take() {
            let _ = tx.send(());
        }
    }
}
