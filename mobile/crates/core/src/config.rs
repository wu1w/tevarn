use serde::{Deserialize, Serialize};
use std::path::PathBuf;

use crate::platform::PlatformKind;

/// Runtime configuration for the mobile client.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AppConfig {
    /// Takton backend base, e.g. `http://192.168.5.32:8090`
    pub base_url: String,
    /// Host bind for the mobile shell (web preview / Android local server)
    pub host_bind: String,
    pub host_port: u16,
    pub platform: PlatformKind,
    /// Persist credentials under this dir (platform-specific default)
    pub data_dir: PathBuf,
}

impl Default for AppConfig {
    fn default() -> Self {
        let data_dir = dirs::data_dir()
            .unwrap_or_else(|| PathBuf::from("."))
            .join("takton-mobile");
        Self {
            base_url: std::env::var("TAKTON_BASE_URL")
                .unwrap_or_else(|_| "http://127.0.0.1:8090".into()),
            host_bind: std::env::var("TAKTON_MOBILE_HOST").unwrap_or_else(|_| "0.0.0.0".into()),
            host_port: std::env::var("TAKTON_MOBILE_PORT")
                .ok()
                .and_then(|s| s.parse().ok())
                .unwrap_or(8080),
            platform: PlatformKind::detect(),
            data_dir,
        }
    }
}

impl AppConfig {
    pub fn api_root(&self) -> String {
        let b = self.base_url.trim_end_matches('/');
        if b.ends_with("/api") {
            b.to_string()
        } else {
            format!("{b}/api")
        }
    }

    pub fn ws_root(&self) -> String {
        let api = self.api_root();
        if let Some(rest) = api.strip_prefix("https://") {
            format!("wss://{rest}")
        } else if let Some(rest) = api.strip_prefix("http://") {
            format!("ws://{rest}")
        } else {
            format!("ws://{api}")
        }
    }
}
