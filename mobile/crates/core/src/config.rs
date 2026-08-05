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
        Self {
            base_url: std::env::var("TAKTON_BASE_URL")
                .unwrap_or_else(|_| "http://127.0.0.1:8090".into()),
            host_bind: std::env::var("TAKTON_MOBILE_HOST").unwrap_or_else(|_| "0.0.0.0".into()),
            host_port: std::env::var("TAKTON_MOBILE_PORT")
                .ok()
                .and_then(|s| s.parse().ok())
                .unwrap_or(8080),
            platform: PlatformKind::detect(),
            data_dir: default_data_dir(),
        }
    }
}

/// Writable data dir for every platform (Android included).
///
/// `dirs::data_dir()` is often `None` on Android → previous code used `.` which
/// is not writable and caused `Store::open` panics → white screen / crash.
pub fn default_data_dir() -> PathBuf {
    if let Ok(p) = std::env::var("TAKTON_DATA_DIR") {
        let pb = PathBuf::from(p);
        if !pb.as_os_str().is_empty() {
            return pb;
        }
    }

    // Prefer known-good app-private path on Android when Flutter didn't pass one.
    #[cfg(target_os = "android")]
    {
        let candidates = [
            PathBuf::from("/data/user/0/dev.takton.takton_mobile/files/takton-mobile"),
            PathBuf::from("/data/data/dev.takton.takton_mobile/files/takton-mobile"),
            std::env::temp_dir().join("takton-mobile"),
        ];
        for c in candidates {
            if std::fs::create_dir_all(&c).is_ok() {
                return c;
            }
        }
        return std::env::temp_dir().join("takton-mobile");
    }

    #[cfg(not(target_os = "android"))]
    {
        if let Some(d) = dirs::data_dir() {
            return d.join("takton-mobile");
        }
        if let Some(h) = dirs::home_dir() {
            return h.join(".local/share/takton-mobile");
        }
        std::env::temp_dir().join("takton-mobile")
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

    /// PC backend port extracted from `base_url` (default 8090).
    pub fn backend_port(&self) -> u16 {
        parse_base_url_parts(&self.base_url).2
    }
}

/// Parse `http(s)://host[:port]/path` → (scheme, host, port).
/// Defaults: http + 127.0.0.1 + 8090 (Takton PC backend).
pub fn parse_base_url_parts(base: &str) -> (String, String, u16) {
    let b = base.trim().trim_end_matches('/');
    if b.is_empty() {
        return ("http".into(), "127.0.0.1".into(), 8090);
    }

    let (scheme, rest) = if let Some(r) = b.strip_prefix("https://") {
        ("https", r)
    } else if let Some(r) = b.strip_prefix("http://") {
        ("http", r)
    } else if b.contains("://") {
        // unknown scheme — keep host parse best-effort
        let after = b.split("://").nth(1).unwrap_or(b);
        return parse_host_port("http", after);
    } else {
        return parse_host_port("http", b);
    };

    parse_host_port(scheme, rest)
}

fn parse_host_port(scheme: &str, rest: &str) -> (String, String, u16) {
    // strip path/query
    let authority = rest.split(['/', '?', '#']).next().unwrap_or(rest);
    if authority.is_empty() {
        return (scheme.into(), "127.0.0.1".into(), default_port(scheme));
    }

    // [ipv6]:port or host:port
    if let Some(inner) = authority.strip_prefix('[') {
        if let Some(end) = inner.find(']') {
            let host = &inner[..end];
            let after = &inner[end + 1..];
            let port = after
                .strip_prefix(':')
                .and_then(|p| p.parse().ok())
                .unwrap_or_else(|| default_port(scheme));
            return (scheme.into(), host.to_string(), port);
        }
    }

    // host:port — only split on last colon if it looks like a port
    if let Some((host, port_s)) = authority.rsplit_once(':') {
        if !host.is_empty() && port_s.chars().all(|c| c.is_ascii_digit()) {
            if let Ok(port) = port_s.parse::<u16>() {
                return (scheme.into(), host.to_string(), port);
            }
        }
    }

    (scheme.into(), authority.to_string(), default_port(scheme))
}

fn default_port(scheme: &str) -> u16 {
    match scheme {
        "https" => 443,
        // Takton PC backend listens on 8090 when port omitted from base_url
        _ => 8090,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_explicit_port() {
        let (s, h, p) = parse_base_url_parts("http://192.168.1.8:8090");
        assert_eq!(s, "http");
        assert_eq!(h, "192.168.1.8");
        assert_eq!(p, 8090);
    }

    #[test]
    fn defaults_http_to_8090() {
        let (s, h, p) = parse_base_url_parts("http://127.0.0.1");
        assert_eq!((s.as_str(), h.as_str(), p), ("http", "127.0.0.1", 8090));
    }

    #[test]
    fn strips_path() {
        let (_, h, p) = parse_base_url_parts("http://pc.local:9000/api");
        assert_eq!(h, "pc.local");
        assert_eq!(p, 9000);
    }
}
