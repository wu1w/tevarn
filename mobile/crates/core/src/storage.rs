use crate::error::{Error, Result};
use serde::{de::DeserializeOwned, Serialize};
use std::fs;
use std::path::{Path, PathBuf};

#[derive(Clone)]
pub struct Store {
    root: PathBuf,
}


impl Store {
    pub fn open(root: impl AsRef<Path>) -> Result<Self> {
        let root = root.as_ref().to_path_buf();
        fs::create_dir_all(&root)?;
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            let _ = fs::set_permissions(&root, fs::Permissions::from_mode(0o700));
        }
        Ok(Self { root })
    }

    pub fn path(&self, name: &str) -> PathBuf {
        self.root.join(name)
    }

    pub fn save_json<T: Serialize>(&self, name: &str, value: &T) -> Result<()> {
        let path = self.path(name);
        if let Some(parent) = path.parent() {
            fs::create_dir_all(parent)?;
        }
        let data = serde_json::to_vec_pretty(value)?;
        // Atomic write: temp + rename
        let tmp = path.with_extension("tmp");
        fs::write(&tmp, data)?;
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            let _ = fs::set_permissions(&tmp, fs::Permissions::from_mode(0o600));
        }
        fs::rename(&tmp, &path)?;
        Ok(())
    }

    pub fn load_json<T: DeserializeOwned>(&self, name: &str) -> Result<Option<T>> {
        let path = self.path(name);
        if !path.exists() {
            return Ok(None);
        }
        let data = fs::read(path)?;
        Ok(Some(serde_json::from_slice(&data)?))
    }

    pub fn delete(&self, name: &str) -> Result<()> {
        let path = self.path(name);
        if path.exists() {
            fs::remove_file(path)?;
        }
        Ok(())
    }

    pub fn clear_session_cache(&self) -> Result<()> {
        let p = self.path("session_cache.json");
        if p.exists() {
            fs::remove_file(p).map_err(Error::from)?;
        }
        Ok(())
    }
}
