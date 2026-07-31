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
}

pub struct IpcBus {
    /// mailbox per process_id
    mailboxes: HashMap<String, VecDeque<IpcMessage>>,
    max_mailbox: usize,
    sent: u64,
    dropped: u64,
    denied: u64,
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
            max_mailbox: max_mailbox.max(1),
            sent: 0,
            dropped: 0,
            denied: 0,
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

    pub fn peek_len(&self, process_id: &str) -> usize {
        self.mailboxes
            .get(process_id)
            .map(|q| q.len())
            .unwrap_or(0)
    }

    pub fn drop_process(&mut self, process_id: &str) {
        self.mailboxes.remove(process_id);
    }

    pub fn status(&self) -> Value {
        json!({
            "mailboxes": self.mailboxes.len(),
            "max_mailbox": self.max_mailbox,
            "sent": self.sent,
            "dropped": self.dropped,
            "denied": self.denied,
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
}
