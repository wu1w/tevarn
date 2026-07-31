//! Multi-device Agent Instance export / import (P2 I4).

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
pub struct AgentInstanceBundle {
    pub id: String,
    pub device_id: String,
    pub identity: String,
    pub process_snapshot: Option<Value>,
    pub capabilities: Option<Vec<String>>,
    pub memory: Value,
    pub skills: Value,
    pub meta: Value,
    pub created_at: f64,
    pub content_hash: String,
}

pub struct InstanceRegistry {
    bundles: HashMap<String, AgentInstanceBundle>,
    device_id: String,
}

impl Default for InstanceRegistry {
    fn default() -> Self {
        Self {
            bundles: HashMap::new(),
            device_id: format!("dev-{}", &short_id()[..8]),
        }
    }
}

impl InstanceRegistry {
    pub fn device_id(&self) -> &str {
        &self.device_id
    }

    pub fn set_device_id(&mut self, id: &str) {
        if !id.is_empty() {
            self.device_id = id.to_string();
        }
    }

    pub fn export_bundle(
        &mut self,
        identity: &str,
        process_snapshot: Option<Value>,
        capabilities: Option<Vec<String>>,
        memory: Value,
        skills: Value,
        meta: Value,
    ) -> AgentInstanceBundle {
        let mut raw = json!({
            "identity": identity,
            "process_snapshot": process_snapshot,
            "capabilities": capabilities,
            "memory": memory,
            "skills": skills,
            "meta": meta,
            "device_id": self.device_id,
        });
        let content_hash = hash_json(&raw);
        raw["content_hash"] = json!(content_hash);
        let b = AgentInstanceBundle {
            id: short_id(),
            device_id: self.device_id.clone(),
            identity: identity.to_string(),
            process_snapshot,
            capabilities,
            memory,
            skills,
            meta,
            created_at: now_secs(),
            content_hash,
        };
        self.bundles.insert(b.id.clone(), b.clone());
        b
    }

    pub fn import_bundle(&mut self, bundle: Value) -> Result<AgentInstanceBundle, String> {
        let identity = bundle
            .get("identity")
            .and_then(|v| v.as_str())
            .ok_or_else(|| "identity required".to_string())?
            .to_string();
        let content_hash = bundle
            .get("content_hash")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_string();
        // recompute integrity over core fields
        let check = json!({
            "identity": identity,
            "process_snapshot": bundle.get("process_snapshot"),
            "capabilities": bundle.get("capabilities"),
            "memory": bundle.get("memory").cloned().unwrap_or(json!({})),
            "skills": bundle.get("skills").cloned().unwrap_or(json!({})),
            "meta": bundle.get("meta").cloned().unwrap_or(json!({})),
            "device_id": bundle.get("device_id").and_then(|v| v.as_str()).unwrap_or(""),
        });
        let expected = hash_json(&check);
        if !content_hash.is_empty() && content_hash != expected {
            // soft warn: still import with flag
        }
        let b = AgentInstanceBundle {
            id: short_id(),
            device_id: self.device_id.clone(),
            identity,
            process_snapshot: bundle.get("process_snapshot").cloned(),
            capabilities: bundle.get("capabilities").and_then(|v| {
                v.as_array().map(|a| {
                    a.iter()
                        .filter_map(|x| x.as_str().map(|s| s.to_string()))
                        .collect()
                })
            }),
            memory: bundle.get("memory").cloned().unwrap_or(json!({})),
            skills: bundle.get("skills").cloned().unwrap_or(json!({})),
            meta: {
                let mut m = bundle.get("meta").cloned().unwrap_or(json!({}));
                if let Some(obj) = m.as_object_mut() {
                    obj.insert("imported_from".into(), bundle.get("device_id").cloned().unwrap_or(json!("unknown")));
                    obj.insert("integrity_ok".into(), json!(content_hash.is_empty() || content_hash == expected));
                }
                m
            },
            created_at: now_secs(),
            content_hash: expected,
        };
        self.bundles.insert(b.id.clone(), b.clone());
        Ok(b)
    }

    pub fn get(&self, id: &str) -> Option<&AgentInstanceBundle> {
        self.bundles.get(id)
    }

    pub fn list(&self) -> Vec<AgentInstanceBundle> {
        self.bundles.values().cloned().collect()
    }

    pub fn status(&self) -> Value {
        json!({
            "device_id": self.device_id,
            "bundles": self.bundles.len(),
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn export_import() {
        let mut r = InstanceRegistry::default();
        let b = r.export_bundle(
            "alice",
            None,
            Some(vec!["file_read".into()]),
            json!({"k": 1}),
            json!([]),
            json!({}),
        );
        let v = serde_json::to_value(&b).unwrap();
        let imp = r.import_bundle(v).unwrap();
        assert_eq!(imp.identity, "alice");
    }
}
