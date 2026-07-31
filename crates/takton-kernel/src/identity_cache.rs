//! Identity hot cache in kernel (P1-A F6) — create_process without ORM wait.

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

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct IdentityRecord {
    pub id: String,
    pub name: String,
    pub role: String,
    pub status: String,
    pub capabilities: Option<Vec<String>>,
    pub credit_score: f64,
    pub meta: Value,
    pub updated_at: f64,
}

#[derive(Default)]
pub struct IdentityCache {
    by_id: HashMap<String, IdentityRecord>,
    by_name: HashMap<String, String>,
}

impl IdentityCache {
    pub fn upsert(&mut self, rec: IdentityRecord) {
        self.by_name.insert(rec.name.clone(), rec.id.clone());
        self.by_id.insert(rec.id.clone(), rec);
    }

    pub fn put_json(&mut self, data: &Value) -> Result<IdentityRecord, String> {
        let id = data
            .get("id")
            .and_then(|v| v.as_str())
            .ok_or_else(|| "id required".to_string())?
            .to_string();
        let name = data
            .get("name")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_string();
        if name.is_empty() {
            return Err("name required".into());
        }
        let caps = data.get("capabilities").and_then(|v| {
            if v.is_null() {
                None
            } else {
                v.as_array().map(|a| {
                    a.iter()
                        .filter_map(|x| x.as_str().map(|s| s.to_string()))
                        .collect()
                })
            }
        });
        let rec = IdentityRecord {
            id,
            name,
            role: data
                .get("role")
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .to_string(),
            status: data
                .get("status")
                .and_then(|v| v.as_str())
                .unwrap_or("active")
                .to_string(),
            capabilities: caps,
            credit_score: data
                .get("credit_score")
                .and_then(|v| v.as_f64())
                .unwrap_or(0.0),
            meta: data.get("meta").cloned().unwrap_or(json!({})),
            updated_at: now_secs(),
        };
        self.upsert(rec.clone());
        Ok(rec)
    }

    pub fn get(&self, id_or_name: &str) -> Option<&IdentityRecord> {
        self.by_id
            .get(id_or_name)
            .or_else(|| {
                self.by_name
                    .get(id_or_name)
                    .and_then(|id| self.by_id.get(id))
            })
    }

    pub fn remove(&mut self, id: &str) -> bool {
        if let Some(r) = self.by_id.remove(id) {
            self.by_name.remove(&r.name);
            return true;
        }
        false
    }

    pub fn list(&self) -> Vec<IdentityRecord> {
        self.by_id.values().cloned().collect()
    }

    pub fn status(&self) -> Value {
        json!({
            "count": self.by_id.len(),
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn upsert_get() {
        let mut c = IdentityCache::default();
        c.put_json(&json!({"id":"i1","name":"Alice","capabilities":["file_read"]}))
            .unwrap();
        assert_eq!(c.get("Alice").unwrap().id, "i1");
        assert_eq!(c.get("i1").unwrap().name, "Alice");
    }
}
