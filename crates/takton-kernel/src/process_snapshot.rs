//! Process-level checkpoint engine (P0.5 E1).
//!
//! Distinct from file write checkpoints: stores process state + audit tail_hash
//! so recovery = snapshot + incremental events after tail_hash (no full replay).

use std::collections::HashMap;
use std::fs;
use std::path::PathBuf;
use std::time::{SystemTime, UNIX_EPOCH};

use serde::{Deserialize, Serialize};
use serde_json::{json, Value};

use crate::process::AgentProcess;

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
pub struct ProcessSnapshot {
    pub id: String,
    pub seq: u64,
    pub process_id: String,
    pub tail_hash: String,
    pub event_count: u64,
    pub process: Value,
    pub meta: Value,
    pub created_at: f64,
}

pub struct ProcessSnapshotStore {
    by_id: HashMap<String, ProcessSnapshot>,
    by_process: HashMap<String, Vec<String>>,
    global_seq: u64,
    /// optional disk dir for durability across host restarts
    persist_dir: Option<PathBuf>,
}

impl Default for ProcessSnapshotStore {
    fn default() -> Self {
        Self::new(None)
    }
}

impl ProcessSnapshotStore {
    pub fn new(persist_dir: Option<PathBuf>) -> Self {
        let mut store = Self {
            by_id: HashMap::new(),
            by_process: HashMap::new(),
            global_seq: 0,
            persist_dir,
        };
        store.load_from_disk();
        store
    }

    pub fn default_dir() -> PathBuf {
        std::env::var_os("USERPROFILE")
            .or_else(|| std::env::var_os("HOME"))
            .map(PathBuf::from)
            .unwrap_or_else(|| PathBuf::from("."))
            .join(".takton")
            .join("process_snapshots")
    }

    fn load_from_disk(&mut self) {
        let Some(ref dir) = self.persist_dir else {
            return;
        };
        let _ = fs::create_dir_all(dir);
        let Ok(entries) = fs::read_dir(dir) else {
            return;
        };
        for ent in entries.flatten() {
            let path = ent.path();
            if path.extension().and_then(|s| s.to_str()) != Some("json") {
                continue;
            }
            let Ok(bytes) = fs::read(&path) else {
                continue;
            };
            let Ok(snap) = serde_json::from_slice::<ProcessSnapshot>(&bytes) else {
                continue;
            };
            self.global_seq = self.global_seq.max(snap.seq);
            self.by_process
                .entry(snap.process_id.clone())
                .or_default()
                .push(snap.id.clone());
            self.by_id.insert(snap.id.clone(), snap);
        }
    }

    fn persist(&self, snap: &ProcessSnapshot) {
        let Some(ref dir) = self.persist_dir else {
            return;
        };
        let _ = fs::create_dir_all(dir);
        let path = dir.join(format!("{}.json", snap.id));
        if let Ok(bytes) = serde_json::to_vec_pretty(snap) {
            let _ = fs::write(path, bytes);
        }
    }

    /// Capture process state + current audit chain tail.
    pub fn capture(
        &mut self,
        process: &AgentProcess,
        tail_hash: &str,
        event_count: u64,
        meta: Option<Value>,
    ) -> ProcessSnapshot {
        self.global_seq += 1;
        let snap = ProcessSnapshot {
            id: short_id(),
            seq: self.global_seq,
            process_id: process.id.clone(),
            tail_hash: tail_hash.to_string(),
            event_count,
            process: process.to_dict(),
            meta: meta.unwrap_or_else(|| json!({})),
            created_at: now_secs(),
        };
        self.by_process
            .entry(process.id.clone())
            .or_default()
            .push(snap.id.clone());
        self.persist(&snap);
        self.by_id.insert(snap.id.clone(), snap.clone());
        snap
    }

    pub fn latest_for_process(&self, process_id: &str) -> Option<&ProcessSnapshot> {
        self.by_process
            .get(process_id)
            .and_then(|ids| ids.last())
            .and_then(|id| self.by_id.get(id))
    }

    pub fn get(&self, id: &str) -> Option<&ProcessSnapshot> {
        self.by_id.get(id)
    }

    pub fn list_for_process(&self, process_id: &str) -> Vec<Value> {
        self.by_process
            .get(process_id)
            .into_iter()
            .flatten()
            .filter_map(|id| self.by_id.get(id))
            .map(|s| json!(s))
            .collect()
    }

    /// Recovery plan: snapshot + instruction to read events after tail_hash.
    /// Does NOT full-replay history into process table.
    pub fn recovery_plan(&self, process_id: &str) -> Value {
        match self.latest_for_process(process_id) {
            Some(s) => json!({
                "mode": "snapshot_plus_incremental",
                "full_replay": false,
                "snapshot_id": s.id,
                "seq": s.seq,
                "tail_hash": s.tail_hash,
                "event_count": s.event_count,
                "process": s.process,
                "meta": s.meta,
            }),
            None => json!({
                "mode": "none",
                "full_replay": false,
                "snapshot_id": null,
                "message": "no process snapshot; resume from DB/archive only",
            }),
        }
    }

    pub fn status(&self) -> Value {
        json!({
            "snapshots": self.by_id.len(),
            "processes_tracked": self.by_process.len(),
            "global_seq": self.global_seq,
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::process::AgentProcess;
    use std::collections::BTreeMap;

    #[test]
    fn capture_and_recovery_no_full_replay() {
        let mut store = ProcessSnapshotStore::new(None);
        let p = AgentProcess::new("t", Some("s1".into()), None, Some(vec!["file_read".into()]), Some(1000), BTreeMap::new());
        let snap = store.capture(&p, "abc123", 42, None);
        assert_eq!(snap.event_count, 42);
        let plan = store.recovery_plan(&p.id);
        assert_eq!(plan["full_replay"], false);
        assert_eq!(plan["mode"], "snapshot_plus_incremental");
        assert_eq!(plan["tail_hash"], "abc123");
    }
}
