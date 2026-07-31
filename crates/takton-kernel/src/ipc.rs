//! Point-to-point Agent IPC (P1-A F1/F2).
//! Messages only pass after capability mediation (ipc_send / ipc_recv).

use std::collections::{HashMap, VecDeque};
use std::time::{SystemTime, UNIX_EPOCH};

use serde::{Deserialize, Serialize};
use serde_json::{json, Value};

fn now_secs() -> f64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs_f64())
        .unwrap_or(0.0)
}

fn short_id() -> String {
    uuid::Uuid::new_v4().simple().to_string()[..12].to_string()
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct IpcMessage {
    pub id: String,
    pub from: String,
    pub to: String,
    pub kind: String,
    pub payload: Value,
    pub ts: f64,
    /// Optional correlation for request/response multi-agent patterns (M-01).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub reply_to: Option<String>,
    /// Optional named channel (pub/sub). Empty = direct mailbox.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub channel: Option<String>,
}

pub struct IpcBus {
    /// mailbox per process_id
    mailboxes: HashMap<String, VecDeque<IpcMessage>>,
    /// channel name -> subscriber process ids
    channels: HashMap<String, Vec<String>>,
    max_mailbox: usize,
    sent: u64,
    dropped: u64,
    denied: u64,
    broadcasts: u64,
}

impl Default for IpcBus {
    fn default() -> Self {
        Self::new(64)
    }
}

impl IpcBus {
    pub fn new(max_mailbox: usize) -> Self {
        Self {
            mailboxes: HashMap::new(),
            channels: HashMap::new(),
            max_mailbox: max_mailbox.max(1),
            sent: 0,
            dropped: 0,
            denied: 0,
            broadcasts: 0,
        }
    }

    pub fn record_denied(&mut self) {
        self.denied = self.denied.saturating_add(1);
    }

    /// Enqueue after auth. Returns Err on backpressure.
    pub fn send(
        &mut self,
        from: &str,
        to: &str,
        kind: &str,
        payload: Value,
    ) -> Result<IpcMessage, String> {
        self.send_ex(from, to, kind, payload, None, None)
    }

    pub fn send_ex(
        &mut self,
        from: &str,
        to: &str,
        kind: &str,
        payload: Value,
        reply_to: Option<String>,
        channel: Option<String>,
    ) -> Result<IpcMessage, String> {
        let q = self.mailboxes.entry(to.to_string()).or_default();
        if q.len() >= self.max_mailbox {
            self.dropped = self.dropped.saturating_add(1);
            return Err(format!(
                "ipc backpressure: mailbox full for {to} (max {})",
                self.max_mailbox
            ));
        }
        let msg = IpcMessage {
            id: short_id(),
            from: from.to_string(),
            to: to.to_string(),
            kind: kind.to_string(),
            payload,
            ts: now_secs(),
            reply_to,
            channel,
        };
        q.push_back(msg.clone());
        self.sent = self.sent.saturating_add(1);
        Ok(msg)
    }

    pub fn recv(&mut self, process_id: &str, max: usize) -> Vec<IpcMessage> {
        let n = max.max(1).min(32);
        let Some(q) = self.mailboxes.get_mut(process_id) else {
            return vec![];
        };
        let mut out = Vec::with_capacity(n);
        for _ in 0..n {
            match q.pop_front() {
                Some(m) => out.push(m),
                None => break,
            }
        }
        out
    }

    /// Subscribe process to a named channel (multi-agent pub/sub).
    pub fn channel_subscribe(&mut self, channel: &str, process_id: &str) -> Value {
        let ch = channel.trim();
        if ch.is_empty() {
            return json!({"ok": false, "error": "empty channel"});
        }
        let subs = self.channels.entry(ch.to_string()).or_default();
        if !subs.iter().any(|s| s == process_id) {
            subs.push(process_id.to_string());
        }
        json!({
            "ok": true,
            "channel": ch,
            "process_id": process_id,
            "subscribers": subs.len(),
        })
    }

    pub fn channel_unsubscribe(&mut self, channel: &str, process_id: &str) -> bool {
        let Some(subs) = self.channels.get_mut(channel) else {
            return false;
        };
        let before = subs.len();
        subs.retain(|s| s != process_id);
        before != subs.len()
    }

    /// Publish to all channel subscribers except sender. Returns delivered count.
    pub fn channel_publish(
        &mut self,
        from: &str,
        channel: &str,
        kind: &str,
        payload: Value,
    ) -> Result<Value, String> {
        let subs = self
            .channels
            .get(channel)
            .cloned()
            .unwrap_or_default();
        if subs.is_empty() {
            return Ok(json!({
                "ok": true,
                "channel": channel,
                "delivered": 0,
                "note": "no subscribers",
            }));
        }
        let mut delivered = 0u32;
        let mut errors = vec![];
        for to in subs {
            if to == from {
                continue;
            }
            match self.send_ex(
                from,
                &to,
                kind,
                payload.clone(),
                None,
                Some(channel.to_string()),
            ) {
                Ok(_) => delivered += 1,
                Err(e) => errors.push(json!({"to": to, "error": e})),
            }
        }
        self.broadcasts = self.broadcasts.saturating_add(1);
        Ok(json!({
            "ok": errors.is_empty(),
            "channel": channel,
            "delivered": delivered,
            "errors": errors,
        }))
    }

    /// Fan-out to all live peer process ids (caller filters terminal).
    pub fn broadcast_to(
        &mut self,
        from: &str,
        peers: &[String],
        kind: &str,
        payload: Value,
    ) -> Value {
        let mut delivered = 0u32;
        let mut errors = vec![];
        for to in peers {
            if to == from {
                continue;
            }
            match self.send(from, to, kind, payload.clone()) {
                Ok(_) => delivered += 1,
                Err(e) => errors.push(json!({"to": to, "error": e})),
            }
        }
        self.broadcasts = self.broadcasts.saturating_add(1);
        json!({
            "ok": errors.is_empty(),
            "delivered": delivered,
            "errors": errors,
        })
    }

    pub fn peek_len(&self, process_id: &str) -> usize {
        self.mailboxes
            .get(process_id)
            .map(|q| q.len())
            .unwrap_or(0)
    }

    pub fn drop_process(&mut self, process_id: &str) {
        self.mailboxes.remove(process_id);
        for subs in self.channels.values_mut() {
            subs.retain(|s| s != process_id);
        }
    }

    pub fn status(&self) -> Value {
        json!({
            "mailboxes": self.mailboxes.len(),
            "channels": self.channels.len(),
            "max_mailbox": self.max_mailbox,
            "sent": self.sent,
            "dropped": self.dropped,
            "denied": self.denied,
            "broadcasts": self.broadcasts,
            "queued": self.mailboxes.values().map(|q| q.len()).sum::<usize>(),
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn send_recv_and_backpressure() {
        let mut b = IpcBus::new(1);
        let m = b
            .send("a", "b", "ping", json!({"x": 1}))
            .unwrap();
        assert_eq!(m.to, "b");
        assert!(b.send("a", "b", "ping", json!({})).is_err());
        let got = b.recv("b", 10);
        assert_eq!(got.len(), 1);
    }

    #[test]
    fn channel_pub_sub() {
        let mut b = IpcBus::new(16);
        b.channel_subscribe("crew", "w1");
        b.channel_subscribe("crew", "w2");
        let r = b
            .channel_publish("boss", "crew", "task", json!({"op": "scan"}))
            .unwrap();
        assert_eq!(r["delivered"], 2);
        assert_eq!(b.recv("w1", 5).len(), 1);
        assert_eq!(b.recv("w2", 5).len(), 1);
    }

    #[test]
    fn reply_to_correlation() {
        let mut b = IpcBus::new(8);
        let req = b
            .send_ex("a", "b", "req", json!({"q": 1}), None, None)
            .unwrap();
        let rep = b
            .send_ex(
                "b",
                "a",
                "resp",
                json!({"ok": true}),
                Some(req.id.clone()),
                None,
            )
            .unwrap();
        assert_eq!(rep.reply_to.as_deref(), Some(req.id.as_str()));
    }
}
