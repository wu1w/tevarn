//! External tool-result spill store (P0.5 E2).
//!
//! Large tool outputs are written to disk; context keeps a compact handle.

use std::collections::HashMap;
use std::fs;
use std::path::PathBuf;
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
    uuid::Uuid::new_v4().simple().to_string()[..16].to_string()
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ResultHandle {
    pub id: String,
    pub process_id: String,
    pub tool: String,
    pub path: String,
    pub bytes: u64,
    pub sha256: String,
    pub preview: String,
    pub created_at: f64,
}

pub struct ResultSpillStore {
    dir: PathBuf,
    by_id: HashMap<String, ResultHandle>,
    by_process: HashMap<String, Vec<String>>,
    /// spill when content length >= this (chars / bytes of utf-8)
    threshold: usize,
    preview_chars: usize,
}

impl Default for ResultSpillStore {
    fn default() -> Self {
        // Soft defaults (Claude Code–style envelope): keep mid-size results
        // inline at the Python layer; Rust store still accepts forced spills.
        // Override: TAKTON_RESULT_SPILL_THRESHOLD / TAKTON_RESULT_SPILL_PREVIEW.
        let thr = std::env::var("TAKTON_RESULT_SPILL_THRESHOLD")
            .ok()
            .and_then(|s| s.parse().ok())
            .unwrap_or(16_000);
        let preview = std::env::var("TAKTON_RESULT_SPILL_PREVIEW")
            .ok()
            .and_then(|s| s.parse().ok())
            .unwrap_or(8_000);
        Self::new(Self::default_dir(), thr, preview)
    }
}

impl ResultSpillStore {
    pub fn new(dir: impl Into<PathBuf>, threshold: usize, preview_chars: usize) -> Self {
        Self {
            dir: dir.into(),
            by_id: HashMap::new(),
            by_process: HashMap::new(),
            threshold: threshold.max(64),
            preview_chars: preview_chars.max(32),
        }
    }

    pub fn default_dir() -> PathBuf {
        std::env::var_os("USERPROFILE")
            .or_else(|| std::env::var_os("HOME"))
            .map(PathBuf::from)
            .unwrap_or_else(|| PathBuf::from("."))
            .join(".takton")
            .join("tool_results")
    }

    pub fn set_threshold(&mut self, n: usize) {
        self.threshold = n.max(64);
    }

    /// If content is large, spill to disk and return handle summary text for context.
    /// Small content returns None (caller keeps original).
    pub fn maybe_spill(
        &mut self,
        process_id: &str,
        tool: &str,
        content: &str,
    ) -> Option<ResultHandle> {
        if content.len() < self.threshold {
            return None;
        }
        let _ = fs::create_dir_all(&self.dir);
        let id = short_id();
        let path = self.dir.join(format!("{id}.txt"));
        if fs::write(&path, content.as_bytes()).is_err() {
            return None;
        }
        let mut hasher = Sha256::new();
        hasher.update(content.as_bytes());
        let sha = hex::encode(hasher.finalize());
        let preview: String = content.chars().take(self.preview_chars).collect();
        let h = ResultHandle {
            id: id.clone(),
            process_id: process_id.to_string(),
            tool: tool.to_string(),
            path: path.display().to_string(),
            bytes: content.len() as u64,
            sha256: sha,
            preview,
            created_at: now_secs(),
        };
        self.by_process
            .entry(process_id.to_string())
            .or_default()
            .push(id.clone());
        self.by_id.insert(id, h.clone());
        Some(h)
    }

    /// Load full spill content. When `caller_process_id` is set, it must match
    /// the handle's process (prevents cross-process lateral read of tool results).
    pub fn load(
        &self,
        handle_id: &str,
        caller_process_id: Option<&str>,
    ) -> Result<String, String> {
        let h = self
            .by_id
            .get(handle_id)
            .ok_or_else(|| format!("unknown result handle {handle_id}"))?;
        if let Some(pid) = caller_process_id.map(str::trim).filter(|s| !s.is_empty()) {
            if h.process_id != pid {
                return Err(format!(
                    "result handle {handle_id} belongs to another process"
                ));
            }
        } else {
            // Strict: callers must bind process (agent injects _kernel_process_id).
            return Err("process_id required to load result handle".into());
        }
        fs::read_to_string(&h.path).map_err(|e| e.to_string())
    }

    pub fn get(&self, handle_id: &str) -> Option<&ResultHandle> {
        self.by_id.get(handle_id)
    }

    pub fn drop_process(&mut self, process_id: &str) {
        if let Some(ids) = self.by_process.remove(process_id) {
            for id in ids {
                if let Some(h) = self.by_id.remove(&id) {
                    let _ = fs::remove_file(&h.path);
                }
            }
        }
    }

    /// Compact context line for models (Python layer may replace with richer envelope).
    pub fn handle_summary(h: &ResultHandle) -> String {
        format!(
            "[tool_result_handle id={} tool={} bytes={} sha256={}…]\n\
FULL BODY external — do NOT re-run the tool; page with:\n\
  result_load(id=\"{}\", offset=0, max_chars=20000)\n\
--- preview ---\n{}\n\
--- end preview; result_load id={} for more ---",
            h.id,
            h.tool,
            h.bytes,
            &h.sha256[..h.sha256.len().min(12)],
            h.id,
            h.preview,
            h.id
        )
    }

    pub fn status(&self) -> Value {
        json!({
            "handles": self.by_id.len(),
            "threshold": self.threshold,
            "preview_chars": self.preview_chars,
            "dir": self.dir.display().to_string(),
            "aggressive_default": false,
            "policy": "spill when len>=threshold; context keeps handle+head/tail preview; page via result_load",
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::env;

    #[test]
    fn spills_large_keeps_small() {
        let dir = env::temp_dir().join(format!("takton_spill_{}", short_id()));
        let mut s = ResultSpillStore::new(&dir, 100, 40);
        assert!(s.maybe_spill("p1", "cmd", "short").is_none());
        let big = "x".repeat(200);
        let h = s.maybe_spill("p1", "cmd", &big).expect("spill");
        assert_eq!(h.bytes, 200);
        let loaded = s.load(&h.id, Some("p1")).unwrap();
        assert_eq!(loaded.len(), 200);
        assert!(s.load(&h.id, None).is_err());
        assert!(s.load(&h.id, Some("other")).is_err());
        s.drop_process("p1");
        let _ = fs::remove_dir_all(&dir);
    }
}
