//! WebSocket chat session against Takton `WS /api/ws/{session_id}`.

use crate::client::TaktonClient;
use crate::error::{Error, Result};
use futures_util::{SinkExt, StreamExt};
use serde_json::{json, Value};
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

        // writer
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
        });

        // reader
        let event_tx = on_event.clone();
        tokio::spawn(async move {
            while let Some(item) = stream.next().await {
                match item {
                    Ok(Message::Text(t)) => {
                        match serde_json::from_str::<Value>(&t) {
                            Ok(v) => {
                                let _ = event_tx.send(ChatEvent::Json(v));
                            }
                            Err(e) => {
                                let _ = event_tx.send(ChatEvent::Error(e.to_string()));
                            }
                        }
                    }
                    Ok(Message::Ping(_)) => {}
                    Ok(Message::Close(_)) => {
                        let _ = event_tx.send(ChatEvent::Closed("close frame".into()));
                        break;
                    }
                    Ok(_) => {}
                    Err(e) => {
                        let _ = event_tx.send(ChatEvent::Error(e.to_string()));
                        break;
                    }
                }
            }
            let _ = on_event.send(ChatEvent::Closed("stream end".into()));
        });

        // auth message (belt+suspenders if query token ignored)
        if let Some(token) = client.token() {
            let _ = out_tx.send(json!({ "type": "auth", "token": token }).to_string());
        }
        let _ = out_tx.send(json!({ "type": "sync" }).to_string());

        Ok(Arc::new(Self {
            tx: out_tx,
            _close: Mutex::new(Some(close_tx)),
        }))
    }

    pub fn send_json(&self, v: Value) -> Result<()> {
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
        if let Some(tx) = self._close.lock().await.take() {
            let _ = tx.send(());
        }
    }
}
