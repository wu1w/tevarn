//! Session-scoped tool grants (this-session allow).
//!
//! Python `grant_store` persists to disk; the host map is the live court
//! authority so `decide_tool` does not depend on `_session_grant` flags.

use std::collections::{HashMap, HashSet};

use serde_json::Value;

#[derive(Debug, Default, Clone)]
pub struct SessionGrantStore {
    grants: HashMap<String, HashSet<String>>,
}

impl SessionGrantStore {
    pub fn add(&mut self, session_id: &str, sigs: impl IntoIterator<Item = String>) {
        let sid = session_id.trim();
        if sid.is_empty() {
            return;
        }
        let bucket = self.grants.entry(sid.to_string()).or_default();
        for s in sigs {
            let t = s.trim();
            if !t.is_empty() {
                bucket.insert(t.to_string());
            }
        }
    }

    pub fn has(&self, session_id: &str, sig: &str) -> bool {
        self.grants
            .get(session_id)
            .map(|s| s.contains(sig))
            .unwrap_or(false)
    }

    pub fn clear(&mut self, session_id: &str) {
        self.grants.remove(session_id);
    }

    pub fn snapshot(&self, session_id: &str) -> Vec<String> {
        let mut v: Vec<String> = self
            .grants
            .get(session_id)
            .map(|s| s.iter().cloned().collect())
            .unwrap_or_default();
        v.sort();
        v
    }
}

/// Parity with Python `grant_store.allow_signature`.
pub fn allow_signature(tool: &str, args: Option<&Value>) -> String {
    const CMD: &[&str] = &["command", "bash", "shell", "python", "process"];
    if CMD.contains(&tool) {
        if let Some(raw) = args.and_then(|a| {
            a.get("command")
                .or_else(|| a.get("cmd"))
                .and_then(|v| v.as_str())
        }) {
            let head = raw.split_whitespace().next().unwrap_or("");
            let head = head
                .rsplit(['/', '\\'])
                .next()
                .unwrap_or(head)
                .trim();
            if !head.is_empty() {
                return format!("{tool}:{head}");
            }
        }
    }
    tool.to_string()
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn command_head_signature() {
        let args = json!({"command": "rm -rf build"});
        assert_eq!(
            allow_signature("command", Some(&args)),
            "command:rm"
        );
        assert_eq!(allow_signature("file_write", Some(&args)), "file_write");
    }

    #[test]
    fn store_roundtrip() {
        let mut s = SessionGrantStore::default();
        s.add("s1", ["command:rm".into(), "command".into()]);
        assert!(s.has("s1", "command:rm"));
        assert!(s.has("s1", "command"));
        assert!(!s.has("s1", "command:npm"));
        s.clear("s1");
        assert!(!s.has("s1", "command"));
    }
}
