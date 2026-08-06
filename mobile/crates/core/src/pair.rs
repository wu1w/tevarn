//! M1 QR pairing protocol (phone scans PC-generated QR).
//!
//! Payload URI (v3 seamless mesh / v4 VPS):
//! `takton://pair?v=3&pair_id=…&code=…&host=…&port=8090&exp=…&mesh=auto&scheme=http&lan=…&ts=…&hn=…&tsk=…`
//! v4 adds: `&vps=…&vp=443&vps_path=/t/{id}&vpt=…`
//!
//! `tsk` is an optional short-window phone join key (same tailnet preauth) so the
//! phone can embed tsnet with zero UI. Strip from logs after claim when possible.
//! `vpt` is a short-window pair-scoped token for VPS path (never master token).
//!
//! Server stores pending sessions; code is single-use.
//! Default TTL is 5 minutes so users can scan offline and claim when any path is up.

use crate::error::{Error, Result};
use crate::path::{Endpoint, EndpointKind};
use crate::storage::Store;
use chrono::{Duration as ChronoDuration, Utc};
use parking_lot::RwLock;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::collections::HashMap;
use std::sync::Arc;
use uuid::Uuid;

/// Pair QR / claim window. Extended so cross-network soft-pair can complete later.
pub const PAIR_TTL_SECS: i64 = 300;
const PAIRED_FILE: &str = "paired_devices.json";
const SECRET_FILE: &str = "pair_hmac_secret.txt";

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, Default)]
#[serde(rename_all = "lowercase")]
pub enum MeshMode {
    #[default]
    Off,
    Lan,
    /// Tailscale / tsnet mesh
    Ts,
    /// User-owned VPS reverse relay
    Vps,
    /// Advertise all known paths; client prefers LAN → Host → Vps → Ts
    Auto,
}

impl MeshMode {
    pub fn as_str(self) -> &'static str {
        match self {
            MeshMode::Off => "off",
            MeshMode::Lan => "lan",
            MeshMode::Ts => "ts",
            MeshMode::Vps => "vps",
            MeshMode::Auto => "auto",
        }
    }

    pub fn parse(s: &str) -> Self {
        match s.to_ascii_lowercase().as_str() {
            "ts" | "tailscale" | "mesh" => MeshMode::Ts,
            "vps" | "relay" => MeshMode::Vps,
            "lan" | "wifi" | "local" => MeshMode::Lan,
            "auto" | "dual" | "both" | "smart" => MeshMode::Auto,
            _ => MeshMode::Off,
        }
    }
}

/// Decoded QR / deep-link payload.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PairPayload {
    pub v: u8,
    pub pair_id: String,
    pub code: String,
    /// Primary host (first claim / display)
    pub host: String,
    pub port: u16,
    pub exp: i64,
    pub mesh: MeshMode,
    pub scheme: String,
    /// Optional MagicDNS / display name
    pub name: Option<String>,
    /// Optional LAN IPv4
    #[serde(default)]
    pub lan: Option<String>,
    /// Optional Tailscale IPv4 / MagicDNS
    #[serde(default)]
    pub ts: Option<String>,
    /// Optional hostname (MagicDNS / mDNS-ish label)
    #[serde(default)]
    pub hn: Option<String>,
    /// Optional phone-side Tailscale auth key for seamless embed join (v3).
    /// Never log the full value. Cleared from phone prefs after successful join when possible.
    #[serde(default)]
    pub tsk: Option<String>,
    /// Optional VPS relay host (v4)
    #[serde(default)]
    pub vps: Option<String>,
    /// Optional VPS public port (default 443/80)
    #[serde(default)]
    pub vp: Option<u16>,
    /// Optional path prefix e.g. `/t/{tunnel_id}`
    #[serde(default)]
    pub vps_path: Option<String>,
    /// Short-window pair-scoped VPS token (never master token)
    #[serde(default)]
    pub vpt: Option<String>,
    /// Optional scheme for VPS endpoint (http/https)
    #[serde(default)]
    pub vps_scheme: Option<String>,
}

impl PairPayload {
    pub fn base_url(&self) -> String {
        let scheme = if self.scheme.is_empty() {
            "http"
        } else {
            self.scheme.as_str()
        };
        format!("{scheme}://{}:{}", self.host, self.port)
    }

    /// All known endpoints ordered: LAN → hostname → primary → TS → loopback-skip.
    pub fn endpoints(&self) -> Vec<Endpoint> {
        let scheme = if self.scheme.is_empty() {
            "http"
        } else {
            self.scheme.as_str()
        };
        let port = if self.port == 0 { 8090 } else { self.port };
        let mut out = Vec::new();
        if let Some(lan) = self.lan.as_deref().filter(|s| !s.trim().is_empty()) {
            if let Some(ep) = Endpoint::from_parts(scheme, lan, port, EndpointKind::Lan) {
                out.push(ep);
            }
        }
        if let Some(hn) = self
            .hn
            .as_deref()
            .or(self.name.as_deref())
            .filter(|s| !s.trim().is_empty())
        {
            // only treat as host endpoint if it looks like a name (not raw IP already used)
            if hn.parse::<std::net::IpAddr>().is_err() {
                if let Some(ep) = Endpoint::from_parts(scheme, hn, port, EndpointKind::Host) {
                    out.push(ep);
                }
            }
        }
        if let Some(ep) =
            Endpoint::from_parts(scheme, &self.host, port, crate::path::classify_host(&self.host))
        {
            if !out.iter().any(|e| e.url == ep.url) {
                out.push(ep);
            }
        }
        if let Some(vps) = self.vps.as_deref().filter(|s| !s.trim().is_empty()) {
            let vps_scheme = self
                .vps_scheme
                .as_deref()
                .filter(|s| !s.is_empty())
                .unwrap_or(if self.vp.unwrap_or(0) == 443 {
                    "https"
                } else {
                    "http"
                });
            let vp = self.vp.unwrap_or(if vps_scheme == "https" { 443 } else { 80 });
            let path = self
                .vps_path
                .as_deref()
                .unwrap_or("")
                .trim()
                .trim_end_matches('/');
            let url = if path.is_empty() {
                format!("{vps_scheme}://{vps}:{vp}")
            } else {
                let p = if path.starts_with('/') {
                    path.to_string()
                } else {
                    format!("/{path}")
                };
                format!("{vps_scheme}://{vps}:{vp}{p}")
            };
            if let Some(ep) = Endpoint::from_url(&url, EndpointKind::Vps) {
                if !out.iter().any(|e| e.url == ep.url) {
                    out.push(ep);
                }
            }
        }
        if let Some(ts) = self.ts.as_deref().filter(|s| !s.trim().is_empty()) {
            if let Some(ep) = Endpoint::from_parts(scheme, ts, port, EndpointKind::Ts) {
                if !out.iter().any(|e| e.url == ep.url) {
                    out.push(ep);
                }
            }
        }
        out
    }

    pub fn is_expired(&self) -> bool {
        Utc::now().timestamp() > self.exp
    }

    pub fn to_uri(&self) -> String {
        let mut q = format!(
            "takton://pair?v={}&pair_id={}&code={}&host={}&port={}&exp={}&mesh={}&scheme={}",
            self.v,
            urlencoding(&self.pair_id),
            urlencoding(&self.code),
            urlencoding(&self.host),
            self.port,
            self.exp,
            self.mesh.as_str(),
            urlencoding(&self.scheme),
        );
        if let Some(n) = &self.name {
            if !n.is_empty() {
                q.push_str(&format!("&name={}", urlencoding(n)));
            }
        }
        if let Some(lan) = &self.lan {
            if !lan.is_empty() {
                q.push_str(&format!("&lan={}", urlencoding(lan)));
            }
        }
        if let Some(ts) = &self.ts {
            if !ts.is_empty() {
                q.push_str(&format!("&ts={}", urlencoding(ts)));
            }
        }
        if let Some(hn) = &self.hn {
            if !hn.is_empty() {
                q.push_str(&format!("&hn={}", urlencoding(hn)));
            }
        }
        if let Some(tsk) = &self.tsk {
            if !tsk.is_empty() {
                q.push_str(&format!("&tsk={}", urlencoding(tsk)));
            }
        }
        if let Some(vps) = &self.vps {
            if !vps.is_empty() {
                q.push_str(&format!("&vps={}", urlencoding(vps)));
            }
        }
        if let Some(vp) = self.vp {
            q.push_str(&format!("&vp={vp}"));
        }
        if let Some(path) = &self.vps_path {
            if !path.is_empty() {
                q.push_str(&format!("&vps_path={}", urlencoding(path)));
            }
        }
        if let Some(vpt) = &self.vpt {
            if !vpt.is_empty() {
                q.push_str(&format!("&vpt={}", urlencoding(vpt)));
            }
        }
        if let Some(vs) = &self.vps_scheme {
            if !vs.is_empty() {
                q.push_str(&format!("&vps_scheme={}", urlencoding(vs)));
            }
        }
        q
    }

    pub fn parse_uri(raw: &str) -> Result<Self> {
        let s = raw.trim();
        // Accept raw URI or full URL-wrapped
        let s = s
            .strip_prefix("https://takton.local/pair?")
            .or_else(|| s.strip_prefix("http://takton.local/pair?"))
            .unwrap_or(s);
        let s = if let Some(rest) = s.strip_prefix("takton://pair?") {
            rest
        } else if let Some(rest) = s.strip_prefix("takton:pair?") {
            rest
        } else if s.contains("pair_id=") {
            s.trim_start_matches('?')
        } else {
            return Err(Error::Msg("invalid pair QR · expected takton://pair?…".into()));
        };

        let mut map: HashMap<String, String> = HashMap::new();
        for part in s.split('&') {
            if part.is_empty() {
                continue;
            }
            let mut it = part.splitn(2, '=');
            let k = it.next().unwrap_or("");
            let v = it.next().unwrap_or("");
            map.insert(k.to_string(), urldecoding(v));
        }

        let pair_id = map
            .get("pair_id")
            .cloned()
            .filter(|x| !x.is_empty())
            .ok_or_else(|| Error::Msg("missing pair_id".into()))?;
        let code = map
            .get("code")
            .cloned()
            .filter(|x| !x.is_empty())
            .ok_or_else(|| Error::Msg("missing code".into()))?;
        let host = map
            .get("host")
            .cloned()
            .filter(|x| !x.is_empty())
            .ok_or_else(|| Error::Msg("missing host".into()))?;
        let port = map
            .get("port")
            .and_then(|p| p.parse().ok())
            .unwrap_or(8090);
        let exp = map
            .get("exp")
            .and_then(|e| e.parse().ok())
            .unwrap_or(0);
        let mesh = MeshMode::parse(map.get("mesh").map(|s| s.as_str()).unwrap_or("lan"));
        let scheme = map
            .get("scheme")
            .cloned()
            .filter(|x| !x.is_empty())
            .unwrap_or_else(|| "http".into());
        let name = map.get("name").cloned().filter(|x| !x.is_empty());
        let lan = map
            .get("lan")
            .or_else(|| map.get("lan_host"))
            .cloned()
            .filter(|x| !x.is_empty());
        let ts = map
            .get("ts")
            .or_else(|| map.get("ts_host"))
            .cloned()
            .filter(|x| !x.is_empty());
        let hn = map
            .get("hn")
            .or_else(|| map.get("hostname"))
            .cloned()
            .filter(|x| !x.is_empty());
        let tsk = map
            .get("tsk")
            .or_else(|| map.get("ts_key"))
            .or_else(|| map.get("authkey"))
            .cloned()
            .filter(|x| !x.is_empty());
        let vps = map
            .get("vps")
            .or_else(|| map.get("relay"))
            .cloned()
            .filter(|x| !x.is_empty());
        let vp = map.get("vp").and_then(|p| p.parse().ok());
        let vps_path = map
            .get("vps_path")
            .or_else(|| map.get("vpath"))
            .cloned()
            .filter(|x| !x.is_empty());
        let vpt = map
            .get("vpt")
            .or_else(|| map.get("vps_token"))
            .cloned()
            .filter(|x| !x.is_empty());
        let vps_scheme = map
            .get("vps_scheme")
            .cloned()
            .filter(|x| !x.is_empty());
        // comma-separated hosts=lan,ts,name
        let mut hosts_extra: Vec<String> = map
            .get("hosts")
            .map(|s| {
                s.split(',')
                    .map(|x| x.trim().to_string())
                    .filter(|x| !x.is_empty())
                    .collect()
            })
            .unwrap_or_default();
        let mut lan = lan;
        let mut ts = ts;
        for h in hosts_extra.drain(..) {
            match crate::path::classify_host(&h) {
                EndpointKind::Lan if lan.is_none() => lan = Some(h),
                EndpointKind::Ts if ts.is_none() => ts = Some(h),
                _ => {}
            }
        }
        let v = map
            .get("v")
            .and_then(|x| x.parse().ok())
            .unwrap_or(1);

        Ok(Self {
            v,
            pair_id,
            code,
            host,
            port,
            exp,
            mesh,
            scheme,
            name,
            lan,
            ts,
            hn,
            tsk,
            vps,
            vp,
            vps_path,
            vpt,
            vps_scheme,
        })
    }

    /// QR safe for logs / clipboard history (auth key redacted).
    pub fn to_uri_redacted(&self) -> String {
        let mut p = self.clone();
        if p.tsk.as_ref().map(|s| !s.is_empty()).unwrap_or(false) {
            p.tsk = Some("***".into());
        }
        if p.vpt.as_ref().map(|s| !s.is_empty()).unwrap_or(false) {
            p.vpt = Some("***".into());
        }
        p.to_uri()
    }

    /// Drop phone join key after successful bind (keep path fields).
    pub fn without_tsk(&self) -> Self {
        let mut p = self.clone();
        p.tsk = None;
        p
    }

    /// Drop short-lived VPS pair token after claim.
    pub fn without_vpt(&self) -> Self {
        let mut p = self.clone();
        p.vpt = None;
        p
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PendingPair {
    pub pair_id: String,
    pub code: String,
    pub host: String,
    pub port: u16,
    pub scheme: String,
    pub mesh: MeshMode,
    pub name: Option<String>,
    #[serde(default)]
    pub lan: Option<String>,
    #[serde(default)]
    pub ts: Option<String>,
    #[serde(default)]
    pub hn: Option<String>,
    pub exp: i64,
    pub created_at: i64,
    /// Optional: require local confirm before claim succeeds
    pub require_confirm: bool,
    pub confirmed: bool,
    pub claimed: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PairedDevice {
    pub id: String,
    pub name: String,
    pub token: String,
    pub host: String,
    pub port: u16,
    pub scheme: String,
    pub mesh: MeshMode,
    pub base_url: String,
    #[serde(default)]
    pub endpoints: Vec<String>,
    #[serde(default)]
    pub lan: Option<String>,
    #[serde(default)]
    pub ts: Option<String>,
    #[serde(default)]
    pub hostname: Option<String>,
    pub paired_at: i64,
    pub last_seen: i64,
    /// role: phone connected TO this host, or this device as phone client
    pub role: String,
}

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
struct PairedFile {
    devices: Vec<PairedDevice>,
}

/// In-memory pending + disk-backed paired devices.
#[derive(Clone)]
pub struct PairService {
    store: Store,
    pending: Arc<RwLock<HashMap<String, PendingPair>>>,
    secret: Arc<String>,
}

impl PairService {
    pub fn open(store: Store) -> Self {
        let secret = store
            .load_json::<String>(SECRET_FILE)
            .ok()
            .flatten()
            .unwrap_or_else(|| {
                let s = Uuid::new_v4().to_string();
                let _ = store.save_json(SECRET_FILE, &s);
                s
            });
        Self {
            store,
            pending: Arc::new(RwLock::new(HashMap::new())),
            secret: Arc::new(secret),
        }
    }

    pub fn list_paired(&self) -> Vec<PairedDevice> {
        self.store
            .load_json::<PairedFile>(PAIRED_FILE)
            .ok()
            .flatten()
            .map(|f| f.devices)
            .unwrap_or_default()
    }

    fn save_paired(&self, devices: &[PairedDevice]) -> Result<()> {
        self.store.save_json(
            PAIRED_FILE,
            &PairedFile {
                devices: devices.to_vec(),
            },
        )
    }

    /// Start a host-side pairing session (PC or demo host).
    pub fn start(
        &self,
        host: &str,
        port: u16,
        scheme: &str,
        mesh: MeshMode,
        name: Option<String>,
        require_confirm: bool,
        lan: Option<String>,
        ts: Option<String>,
        hn: Option<String>,
        // Phone join key for seamless embed (optional, short window)
        tsk: Option<String>,
    ) -> (PendingPair, PairPayload) {
        self.gc();
        let pair_id = Uuid::new_v4().to_string();
        let code = format!("{:06}", fastrand_u32() % 1_000_000);
        let now = Utc::now().timestamp();
        let exp = now + PAIR_TTL_SECS;
        let pending = PendingPair {
            pair_id: pair_id.clone(),
            code: code.clone(),
            host: host.to_string(),
            port,
            scheme: scheme.to_string(),
            mesh,
            name: name.clone(),
            lan: lan.clone(),
            ts: ts.clone(),
            hn: hn.clone(),
            exp,
            created_at: now,
            require_confirm,
            confirmed: !require_confirm,
            claimed: false,
        };
        self.pending
            .write()
            .insert(pair_id.clone(), pending.clone());
        let has_tsk = tsk.as_ref().map(|s| !s.is_empty()).unwrap_or(false);
        let payload = PairPayload {
            v: if has_tsk { 3 } else { 2 },
            pair_id,
            code,
            host: host.to_string(),
            port,
            exp,
            mesh,
            scheme: scheme.to_string(),
            name,
            lan,
            ts,
            hn,
            tsk,
            vps: None,
            vp: None,
            vps_path: None,
            vpt: None,
            vps_scheme: None,
        };
        (pending, payload)
    }

    pub fn status(&self, pair_id: &str) -> Option<Value> {
        self.gc();
        let g = self.pending.read();
        g.get(pair_id).map(|p| {
            json!({
                "pair_id": p.pair_id,
                "exp": p.exp,
                "remaining_secs": (p.exp - Utc::now().timestamp()).max(0),
                "confirmed": p.confirmed,
                "claimed": p.claimed,
                "require_confirm": p.require_confirm,
                "mesh": p.mesh.as_str(),
                "host": p.host,
                "port": p.port,
                "lan": p.lan,
                "ts": p.ts,
                "hn": p.hn,
                "ttl_secs": PAIR_TTL_SECS,
            })
        })
    }

    pub fn confirm(&self, pair_id: &str) -> Result<()> {
        let mut g = self.pending.write();
        let p = g
            .get_mut(pair_id)
            .ok_or_else(|| Error::Msg("pair session not found".into()))?;
        if Utc::now().timestamp() > p.exp {
            return Err(Error::Msg("pair expired".into()));
        }
        p.confirmed = true;
        Ok(())
    }

    pub fn cancel(&self, pair_id: &str) {
        self.pending.write().remove(pair_id);
    }

    /// Phone claims a pending pair. Returns device token + base_url.
    pub fn claim(
        &self,
        pair_id: &str,
        code: &str,
        device_name: &str,
    ) -> Result<(PairedDevice, PendingPair)> {
        self.gc();
        let mut g = self.pending.write();
        let p = g
            .get_mut(pair_id)
            .ok_or_else(|| Error::Msg("pair session not found or expired".into()))?;
        if Utc::now().timestamp() > p.exp {
            return Err(Error::Msg("pair expired · generate a new QR".into()));
        }
        if p.claimed {
            return Err(Error::Msg("pair already used".into()));
        }
        if p.code != code.trim() {
            return Err(Error::Msg("invalid pair code".into()));
        }
        if p.require_confirm && !p.confirmed {
            return Err(Error::Msg("waiting for PC confirmation".into()));
        }
        p.claimed = true;
        let pending = p.clone();
        drop(g);

        let now = Utc::now().timestamp();
        let token = format!("pd_{}", Uuid::new_v4());
        let name = if device_name.trim().is_empty() {
            "Phone".into()
        } else {
            device_name.trim().to_string()
        };
        let payload_like = PairPayload {
            v: 2,
            pair_id: pending.pair_id.clone(),
            code: pending.code.clone(),
            host: pending.host.clone(),
            port: pending.port,
            exp: pending.exp,
            mesh: pending.mesh,
            scheme: pending.scheme.clone(),
            name: pending.name.clone(),
            lan: pending.lan.clone(),
            ts: pending.ts.clone(),
            hn: pending.hn.clone(),
            tsk: None,
            vps: None,
            vp: None,
            vps_path: None,
            vpt: None,
            vps_scheme: None,
        };
        let endpoints: Vec<String> = payload_like.endpoints().into_iter().map(|e| e.url).collect();
        let device = PairedDevice {
            id: Uuid::new_v4().to_string(),
            name,
            token: token.clone(),
            host: pending.host.clone(),
            port: pending.port,
            scheme: pending.scheme.clone(),
            mesh: pending.mesh,
            base_url: format!(
                "{}://{}:{}",
                pending.scheme, pending.host, pending.port
            ),
            endpoints: endpoints.clone(),
            lan: pending.lan.clone(),
            ts: pending.ts.clone(),
            hostname: pending.hn.clone().or(pending.name.clone()),
            paired_at: now,
            last_seen: now,
            role: "phone".into(),
        };

        let mut list = self.list_paired();
        // Replace same name host role phone
        list.retain(|d| !(d.role == "phone" && d.base_url == device.base_url));
        list.push(device.clone());
        self.save_paired(&list)?;

        // Also record host-side view
        let host_view = PairedDevice {
            id: device.id.clone(),
            name: device.name.clone(),
            token,
            host: pending.host.clone(),
            port: pending.port,
            scheme: pending.scheme.clone(),
            mesh: pending.mesh,
            base_url: device.base_url.clone(),
            endpoints,
            lan: pending.lan.clone(),
            ts: pending.ts.clone(),
            hostname: pending.hn.clone().or(pending.name.clone()),
            paired_at: now,
            last_seen: now,
            role: "host".into(),
        };
        let mut list2 = self.list_paired();
        if !list2.iter().any(|d| d.id == host_view.id && d.role == "host") {
            list2.push(host_view);
            let _ = self.save_paired(&list2);
        }

        Ok((device, pending))
    }

    pub fn revoke(&self, device_id: &str) -> Result<()> {
        let mut list = self.list_paired();
        let before = list.len();
        list.retain(|d| d.id != device_id && d.token != device_id);
        if list.len() == before {
            return Err(Error::Msg("device not found".into()));
        }
        self.save_paired(&list)
    }

    pub fn touch(&self, device_id: &str) {
        let mut list = self.list_paired();
        let now = Utc::now().timestamp();
        for d in &mut list {
            if d.id == device_id || d.token == device_id {
                d.last_seen = now;
            }
        }
        let _ = self.save_paired(&list);
    }

    /// Update stored endpoints for a device (LAN drift refresh).
    pub fn update_endpoints(
        &self,
        device_id: &str,
        endpoints: &[String],
        lan: Option<String>,
        ts: Option<String>,
        hostname: Option<String>,
    ) -> Result<()> {
        let mut list = self.list_paired();
        let mut hit = false;
        for d in &mut list {
            if d.id == device_id || d.token == device_id {
                d.endpoints = endpoints.to_vec();
                if let Some(l) = lan.clone() {
                    d.lan = Some(l);
                }
                if let Some(t) = ts.clone() {
                    d.ts = Some(t);
                }
                if let Some(h) = hostname.clone() {
                    d.hostname = Some(h);
                }
                if let Some(first) = endpoints.first() {
                    d.base_url = first.clone();
                }
                d.last_seen = Utc::now().timestamp();
                hit = true;
            }
        }
        if !hit {
            return Err(Error::Msg("device not found".into()));
        }
        self.save_paired(&list)
    }

    /// Validate device token (for future gated remote API).
    pub fn validate_token(&self, token: &str) -> Option<PairedDevice> {
        self.list_paired()
            .into_iter()
            .find(|d| d.token == token)
    }

    fn gc(&self) {
        let now = Utc::now().timestamp();
        self.pending.write().retain(|_, p| !p.claimed && p.exp >= now);
    }

    pub fn secret(&self) -> &str {
        &self.secret
    }

    pub fn pending_snapshot(&self) -> Vec<Value> {
        self.gc();
        self.pending
            .read()
            .values()
            .map(|p| {
                json!({
                    "pair_id": p.pair_id,
                    "exp": p.exp,
                    "remaining_secs": (p.exp - Utc::now().timestamp()).max(0),
                    "confirmed": p.confirmed,
                    "claimed": p.claimed,
                    "mesh": p.mesh.as_str(),
                    "host": p.host,
                    "port": p.port,
                    "lan": p.lan,
                    "ts": p.ts,
                    "code_hint": format!("{}***", &p.code.chars().take(2).collect::<String>()),
                })
            })
            .collect()
    }
}

/// Advertise host for QR: env override → first non-loopback IPv4 → 127.0.0.1
pub fn advertise_host() -> String {
    if let Ok(h) = std::env::var("TAKTON_PAIR_HOST") {
        if !h.trim().is_empty() {
            return h.trim().to_string();
        }
    }
    if let Ok(h) = std::env::var("TAKTON_ADVERTISE_HOST") {
        if !h.trim().is_empty() {
            return h.trim().to_string();
        }
    }
    local_non_loopback_ip().unwrap_or_else(|| "127.0.0.1".into())
}

fn local_non_loopback_ip() -> Option<String> {
    use std::net::UdpSocket;
    let sock = UdpSocket::bind("0.0.0.0:0").ok()?;
    sock.connect("8.8.8.8:80").ok()?;
    let ip = sock.local_addr().ok()?.ip();
    if ip.is_loopback() {
        return None;
    }
    Some(ip.to_string())
}

fn fastrand_u32() -> u32 {
    use std::time::{SystemTime, UNIX_EPOCH};
    let t = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_nanos() as u32)
        .unwrap_or(1);
    t.wrapping_mul(1664525).wrapping_add(1013904223)
}

fn urlencoding(s: &str) -> String {
    let mut out = String::with_capacity(s.len());
    for b in s.bytes() {
        match b {
            b'A'..=b'Z' | b'a'..=b'z' | b'0'..=b'9' | b'-' | b'_' | b'.' | b'~' => {
                out.push(b as char)
            }
            _ => out.push_str(&format!("%{b:02X}")),
        }
    }
    out
}

fn urldecoding(s: &str) -> String {
    let bytes = s.as_bytes();
    let mut out = Vec::with_capacity(bytes.len());
    let mut i = 0;
    while i < bytes.len() {
        match bytes[i] {
            b'%' if i + 2 < bytes.len() => {
                let h = || {
                    let a = bytes[i + 1];
                    let b = bytes[i + 2];
                    let n = |c: u8| match c {
                        b'0'..=b'9' => Some(c - b'0'),
                        b'a'..=b'f' => Some(c - b'a' + 10),
                        b'A'..=b'F' => Some(c - b'A' + 10),
                        _ => None,
                    };
                    Some(n(a)? * 16 + n(b)?)
                };
                if let Some(v) = h() {
                    out.push(v);
                    i += 3;
                    continue;
                }
                out.push(bytes[i]);
                i += 1;
            }
            b'+' => {
                out.push(b' ');
                i += 1;
            }
            c => {
                out.push(c);
                i += 1;
            }
        }
    }
    String::from_utf8_lossy(&out).into_owned()
}

/// Expiry helper for UI.
pub fn expires_in_label(exp: i64) -> String {
    let rem = exp - Utc::now().timestamp();
    if rem <= 0 {
        "已过期".into()
    } else if rem >= 60 {
        format!("{}m{}s", rem / 60, rem % 60)
    } else {
        format!("{rem}s")
    }
}

pub fn default_exp() -> i64 {
    (Utc::now() + ChronoDuration::seconds(PAIR_TTL_SECS)).timestamp()
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::env::temp_dir;

    #[test]
    fn roundtrip_uri_v2() {
        let p = PairPayload {
            v: 2,
            pair_id: "abc-123".into(),
            code: "654321".into(),
            host: "192.168.1.8".into(),
            port: 8090,
            exp: 9999999999,
            mesh: MeshMode::Auto,
            scheme: "http".into(),
            name: Some("MyPC".into()),
            lan: Some("192.168.1.8".into()),
            ts: Some("100.64.0.12".into()),
            hn: Some("takton-pc".into()),
            tsk: Some("tskey-auth-demo".into()),
            vps: None,
            vp: None,
            vps_path: None,
            vpt: None,
            vps_scheme: None,
        };
        let uri = p.to_uri();
        assert!(uri.contains("tsk="));
        assert!(!p.to_uri_redacted().contains("tskey-auth-demo"));
        assert!(uri.starts_with("takton://pair?"));
        let back = PairPayload::parse_uri(&uri).unwrap();
        assert_eq!(back.pair_id, "abc-123");
        assert_eq!(back.code, "654321");
        assert_eq!(back.host, "192.168.1.8");
        assert_eq!(back.port, 8090);
        assert_eq!(back.mesh, MeshMode::Auto);
        assert_eq!(back.name.as_deref(), Some("MyPC"));
        assert_eq!(back.lan.as_deref(), Some("192.168.1.8"));
        assert_eq!(back.ts.as_deref(), Some("100.64.0.12"));
        assert_eq!(back.hn.as_deref(), Some("takton-pc"));
        assert_eq!(back.tsk.as_deref(), Some("tskey-auth-demo"));
        let eps = back.endpoints();
        assert!(eps.len() >= 2);
    }

    #[test]
    fn claim_flow() {
        let dir = temp_dir().join(format!("takton-pair-{}", Uuid::new_v4()));
        let store = Store::open(&dir).unwrap();
        let svc = PairService::open(store);
        let (_pending, payload) = svc.start(
            "127.0.0.1",
            8090,
            "http",
            MeshMode::Lan,
            None,
            false,
            Some("192.168.1.8".into()),
            Some("100.64.0.1".into()),
            Some("takton-pc".into()),
            None,
        );
        let (dev, _) = svc
            .claim(&payload.pair_id, &payload.code, "Pixel")
            .unwrap();
        assert_eq!(dev.name, "Pixel");
        assert!(dev.token.starts_with("pd_"));
        assert!(dev.endpoints.len() >= 2);
        assert!(svc.claim(&payload.pair_id, &payload.code, "X").is_err());
    }

    #[test]
    fn ttl_is_five_minutes() {
        assert_eq!(PAIR_TTL_SECS, 300);
    }
}
