//! Mesh + embedded Tailscale (seamless remote access).
//!
//! Product contract:
//! - Default mode is **auto** (LAN + TS dual path).
//! - PC holds auth key once; pair QR carries short-window `tsk` so the phone
//!   joins the same tailnet with **zero UI**.
//! - `tevarn-tsnet` is spawned as a child process by this service — users do
//!   not install or open system Tailscale on the happy path.

use crate::error::{Error, Result};
use crate::pair::MeshMode;
use crate::storage::Store;
use crate::tsnet_embed::{TsnetEmbed, TsnetRole};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::net::UdpSocket;
use std::process::Command;
use std::sync::Arc;
use std::time::{Duration, Instant};

const MESH_FILE: &str = "mesh_config.json";
const MESH_RUNTIME_FILE: &str = "mesh_runtime.json";

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct MeshConfig {
    pub mode: MeshMode,
    pub hostname: String,
    pub require_pair_confirm: bool,
    pub sidecar_bin: Option<String>,
    pub auth_key_set: bool,
    /// When true (default), pair QR embeds phone join key for seamless mesh.
    #[serde(default = "default_true")]
    pub seamless_qr: bool,
}

fn default_true() -> bool {
    true
}

impl Default for MeshConfig {
    fn default() -> Self {
        Self {
            // Product default: dual path. Offline/LAN-only still works.
            mode: MeshMode::Auto,
            hostname: std::env::var("TEVARN_MESH_HOSTNAME")
                .unwrap_or_else(|_| "tevarn-pc".into()),
            require_pair_confirm: false,
            sidecar_bin: std::env::var("TEVARN_TSNET_BIN").ok(),
            auth_key_set: std::env::var("TS_AUTHKEY").is_ok()
                || std::env::var("TEVARN_TS_AUTHKEY").is_ok(),
            seamless_qr: true,
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct MeshStatus {
    pub mode: MeshMode,
    pub online: bool,
    pub advertise_host: String,
    pub advertise_port: u16,
    pub scheme: String,
    pub backend: String,
    pub detail: String,
    pub tailscale_ip: Option<String>,
    pub lan_ip: Option<String>,
    pub sidecar_running: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct MeshRuntimeState {
    pub up: bool,
    pub hostname: String,
    #[serde(default)]
    pub auth_key_set: bool,
    #[serde(default)]
    pub backend: String,
    #[serde(default)]
    pub detail: String,
    #[serde(default)]
    pub ifaces: Vec<String>,
    #[serde(default)]
    pub fingerprint: String,
    #[serde(default)]
    pub last_change_at: i64,
}

pub struct MeshService {
    store: Store,
    config: parking_lot::RwLock<MeshConfig>,
    runtime: parking_lot::RwLock<MeshRuntimeState>,
    last: parking_lot::Mutex<Option<(Instant, MeshStatus)>>,
    port: parking_lot::RwLock<u16>,
    /// Embedded tsnet process (PC or phone role)
    embed: Arc<TsnetEmbed>,
}

impl MeshService {
    pub fn open(store: Store, port: u16) -> Self {
        let config = store
            .load_json::<MeshConfig>(MESH_FILE)
            .ok()
            .flatten()
            .unwrap_or_default();
        let runtime = store
            .load_json::<MeshRuntimeState>(MESH_RUNTIME_FILE)
            .ok()
            .flatten()
            .unwrap_or_default();
        let embed = Arc::new(TsnetEmbed::open(store.clone()));
        // Sync binary path from mesh config
        if let Some(bin) = config.sidecar_bin.as_deref() {
            let _ = embed.set_binary(bin);
        }
        // Default role from hostname hint
        let role = if config.hostname.contains("phone") {
            TsnetRole::Phone
        } else {
            TsnetRole::Pc
        };
        let _ = embed.set_role(role);
        let _ = embed.set_hostname(&config.hostname);
        let _ = embed.set_backend(&format!("127.0.0.1:{port}"));

        let mut cfg = config;
        cfg.auth_key_set = cfg.auth_key_set || embed.auth_key_set();
        let _ = store.save_json(MESH_FILE, &cfg);

        Self {
            store,
            config: parking_lot::RwLock::new(cfg),
            runtime: parking_lot::RwLock::new(runtime),
            last: parking_lot::Mutex::new(None),
            port: parking_lot::RwLock::new(port),
            embed,
        }
    }

    pub fn embed(&self) -> Arc<TsnetEmbed> {
        self.embed.clone()
    }

    pub fn set_backend_port(&self, port: u16) {
        if port == 0 {
            return;
        }
        let mut p = self.port.write();
        if *p != port {
            *p = port;
            *self.last.lock() = None;
            let _ = self.embed.set_backend(&format!("127.0.0.1:{port}"));
        }
    }

    pub fn backend_port(&self) -> u16 {
        *self.port.read()
    }

    pub fn config(&self) -> MeshConfig {
        let mut c = self.config.read().clone();
        c.auth_key_set = c.auth_key_set || self.embed.auth_key_set();
        c
    }

    pub fn set_mode(&self, mode: MeshMode) -> Result<MeshConfig> {
        let mut c = self.config.write();
        c.mode = mode;
        c.auth_key_set = self.embed.auth_key_set() || env_has_auth_key();
        self.store.save_json(MESH_FILE, &*c)?;
        *self.last.lock() = None;
        // Auto-start embed when entering ts/auto (silent)
        if matches!(mode, MeshMode::Ts | MeshMode::Auto) {
            let _ = self.embed.ensure_started_for_mode(true);
        }
        Ok(c.clone())
    }

    pub fn set_hostname(&self, name: &str) -> Result<MeshConfig> {
        let mut c = self.config.write();
        c.hostname = name.trim().to_string();
        if c.hostname.is_empty() {
            c.hostname = "tevarn-pc".into();
        }
        let _ = self.embed.set_hostname(&c.hostname);
        self.store.save_json(MESH_FILE, &*c)?;
        Ok(c.clone())
    }

    pub fn set_require_confirm(&self, v: bool) -> Result<MeshConfig> {
        let mut c = self.config.write();
        c.require_pair_confirm = v;
        self.store.save_json(MESH_FILE, &*c)?;
        Ok(c.clone())
    }

    /// One-time PC setup: store auth key, start embed. Phone never needs this UI
    /// when QR carries `tsk`.
    pub fn set_auth_key(&self, key: &str) -> Result<Value> {
        self.embed.set_auth_key(key)?;
        {
            let mut c = self.config.write();
            c.auth_key_set = true;
            self.store.save_json(MESH_FILE, &*c)?;
        }
        *self.last.lock() = None;
        // Start quietly if mode wants mesh
        let mode = self.config.read().mode;
        if matches!(mode, MeshMode::Ts | MeshMode::Auto) {
            match self.embed.start() {
                Ok(s) => Ok(json!({
                    "ok": true,
                    "auth_key_set": true,
                    "embed": s,
                    "detail": "远程已启用 · 之后扫码即可连接"
                })),
                Err(e) => Ok(json!({
                    "ok": true,
                    "auth_key_set": true,
                    "embed_error": e.to_string(),
                    "detail": "密钥已保存 · 启动 mesh 稍后自动重试"
                })),
            }
        } else {
            Ok(json!({
                "ok": true,
                "auth_key_set": true,
                "detail": "密钥已保存"
            }))
        }
    }

    pub fn clear_auth_key(&self) -> Result<()> {
        self.embed.clear_auth_key()?;
        let mut c = self.config.write();
        c.auth_key_set = false;
        self.store.save_json(MESH_FILE, &*c)?;
        Ok(())
    }

    /// Ensure mesh is ready for QR generation (PC host).
    /// Returns (lan, ts, hn, phone_join_key_opt)
    pub fn prepare_for_pairing(&self, mesh: MeshMode) -> (Option<String>, Option<String>, String, Option<String>) {
        let _ = self.set_mode(mesh);
        let cfg = self.config();
        let port = *self.port.read();
        let _ = self.embed.set_backend(&format!("127.0.0.1:{port}"));
        let _ = self.embed.set_hostname(&cfg.hostname);
        let _ = self.embed.set_role(TsnetRole::Pc);

        if matches!(mesh, MeshMode::Ts | MeshMode::Auto) && self.embed.auth_key_set() {
            // Best-effort start; do not fail pairing on mesh errors (LAN still works)
            let _ = self.embed.start();
            // Brief wait for IP so QR is complete on first show
            for _ in 0..12 {
                if self.embed.tailscale_ip().is_some() {
                    break;
                }
                std::thread::sleep(Duration::from_millis(200));
            }
        }

        *self.last.lock() = None;
        let (lan, ts, hn) = self.dual_paths();
        let tsk = if cfg.seamless_qr
            && matches!(mesh, MeshMode::Ts | MeshMode::Auto)
            && self.embed.auth_key_set()
        {
            self.embed.auth_key()
        } else {
            None
        };
        (lan, ts, hn, tsk)
    }

    /// Phone: after scanning QR with `tsk`, join mesh silently then return TS IP if any.
    pub fn phone_join_from_pair_key(&self, tsk: &str, hostname: Option<&str>) -> Result<Value> {
        let key = tsk.trim();
        if key.is_empty() {
            return Err(Error::Msg("empty phone join key".into()));
        }
        let hn = hostname
            .map(|s| s.trim().to_string())
            .filter(|s| !s.is_empty())
            .unwrap_or_else(|| {
                format!(
                    "tevarn-phone-{}",
                    &uuid::Uuid::new_v4().to_string()[..8]
                )
            });
        let _ = self.embed.set_role(TsnetRole::Phone);
        let _ = self.embed.set_hostname(&hn);
        self.embed.set_auth_key(key)?;
        {
            let mut c = self.config.write();
            c.auth_key_set = true;
            c.hostname = hn.clone();
            if c.mode == MeshMode::Off {
                c.mode = MeshMode::Auto;
            }
            self.store.save_json(MESH_FILE, &*c)?;
        }
        // Start client-only embed
        match self.embed.start() {
            Ok(st) => {
                let mut rt = self.runtime.write();
                rt.up = true;
                rt.hostname = hn;
                rt.auth_key_set = true;
                rt.backend = if st.running {
                    "embedded-tsnet".into()
                } else {
                    "pending".into()
                };
                rt.detail = st.detail.clone();
                let _ = self.store.save_json(MESH_RUNTIME_FILE, &*rt);
                *self.last.lock() = None;
                Ok(json!({
                    "ok": true,
                    "joined": st.running,
                    "tailscale_ip": st.tailscale_ip,
                    "detail": st.detail,
                    "backend": "embedded-tsnet",
                }))
            }
            Err(e) => {
                // Still mark intent — soft-pair can complete later
                let mut rt = self.runtime.write();
                rt.up = true;
                rt.auth_key_set = true;
                rt.backend = "embed-error".into();
                rt.detail = e.to_string();
                let _ = self.store.save_json(MESH_RUNTIME_FILE, &*rt);
                Ok(json!({
                    "ok": true,
                    "joined": false,
                    "error": e.to_string(),
                    "detail": "mesh 稍后自动重试 · 局域网仍可配对",
                    "backend": "embed-error",
                }))
            }
        }
    }

    pub fn start_embed(&self) -> Result<Value> {
        let st = self.embed.start()?;
        *self.last.lock() = None;
        Ok(json!(st))
    }

    pub fn stop_embed(&self) -> Result<Value> {
        let st = self.embed.stop()?;
        *self.last.lock() = None;
        Ok(json!(st))
    }

    pub fn status(&self) -> MeshStatus {
        if let Some((at, s)) = self.last.lock().as_ref() {
            if at.elapsed() < Duration::from_secs(2) {
                return s.clone();
            }
        }
        let s = self.probe();
        *self.last.lock() = Some((Instant::now(), s.clone()));
        s
    }

    fn probe(&self) -> MeshStatus {
        let cfg = self.config.read().clone();
        let port = *self.port.read();
        let lan = lan_ip();
        // Prefer embed IP, then system, then env
        let ts_ip = self
            .embed
            .tailscale_ip()
            .or_else(detect_tailscale_ip);
        let embed_running = self.embed.is_running();
        let (advertise_host, backend, online, detail) = match cfg.mode {
            MeshMode::Off => (
                "127.0.0.1".into(),
                "loopback".into(),
                true,
                "远程访问关闭 · 仅本机".into(),
            ),
            MeshMode::Lan => {
                let h = lan.clone().unwrap_or_else(|| "127.0.0.1".into());
                let ok = lan.is_some();
                (
                    h,
                    "lan".into(),
                    ok,
                    if ok {
                        "局域网可配对".into()
                    } else {
                        "未检测到局域网 IP · 回退 loopback".into()
                    },
                )
            }
            MeshMode::Ts => {
                if let Some(ip) = ts_ip.clone() {
                    (
                        ip,
                        if embed_running {
                            "embedded-tsnet".into()
                        } else {
                            "tailscale".into()
                        },
                        true,
                        "远程已就绪".into(),
                    )
                } else if cfg.auth_key_set || self.embed.auth_key_set() {
                    (
                        lan.clone().unwrap_or_else(|| "127.0.0.1".into()),
                        "tsnet-pending".into(),
                        false,
                        "正在建立安全连接…".into(),
                    )
                } else {
                    (
                        lan.clone().unwrap_or_else(|| "127.0.0.1".into()),
                        "ts-setup".into(),
                        false,
                        "首次使用：在连接页启用远程（一次即可）".into(),
                    )
                }
            }
            // VPS path is PC-backend / QR driven; mobile host mesh status stays LAN-first.
            MeshMode::Vps => {
                let h = lan.clone().unwrap_or_else(|| "127.0.0.1".into());
                (
                    h,
                    "vps".into(),
                    lan.is_some(),
                    "VPS 中继由 PC 出码配置 · 扫码即可".into(),
                )
            }
            MeshMode::Auto => {
                if let (Some(l), Some(_t)) = (lan.clone(), ts_ip.clone()) {
                    (
                        l.clone(),
                        "auto".into(),
                        true,
                        "双路径就绪".into(),
                    )
                } else if let Some(l) = lan.clone() {
                    (
                        l,
                        "auto-lan".into(),
                        true,
                        if self.embed.auth_key_set() {
                            "局域网可用 · 远程连接准备中".into()
                        } else {
                            "局域网可用".into()
                        },
                    )
                } else if let Some(t) = ts_ip.clone() {
                    (
                        t,
                        "auto-ts".into(),
                        true,
                        "远程路径就绪".into(),
                    )
                } else if cfg.auth_key_set || self.embed.auth_key_set() {
                    (
                        "127.0.0.1".into(),
                        "auto-pending".into(),
                        false,
                        "正在准备连接路径…".into(),
                    )
                } else {
                    (
                        "127.0.0.1".into(),
                        "auto-lan-only".into(),
                        lan.is_some(),
                        "当前可局域网配对".into(),
                    )
                }
            }
        };

        MeshStatus {
            mode: cfg.mode,
            online,
            advertise_host,
            advertise_port: port,
            scheme: "http".into(),
            backend,
            detail,
            tailscale_ip: ts_ip,
            lan_ip: lan,
            sidecar_running: embed_running || sidecar_alive(),
        }
    }

    pub fn status_json(&self) -> Value {
        let s = self.status();
        let c = self.config();
        let rt = self.runtime.read().clone();
        let emb = self.embed.status_json();
        json!({
            "ok": true,
            "mode": s.mode.as_str(),
            "online": s.online,
            "advertise_host": s.advertise_host,
            "advertise_port": s.advertise_port,
            "scheme": s.scheme,
            "backend": s.backend,
            "detail": s.detail,
            "tailscale_ip": s.tailscale_ip,
            "lan_ip": s.lan_ip,
            "sidecar_running": s.sidecar_running,
            "hostname": c.hostname,
            "require_pair_confirm": c.require_pair_confirm,
            "auth_key_set": c.auth_key_set || self.embed.auth_key_set(),
            "seamless_qr": c.seamless_qr,
            "embed": emb,
            "runtime": {
                "up": rt.up,
                "hostname": rt.hostname,
                "backend": rt.backend,
                "detail": rt.detail,
                "fingerprint": rt.fingerprint,
                "ifaces": rt.ifaces,
                "last_change_at": rt.last_change_at,
            }
        })
    }

    pub fn pair_endpoint(&self) -> (String, u16, String, MeshMode, String) {
        let s = self.status();
        let c = self.config();
        (
            s.advertise_host,
            s.advertise_port,
            s.scheme,
            c.mode,
            c.hostname,
        )
    }

    pub fn dual_paths(&self) -> (Option<String>, Option<String>, String) {
        let s = self.status();
        let c = self.config();
        (s.lan_ip, s.tailscale_ip, c.hostname)
    }

    pub fn runtime_up(
        &self,
        hostname: Option<&str>,
        ifaces: Option<Vec<String>>,
        auth_key_present: bool,
    ) -> Result<MeshRuntimeState> {
        let mut rt = self.runtime.write();
        let hn = hostname
            .map(|s| s.trim().to_string())
            .filter(|s| !s.is_empty())
            .unwrap_or_else(|| "tevarn-phone".into());
        let ifaces = ifaces.unwrap_or_else(detect_local_ifaces);
        let fp = iface_fingerprint(&ifaces);
        let changed = rt.fingerprint != fp;
        let ts = self.embed.tailscale_ip().or_else(detect_tailscale_ip);
        let embed_up = self.embed.is_running();
        let (backend, detail) = if embed_up {
            (
                "embedded-tsnet".into(),
                format!(
                    "安全连接已建立{}",
                    ts.as_ref().map(|t| format!(" · {t}")).unwrap_or_default()
                ),
            )
        } else if ts.is_some() {
            (
                "system-tailscale".into(),
                "安全连接已建立".into(),
            )
        } else if auth_key_present || self.embed.auth_key_set() || env_has_auth_key() {
            // Try start embed silently
            drop(rt);
            let _ = self.embed.set_role(TsnetRole::Phone);
            let _ = self.embed.set_hostname(&hn);
            let _ = self.embed.ensure_started_for_mode(true);
            let mut rt = self.runtime.write();
            let ts2 = self.embed.tailscale_ip();
            let (backend, detail) = if self.embed.is_running() {
                (
                    "embedded-tsnet".into(),
                    "安全连接已建立".into(),
                )
            } else {
                (
                    "tsnet-pending".into(),
                    "正在建立安全连接…".into(),
                )
            };
            rt.up = true;
            rt.hostname = hn;
            rt.auth_key_set = true;
            rt.backend = backend;
            rt.detail = detail;
            rt.ifaces = ifaces;
            if changed {
                rt.fingerprint = fp;
                rt.last_change_at = chrono::Utc::now().timestamp();
            } else if rt.fingerprint.is_empty() {
                rt.fingerprint = fp;
            }
            let _ = ts2;
            self.store.save_json(MESH_RUNTIME_FILE, &*rt)?;
            return Ok(rt.clone());
        } else if ifaces.iter().any(|i| i.starts_with("100.")) {
            (
                "iface-ts".into(),
                "检测到安全网卡".into(),
            )
        } else {
            (
                "lan-only".into(),
                "局域网就绪 · 扫码即可（外出自动走安全通道）".into(),
            )
        };
        rt.up = true;
        rt.hostname = hn;
        rt.auth_key_set = auth_key_present || self.embed.auth_key_set() || env_has_auth_key();
        rt.backend = backend;
        rt.detail = detail;
        rt.ifaces = ifaces;
        if changed {
            rt.fingerprint = fp;
            rt.last_change_at = chrono::Utc::now().timestamp();
        } else if rt.fingerprint.is_empty() {
            rt.fingerprint = fp;
        }
        self.store.save_json(MESH_RUNTIME_FILE, &*rt)?;
        Ok(rt.clone())
    }

    pub fn runtime_down(&self) -> Result<MeshRuntimeState> {
        let _ = self.embed.stop();
        let mut rt = self.runtime.write();
        rt.up = false;
        rt.detail = "mesh 已关闭".into();
        self.store.save_json(MESH_RUNTIME_FILE, &*rt)?;
        Ok(rt.clone())
    }

    pub fn report_ifaces(&self, ifaces: Vec<String>) -> Result<(bool, MeshRuntimeState)> {
        let mut rt = self.runtime.write();
        let fp = iface_fingerprint(&ifaces);
        let changed = !rt.fingerprint.is_empty() && rt.fingerprint != fp;
        rt.ifaces = ifaces;
        if changed || rt.fingerprint.is_empty() {
            if changed {
                rt.last_change_at = chrono::Utc::now().timestamp();
            }
            rt.fingerprint = fp;
        }
        if rt.up {
            if let Some(ts) = self.embed.tailscale_ip().or_else(detect_tailscale_ip) {
                rt.backend = if self.embed.is_running() {
                    "embedded-tsnet".into()
                } else {
                    "system-tailscale".into()
                };
                rt.detail = format!("路径已更新 · {ts}");
            }
        }
        self.store.save_json(MESH_RUNTIME_FILE, &*rt)?;
        Ok((changed, rt.clone()))
    }

    pub fn runtime_state(&self) -> MeshRuntimeState {
        self.runtime.read().clone()
    }
}

fn lan_ip() -> Option<String> {
    if let Ok(h) = std::env::var("TEVARN_PAIR_HOST") {
        if !h.trim().is_empty() {
            return Some(h.trim().to_string());
        }
    }
    let sock = UdpSocket::bind("0.0.0.0:0").ok()?;
    sock.connect("8.8.8.8:80").ok()?;
    let ip = sock.local_addr().ok()?.ip();
    if ip.is_loopback() {
        return None;
    }
    Some(ip.to_string())
}

fn detect_tailscale_ip() -> Option<String> {
    if let Ok(ip) = std::env::var("TEVARN_TS_IP") {
        if !ip.trim().is_empty() {
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

fn sidecar_alive() -> bool {
    if let Ok(p) = std::env::var("TEVARN_TSNET_HEALTH") {
        return std::path::Path::new(&p).exists();
    }
    false
}

fn detect_local_ifaces() -> Vec<String> {
    let mut out = Vec::new();
    if let Some(ip) = lan_ip() {
        out.push(ip);
    }
    if let Ok(o) = Command::new("ip")
        .args(["-4", "-o", "addr", "show", "scope", "global"])
        .output()
    {
        if o.status.success() {
            let s = String::from_utf8_lossy(&o.stdout);
            for line in s.lines() {
                if let Some(idx) = line.find("inet ") {
                    let rest = &line[idx + 5..];
                    let addr = rest.split_whitespace().next().unwrap_or("");
                    let host = addr.split('/').next().unwrap_or("").trim();
                    if !host.is_empty() && !out.contains(&host.to_string()) {
                        out.push(host.to_string());
                    }
                }
            }
        }
    }
    if let Some(ts) = detect_tailscale_ip() {
        if !out.contains(&ts) {
            out.push(ts);
        }
    }
    out.sort();
    out.dedup();
    out
}

fn iface_fingerprint(ifaces: &[String]) -> String {
    let mut v = ifaces.to_vec();
    v.sort();
    v.dedup();
    v.join("|")
}

pub fn env_has_auth_key() -> bool {
    std::env::var("TS_AUTHKEY")
        .or_else(|_| std::env::var("TEVARN_TS_AUTHKEY"))
        .map(|s| !s.is_empty())
        .unwrap_or(false)
}

pub fn parse_mode(s: &str) -> Result<MeshMode> {
    match s.to_ascii_lowercase().as_str() {
        "off" | "0" | "false" => Ok(MeshMode::Off),
        "lan" | "wifi" | "local" => Ok(MeshMode::Lan),
        "ts" | "tailscale" | "mesh" => Ok(MeshMode::Ts),
        "vps" | "relay" => Ok(MeshMode::Vps),
        "auto" | "dual" | "both" | "smart" => Ok(MeshMode::Auto),
        other => Err(Error::Msg(format!("unknown mesh mode: {other}"))),
    }
}
