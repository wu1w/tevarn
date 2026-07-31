//! ABI compatibility window + break tracking (E-03).
//!
//! Policy: same major.minor is compatible; patch is additive only.
//! Breaking changes bump major and must be recorded (`abi_break_count` target 0).

use std::time::{SystemTime, UNIX_EPOCH};

use serde::{Deserialize, Serialize};
use serde_json::{json, Value};

use crate::{ABI_METHODS, ABI_VERSION};

fn now_secs() -> f64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs_f64())
        .unwrap_or(0.0)
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AbiBreakRecord {
    pub from_abi: String,
    pub to_abi: String,
    pub reason: String,
    pub methods_removed: Vec<String>,
    pub recorded_at: f64,
}

#[derive(Debug, Clone)]
pub struct AbiCompatState {
    pub min_compatible: String,
    pub max_compatible: String,
    pub breaks: Vec<AbiBreakRecord>,
    pub last_negotiate: Option<Value>,
}

impl Default for AbiCompatState {
    fn default() -> Self {
        Self {
            min_compatible: "1.0.0".into(),
            max_compatible: ABI_VERSION.into(),
            breaks: Vec::new(),
            last_negotiate: None,
        }
    }
}

/// Parse `major.minor.patch` (extra suffix ignored after first three numeric parts).
pub fn parse_semver(s: &str) -> Option<(u32, u32, u32)> {
    let core = s.split(|c| c == '-' || c == '+').next().unwrap_or(s);
    let mut parts = core.split('.');
    let major = parts.next()?.parse().ok()?;
    let minor = parts.next().unwrap_or("0").parse().unwrap_or(0);
    let patch = parts.next().unwrap_or("0").parse().unwrap_or(0);
    Some((major, minor, patch))
}

/// Compatible when same major and client.minor <= server.minor within advertised window.
pub fn is_compatible(client: &str, server: &str, min: &str, max: &str) -> bool {
    let Some((cm, cmin, _)) = parse_semver(client) else {
        return false;
    };
    let Some((sm, smin, _)) = parse_semver(server) else {
        return false;
    };
    if cm != sm {
        return false;
    }
    // client must sit inside [min, max] major.minor band
    if let Some((mn_m, mn_n, _)) = parse_semver(min) {
        if cm < mn_m || (cm == mn_m && cmin < mn_n) {
            return false;
        }
    }
    if let Some((mx_m, mx_n, _)) = parse_semver(max) {
        if cm > mx_m || (cm == mx_m && cmin > mx_n) {
            return false;
        }
    }
    // client minor must not exceed server minor on same major
    cmin <= smin && sm == cm
}

impl AbiCompatState {
    pub fn break_count(&self) -> u64 {
        self.breaks.len() as u64
    }

    pub fn snapshot(&self) -> Value {
        json!({
            "abi": ABI_VERSION,
            "min_compatible_abi": self.min_compatible,
            "max_compatible_abi": self.max_compatible,
            "compat_window": "same major; client minor <= server minor; patch additive only",
            "breaking_policy": "bump major; dual-run host for one release when possible; record every break",
            "methods_count": ABI_METHODS.len(),
            "abi_break_count": self.break_count(),
            "breaks": self.breaks,
            "last_negotiate": self.last_negotiate,
            "target_break_count": 0,
        })
    }

    /// Negotiate client ABI against host window. Stores last result.
    pub fn negotiate(&mut self, client_abi: &str) -> Value {
        let client = if client_abi.trim().is_empty() {
            "0.0.0"
        } else {
            client_abi.trim()
        };
        let compatible = is_compatible(
            client,
            ABI_VERSION,
            &self.min_compatible,
            &self.max_compatible,
        );
        let mut missing: Vec<String> = Vec::new();
        let mut reason = String::new();
        if !compatible {
            match (parse_semver(client), parse_semver(ABI_VERSION)) {
                (Some((cm, _, _)), Some((sm, _, _))) if cm != sm => {
                    reason = format!("major mismatch: client {client} vs host {ABI_VERSION}");
                }
                (Some((cm, cmin, _)), Some((sm, smin, _))) if cm == sm && cmin > smin => {
                    reason = format!(
                        "client minor {cmin} ahead of host minor {smin}; upgrade host"
                    );
                }
                (None, _) => {
                    reason = format!("unparseable client abi: {client}");
                }
                _ => {
                    reason = format!(
                        "client {client} outside window [{}, {}]",
                        self.min_compatible, self.max_compatible
                    );
                }
            }
            missing.push("upgrade_host_or_downgrade_client".into());
        }
        let result = json!({
            "ok": compatible,
            "compatible": compatible,
            "client_abi": client,
            "host_abi": ABI_VERSION,
            "min_compatible_abi": self.min_compatible,
            "max_compatible_abi": self.max_compatible,
            "reason": reason,
            "missing": missing,
            "methods_count": ABI_METHODS.len(),
            "negotiated_at": now_secs(),
        });
        self.last_negotiate = Some(result.clone());
        result
    }

    /// Record a deliberate ABI break (should stay 0 in production).
    pub fn record_break(
        &mut self,
        from_abi: &str,
        to_abi: &str,
        reason: &str,
        methods_removed: Vec<String>,
    ) -> AbiBreakRecord {
        let rec = AbiBreakRecord {
            from_abi: from_abi.to_string(),
            to_abi: to_abi.to_string(),
            reason: reason.chars().take(500).collect(),
            methods_removed,
            recorded_at: now_secs(),
        };
        self.breaks.push(rec.clone());
        // expand max window to new major only if to_abi parses higher
        if let (Some((tm, tn, _)), Some((mm, mn, _))) =
            (parse_semver(to_abi), parse_semver(&self.max_compatible))
        {
            if tm > mm || (tm == mm && tn >= mn) {
                self.max_compatible = to_abi.to_string();
            }
        }
        rec
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn same_major_compatible() {
        assert!(is_compatible("1.0.0", "1.0.0", "1.0.0", "1.0.0"));
        assert!(is_compatible("1.0.5", "1.0.0", "1.0.0", "1.0.9"));
        assert!(!is_compatible("2.0.0", "1.0.0", "1.0.0", "1.0.0"));
        assert!(!is_compatible("1.1.0", "1.0.0", "1.0.0", "1.0.0"));
    }

    #[test]
    fn negotiate_and_break_track() {
        let mut s = AbiCompatState::default();
        let ok = s.negotiate("1.0.0");
        assert_eq!(ok["compatible"], true);
        let bad = s.negotiate("2.0.0");
        assert_eq!(bad["compatible"], false);
        assert_eq!(s.break_count(), 0);
        s.record_break("1.0.0", "2.0.0", "rename mediate", vec!["old_mediate".into()]);
        assert_eq!(s.break_count(), 1);
        assert_eq!(s.snapshot()["abi_break_count"], 1);
    }
}
