//! Hash-chained kernel events (SHA-256).

use std::fs::{self, File, OpenOptions};
use std::io::{BufRead, BufReader, Write};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};
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

/// Append-only JSONL audit store with size-based rotation + optional WORM.
///
/// WORM (`TEVARN_AUDIT_WORM=1`): rotated segments are never deleted; new
/// segments get monotonic names `.worm.<ts>`. External anchor file holds
/// signed tip hash for offline verification.
pub struct AuditEventStore {
    path: PathBuf,
    lock: Mutex<()>,
    max_bytes: u64,
    keep_segments: u32,
    worm: bool,
    anchor_path: PathBuf,
    // audit-fix: anchor 降频状态（事件计数 / 上次 anchor 时刻 epoch millis）
    anchor_events: AtomicU64,
    anchor_last_ms: AtomicU64,
}

impl AuditEventStore {
    pub fn new(path: impl Into<PathBuf>) -> Self {
        let path = path.into();
        let max_bytes = std::env::var("TEVARN_AUDIT_MAX_BYTES")
            .ok()
            .and_then(|s| s.parse().ok())
            .unwrap_or(32 * 1024 * 1024);
        let keep_segments = std::env::var("TEVARN_AUDIT_KEEP_SEGMENTS")
            .ok()
            .and_then(|s| s.parse().ok())
            .unwrap_or(7u32)
            .max(1);
        let worm = std::env::var("TEVARN_AUDIT_WORM")
            .map(|v| {
                let t = v.trim().to_lowercase();
                t == "1" || t == "true" || t == "yes" || t == "on"
            })
            .unwrap_or(false);
        let anchor_path = path.with_extension("anchor.json");
        Self {
            path,
            lock: Mutex::new(()),
            max_bytes,
            keep_segments,
            worm,
            anchor_path,
            anchor_events: AtomicU64::new(0),
            anchor_last_ms: AtomicU64::new(0),
        }
    }

    pub fn default_path() -> PathBuf {
        dirs_fallback_home().join(".tevarn").join("kernel_events.jsonl")
    }

    pub fn path(&self) -> &Path {
        &self.path
    }

    pub fn worm(&self) -> bool {
        self.worm
    }

    pub fn set_worm(&mut self, on: bool) {
        self.worm = on;
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
        if self.worm {
            // WORM: never delete; seal active file under unique name
            let sealed = format!(
                "{base}.worm.{}",
                SystemTime::now()
                    .duration_since(UNIX_EPOCH)
                    .map(|d| d.as_secs())
                    .unwrap_or(0)
            );
            let _ = fs::rename(&self.path, &sealed);
            return;
        }
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
        if writeln!(f, "{line}").is_err() {
            return false;
        }
        // audit-fix: anchor 降频——每 32 事件或距上次 >5s 才刷新 anchor 文件；
        // JSONL append 保持每事件（审计链完整性优先）
        if let Some(h) = event.get("hash").and_then(|v| v.as_str()) {
            let n = self.anchor_events.fetch_add(1, Ordering::Relaxed) + 1;
            let now_ms = SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .map(|d| d.as_millis() as u64)
                .unwrap_or(0);
            let last = self.anchor_last_ms.load(Ordering::Relaxed);
            if n % 32 == 0 || now_ms.saturating_sub(last) > 5000 {
                self.anchor_last_ms.store(now_ms, Ordering::Relaxed);
                let _ =
                    self.write_anchor(h, event.get("prev_hash").and_then(|v| v.as_str()));
            }
        }
        true
    }

    /// External anchor: tip hash + monotonic seq file for offline integrity checks.
    pub fn write_anchor(&self, tip_hash: &str, prev_hash: Option<&str>) -> bool {
        let body = serde_json::json!({
            "tip_hash": tip_hash,
            "prev_hash": prev_hash.unwrap_or(""),
            "path": self.path.display().to_string(),
            "worm": self.worm,
            "anchored_at": SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .map(|d| d.as_secs_f64())
                .unwrap_or(0.0),
            "schema": "tevarn-audit-anchor-v1",
        });
        // content hash of anchor body (without self-hash) for external verify
        let mut payload = body.clone();
        if let Some(obj) = payload.as_object_mut() {
            obj.remove("anchor_hash");
        }
        let text = serde_json::to_string(&payload).unwrap_or_default();
        let mut hasher = Sha256::new();
        hasher.update(text.as_bytes());
        let anchor_hash = hex::encode(hasher.finalize());
        let mut out = body;
        if let Some(obj) = out.as_object_mut() {
            obj.insert("anchor_hash".into(), serde_json::json!(anchor_hash));
        }
        if let Some(parent) = self.anchor_path.parent() {
            let _ = fs::create_dir_all(parent);
        }
        fs::write(
            &self.anchor_path,
            serde_json::to_string_pretty(&out).unwrap_or_default(),
        )
        .is_ok()
    }

    pub fn read_anchor(&self) -> Option<Value> {
        let s = fs::read_to_string(&self.anchor_path).ok()?;
        serde_json::from_str(&s).ok()
    }

    /// Verify anchor tip matches active file tail hash.
    pub fn verify_anchor(&self) -> Value {
        let tail = self.load_tail_hash();
        let anchor = self.read_anchor();
        let tip = anchor
            .as_ref()
            .and_then(|a| a.get("tip_hash"))
            .and_then(|v| v.as_str())
            .map(|s| s.to_string());
        let ok = match (&tail, &tip) {
            (Some(t), Some(a)) => t == a,
            (None, None) => true,
            _ => false,
        };
        serde_json::json!({
            "ok": ok,
            "worm": self.worm,
            "tail_hash": tail,
            "anchor_tip": tip,
            "anchor_path": self.anchor_path.display().to_string(),
            "audit_path": self.path.display().to_string(),
        })
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
