//! Hash-chained kernel events (SHA-256).

use std::fs::{self, File, OpenOptions};
use std::io::{BufRead, BufReader, Write};
use std::path::{Path, PathBuf};
use std::sync::Mutex;
use std::time::{SystemTime, UNIX_EPOCH};

use serde::{Deserialize, Serialize};
use serde_json::Value;
use sha2::{Digest, Sha256};

pub const GENESIS_HASH: &str = "0000000000000000000000000000000000000000000000000000000000000000";
pub const EVENT_BUFFER_MAX: usize = 5000;

fn now_secs() -> f64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs_f64())
        .unwrap_or(0.0)
}

fn short_id() -> String {
    uuid::Uuid::new_v4().simple().to_string()[..16].to_string()
}

/// Match Python `_event_hash`: json.dumps(sort_keys=True, ensure_ascii=False, default=str).
pub fn event_hash(
    prev_hash: &str,
    kind: &str,
    process_id: &str,
    detail: &Value,
    ts: f64,
    eid: &str,
) -> String {
    // Python includes floats as-is; we build a BTreeMap-equivalent via serde_json Map (sorted).
    let payload = serde_json::json!({
        "detail": detail,
        "id": eid,
        "kind": kind,
        "prev": prev_hash,
        "process_id": process_id,
        "ts": ts,
    });
    // serde_json object keys are sorted alphabetically when serializing Map —
    // but json! macro uses Map which preserves insertion. Force via Value re-parse.
    let text = compact_sorted_json(&payload);
    let mut hasher = Sha256::new();
    hasher.update(text.as_bytes());
    hex::encode(hasher.finalize())
}

/// Produce JSON with sorted object keys (recursive), matching Python sort_keys=True.
fn compact_sorted_json(v: &Value) -> String {
    match v {
        Value::Null => "null".into(),
        Value::Bool(b) => {
            if *b {
                "true".into()
            } else {
                "false".into()
            }
        }
        Value::Number(n) => n.to_string(),
        Value::String(s) => serde_json::to_string(s).unwrap_or_else(|_| "\"\"".into()),
        Value::Array(arr) => {
            let parts: Vec<_> = arr.iter().map(compact_sorted_json).collect();
            format!("[{}]", parts.join(", "))
        }
        Value::Object(map) => {
            let mut keys: Vec<_> = map.keys().cloned().collect();
            keys.sort();
            let parts: Vec<_> = keys
                .iter()
                .map(|k| {
                    let key = serde_json::to_string(k).unwrap_or_else(|_| "\"\"".into());
                    let val = compact_sorted_json(&map[k]);
                    format!("{key}: {val}")
                })
                .collect();
            format!("{{{}}}", parts.join(", "))
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct KernelEvent {
    pub id: String,
    pub kind: String,
    pub process_id: String,
    pub detail: Value,
    pub ts: f64,
    pub prev_hash: String,
    pub hash: String,
}

impl KernelEvent {
    pub fn new(kind: impl Into<String>, process_id: impl Into<String>, detail: Value, prev_hash: &str) -> Self {
        let id = short_id();
        let ts = now_secs();
        let kind = kind.into();
        let process_id = process_id.into();
        let hash = event_hash(prev_hash, &kind, &process_id, &detail, ts, &id);
        Self {
            id,
            kind,
            process_id,
            detail,
            ts,
            prev_hash: prev_hash.to_string(),
            hash,
        }
    }

    pub fn to_dict(&self) -> Value {
        serde_json::json!({
            "id": self.id,
            "kind": self.kind,
            "process_id": self.process_id,
            "detail": self.detail,
            "ts": self.ts,
            "prev_hash": self.prev_hash,
            "hash": self.hash,
        })
    }
}

/// Append-only JSONL audit store with size-based rotation (H2-C2).
pub struct AuditEventStore {
    path: PathBuf,
    lock: Mutex<()>,
    max_bytes: u64,
    keep_segments: u32,
}

impl AuditEventStore {
    pub fn new(path: impl Into<PathBuf>) -> Self {
        let max_bytes = std::env::var("TAKTON_AUDIT_MAX_BYTES")
            .ok()
            .and_then(|s| s.parse().ok())
            .unwrap_or(32 * 1024 * 1024);
        let keep_segments = std::env::var("TAKTON_AUDIT_KEEP_SEGMENTS")
            .ok()
            .and_then(|s| s.parse().ok())
            .unwrap_or(7u32)
            .max(1);
        Self {
            path: path.into(),
            lock: Mutex::new(()),
            max_bytes,
            keep_segments,
        }
    }

    pub fn default_path() -> PathBuf {
        dirs_fallback_home().join(".takton").join("kernel_events.jsonl")
    }

    pub fn path(&self) -> &Path {
        &self.path
    }

    fn rotate_if_needed(&self) {
        let meta = match fs::metadata(&self.path) {
            Ok(m) => m,
            Err(_) => return,
        };
        if meta.len() < self.max_bytes {
            return;
        }
        let base = self.path.to_string_lossy().to_string();
        // cascade .N -> .(N+1)
        for i in (1..self.keep_segments).rev() {
            let src = format!("{base}.{i}");
            let dst = format!("{base}.{}", i + 1);
            if Path::new(&src).exists() {
                let _ = fs::remove_file(&dst);
                let _ = fs::rename(&src, &dst);
            }
        }
        let oldest = format!("{base}.{}", self.keep_segments + 1);
        let _ = fs::remove_file(&oldest);
        let rotated = format!("{base}.1");
        let _ = fs::remove_file(&rotated);
        let _ = fs::rename(&self.path, &rotated);
    }

    pub fn append(&self, event: &Value) -> bool {
        let _g = self.lock.lock().unwrap_or_else(|e| e.into_inner());
        if let Some(parent) = self.path.parent() {
            let _ = fs::create_dir_all(parent);
        }
        self.rotate_if_needed();
        let Ok(mut f) = OpenOptions::new().create(true).append(true).open(&self.path) else {
            return false;
        };
        let Ok(line) = serde_json::to_string(event) else {
            return false;
        };
        writeln!(f, "{line}").is_ok()
    }

    pub fn load_tail_hash(&self) -> Option<String> {
        let f = File::open(&self.path).ok()?;
        let reader = BufReader::new(f);
        let mut last: Option<String> = None;
        for line in reader.lines().map_while(Result::ok) {
            let line = line.trim();
            if line.is_empty() {
                continue;
            }
            if let Ok(v) = serde_json::from_str::<Value>(line) {
                if let Some(h) = v.get("hash").and_then(|x| x.as_str()) {
                    last = Some(h.to_string());
                }
            }
        }
        last
    }

    pub fn verify_file_chain(&self) -> (bool, i64) {
        let Ok(f) = File::open(&self.path) else {
            return (false, 0);
        };
        let reader = BufReader::new(f);
        let mut prev: Option<String> = None;
        for (lineno, line) in reader.lines().map_while(Result::ok).enumerate() {
            let line = line.trim();
            if line.is_empty() {
                continue;
            }
            let Ok(e) = serde_json::from_str::<Value>(line) else {
                return (false, (lineno + 1) as i64);
            };
            let expected = event_hash(
                e.get("prev_hash").and_then(|v| v.as_str()).unwrap_or(""),
                e.get("kind").and_then(|v| v.as_str()).unwrap_or(""),
                e.get("process_id").and_then(|v| v.as_str()).unwrap_or(""),
                e.get("detail").unwrap_or(&Value::Null),
                e.get("ts").and_then(|v| v.as_f64()).unwrap_or(0.0),
                e.get("id").and_then(|v| v.as_str()).unwrap_or(""),
            );
            if e.get("hash").and_then(|v| v.as_str()) != Some(expected.as_str()) {
                return (false, (lineno + 1) as i64);
            }
            if let Some(ref p) = prev {
                if e.get("prev_hash").and_then(|v| v.as_str()) != Some(p.as_str()) {
                    return (false, (lineno + 1) as i64);
                }
            }
            prev = e.get("hash").and_then(|v| v.as_str()).map(|s| s.to_string());
        }
        (true, -1)
    }
}

fn dirs_fallback_home() -> PathBuf {
    std::env::var_os("USERPROFILE")
        .or_else(|| std::env::var_os("HOME"))
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from("."))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn chain_links() {
        let e1 = KernelEvent::new("process_created", "p1", serde_json::json!({"a": 1}), GENESIS_HASH);
        let e2 = KernelEvent::new("mediation", "p1", serde_json::json!({"allowed": true}), &e1.hash);
        assert_eq!(e2.prev_hash, e1.hash);
        let expected = event_hash(&e2.prev_hash, &e2.kind, &e2.process_id, &e2.detail, e2.ts, &e2.id);
        assert_eq!(e2.hash, expected);
    }
}
