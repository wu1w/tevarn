//! System service framework + Memory / Notify (P1-A F3–F5).

use std::collections::HashMap;
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

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ServicePrivilege {
    System,
    Privileged,
    User,
}

impl ServicePrivilege {
    pub fn parse(s: &str) -> Self {
        match s {
            "system" => Self::System,
            "privileged" => Self::Privileged,
            _ => Self::User,
        }
    }

    pub fn as_str(self) -> &'static str {
        match self {
            Self::System => "system",
            Self::Privileged => "privileged",
            Self::User => "user",
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ServiceRecord {
    pub name: String,
    pub privilege: String,
    pub healthy: bool,
    pub meta: Value,
    pub registered_at: f64,
    pub last_health_at: f64,
}

/// In-memory KV store for Memory system service (Agent never touches SQL).
#[derive(Default)]
pub struct MemoryService {
    /// identity -> key -> value
    store: HashMap<String, HashMap<String, Value>>,
    writes: u64,
    reads: u64,
}

impl MemoryService {
    pub fn put(&mut self, identity: &str, key: &str, value: Value) -> Value {
        self.writes = self.writes.saturating_add(1);
        self.store
            .entry(identity.to_string())
            .or_default()
            .insert(key.to_string(), value.clone());
        json!({"ok": true, "identity": identity, "key": key})
    }

    pub fn get(&mut self, identity: &str, key: &str) -> Value {
        self.reads = self.reads.saturating_add(1);
        match self
            .store
            .get(identity)
            .and_then(|m| m.get(key))
            .cloned()
        {
            Some(v) => json!({"ok": true, "found": true, "value": v}),
            None => json!({"ok": true, "found": false, "value": null}),
        }
    }

    pub fn list_keys(&self, identity: &str) -> Value {
        let keys: Vec<String> = self
            .store
            .get(identity)
            .map(|m| m.keys().cloned().collect())
            .unwrap_or_default();
        json!({"identity": identity, "keys": keys})
    }

    /// Full KV dump for instance export / multi-device migrate.
    pub fn export_map(&self, identity: &str) -> Value {
        match self.store.get(identity) {
            Some(m) => json!(m),
            None => json!({}),
        }
    }

    /// Hydrate from instance bundle memory map (object of key → value).
    pub fn import_map(&mut self, identity: &str, map: &Value) -> usize {
        let Some(obj) = map.as_object() else {
            return 0;
        };
        let mut n = 0usize;
        for (k, v) in obj {
            self.put(identity, k, v.clone());
            n += 1;
        }
        n
    }

    pub fn delete(&mut self, identity: &str, key: &str) -> bool {
        self.store
            .get_mut(identity)
            .map(|m| m.remove(key).is_some())
            .unwrap_or(false)
    }

    pub fn status(&self) -> Value {
        json!({
            "identities": self.store.len(),
            "writes": self.writes,
            "reads": self.reads,
        })
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Notification {
    pub id: String,
    pub process_id: String,
    pub level: String,
    pub title: String,
    pub body: String,
    pub ts: f64,
    pub acked: bool,
}

pub struct NotifyService {
    items: VecDeque<Notification>,
    max: usize,
}

use std::collections::VecDeque;

impl NotifyService {
    pub fn new(max: usize) -> Self {
        Self {
            items: VecDeque::new(),
            max: max.max(16),
        }
    }

    pub fn push(
        &mut self,
        process_id: &str,
        level: &str,
        title: &str,
        body: &str,
    ) -> Notification {
        let n = Notification {
            id: short_id(),
            process_id: process_id.to_string(),
            level: level.to_string(),
            title: title.to_string(),
            body: body.chars().take(2000).collect(),
            ts: now_secs(),
            acked: false,
        };
        self.items.push_back(n.clone());
        while self.items.len() > self.max {
            self.items.pop_front();
        }
        n
    }

    pub fn list(&self, process_id: Option<&str>, limit: usize) -> Vec<Notification> {
        let lim = limit.max(1).min(100);
        self.items
            .iter()
            .rev()
            .filter(|n| process_id.map(|p| n.process_id == p).unwrap_or(true))
            .take(lim)
            .cloned()
            .collect()
    }

    pub fn ack(&mut self, id: &str) -> bool {
        for n in &mut self.items {
            if n.id == id {
                n.acked = true;
                return true;
            }
        }
        false
    }

    pub fn status(&self) -> Value {
        json!({
            "count": self.items.len(),
            "unacked": self.items.iter().filter(|n| !n.acked).count(),
            "max": self.max,
        })
    }
}

impl Default for NotifyService {
    fn default() -> Self {
        Self::new(200)
    }
}

pub struct ServiceSupervisor {
    services: HashMap<String, ServiceRecord>,
    pub memory: MemoryService,
    pub notify: NotifyService,
}

impl Default for ServiceSupervisor {
    fn default() -> Self {
        let mut s = Self {
            services: HashMap::new(),
            memory: MemoryService::default(),
            notify: NotifyService::default(),
        };
        // bootstrap built-ins
        s.register("sys.memory", ServicePrivilege::System, json!({"kind": "memory"}));
        s.register("sys.notify", ServicePrivilege::System, json!({"kind": "notify"}));
        s
    }
}

impl ServiceSupervisor {
    pub fn register(&mut self, name: &str, privilege: ServicePrivilege, meta: Value) -> ServiceRecord {
        let now = now_secs();
        let rec = ServiceRecord {
            name: name.to_string(),
            privilege: privilege.as_str().to_string(),
            healthy: true,
            meta,
            registered_at: now,
            last_health_at: now,
        };
        self.services.insert(name.to_string(), rec.clone());
        rec
    }

    pub fn health_check(&mut self, name: &str, healthy: bool) -> Option<ServiceRecord> {
        let rec = self.services.get_mut(name)?;
        rec.healthy = healthy;
        rec.last_health_at = now_secs();
        Some(rec.clone())
    }

    pub fn get(&self, name: &str) -> Option<&ServiceRecord> {
        self.services.get(name)
    }

    pub fn list(&self) -> Vec<ServiceRecord> {
        self.services.values().cloned().collect()
    }

    pub fn status(&self) -> Value {
        json!({
            "services": self.list(),
            "memory": self.memory.status(),
            "notify": self.notify.status(),
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn memory_and_notify() {
        let mut s = ServiceSupervisor::default();
        s.memory.put("alice", "pref.lang", json!("zh"));
        let g = s.memory.get("alice", "pref.lang");
        assert_eq!(g["found"], true);
        let n = s.notify.push("p1", "info", "hi", "body");
        assert!(!n.id.is_empty());
        assert!(s.list().len() >= 2);
    }
}
