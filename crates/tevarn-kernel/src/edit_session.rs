//! File edit sessions with confirm / diff / rollback (P2 H3).

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
    uuid::Uuid::new_v4().simple().to_string()[..12].to_string()
}

fn hash_bytes(b: &[u8]) -> String {
    let mut h = Sha256::new();
    h.update(b);
    hex::encode(h.finalize())
}

/// Unified diff-ish preview (line-based LCS-lite: show -old / +new hunks).
pub fn simple_diff(before: &str, after: &str) -> String {
    let old: Vec<&str> = before.lines().collect();
    let new: Vec<&str> = after.lines().collect();
    let mut out = String::new();
    out.push_str("--- before\n+++ after\n");
    let max = old.len().max(new.len());
    for i in 0..max {
        let o = old.get(i).copied();
        let n = new.get(i).copied();
        match (o, n) {
            (Some(a), Some(b)) if a == b => {
                out.push_str(&format!(" {a}\n"));
            }
            (Some(a), Some(b)) => {
                out.push_str(&format!("-{a}\n+{b}\n"));
            }
            (Some(a), None) => out.push_str(&format!("-{a}\n")),
            (None, Some(b)) => out.push_str(&format!("+{b}\n")),
            _ => {}
        }
    }
    out
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct EditSession {
    pub id: String,
    pub process_id: String,
    pub path: String,
    pub before: String,
    pub after: String,
    pub before_hash: String,
    pub after_hash: String,
    pub diff: String,
    pub status: String, // proposed | applied | rejected | rolled_back
    pub created_at: f64,
    pub resolved_at: Option<f64>,
    pub backup_path: Option<String>,
}

pub struct EditSessionStore {
    by_id: HashMap<String, EditSession>,
    by_process: HashMap<String, Vec<String>>,
    backup_dir: PathBuf,
}

impl Default for EditSessionStore {
    fn default() -> Self {
        Self::new(Self::default_dir())
    }
}

impl EditSessionStore {
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
            .join("edit_sessions")
    }

    pub fn propose(
        &mut self,
        process_id: &str,
        path: &str,
        after: &str,
    ) -> Result<EditSession, String> {
        let p = PathBuf::from(path);
        let before = if p.is_file() {
            fs::read_to_string(&p).map_err(|e| e.to_string())?
        } else {
            String::new()
        };
        let diff = simple_diff(&before, after);
        let sess = EditSession {
            id: short_id(),
            process_id: process_id.to_string(),
            path: path.to_string(),
            before_hash: hash_bytes(before.as_bytes()),
            after_hash: hash_bytes(after.as_bytes()),
            before,
            after: after.to_string(),
            diff,
            status: "proposed".into(),
            created_at: now_secs(),
            resolved_at: None,
            backup_path: None,
        };
        self.by_process
            .entry(process_id.to_string())
            .or_default()
            .push(sess.id.clone());
        self.by_id.insert(sess.id.clone(), sess.clone());
        Ok(sess)
    }

    pub fn confirm(&mut self, session_id: &str) -> Result<EditSession, String> {
        let sess = self
            .by_id
            .get_mut(session_id)
            .ok_or_else(|| format!("unknown edit session {session_id}"))?;
        if sess.status != "proposed" {
            return Err(format!("session status {}", sess.status));
        }
        let _ = fs::create_dir_all(&self.backup_dir);
        let bp = self.backup_dir.join(format!("{}_{}", sess.id, PathBuf::from(&sess.path).file_name().and_then(|s| s.to_str()).unwrap_or("file")));
        fs::write(&bp, sess.before.as_bytes()).map_err(|e| e.to_string())?;
        // ensure parent
        if let Some(parent) = PathBuf::from(&sess.path).parent() {
            let _ = fs::create_dir_all(parent);
        }
        fs::write(&sess.path, sess.after.as_bytes()).map_err(|e| e.to_string())?;
        sess.backup_path = Some(bp.display().to_string());
        sess.status = "applied".into();
        sess.resolved_at = Some(now_secs());
        Ok(sess.clone())
    }

    pub fn reject(&mut self, session_id: &str) -> Result<EditSession, String> {
        let sess = self
            .by_id
            .get_mut(session_id)
            .ok_or_else(|| format!("unknown edit session {session_id}"))?;
        if sess.status != "proposed" {
            return Err(format!("session status {}", sess.status));
        }
        sess.status = "rejected".into();
        sess.resolved_at = Some(now_secs());
        Ok(sess.clone())
    }

    pub fn rollback(&mut self, session_id: &str) -> Result<EditSession, String> {
        let sess = self
            .by_id
            .get_mut(session_id)
            .ok_or_else(|| format!("unknown edit session {session_id}"))?;
        if sess.status != "applied" {
            return Err("only applied sessions can rollback".into());
        }
        if let Some(ref bp) = sess.backup_path {
            let bytes = fs::read(bp).map_err(|e| e.to_string())?;
            fs::write(&sess.path, bytes).map_err(|e| e.to_string())?;
        } else {
            fs::write(&sess.path, sess.before.as_bytes()).map_err(|e| e.to_string())?;
        }
        sess.status = "rolled_back".into();
        sess.resolved_at = Some(now_secs());
        Ok(sess.clone())
    }

    pub fn get(&self, id: &str) -> Option<&EditSession> {
        self.by_id.get(id)
    }

    pub fn list(&self, process_id: &str) -> Vec<Value> {
        self.by_process
            .get(process_id)
            .into_iter()
            .flatten()
            .filter_map(|id| self.by_id.get(id))
            .map(|s| {
                json!({
                    "id": s.id,
                    "path": s.path,
                    "status": s.status,
                    "diff_preview": s.diff.chars().take(500).collect::<String>(),
                    "created_at": s.created_at,
                })
            })
            .collect()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::env;

    #[test]
    fn propose_confirm_rollback() {
        let dir = env::temp_dir().join(format!("edit_{}", short_id()));
        let _ = fs::create_dir_all(&dir);
        let path = dir.join("a.txt");
        fs::write(&path, "hello\n").unwrap();
        let mut st = EditSessionStore::new(dir.join("bak"));
        let s = st
            .propose("p1", path.to_str().unwrap(), "hello\nworld\n")
            .unwrap();
        assert!(s.diff.contains("+world") || s.diff.contains("world"));
        st.confirm(&s.id).unwrap();
        assert_eq!(fs::read_to_string(&path).unwrap(), "hello\nworld\n");
        st.rollback(&s.id).unwrap();
        assert_eq!(fs::read_to_string(&path).unwrap(), "hello\n");
        let _ = fs::remove_dir_all(&dir);
    }
}
