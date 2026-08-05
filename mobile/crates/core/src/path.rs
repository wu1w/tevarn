//! Multi-endpoint path selection (LAN preferred → MagicDNS/host → Tailscale).
//!
//! Solves:
//! - single base_url with no failover
//! - Wi‑Fi ↔ cellular path changes
//! - LAN DHCP drift (refresh endpoints after successful connect; keep TS/hostname)
//! - deferred claim when QR scanned offline (claim when any endpoint is reachable)

use crate::error::{Error, Result};
use crate::pair::{MeshMode, PairPayload};
use crate::storage::Store;
use chrono::Utc;
use parking_lot::RwLock;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::time::Duration;

const PROFILE_FILE: &str = "path_profile.json";
const PROBE_TIMEOUT: Duration = Duration::from_millis(1200);

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, Default)]
#[serde(rename_all = "lowercase")]
pub enum EndpointKind {
    #[default]
    Unknown,
    Lan,
    Ts,
    Host,
    Loopback,
    Manual,
}

impl EndpointKind {
    pub fn as_str(self) -> &'static str {
        match self {
            EndpointKind::Unknown => "unknown",
            EndpointKind::Lan => "lan",
            EndpointKind::Ts => "ts",
            EndpointKind::Host => "host",
            EndpointKind::Loopback => "loopback",
            EndpointKind::Manual => "manual",
        }
    }

    /// Lower is better when ranking (LAN first).
    pub fn rank(self) -> u8 {
        match self {
            EndpointKind::Lan => 0,
            EndpointKind::Host => 1,
            EndpointKind::Ts => 2,
            EndpointKind::Manual => 3,
            EndpointKind::Unknown => 4,
            EndpointKind::Loopback => 5,
        }
    }

    pub fn parse(s: &str) -> Self {
        match s.to_ascii_lowercase().as_str() {
            "lan" | "wifi" | "local" => EndpointKind::Lan,
            "ts" | "tailscale" | "mesh" => EndpointKind::Ts,
            "host" | "dns" | "magic" | "name" => EndpointKind::Host,
            "loopback" | "lo" => EndpointKind::Loopback,
            "manual" => EndpointKind::Manual,
            _ => EndpointKind::Unknown,
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Endpoint {
    pub url: String,
    pub kind: EndpointKind,
    #[serde(default)]
    pub host: String,
    #[serde(default)]
    pub port: u16,
    #[serde(default)]
    pub scheme: String,
}

impl Endpoint {
    pub fn from_parts(scheme: &str, host: &str, port: u16, kind: EndpointKind) -> Option<Self> {
        let host = host.trim();
        if host.is_empty() {
            return None;
        }
        let scheme = if scheme.is_empty() { "http" } else { scheme };
        let port = if port == 0 { 8090 } else { port };
        let url = format!("{scheme}://{host}:{port}");
        Some(Self {
            url,
            kind,
            host: host.to_string(),
            port,
            scheme: scheme.to_string(),
        })
    }

    pub fn from_url(url: &str, kind: EndpointKind) -> Option<Self> {
        let url = url.trim().trim_end_matches('/').to_string();
        if url.is_empty() {
            return None;
        }
        let (scheme, host, port) = crate::config::parse_base_url_parts(&url);
        if host.is_empty() {
            return None;
        }
        let kind = if kind == EndpointKind::Unknown {
            classify_host(&host)
        } else {
            kind
        };
        Some(Self {
            url: format!("{scheme}://{host}:{port}"),
            kind,
            host,
            port,
            scheme,
        })
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct DeferredClaim {
    pub pair_id: String,
    pub code: String,
    pub exp: i64,
    pub device_name: String,
    #[serde(default)]
    pub qr: String,
}

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct PathProfile {
    pub endpoints: Vec<Endpoint>,
    #[serde(default)]
    pub active_url: Option<String>,
    #[serde(default)]
    pub last_ok_at: i64,
    #[serde(default)]
    pub last_kind: Option<EndpointKind>,
    #[serde(default)]
    pub pair_id: Option<String>,
    #[serde(default)]
    pub device_token: Option<String>,
    #[serde(default)]
    pub hostname: Option<String>,
    #[serde(default)]
    pub deferred_claim: Option<DeferredClaim>,
    #[serde(default)]
    pub mesh: MeshMode,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ProbeResult {
    pub url: String,
    pub kind: EndpointKind,
    pub ok: bool,
    pub latency_ms: u64,
    #[serde(default)]
    pub detail: String,
}

pub struct PathService {
    store: Store,
    profile: RwLock<PathProfile>,
}

impl PathService {
    pub fn open(store: Store) -> Self {
        let profile = store
            .load_json::<PathProfile>(PROFILE_FILE)
            .ok()
            .flatten()
            .unwrap_or_default();
        Self {
            store,
            profile: RwLock::new(profile),
        }
    }

    pub fn profile(&self) -> PathProfile {
        self.profile.read().clone()
    }

    pub fn save(&self, p: &PathProfile) -> Result<()> {
        *self.profile.write() = p.clone();
        self.store.save_json(PROFILE_FILE, p)
    }

    pub fn clear(&self) -> Result<()> {
        *self.profile.write() = PathProfile::default();
        self.store.delete(PROFILE_FILE).or_else(|_| {
            self.store
                .save_json(PROFILE_FILE, &PathProfile::default())
        })
    }

    /// Merge candidates from QR / mesh / manual into the profile.
    pub fn merge_from_payload(
        &self,
        payload: &PairPayload,
        device_token: Option<String>,
        deferred: Option<DeferredClaim>,
    ) -> Result<PathProfile> {
        let mut p = self.profile();
        for ep in payload.endpoints() {
            upsert_endpoint(&mut p.endpoints, ep);
        }
        sort_endpoints(&mut p.endpoints);
        p.pair_id = Some(payload.pair_id.clone());
        p.hostname = payload.name.clone().or(payload.hn.clone());
        p.mesh = payload.mesh;
        if let Some(t) = device_token {
            if !t.is_empty() {
                p.device_token = Some(t);
            }
        }
        if let Some(d) = deferred {
            p.deferred_claim = Some(d);
        }
        if p.active_url.is_none() {
            p.active_url = p.endpoints.first().map(|e| e.url.clone());
        }
        self.save(&p)?;
        Ok(p)
    }

    pub fn merge_urls(&self, urls: &[String], kind: EndpointKind) -> Result<PathProfile> {
        let mut p = self.profile();
        for u in urls {
            if let Some(ep) = Endpoint::from_url(u, kind) {
                upsert_endpoint(&mut p.endpoints, ep);
            }
        }
        sort_endpoints(&mut p.endpoints);
        self.save(&p)?;
        Ok(p)
    }

    pub fn set_active(&self, url: &str, kind: EndpointKind) -> Result<PathProfile> {
        let mut p = self.profile();
        let url = url.trim().trim_end_matches('/').to_string();
        if let Some(ep) = Endpoint::from_url(&url, kind) {
            upsert_endpoint(&mut p.endpoints, ep);
        }
        p.active_url = Some(url);
        p.last_kind = Some(kind);
        p.last_ok_at = Utc::now().timestamp();
        // Successful path clears deferred claim if any leftover
        sort_endpoints(&mut p.endpoints);
        self.save(&p)?;
        Ok(p)
    }

    pub fn clear_deferred(&self) -> Result<PathProfile> {
        let mut p = self.profile();
        p.deferred_claim = None;
        self.save(&p)?;
        Ok(p)
    }

    pub fn set_deferred(&self, d: DeferredClaim) -> Result<PathProfile> {
        let mut p = self.profile();
        p.deferred_claim = Some(d);
        self.save(&p)?;
        Ok(p)
    }

    /// Refresh LAN/TS/hostname candidates (e.g. after mesh probe or successful login).
    pub fn refresh_candidates(
        &self,
        lan: Option<&str>,
        ts: Option<&str>,
        hostname: Option<&str>,
        port: u16,
        scheme: &str,
    ) -> Result<PathProfile> {
        let mut p = self.profile();
        let port = if port == 0 { 8090 } else { port };
        if let Some(h) = lan.filter(|s| !s.trim().is_empty()) {
            if let Some(ep) = Endpoint::from_parts(scheme, h, port, EndpointKind::Lan) {
                upsert_endpoint(&mut p.endpoints, ep);
            }
        }
        if let Some(h) = ts.filter(|s| !s.trim().is_empty()) {
            if let Some(ep) = Endpoint::from_parts(scheme, h, port, EndpointKind::Ts) {
                upsert_endpoint(&mut p.endpoints, ep);
            }
        }
        if let Some(h) = hostname.filter(|s| !s.trim().is_empty()) {
            p.hostname = Some(h.to_string());
            if let Some(ep) = Endpoint::from_parts(scheme, h, port, EndpointKind::Host) {
                upsert_endpoint(&mut p.endpoints, ep);
            }
        }
        // Drop stale private LAN hosts that no longer match current lan (DHCP drift).
        // Keep at most the freshest LAN endpoint (last upsert wins via replace).
        if let Some(cur_lan) = lan.filter(|s| !s.trim().is_empty()) {
            p.endpoints.retain(|e| {
                e.kind != EndpointKind::Lan || e.host == cur_lan.trim()
            });
        }
        sort_endpoints(&mut p.endpoints);
        self.save(&p)?;
        Ok(p)
    }

    /// Ordered candidate URLs: last active first among same rank, then LAN → host → ts.
    pub fn candidate_urls(&self, extra: &[String]) -> Vec<Endpoint> {
        let p = self.profile();
        let mut list = p.endpoints.clone();
        for u in extra {
            if let Some(ep) = Endpoint::from_url(u, EndpointKind::Unknown) {
                upsert_endpoint(&mut list, ep);
            }
        }
        if let Some(active) = p.active_url.as_ref() {
            // Stable prefer: move matching active to front within its kind band
            if let Some(pos) = list.iter().position(|e| e.url == *active) {
                let ep = list.remove(pos);
                list.insert(0, ep);
            }
        }
        // Re-sort but keep active first if present
        let active = p.active_url.clone();
        list.sort_by(|a, b| {
            let a_act = active.as_ref().map(|u| u == &a.url).unwrap_or(false);
            let b_act = active.as_ref().map(|u| u == &b.url).unwrap_or(false);
            match (a_act, b_act) {
                (true, false) => std::cmp::Ordering::Less,
                (false, true) => std::cmp::Ordering::Greater,
                _ => a
                    .kind
                    .rank()
                    .cmp(&b.kind.rank())
                    .then_with(|| a.url.cmp(&b.url)),
            }
        });
        // Dedup by url
        let mut seen = std::collections::HashSet::new();
        list.retain(|e| seen.insert(e.url.clone()));
        list
    }

    pub fn profile_json(&self) -> Value {
        let p = self.profile();
        json!({
            "ok": true,
            "active_url": p.active_url,
            "last_ok_at": p.last_ok_at,
            "last_kind": p.last_kind.map(|k| k.as_str()),
            "pair_id": p.pair_id,
            "hostname": p.hostname,
            "mesh": p.mesh.as_str(),
            "has_device_token": p.device_token.as_ref().map(|t| !t.is_empty()).unwrap_or(false),
            "deferred_claim": p.deferred_claim.as_ref().map(|d| json!({
                "pair_id": d.pair_id,
                "exp": d.exp,
                "remaining_secs": (d.exp - Utc::now().timestamp()).max(0),
                "has_code": !d.code.is_empty(),
            })),
            "endpoints": p.endpoints.iter().map(|e| json!({
                "url": e.url,
                "kind": e.kind.as_str(),
                "host": e.host,
                "port": e.port,
                "scheme": e.scheme,
            })).collect::<Vec<_>>(),
        })
    }
}

/// Probe `/health` or `/api/health` with short timeout. Returns latency.
pub async fn probe_endpoint(url: &str) -> ProbeResult {
    let (scheme, host, port) = crate::config::parse_base_url_parts(url);
    let kind = classify_host(&host);
    let base = format!("{scheme}://{host}:{port}");
    let started = std::time::Instant::now();
    let client = match reqwest::Client::builder()
        .timeout(PROBE_TIMEOUT)
        .connect_timeout(PROBE_TIMEOUT)
        .build()
    {
        Ok(c) => c,
        Err(e) => {
            return ProbeResult {
                url: base,
                kind,
                ok: false,
                latency_ms: 0,
                detail: e.to_string(),
            };
        }
    };

    // Try common health paths (PC backend + mobile shell)
    let paths = [
        format!("{base}/api/health"),
        format!("{base}/api/mobile/health"),
        format!("{base}/health"),
    ];
    for path in &paths {
        match client.get(path).send().await {
            Ok(res) if res.status().is_success() || res.status().as_u16() == 401 => {
                return ProbeResult {
                    url: base,
                    kind,
                    ok: true,
                    latency_ms: started.elapsed().as_millis() as u64,
                    detail: format!("HTTP {}", res.status().as_u16()),
                };
            }
            Ok(res) => {
                // reachable but unexpected — still counts as path-up for failover
                if res.status().as_u16() < 500 {
                    return ProbeResult {
                        url: base,
                        kind,
                        ok: true,
                        latency_ms: started.elapsed().as_millis() as u64,
                        detail: format!("HTTP {}", res.status().as_u16()),
                    };
                }
            }
            Err(_) => continue,
        }
    }
    ProbeResult {
        url: base,
        kind,
        ok: false,
        latency_ms: started.elapsed().as_millis() as u64,
        detail: "unreachable".into(),
    }
}

/// Probe all candidates in parallel; return ordered results + best URL.
pub async fn select_best(endpoints: &[Endpoint]) -> (Option<Endpoint>, Vec<ProbeResult>) {
    if endpoints.is_empty() {
        return (None, vec![]);
    }
    let futs: Vec<_> = endpoints
        .iter()
        .map(|e| {
            let url = e.url.clone();
            let kind = e.kind;
            async move {
                let mut r = probe_endpoint(&url).await;
                // preserve classified kind from profile when more specific
                if kind != EndpointKind::Unknown {
                    r.kind = kind;
                }
                r
            }
        })
        .collect();
    let results = futures_util::future::join_all(futs).await;

    let mut best: Option<(Endpoint, u64)> = None;
    for (ep, r) in endpoints.iter().zip(results.iter()) {
        if !r.ok {
            continue;
        }
        let score = (ep.kind.rank() as u64) * 10_000 + r.latency_ms;
        match &best {
            None => {
                best = Some((ep.clone(), score));
            }
            Some((_, s)) if score < *s => {
                best = Some((ep.clone(), score));
            }
            _ => {}
        }
    }
    (best.map(|(e, _)| e), results)
}

pub fn classify_host(host: &str) -> EndpointKind {
    let h = host.trim().to_ascii_lowercase();
    if h == "127.0.0.1" || h == "localhost" || h == "::1" {
        return EndpointKind::Loopback;
    }
    // Tailscale CGNAT 100.64.0.0/10
    if let Some(ip) = h.parse::<std::net::Ipv4Addr>().ok() {
        let o = ip.octets();
        if o[0] == 100 && (o[1] & 0xC0) == 0x40 {
            return EndpointKind::Ts;
        }
        if ip.is_private() {
            return EndpointKind::Lan;
        }
        return EndpointKind::Unknown;
    }
    // MagicDNS / hostname
    if h.contains('.') || h.chars().all(|c| c.is_ascii_alphanumeric() || c == '-') {
        // heuristic: *.ts.net is TS
        if h.ends_with(".ts.net") || h.ends_with(".tailscale.net") {
            return EndpointKind::Ts;
        }
        return EndpointKind::Host;
    }
    EndpointKind::Unknown
}

fn upsert_endpoint(list: &mut Vec<Endpoint>, ep: Endpoint) {
    if let Some(i) = list.iter().position(|e| e.url == ep.url) {
        list[i] = ep;
    } else if let Some(i) = list
        .iter()
        .position(|e| e.kind == ep.kind && e.kind == EndpointKind::Lan && e.host != ep.host)
    {
        // Replace drifted LAN IP of same kind rather than accumulating dead private IPs
        list[i] = ep;
    } else {
        list.push(ep);
    }
}

fn sort_endpoints(list: &mut [Endpoint]) {
    list.sort_by(|a, b| {
        a.kind
            .rank()
            .cmp(&b.kind.rank())
            .then_with(|| a.url.cmp(&b.url))
    });
}

/// Build claim URL list from payload (all hosts × ports).
pub fn claim_urls(payload: &PairPayload, shell_port: u16) -> Vec<String> {
    let mut urls = Vec::new();
    for ep in payload.endpoints() {
        urls.push(format!("{}/api/mobile/pair/claim", ep.url.trim_end_matches('/')));
        if ep.port != shell_port && shell_port != 0 {
            urls.push(format!(
                "{}://{}:{}/api/mobile/pair/claim",
                ep.scheme, ep.host, shell_port
            ));
        }
    }
    let loop_url = format!("http://127.0.0.1:{shell_port}/api/mobile/pair/claim");
    if shell_port != 0 && !urls.contains(&loop_url) {
        urls.push(loop_url);
    }
    let mut seen = std::collections::HashSet::new();
    urls.retain(|u| seen.insert(u.clone()));
    urls
}

pub fn err_msg(e: impl ToString) -> Error {
    Error::Msg(e.to_string())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn classify_ts_cgnat() {
        assert_eq!(classify_host("100.64.1.2"), EndpointKind::Ts);
        assert_eq!(classify_host("192.168.1.8"), EndpointKind::Lan);
        assert_eq!(classify_host("takton-pc"), EndpointKind::Host);
        assert_eq!(classify_host("127.0.0.1"), EndpointKind::Loopback);
    }

    #[test]
    fn endpoint_from_parts() {
        let e = Endpoint::from_parts("http", "192.168.1.8", 8090, EndpointKind::Lan).unwrap();
        assert_eq!(e.url, "http://192.168.1.8:8090");
    }
}
