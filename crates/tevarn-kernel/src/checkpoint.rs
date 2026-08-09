//! File write checkpoints for confirm/rollback (P0-D D8).

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
pub struct FileCheckpoint {
    pub id: String,
    pub process_id: String,
    pub path: String,
    pub backup_path: Option<String>,
    pub content_hash_before: Option<String>,
    pub existed: bool,
    pub created_at: f64,
    pub restored_at: Option<f64>,
}

pub struct CheckpointStore {
    by_id: HashMap<String, FileCheckpoint>,
    by_process: HashMap<String, Vec<String>>,
    backup_dir: PathBuf,
}

impl Default for CheckpointStore {
    fn default() -> Self {
        Self::new(Self::default_dir())
    }
}

impl CheckpointStore {
    pub fn new(backup_dir: impl Into<PathBuf>) -> Self {
        Self {
            by_id: HashMap::new(),
            by_process: HashMap::new(),
            backup_dir: backup_dir.into(),
        }
    }

    pub fn default_dir() -> PathBuf {
        std::env::var_os("USERPROFILE")
            .or_else(|| std::env::var_os("HOME"))
            .map(PathBuf::from)
            .unwrap_or_else(|| PathBuf::from("."))
            .join(".tevarn")
            .join("checkpoints")
    }

    /// Snapshot file before write. Returns checkpoint id.
    pub fn begin(
        &mut self,
        process_id: &str,
        path: &str,
    ) -> Result<FileCheckpoint, String> {
        let _ = fs::create_dir_all(&self.backup_dir);
        let p = PathBuf::from(path);
        let existed = p.is_file();
        let mut content_hash = None;
        let mut backup_path = None;
        if existed {
            let bytes = fs::read(&p).map_err(|e| e.to_string())?;
            let mut hasher = Sha256::new();
            hasher.update(&bytes);
            content_hash = Some(hex::encode(hasher.finalize()));
            let bp = self
                .backup_dir
                .join(format!("{}_{}", short_id(), p.file_name().and_then(|s| s.to_str()).unwrap_or("file")));
            fs::write(&bp, &bytes).map_err(|e| e.to_string())?;
            backup_path = Some(bp.display().to_string());
        }
        let cp = FileCheckpoint {
            id: short_id(),
            process_id: process_id.to_string(),
            path: path.to_string(),
            backup_path,
            content_hash_before: content_hash,
            existed,
            created_at: now_secs(),
            restored_at: None,
        };
        self.by_process
            .entry(process_id.to_string())
            .or_default()
            .push(cp.id.clone());
        self.by_id.insert(cp.id.clone(), cp.clone());
        Ok(cp)
    }

    pub fn restore(&mut self, checkpoint_id: &str) -> Result<FileCheckpoint, String> {
        let cp = self
            .by_id
            .get_mut(checkpoint_id)
            .ok_or_else(|| format!("unknown checkpoint {checkpoint_id}"))?;
        if let Some(ref bp) = cp.backup_path {
            let bytes = fs::read(bp).map_err(|e| e.to_string())?;
            fs::write(&cp.path, bytes).map_err(|e| e.to_string())?;
        } else if !cp.existed {
            let _ = fs::remove_file(&cp.path);
        }
        cp.restored_at = Some(now_secs());
        Ok(cp.clone())
    }

    pub fn list_for_process(&self, process_id: &str) -> Vec<Value> {
        self.by_process
            .get(process_id)
            .into_iter()
            .flatten()
            .filter_map(|id| self.by_id.get(id))
            .map(|c| json!(c))
            .collect()
    }

    pub fn get(&self, id: &str) -> Option<&FileCheckpoint> {
        self.by_id.get(id)
    }
}
