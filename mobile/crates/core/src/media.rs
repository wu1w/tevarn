//! Local media capture store (voice notes / camera photos) — no mock, real files.

use crate::error::{Error, Result};
use crate::storage::Store;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::path::{Path, PathBuf};
use uuid::Uuid;

const INDEX_FILE: &str = "media_index.json";

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct MediaItem {
    pub id: String,
    pub kind: String, // image | audio | file
    pub filename: String,
    pub mime: String,
    pub size: u64,
    pub path: String,
    pub created_at: String,
}

#[derive(Debug, Default, Serialize, Deserialize)]
struct MediaIndex {
    items: Vec<MediaItem>,
}

pub struct MediaStore {
    root: PathBuf,
    store: Store,
}

impl MediaStore {
    pub fn open(data_dir: &Path) -> Result<Self> {
        let root = data_dir.join("media");
        std::fs::create_dir_all(&root).map_err(|e| Error::Io(e))?;
        let store = Store::open(data_dir)?;
        Ok(Self { root, store })
    }

    pub fn save(
        &self,
        kind: &str,
        filename: &str,
        mime: &str,
        bytes: &[u8],
    ) -> Result<MediaItem> {
        if bytes.is_empty() {
            return Err(Error::Msg("empty media".into()));
        }
        if bytes.len() > 15 * 1024 * 1024 {
            return Err(Error::Msg("media too large (max 15MB)".into()));
        }
        let id = Uuid::new_v4().to_string();
        let safe = filename
            .chars()
            .map(|c| if c.is_ascii_alphanumeric() || c == '.' || c == '-' || c == '_' { c } else { '_' })
            .collect::<String>();
        let safe = if safe.is_empty() { format!("{id}.bin") } else { safe };
        let rel = format!("{id}_{safe}");
        let path = self.root.join(&rel);
        std::fs::write(&path, bytes).map_err(|e| Error::Io(e))?;
        let item = MediaItem {
            id: id.clone(),
            kind: kind.to_string(),
            filename: safe,
            mime: mime.to_string(),
            size: bytes.len() as u64,
            path: path.display().to_string(),
            created_at: chrono::Utc::now().to_rfc3339(),
        };
        let mut idx: MediaIndex = self.store.load_json(INDEX_FILE)?.unwrap_or_default();
        idx.items.insert(0, item.clone());
        if idx.items.len() > 200 {
            idx.items.truncate(200);
        }
        self.store.save_json(INDEX_FILE, &idx)?;
        Ok(item)
    }

    pub fn list(&self) -> Result<Vec<MediaItem>> {
        let idx: MediaIndex = self.store.load_json(INDEX_FILE)?.unwrap_or_default();
        Ok(idx.items)
    }

    pub fn get_bytes(&self, id: &str) -> Result<(MediaItem, Vec<u8>)> {
        let idx: MediaIndex = self.store.load_json(INDEX_FILE)?.unwrap_or_default();
        let item = idx
            .items
            .iter()
            .find(|i| i.id == id)
            .cloned()
            .ok_or_else(|| Error::Msg("media not found".into()))?;
        let bytes = std::fs::read(&item.path).map_err(|e| Error::Io(e))?;
        Ok((item, bytes))
    }

    pub fn as_json(&self) -> Value {
        json!({ "items": self.list().unwrap_or_default() })
    }
}
