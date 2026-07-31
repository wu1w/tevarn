//! Multi-device Kernel state sync productization (P2 / analysis P1).
//!
//! Protocol: push/pull bundles with content_hash + last-writer-wins by
//! `revision` (monotonic per device) and `updated_at` tie-break.
//! Does not replace instance export/import — wraps it as a sync plane.

use std::collections::HashMap;
use std::time::{SystemTime, UNIX_EPOCH};

use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};

fn now_secs() -> f64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs_f64())
        .unwrap_or(0.0)
}

fn short_id() -> String {
    uuid::Uuid::new_v4().simple().to_string()[..12].to_string()
}

fn hash_json(v: &Value) -> String {
    let s = serde_json::to_string(v).unwrap_or_default();
    let mut h = Sha256::new();
    h.update(s.as_bytes());
    hex::encode(h.finalize())
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct DeviceRecord {
    pub device_id: String,
    pub label: String,
    pub last_seen: f64,
    pub revision: u64,
    pub online: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SyncEnvelope {
    pub sync_id: String,
    pub from_device: String,
    pub identity: String,
    pub revision: u64,
    pub updated_at: f64,
    pub content_hash: String,
    pub payload: Value, // memory + skills + meta + process snapshot optional
    pub direction: String, // push | pull_response
}

pub struct DeviceSyncHub {
    local_device: String,
    devices: HashMap<String, DeviceRecord>,
    /// identity -> latest accepted envelope
    heads: HashMap<String, SyncEnvelope>,
    /// pending outbox for other devices
    outbox: Vec<SyncEnvelope>,
    conflicts: u64,
    accepted: u64,
    rejected: u64,
    local_revision: u64,
}

impl Default for DeviceSyncHub {
    fn default() -> Self {
        let id = format!("dev-{}", &short_id()[..8]);
        let mut devices = HashMap::new();
        devices.insert(
            id.clone(),
            DeviceRecord {
                device_id: id.clone(),
                label: "local".into(),
                last_seen: now_secs(),
                revision: 0,
                online: true,
            },
        );
        Self {
            local_device: id,
            devices,
            heads: HashMap::new(),
            outbox: Vec::new(),
            conflicts: 0,
            accepted: 0,
            rejected: 0,
            local_revision: 0,
        }
    }
}

impl DeviceSyncHub {
    pub fn local_device_id(&self) -> &str {
        &self.local_device
    }

    pub fn set_local_device(&mut self, id: &str, label: &str) {
        if id.is_empty() {
            return;
        }
        self.local_device = id.to_string();
        self.devices.insert(
            id.to_string(),
            DeviceRecord {
                device_id: id.to_string(),
                label: if label.is_empty() {
                    "local".into()
                } else {
                    label.to_string()
                },
                last_seen: now_secs(),
                revision: self.local_revision,
                online: true,
            },
        );
    }

    pub fn register_peer(&mut self, device_id: &str, label: &str) -> DeviceRecord {
        let rec = DeviceRecord {
            device_id: device_id.to_string(),
            label: if label.is_empty() {
                device_id.to_string()
            } else {
                label.to_string()
            },
            last_seen: now_secs(),
            revision: 0,
            online: true,
        };
        self.devices.insert(device_id.to_string(), rec.clone());
        rec
    }

    pub fn list_devices(&self) -> Vec<DeviceRecord> {
        self.devices.values().cloned().collect()
    }

    /// Build push envelope from payload (caller supplies memory/skills).
    pub fn push(
        &mut self,
        identity: &str,
        payload: Value,
        to_device: Option<&str>,
    ) -> SyncEnvelope {
        self.local_revision = self.local_revision.saturating_add(1);
        let content_hash = hash_json(&payload);
        let env = SyncEnvelope {
            sync_id: short_id(),
            from_device: self.local_device.clone(),
            identity: identity.to_string(),
            revision: self.local_revision,
            updated_at: now_secs(),
            content_hash,
            payload,
            direction: "push".into(),
        };
        // update local head if newer
        self.accept_local(&env);
        if let Some(peer) = to_device {
            if peer != self.local_device {
                self.outbox.push(env.clone());
                if let Some(d) = self.devices.get_mut(peer) {
                    d.last_seen = now_secs();
                }
            }
        } else {
            // broadcast outbox copy per peer
            for id in self.devices.keys().cloned().collect::<Vec<_>>() {
                if id != self.local_device {
                    self.outbox.push(env.clone());
                }
            }
        }
        if let Some(d) = self.devices.get_mut(&self.local_device) {
            d.revision = self.local_revision;
            d.last_seen = now_secs();
        }
        env
    }

    fn accept_local(&mut self, env: &SyncEnvelope) {
        let key = env.identity.clone();
        match self.heads.get(&key) {
            None => {
                self.heads.insert(key, env.clone());
                self.accepted += 1;
            }
            Some(cur) => {
                if Self::is_newer(env, cur) {
                    self.heads.insert(key, env.clone());
                    self.accepted += 1;
                }
            }
        }
    }

    fn is_newer(a: &SyncEnvelope, b: &SyncEnvelope) -> bool {
        if a.revision != b.revision {
            return a.revision > b.revision;
        }
        a.updated_at > b.updated_at
    }

    /// Pull head for identity (optionally filter by since_revision).
    pub fn pull(&self, identity: &str, since_revision: Option<u64>) -> Value {
        match self.heads.get(identity) {
            None => json!({
                "ok": true,
                "found": false,
                "identity": identity,
            }),
            Some(env) => {
                if let Some(since) = since_revision {
                    if env.revision <= since {
                        return json!({
                            "ok": true,
                            "found": true,
                            "up_to_date": true,
                            "revision": env.revision,
                            "identity": identity,
                        });
                    }
                }
                json!({
                    "ok": true,
                    "found": true,
                    "up_to_date": false,
                    "envelope": env,
                })
            }
        }
    }

    /// Apply remote envelope with LWW conflict resolution.
    pub fn apply_remote(&mut self, envelope: Value) -> Result<Value, String> {
        let from = envelope
            .get("from_device")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_string();
        let identity = envelope
            .get("identity")
            .and_then(|v| v.as_str())
            .ok_or_else(|| "identity required".to_string())?
            .to_string();
        let revision = envelope
            .get("revision")
            .and_then(|v| v.as_u64())
            .unwrap_or(0);
        let updated_at = envelope
            .get("updated_at")
            .and_then(|v| v.as_f64())
            .unwrap_or_else(now_secs);
        let payload = envelope
            .get("payload")
            .cloned()
            .unwrap_or(json!({}));
        let content_hash = envelope
            .get("content_hash")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_string();
        let expected = hash_json(&payload);
        if !content_hash.is_empty() && content_hash != expected {
            self.rejected += 1;
            return Err("content_hash mismatch".into());
        }
        let env = SyncEnvelope {
            sync_id: envelope
                .get("sync_id")
                .and_then(|v| v.as_str())
                .unwrap_or(&short_id())
                .to_string(),
            from_device: from.clone(),
            identity: identity.clone(),
            revision,
            updated_at,
            content_hash: if content_hash.is_empty() {
                expected
            } else {
                content_hash
            },
            payload,
            direction: "pull_response".into(),
        };
        if !from.is_empty() {
            self.register_peer(&from, "");
        }
        let decision = match self.heads.get(&identity) {
            None => {
                self.heads.insert(identity.clone(), env.clone());
                self.accepted += 1;
                "accepted_new"
            }
            Some(cur) if Self::is_newer(&env, cur) => {
                if cur.from_device != env.from_device && cur.revision == env.revision {
                    self.conflicts += 1;
                }
                self.heads.insert(identity.clone(), env.clone());
                self.accepted += 1;
                "accepted_newer"
            }
            Some(_) => {
                self.rejected += 1;
                "rejected_stale"
            }
        };
        Ok(json!({
            "ok": decision.starts_with("accepted"),
            "decision": decision,
            "identity": identity,
            "revision": revision,
            "head": self.heads.get(&identity),
        }))
    }

    pub fn drain_outbox(&mut self, limit: usize) -> Vec<SyncEnvelope> {
        let n = limit.min(self.outbox.len()).max(0);
        self.outbox.drain(0..n).collect()
    }

    pub fn head(&self, identity: &str) -> Option<&SyncEnvelope> {
        self.heads.get(identity)
    }

    pub fn status(&self) -> Value {
        json!({
            "local_device": self.local_device,
            "devices": self.devices.len(),
            "heads": self.heads.len(),
            "outbox": self.outbox.len(),
            "accepted": self.accepted,
            "rejected": self.rejected,
            "conflicts": self.conflicts,
            "local_revision": self.local_revision,
            "protocol": "lww_revision_ts",
            "productized": true,
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn push_pull_lww() {
        let mut a = DeviceSyncHub::default();
        a.set_local_device("dev-a", "A");
        let env = a.push("alice", json!({"k": 1}), None);
        assert_eq!(env.revision, 1);
        let pull = a.pull("alice", None);
        assert_eq!(pull["found"], true);

        let mut b = DeviceSyncHub::default();
        b.set_local_device("dev-b", "B");
        let r = b
            .apply_remote(serde_json::to_value(&env).unwrap())
            .unwrap();
        assert_eq!(r["ok"], true);

        // stale rejected
        let mut old = env.clone();
        old.revision = 0;
        old.payload = json!({"k": 0});
        old.content_hash = hash_json(&old.payload);
        let r2 = b.apply_remote(serde_json::to_value(&old).unwrap()).unwrap();
        assert_eq!(r2["ok"], false);
    }
}
