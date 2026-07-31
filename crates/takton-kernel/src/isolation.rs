//! Isolation supervisor + sandbox profiles (P0-D).
//!
//! Tracks logical isolation intent per process. OS backends (bwrap/job) remain
//! platform adapters; this module is the policy + handle ledger.

use std::collections::HashMap;
use std::time::{SystemTime, UNIX_EPOCH};

use serde::{Deserialize, Serialize};
use serde_json::{json, Value};

fn now_secs() -> f64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs_f64())
        .unwrap_or(0.0)
}

fn short_id() -> String {
    uuid::Uuid::new_v4().simple().to_string()[..12].to_string()
}

/// Product isolation profiles (maps to computer sandbox + permission hints).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, Default)]
#[serde(rename_all = "snake_case")]
pub enum IsolationProfile {
    /// Local, no sandbox (dev only).
    Off,
    /// Interactive chat default: prefer OS sandbox, network ok.
    #[default]
    Interactive,
    /// Workforce jobs: sandbox required when available, network policy tighter.
    Workforce,
    /// Untrusted / generated code: no network, sandbox required.
    Untrusted,
    /// Read-only exploration.
    ReadOnly,
}

impl IsolationProfile {
    pub fn parse(s: &str) -> Self {
        match s.to_ascii_lowercase().replace('-', "_").as_str() {
            "off" | "local" | "none" => Self::Off,
            "workforce" | "job" | "employee" => Self::Workforce,
            "untrusted" | "strict" => Self::Untrusted,
            "read_only" | "readonly" | "plan" => Self::ReadOnly,
            _ => Self::Interactive,
        }
    }

    pub fn as_str(self) -> &'static str {
        match self {
            Self::Off => "off",
            Self::Interactive => "interactive",
            Self::Workforce => "workforce",
            Self::Untrusted => "untrusted",
            Self::ReadOnly => "read_only",
        }
    }

    pub fn prefer_backend(self) -> &'static str {
        match self {
            Self::Off => "local",
            _ => "auto",
        }
    }

    pub fn network_allowed(self) -> bool {
        matches!(self, Self::Off | Self::Interactive | Self::Workforce)
    }

    pub fn sandbox_required(self) -> bool {
        matches!(self, Self::Untrusted | Self::Workforce)
    }

    pub fn force_readonly(self) -> bool {
        matches!(self, Self::ReadOnly)
    }

    pub fn to_dict(self) -> Value {
        json!({
            "id": self.as_str(),
            "prefer_backend": self.prefer_backend(),
            "network": self.network_allowed(),
            "sandbox_required": self.sandbox_required(),
            "force_readonly": self.force_readonly(),
        })
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct IsolationHandle {
    pub id: String,
    pub process_id: String,
    pub profile: String,
    pub backend: String,
    pub command: String,
    pub started_at: f64,
    pub ended_at: Option<f64>,
    pub exit_code: Option<i32>,
    pub status: String, // running | exited | killed | denied
}

#[derive(Default)]
pub struct IsolationSupervisor {
    /// process_id → profile override
    profiles: HashMap<String, IsolationProfile>,
    default_profile: IsolationProfile,
    handles: HashMap<String, IsolationHandle>,
    /// process_id → live handle count
    live_by_process: HashMap<String, usize>,
}

impl IsolationSupervisor {
    pub fn new() -> Self {
        Self {
            default_profile: IsolationProfile::Interactive,
            ..Default::default()
        }
    }

    pub fn set_default_profile(&mut self, p: IsolationProfile) {
        self.default_profile = p;
    }

    pub fn set_process_profile(&mut self, process_id: &str, profile: IsolationProfile) {
        self.profiles.insert(process_id.to_string(), profile);
    }

    pub fn profile_for(&self, process_id: &str) -> IsolationProfile {
        self.profiles
            .get(process_id)
            .copied()
            .unwrap_or(self.default_profile)
    }

    /// Resolve execution policy for a process + optional force profile.
    pub fn resolve(
        &self,
        process_id: &str,
        force_profile: Option<&str>,
        is_workforce: bool,
    ) -> Value {
        let mut prof = if let Some(fp) = force_profile {
            IsolationProfile::parse(fp)
        } else {
            self.profile_for(process_id)
        };
        // workforce default bump
        if is_workforce && matches!(prof, IsolationProfile::Interactive) {
            prof = IsolationProfile::Workforce;
        }
        let mut d = prof.to_dict();
        d["process_id"] = json!(process_id);
        d["live_children"] = json!(self.live_by_process.get(process_id).copied().unwrap_or(0));
        d
    }

    /// Register a child exec (logical). Deny if profile forbids local bare exec when required.
    pub fn spawn(
        &mut self,
        process_id: &str,
        command: &str,
        backend: &str,
    ) -> Result<IsolationHandle, String> {
        let prof = self.profile_for(process_id);
        if prof.sandbox_required() && backend == "local" {
            return Err(format!(
                "isolation profile {} requires sandbox (got backend=local)",
                prof.as_str()
            ));
        }
        let h = IsolationHandle {
            id: short_id(),
            process_id: process_id.to_string(),
            profile: prof.as_str().to_string(),
            backend: backend.to_string(),
            command: command.chars().take(500).collect(),
            started_at: now_secs(),
            ended_at: None,
            exit_code: None,
            status: "running".into(),
        };
        *self
            .live_by_process
            .entry(process_id.to_string())
            .or_insert(0) += 1;
        self.handles.insert(h.id.clone(), h.clone());
        Ok(h)
    }

    pub fn complete(&mut self, handle_id: &str, exit_code: i32) -> Option<IsolationHandle> {
        let h = self.handles.get_mut(handle_id)?;
        if h.status != "running" {
            return Some(h.clone());
        }
        h.status = "exited".into();
        h.exit_code = Some(exit_code);
        h.ended_at = Some(now_secs());
        if let Some(n) = self.live_by_process.get_mut(&h.process_id) {
            *n = n.saturating_sub(1);
        }
        Some(h.clone())
    }

    pub fn kill(&mut self, handle_id: &str) -> Option<IsolationHandle> {
        let h = self.handles.get_mut(handle_id)?;
        if h.status == "running" {
            h.status = "killed".into();
            h.ended_at = Some(now_secs());
            if let Some(n) = self.live_by_process.get_mut(&h.process_id) {
                *n = n.saturating_sub(1);
            }
        }
        Some(h.clone())
    }

    pub fn drop_process(&mut self, process_id: &str) {
        let ids: Vec<_> = self
            .handles
            .values()
            .filter(|h| h.process_id == process_id && h.status == "running")
            .map(|h| h.id.clone())
            .collect();
        for id in ids {
            self.kill(&id);
        }
        self.profiles.remove(process_id);
        self.live_by_process.remove(process_id);
    }

    pub fn list_for_process(&self, process_id: &str) -> Vec<Value> {
        self.handles
            .values()
            .filter(|h| h.process_id == process_id)
            .map(|h| json!(h))
            .collect()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn untrusted_rejects_local() {
        let mut s = IsolationSupervisor::new();
        s.set_process_profile("p1", IsolationProfile::Untrusted);
        assert!(s.spawn("p1", "echo hi", "local").is_err());
        assert!(s.spawn("p1", "echo hi", "bwrap").is_ok());
    }

    #[test]
    fn workforce_default_in_resolve() {
        let s = IsolationSupervisor::new();
        let d = s.resolve("p", None, true);
        assert_eq!(d["id"], "workforce");
        assert_eq!(d["sandbox_required"], true);
    }
}
