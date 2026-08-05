//! Local session meta: custom titles + pin order (mirrors PC client-side titles).
//! Remote rename prefers writing `config.mobile_title` when PC is reachable;
//! always mirrored here so list order/titles work offline.

use crate::error::{Error, Result};
use crate::storage::Store;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::collections::HashMap;

const FILE: &str = "session_meta.json";
pub const LOCAL_ID: &str = "__local__";

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct SessionMetaStore {
    /// session_id → display title override
    #[serde(default)]
    pub titles: HashMap<String, String>,
    /// pinned ids, head = highest priority
    #[serde(default)]
    pub pinned: Vec<String>,
}

impl SessionMetaStore {
    pub fn load(store: &Store) -> Result<Self> {
        Ok(store.load_json(FILE)?.unwrap_or_default())
    }

    pub fn save(&self, store: &Store) -> Result<()> {
        store.save_json(FILE, self)
    }

    pub fn title_of(&self, id: &str) -> Option<String> {
        self.titles.get(id).cloned().filter(|s| !s.trim().is_empty())
    }

    pub fn is_pinned(&self, id: &str) -> bool {
        self.pinned.iter().any(|p| p == id)
    }

    pub fn set_title(&mut self, id: &str, title: &str) {
        let t = title.trim();
        if t.is_empty() {
            self.titles.remove(id);
        } else {
            self.titles.insert(id.to_string(), t.to_string());
        }
    }

    pub fn set_pinned(&mut self, id: &str, pinned: bool) {
        self.pinned.retain(|p| p != id);
        if pinned {
            self.pinned.insert(0, id.to_string());
        }
    }

    pub fn remove(&mut self, id: &str) {
        self.titles.remove(id);
        self.pinned.retain(|p| p != id);
    }

    /// Sort sessions: pinned first (by pin order), then keep relative order for unpinned.
    pub fn sort_ids(&self, mut ids: Vec<String>) -> Vec<String> {
        let pin_rank: HashMap<&str, usize> = self
            .pinned
            .iter()
            .enumerate()
            .map(|(i, id)| (id.as_str(), i))
            .collect();
        ids.sort_by(|a, b| {
            let pa = pin_rank.get(a.as_str());
            let pb = pin_rank.get(b.as_str());
            match (pa, pb) {
                (Some(i), Some(j)) => i.cmp(j),
                (Some(_), None) => std::cmp::Ordering::Less,
                (None, Some(_)) => std::cmp::Ordering::Greater,
                (None, None) => std::cmp::Ordering::Equal,
            }
        });
        ids
    }

    pub fn as_json(&self) -> Value {
        json!({
            "titles": self.titles,
            "pinned": self.pinned,
        })
    }
}

pub fn open_store(data_dir: &std::path::Path) -> Result<Store> {
    Store::open(data_dir).map_err(|e| Error::Msg(e.to_string()))
}
