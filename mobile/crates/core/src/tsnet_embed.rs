//! Embedded Tailscale userspace (tsnet) process manager.
//!
//! Both PC and phone run the same `tevarn-tsnet` binary as a child of the
//! Tevarn engine — no separate system Tailscale install required when auth
//! key + binary are present.
//!
//! Roles:
//! - `pc`    — join tailnet + reverse-proxy local backend onto the mesh
//! - `phone` — join tailnet only (dial-out client for remote PC)

use crate::error::{Error, Result};
use crate::storage::Store;
use parking_lot::Mutex;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::fs;
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::Arc;
use std::time::{Duration, Instant};

const AUTH_FILE: &str = "ts_authkey";
const PID_FILE: &str = "tsnet.pid";
const EMBED_CFG: &str = "tsnet_embed.json";
const DEFAULT_STATUS: &str = "127.0.0.1:17891";

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, Default)]
#[serde(rename_all = "lowercase")]
pub enum TsnetRole {
    #[default]
    Pc,
    Phone,
}

impl TsnetRole {
    pub fn as_str(self) -> &'static str {
        match self {
            TsnetRole::Pc => "pc",
            TsnetRole::Phone => "phone",
        }
    }

    pub fn parse(s: &str) -> Self {
        match s.to_ascii_lowercase().as_str() {
            "phone" | "client" | "mobile" => TsnetRole::Phone,
            _ => TsnetRole::Pc,
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TsnetEmbedConfig {
    pub role: TsnetRole,
    pub hostname: String,
    /// Absolute or relative path to tevarn-tsnet binary
    pub binary: Option<String>,
    /// Local backend host:port (PC role)
    pub backend: String,
    /// Listen address on tailnet (PC role)
    pub listen: String,
    /// Local status HTTP for probe
    pub status_addr: String,
    /// Auto-start when mode is ts/auto and auth key is set
    pub auto_start: bool,
    /// Prefer system Tailscale when already up (skip embed)
    pub prefer_system: bool,
}

impl Default for TsnetEmbedConfig {
    fn default() -> Self {
        let role = match std::env::var("TEVARN_TSNET_ROLE")
            .unwrap_or_default()
            .to_ascii_lowercase()
            .as_str()
        {
            "phone" | "client" | "mobile" => TsnetRole::Phone,
            _ => TsnetRole::Pc,
        };
        let default_hn = if role == TsnetRole::Phone {
            "tevarn-phone"
        } else {
            "tevarn-pc"
        };
        Self {
            role,
            hostname: std::env::var("TEVARN_TSNET_HOSTNAME")
                .or_else(|_| std::env::var("TEVARN_MESH_HOSTNAME"))
                .unwrap_or_else(|_| default_hn.into()),
            binary: std::env::var("TEVARN_TSNET_BIN").ok(),
            backend: std::env::var("TEVARN_BACKEND")
                .unwrap_or_else(|_| "127.0.0.1:8090".into()),
            listen: std::env::var("TEVARN_TSNET_LISTEN").unwrap_or_else(|_| ":8090".into()),
            status_addr: std::env::var("TEVARN_TSNET_STATUS")
                .unwrap_or_else(|_| DEFAULT_STATUS.into()),
            auto_start: std::env::var("TEVARN_TSNET_AUTO")
                .map(|v| v != "0" && v.to_ascii_lowercase() != "false")
                .unwrap_or(true),
            prefer_system: true,
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct TsnetEmbedStatus {
    pub running: bool,
    pub role: String,
    pub pid: Option<u32>,
    pub binary: Option<String>,
    pub hostname: String,
    pub tailscale_ip: Option<String>,
    pub backend: String,
    pub detail: String,
    pub auth_key_set: bool,
    pub auth_key_masked: String,
    pub health_path: Option<String>,
    pub status_addr: String,
    pub last_error: Option<String>,
    pub started_at: Option<i64>,
}

struct LiveChild {
    child: Child,
    started_at: Instant,
    role: TsnetRole,
}

/// Process manager — one embed instance per MeshService / data dir.
pub struct TsnetEmbed {
    store: Store,
    cfg: Mutex<TsnetEmbedConfig>,
    live: Mutex<Option<LiveChild>>,
    last_err: Mutex<Option<String>>,
    /// cached IP from last probe
    cached_ip: Mutex<Option<String>>,
}

impl TsnetEmbed {
    pub fn open(store: Store) -> Self {
        let cfg = store
            .load_json::<TsnetEmbedConfig>(EMBED_CFG)
            .ok()
            .flatten()
            .unwrap_or_default();
        // Import env auth key into store once if present
        let emb = Self {
            store,
            cfg: Mutex::new(cfg),
            live: Mutex::new(None),
            last_err: Mutex::new(None),
            cached_ip: Mutex::new(None),
        };
        if emb.auth_key().is_none() {
            if let Ok(k) = std::env::var("TS_AUTHKEY").or_else(|_| std::env::var("TEVARN_TS_AUTHKEY"))
            {
                if !k.trim().is_empty() {
                    let _ = emb.set_auth_key(k.trim());
                }
            }
        }
        emb
    }

    pub fn config(&self) -> TsnetEmbedConfig {
        self.cfg.lock().clone()
    }

    pub fn save_config(&self, c: &TsnetEmbedConfig) -> Result<()> {
        *self.cfg.lock() = c.clone();
        self.store.save_json(EMBED_CFG, c)
    }

    pub fn set_role(&self, role: TsnetRole) -> Result<TsnetEmbedConfig> {
        let mut c = self.config();
        c.role = role;
        if c.hostname == "tevarn-pc" && role == TsnetRole::Phone {
            c.hostname = "tevarn-phone".into();
        }
        if c.hostname == "tevarn-phone" && role == TsnetRole::Pc {
            c.hostname = "tevarn-pc".into();
        }
        // Avoid status-port clash when PC + phone host run on same machine (dev).
        if role == TsnetRole::Phone && c.status_addr.ends_with(":17891") {
            c.status_addr = "127.0.0.1:17892".into();
        }
        if role == TsnetRole::Pc && c.status_addr.ends_with(":17892") {
            c.status_addr = "127.0.0.1:17891".into();
        }
        self.save_config(&c)?;
        Ok(c)
    }

    pub fn set_hostname(&self, name: &str) -> Result<TsnetEmbedConfig> {
        let mut c = self.config();
        let n = name.trim();
        if !n.is_empty() {
            c.hostname = n.to_string();
        }
        self.save_config(&c)?;
        Ok(c)
    }

    pub fn set_backend(&self, backend: &str) -> Result<TsnetEmbedConfig> {
        let mut c = self.config();
        let b = backend.trim();
        if !b.is_empty() {
            c.backend = b.to_string();
        }
        self.save_config(&c)?;
        Ok(c)
    }

    pub fn set_binary(&self, path: &str) -> Result<TsnetEmbedConfig> {
        let mut c = self.config();
        let p = path.trim();
        c.binary = if p.is_empty() { None } else { Some(p.to_string()) };
        self.save_config(&c)?;
        Ok(c)
    }

    pub fn set_auto_start(&self, v: bool) -> Result<TsnetEmbedConfig> {
        let mut c = self.config();
        c.auto_start = v;
        self.save_config(&c)?;
        Ok(c)
    }

    /// Persist auth key (0600 file). Never returns full key via status.
    pub fn set_auth_key(&self, key: &str) -> Result<()> {
        let key = key.trim();
        if key.is_empty() {
            return Err(Error::Msg("auth key empty".into()));
        }
        if !(key.starts_with("tskey-") || key.starts_with("tskeyauth") || key.len() > 20) {
            // soft warn only — Tailscale keys evolve
        }
        let path = self.store.path(AUTH_FILE);
        if let Some(parent) = path.parent() {
            fs::create_dir_all(parent)?;
        }
        fs::write(&path, key.as_bytes())?;
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            let _ = fs::set_permissions(&path, fs::Permissions::from_mode(0o600));
        }
        Ok(())
    }

    pub fn clear_auth_key(&self) -> Result<()> {
        let path = self.store.path(AUTH_FILE);
        if path.exists() {
            fs::remove_file(path)?;
        }
        Ok(())
    }

    pub fn auth_key(&self) -> Option<String> {
        let path = self.store.path(AUTH_FILE);
        if path.exists() {
            let s = fs::read_to_string(path).ok()?;
            let t = s.trim().to_string();
            if t.is_empty() {
                return None;
            }
            return Some(t);
        }
        std::env::var("TS_AUTHKEY")
            .or_else(|_| std::env::var("TEVARN_TS_AUTHKEY"))
            .ok()
            .map(|s| s.trim().to_string())
            .filter(|s| !s.is_empty())
    }

    pub fn auth_key_set(&self) -> bool {
        self.auth_key().is_some()
    }

    pub fn mask_key(key: &str) -> String {
        if key.len() <= 10 {
            return "••••".into();
        }
        format!("{}…{}", &key[..8], &key[key.len().saturating_sub(4)..])
    }

    /// Resolve binary path: config → env → common locations → PATH.
    /// Resolve binary path: config → env → common locations → PATH.
    pub fn resolve_binary(&self) -> Option<PathBuf> {
        let cfg = self.config();
        let mut candidates: Vec<PathBuf> = Vec::new();
        if let Some(b) = cfg.binary {
            candidates.push(PathBuf::from(b));
        }
        if let Ok(b) = std::env::var("TEVARN_TSNET_BIN") {
            if !b.is_empty() {
                candidates.push(PathBuf::from(b));
            }
        }
        if let Ok(exe) = std::env::current_exe() {
            if let Some(dir) = exe.parent() {
                candidates.push(dir.join("tevarn-tsnet"));
            }
        }
        candidates.push(PathBuf::from(
            "/workspace/tevarn-mobile/sidecar/tsnet/tevarn-tsnet",
        ));
        candidates.push(PathBuf::from("./sidecar/tsnet/tevarn-tsnet"));
        candidates.push(PathBuf::from("./tevarn-tsnet"));
        if let Some(h) = dirs::home_dir() {
            candidates.push(h.join(".tevarn/bin/tevarn-tsnet"));
        }
        candidates.push(PathBuf::from("/usr/local/bin/tevarn-tsnet"));

        for c in &candidates {
            if c.is_file() {
                return Some(c.clone());
            }
        }
        if let Ok(out) = Command::new("which").arg("tevarn-tsnet").output() {
            if out.status.success() {
                let s = String::from_utf8_lossy(&out.stdout).trim().to_string();
                if !s.is_empty() && Path::new(&s).is_file() {
                    return Some(PathBuf::from(s));
                }
            }
        }
        None
    }

    pub fn is_running(&self) -> bool {
        self.reap();
        if self.live.lock().is_some() {
            return true;
        }
        // orphan via pid file
        if let Some(pid) = self.read_pid() {
            return process_alive(pid);
        }
        // health file / status HTTP
        if self.health_file().map(|p| p.exists()).unwrap_or(false) {
            return true;
        }
        self.probe_status_ip().is_some()
    }

    fn health_file(&self) -> Option<PathBuf> {
        if let Ok(p) = std::env::var("TEVARN_TSNET_HEALTH") {
            if !p.is_empty() {
                return Some(PathBuf::from(p));
            }
        }
        Some(self.store.path("tsnet.health"))
    }

    fn read_pid(&self) -> Option<u32> {
        let p = self.store.path(PID_FILE);
        let s = fs::read_to_string(p).ok()?;
        s.trim().parse().ok()
    }

    fn write_pid(&self, pid: u32) {
        let _ = fs::write(self.store.path(PID_FILE), pid.to_string());
    }

    fn clear_pid(&self) {
        let _ = fs::remove_file(self.store.path(PID_FILE));
    }

    fn reap(&self) {
        let mut g = self.live.lock();
        if let Some(live) = g.as_mut() {
            match live.child.try_wait() {
                Ok(Some(_status)) => {
                    *g = None;
                    self.clear_pid();
                    if let Some(h) = self.health_file() {
                        let _ = fs::remove_file(h);
                    }
                }
                Ok(None) => {}
                Err(_) => {
                    *g = None;
                }
            }
        }
    }

    /// Start embedded tsnet. Idempotent if already running.
    pub fn start(&self) -> Result<TsnetEmbedStatus> {
        self.reap();
        if self.is_running() {
            // refresh IP
            if let Some(ip) = self.probe_status_ip() {
                *self.cached_ip.lock() = Some(ip.clone());
                std::env::set_var("TEVARN_TS_IP", &ip);
            }
            return Ok(self.status());
        }

        let cfg = self.config();
        if cfg.prefer_system {
            if let Some(ip) = detect_system_ts_ip() {
                *self.cached_ip.lock() = Some(ip.clone());
                std::env::set_var("TEVARN_TS_IP", &ip);
                *self.last_err.lock() = None;
                return Ok(TsnetEmbedStatus {
                    running: true,
                    role: cfg.role.as_str().into(),
                    pid: None,
                    binary: None,
                    hostname: cfg.hostname,
                    tailscale_ip: Some(ip),
                    backend: cfg.backend,
                    detail: "使用系统 Tailscale（已在线，跳过内嵌）".into(),
                    auth_key_set: self.auth_key_set(),
                    auth_key_masked: self
                        .auth_key()
                        .map(|k| Self::mask_key(&k))
                        .unwrap_or_default(),
                    health_path: None,
                    status_addr: cfg.status_addr,
                    last_error: None,
                    started_at: Some(chrono::Utc::now().timestamp()),
                });
            }
        }

        let auth = self
            .auth_key()
            .ok_or_else(|| Error::Msg("未配置 Tailscale auth key · 请在连接页粘贴 tskey-auth-…".into()))?;

        let bin = self.resolve_binary().ok_or_else(|| {
            Error::Msg(
                "未找到 tevarn-tsnet 二进制 · 请在 PC 构建 sidecar/tsnet 或设置 TEVARN_TSNET_BIN"
                    .into(),
            )
        })?;

        let health = self.health_file().unwrap_or_else(|| self.store.path("tsnet.health"));
        if let Some(parent) = health.parent() {
            let _ = fs::create_dir_all(parent);
        }
        let state_dir = self.store.path("tsnet-state");
        let _ = fs::create_dir_all(&state_dir);

        let mut cmd = Command::new(&bin);
        cmd.arg("-role")
            .arg(cfg.role.as_str())
            .arg("-hostname")
            .arg(&cfg.hostname)
            .arg("-state")
            .arg(&state_dir)
            .arg("-health")
            .arg(&health)
            .arg("-status")
            .arg(&cfg.status_addr)
            .env("TS_AUTHKEY", &auth)
            .env("TEVARN_TS_AUTHKEY", &auth)
            .env("TEVARN_TSNET_HOSTNAME", &cfg.hostname)
            .stdout(Stdio::null())
            .stderr(Stdio::piped());

        if cfg.role == TsnetRole::Pc {
            cmd.arg("-backend").arg(&cfg.backend);
            cmd.arg("-listen").arg(&cfg.listen);
        } else {
            // phone: join-only, no proxy (sidecar still accepts flags)
            cmd.arg("-backend").arg("127.0.0.1:9"); // unused
            cmd.arg("-listen").arg("127.0.0.1:0");
            cmd.arg("-client-only");
        }

        let child = cmd.spawn().map_err(|e| {
            Error::Msg(format!("启动 tsnet 失败: {e} · bin={}", bin.display()))
        })?;
        let pid = child.id();
        self.write_pid(pid);
        *self.live.lock() = Some(LiveChild {
            child,
            started_at: Instant::now(),
            role: cfg.role,
        });
        *self.last_err.lock() = None;

        // Wait briefly for IP
        for _ in 0..40 {
            std::thread::sleep(Duration::from_millis(250));
            if let Some(ip) = self.probe_status_ip() {
                *self.cached_ip.lock() = Some(ip.clone());
                std::env::set_var("TEVARN_TS_IP", &ip);
                break;
            }
            self.reap();
            if self.live.lock().is_none() && !process_alive(pid) {
                let err = "tsnet 进程已退出 · 检查 auth key / 网络".to_string();
                *self.last_err.lock() = Some(err.clone());
                self.clear_pid();
                return Err(Error::Msg(err));
            }
        }

        Ok(self.status())
    }

    pub fn stop(&self) -> Result<TsnetEmbedStatus> {
        self.reap();
        // Kill live child
        if let Some(mut live) = self.live.lock().take() {
            let _ = live.child.kill();
            let _ = live.child.wait();
        }
        if let Some(pid) = self.read_pid() {
            #[cfg(unix)]
            unsafe {
                libc_kill(pid as i32, 15);
            }
            #[cfg(not(unix))]
            let _ = pid;
        }
        self.clear_pid();
        if let Some(h) = self.health_file() {
            let _ = fs::remove_file(h);
        }
        *self.cached_ip.lock() = None;
        // Don't clear TEVARN_TS_IP if system TS still has it
        if detect_system_ts_ip().is_none() {
            std::env::remove_var("TEVARN_TS_IP");
        }
        Ok(self.status())
    }

    /// Auto-start when configured (ts/auto mode + key + binary).
    pub fn ensure_started_for_mode(&self, want_ts: bool) -> Result<Option<TsnetEmbedStatus>> {
        if !want_ts {
            return Ok(None);
        }
        let cfg = self.config();
        if !cfg.auto_start {
            return Ok(None);
        }
        if !self.auth_key_set() {
            return Ok(None);
        }
        if self.is_running() {
            return Ok(Some(self.status()));
        }
        // Only auto-start if binary exists (don't error UI on missing bin in LAN-only)
        if self.resolve_binary().is_none() && cfg.prefer_system && detect_system_ts_ip().is_some() {
            return Ok(Some(self.status()));
        }
        if self.resolve_binary().is_none() {
            return Ok(None);
        }
        match self.start() {
            Ok(s) => Ok(Some(s)),
            Err(e) => {
                *self.last_err.lock() = Some(e.to_string());
                Err(e)
            }
        }
    }

    pub fn tailscale_ip(&self) -> Option<String> {
        if let Some(ip) = self.cached_ip.lock().clone() {
            return Some(ip);
        }
        if let Some(ip) = self.probe_status_ip() {
            *self.cached_ip.lock() = Some(ip.clone());
            return Some(ip);
        }
        detect_system_ts_ip()
    }

    fn probe_status_ip(&self) -> Option<String> {
        let cfg = self.config();
        use std::io::{Read, Write};
        use std::net::{SocketAddr, TcpStream, ToSocketAddrs};
        let addr = cfg.status_addr.clone();
        let sock: SocketAddr = addr.to_socket_addrs().ok()?.next()?;
        let mut stream =
            TcpStream::connect_timeout(&sock, Duration::from_millis(400)).ok()?;
        stream
            .set_read_timeout(Some(Duration::from_millis(400)))
            .ok()?;
        let req = format!("GET /ip HTTP/1.0\r\nHost: {addr}\r\nConnection: close\r\n\r\n");
        stream.write_all(req.as_bytes()).ok()?;
        let mut buf = String::new();
        stream.read_to_string(&mut buf).ok()?;
        let body = buf.split("\r\n\r\n").nth(1)?.trim();
        if body.is_empty() {
            return None;
        }
        let ip = body.lines().next()?.trim();
        if ip.parse::<std::net::Ipv4Addr>().is_ok() || ip.parse::<std::net::Ipv6Addr>().is_ok() {
            return Some(ip.to_string());
        }
        None
    }

    pub fn status(&self) -> TsnetEmbedStatus {
        self.reap();
        let cfg = self.config();
        let running = self.is_running();
        let ip = if running {
            self.tailscale_ip()
        } else {
            detect_system_ts_ip()
        };
        let pid = self.live.lock().as_ref().map(|l| l.child.id()).or_else(|| {
            self.read_pid().filter(|&p| process_alive(p))
        });
        let auth = self.auth_key();
        let bin = self.resolve_binary().map(|p| p.display().to_string());
        let detail = if running {
            if let Some(ref ip) = ip {
                format!("内嵌 tsnet 在线 · {ip}")
            } else {
                "内嵌 tsnet 运行中 · 等待分配 IP…".into()
            }
        } else if auth.is_some() && bin.is_some() {
            "已配置 · 可一键启动内嵌 Tailscale".into()
        } else if auth.is_some() {
            "已配置 auth key · 缺少 tevarn-tsnet 二进制".into()
        } else if bin.is_some() {
            "已找到二进制 · 请粘贴 auth key".into()
        } else {
            "未配置内嵌 mesh".into()
        };
        TsnetEmbedStatus {
            running,
            role: cfg.role.as_str().into(),
            pid,
            binary: bin,
            hostname: cfg.hostname,
            tailscale_ip: ip,
            backend: cfg.backend,
            detail,
            auth_key_set: auth.is_some(),
            auth_key_masked: auth.map(|k| Self::mask_key(&k)).unwrap_or_default(),
            health_path: self.health_file().map(|p| p.display().to_string()),
            status_addr: cfg.status_addr,
            last_error: self.last_err.lock().clone(),
            started_at: self
                .live
                .lock()
                .as_ref()
                .map(|l| {
                    let elapsed = l.started_at.elapsed().as_secs() as i64;
                    chrono::Utc::now().timestamp() - elapsed
                }),
        }
    }

    pub fn status_json(&self) -> Value {
        let s = self.status();
        json!({
            "ok": true,
            "running": s.running,
            "role": s.role,
            "pid": s.pid,
            "binary": s.binary,
            "hostname": s.hostname,
            "tailscale_ip": s.tailscale_ip,
            "backend": s.backend,
            "detail": s.detail,
            "auth_key_set": s.auth_key_set,
            "auth_key_masked": s.auth_key_masked,
            "health_path": s.health_path,
            "status_addr": s.status_addr,
            "last_error": s.last_error,
            "started_at": s.started_at,
            "auto_start": self.config().auto_start,
            "prefer_system": self.config().prefer_system,
        })
    }
}

/// Shared handle for MeshService.
pub type TsnetEmbedHandle = Arc<TsnetEmbed>;

fn detect_system_ts_ip() -> Option<String> {
    if let Ok(ip) = std::env::var("TEVARN_TS_IP") {
        if !ip.trim().is_empty() && ip.parse::<std::net::Ipv4Addr>().is_ok() {
            // Only trust env if process/system still up — still ok for advertise
            return Some(ip.trim().to_string());
        }
    }
    let out = Command::new("tailscale")
        .args(["ip", "-4"])
        .output()
        .ok()?;
    if !out.status.success() {
        return None;
    }
    let s = String::from_utf8_lossy(&out.stdout);
    let ip = s.lines().next()?.trim();
    if ip.is_empty() {
        return None;
    }
    Some(ip.to_string())
}

fn process_alive(pid: u32) -> bool {
    #[cfg(unix)]
    {
        // signal 0
        unsafe { libc_kill(pid as i32, 0) == 0 }
    }
    #[cfg(not(unix))]
    {
        let _ = pid;
        false
    }
}

#[cfg(unix)]
unsafe fn libc_kill(pid: i32, sig: i32) -> i32 {
    // Avoid libc crate dep — use kill syscall via libc if linked, else /proc
    extern "C" {
        fn kill(pid: i32, sig: i32) -> i32;
    }
    kill(pid, sig)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::env::temp_dir;
    use uuid::Uuid;

    #[test]
    fn mask_and_auth_roundtrip() {
        let dir = temp_dir().join(format!("tsnet-embed-{}", Uuid::new_v4()));
        let store = Store::open(&dir).unwrap();
        let emb = TsnetEmbed::open(store);
        assert!(!emb.auth_key_set());
        emb.set_auth_key("tskey-auth-abcdefghijklmnopqrstuvwxyz").unwrap();
        assert!(emb.auth_key_set());
        let m = TsnetEmbed::mask_key("tskey-auth-abcdefghijklmnopqrstuvwxyz");
        assert!(m.contains('…'));
        assert!(!m.contains("mnop"));
    }
}
