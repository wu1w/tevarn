//! Repo / workspace index handles under context quota (P2 H4).

use std::collections::HashMap;
use std::fs;
use std::path::{Path, PathBuf};
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
pub struct IndexEntry {
    pub path: String,
    pub kind: String, // file | dir
    pub bytes: u64,
    pub ext: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RepoIndex {
    pub id: String,
    pub process_id: String,
    pub root: String,
    pub entries: Vec<IndexEntry>,
    pub total_files: usize,
    pub total_bytes: u64,
    pub token_estimate: u32,
    pub created_at: f64,
}

pub struct RepoIndexStore {
    by_id: HashMap<String, RepoIndex>,
    by_process: HashMap<String, Vec<String>>,
    max_files: usize,
    max_bytes: u64,
}

impl Default for RepoIndexStore {
    fn default() -> Self {
        Self {
            by_id: HashMap::new(),
            by_process: HashMap::new(),
            max_files: 5_000,
            max_bytes: 50_000_000,
        }
    }
}

impl RepoIndexStore {
    pub fn build(
        &mut self,
        process_id: &str,
        root: &str,
        max_depth: usize,
    ) -> Result<RepoIndex, String> {
        let root_path = PathBuf::from(root);
        if !root_path.is_dir() {
            return Err(format!("not a directory: {root}"));
        }
        let mut entries = Vec::new();
        let mut total_bytes = 0u64;
        Self::walk(
            &root_path,
            &root_path,
            0,
            max_depth.max(1).min(12),
            &mut entries,
            &mut total_bytes,
            self.max_files,
            self.max_bytes,
        )?;
        let token_estimate = (total_bytes / 4).min(u64::from(u32::MAX)) as u32;
        let idx = RepoIndex {
            id: short_id(),
            process_id: process_id.to_string(),
            root: root.to_string(),
            total_files: entries.iter().filter(|e| e.kind == "file").count(),
            total_bytes,
            token_estimate,
            entries,
            created_at: now_secs(),
        };
        self.by_process
            .entry(process_id.to_string())
            .or_default()
            .push(idx.id.clone());
        self.by_id.insert(idx.id.clone(), idx.clone());
        Ok(idx)
    }

    #[allow(clippy::too_many_arguments)]
    fn walk(
        root: &Path,
        cur: &Path,
        depth: usize,
        max_depth: usize,
        out: &mut Vec<IndexEntry>,
        total_bytes: &mut u64,
        max_files: usize,
        max_bytes: u64,
    ) -> Result<(), String> {
        if depth > max_depth || out.len() >= max_files || *total_bytes >= max_bytes {
            return Ok(());
        }
        let rd = fs::read_dir(cur).map_err(|e| e.to_string())?;
        for ent in rd.flatten() {
            if out.len() >= max_files || *total_bytes >= max_bytes {
                break;
            }
            let path = ent.path();
            let name = path
                .file_name()
                .and_then(|s| s.to_str())
                .unwrap_or("")
                .to_string();
            if name.starts_with('.')
                || name == "node_modules"
                || name == "target"
                || name == "__pycache__"
                || name == ".git"
            {
                continue;
            }
            let rel = path
                .strip_prefix(root)
                .unwrap_or(&path)
                .to_string_lossy()
                .replace('\\', "/");
            if path.is_dir() {
                out.push(IndexEntry {
                    path: rel,
                    kind: "dir".into(),
                    bytes: 0,
                    ext: String::new(),
                });
                Self::walk(
                    root,
                    &path,
                    depth + 1,
                    max_depth,
                    out,
                    total_bytes,
                    max_files,
                    max_bytes,
                )?;
            } else if path.is_file() {
                let meta = fs::metadata(&path).map_err(|e| e.to_string())?;
                let bytes = meta.len();
                *total_bytes = total_bytes.saturating_add(bytes);
                let ext = path
                    .extension()
                    .and_then(|s| s.to_str())
                    .unwrap_or("")
                    .to_string();
                out.push(IndexEntry {
                    path: rel,
                    kind: "file".into(),
                    bytes,
                    ext,
                });
            }
        }
        Ok(())
    }

    pub fn get(&self, id: &str) -> Option<&RepoIndex> {
        self.by_id.get(id)
    }

    pub fn list_for_process(&self, process_id: &str) -> Vec<Value> {
        self.by_process
            .get(process_id)
            .into_iter()
            .flatten()
            .filter_map(|id| self.by_id.get(id))
            .map(|i| {
                json!({
                    "id": i.id,
                    "root": i.root,
                    "total_files": i.total_files,
                    "total_bytes": i.total_bytes,
                    "token_estimate": i.token_estimate,
                })
            })
            .collect()
    }

    pub fn drop_process(&mut self, process_id: &str) {
        if let Some(ids) = self.by_process.remove(process_id) {
            for id in ids {
                self.by_id.remove(&id);
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::env;

    #[test]
    fn builds_index() {
        let dir = env::temp_dir().join(format!("repo_{}", short_id()));
        let _ = fs::create_dir_all(dir.join("src"));
        fs::write(dir.join("src/main.rs"), "fn main(){}").unwrap();
        let mut s = RepoIndexStore::default();
        let idx = s.build("p1", dir.to_str().unwrap(), 3).unwrap();
        assert!(idx.total_files >= 1);
        let _ = fs::remove_dir_all(&dir);
    }
}
