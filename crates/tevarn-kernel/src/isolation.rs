//! Isolation supervisor + sandbox profiles (P0-D).
//!
//! Policy + handle ledger, with **real OS process attach** for backends
//! `local` / `os` / `auto`. Sandbox backends (`bwrap`, `job`, `firejail`)
//! stay ledger-only until platform adapters land — but once an `os_pid` or
//! `Child` is attached, reap/kill go through the kernel.

use std::collections::HashMap;
use std::process::{Child, Command, Stdio};
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
    pub status: String, // running | exited | killed | denied | orphan
    /// OS process id when known (for wait/reap)
    pub os_pid: Option<u32>,
    pub reaped: bool,
}

pub struct IsolationSupervisor {
    /// process_id → profile override
    profiles: HashMap<String, IsolationProfile>,
    default_profile: IsolationProfile,
    handles: HashMap<String, IsolationHandle>,
    /// Live OS children owned by this supervisor (handle_id → Child).
    /// Not serializable — process authority lives in-kernel only.
    children: HashMap<String, Child>,
    /// process_id → live handle count
    live_by_process: HashMap<String, usize>,
    /// max concurrent children per agent process
    max_children_per_process: usize,
    reaped_total: u64,
    orphan_kills: u64,
    /// true once any real OS spawn succeeded in this process lifetime
    os_spawned_total: u64,
}

impl Default for IsolationSupervisor {
    fn default() -> Self {
        Self::new()
    }
}

impl IsolationSupervisor {
    pub fn new() -> Self {
        Self {
            profiles: HashMap::new(),
            default_profile: IsolationProfile::Interactive,
            handles: HashMap::new(),
            children: HashMap::new(),
            live_by_process: HashMap::new(),
            max_children_per_process: 8,
            reaped_total: 0,
            orphan_kills: 0,
            os_spawned_total: 0,
        }
    }

    pub fn set_max_children(&mut self, n: usize) {
        self.max_children_per_process = n.max(1);
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

    fn backend_is_os(backend: &str) -> bool {
        matches!(
            backend.to_ascii_lowercase().as_str(),
            "local" | "os" | "auto" | "native" | "process"
        )
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
        d["os_children"] = json!(self.children.len());
        d
    }

    fn policy_allow_spawn(
        &self,
        process_id: &str,
        backend: &str,
    ) -> Result<IsolationProfile, String> {
        let prof = self.profile_for(process_id);
        if prof.sandbox_required() && (backend == "local" || backend == "os" || backend == "native")
        {
            return Err(format!(
                "isolation profile {} requires sandbox (got backend={backend})",
                prof.as_str()
            ));
        }
        let live = self.live_by_process.get(process_id).copied().unwrap_or(0);
        if live >= self.max_children_per_process {
            return Err(format!(
                "isolation max children: live={live} max={}",
                self.max_children_per_process
            ));
        }
        Ok(prof)
    }

    fn register_handle(
        &mut self,
        process_id: &str,
        prof: IsolationProfile,
        command: &str,
        backend: &str,
        os_pid: Option<u32>,
    ) -> IsolationHandle {
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
            os_pid,
            reaped: false,
        };
        *self
            .live_by_process
            .entry(process_id.to_string())
            .or_insert(0) += 1;
        self.handles.insert(h.id.clone(), h.clone());
        h
    }

    /// Spawn: OS backends create a real child; sandbox backends ledger-only.
    pub fn spawn(
        &mut self,
        process_id: &str,
        command: &str,
        backend: &str,
    ) -> Result<IsolationHandle, String> {
        if Self::backend_is_os(backend) {
            return self.spawn_os(process_id, command, backend);
        }
        self.spawn_with_pid(process_id, command, backend, None)
    }

    /// Ledger-only registration (optional external pid attach).
    pub fn spawn_with_pid(
        &mut self,
        process_id: &str,
        command: &str,
        backend: &str,
        os_pid: Option<u32>,
    ) -> Result<IsolationHandle, String> {
        let prof = self.policy_allow_spawn(process_id, backend)?;
        Ok(self.register_handle(process_id, prof, command, backend, os_pid))
    }

    /// Real OS process spawn — kernel owns the Child handle.
    pub fn spawn_os(
        &mut self,
        process_id: &str,
        command: &str,
        backend: &str,
    ) -> Result<IsolationHandle, String> {
        let prof = self.policy_allow_spawn(process_id, backend)?;
        let cmd_line = command.trim();
        if cmd_line.is_empty() {
            return Err("empty command".into());
        }
        let child = Self::build_command(cmd_line)
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .spawn()
            .map_err(|e| format!("os spawn failed: {e}"))?;
        let os_pid = child.id();
        let h = self.register_handle(
            process_id,
            prof,
            cmd_line,
            if backend.is_empty() { "os" } else { backend },
            Some(os_pid),
        );
        self.children.insert(h.id.clone(), child);
        self.os_spawned_total = self.os_spawned_total.saturating_add(1);
        Ok(h)
    }

    /// Platform command builder. Free-form agent command lines go through
    /// the system shell so builtins (`echo`, `dir`) and pipes work.
    ///
    /// Environment is scrubbed: clear inherited secrets (TEVARN_*_SECRET / keys).
    fn build_command(cmd_line: &str) -> Command {
        #[cfg(windows)]
        {
            let mut c = Command::new("cmd");
            c.args(["/C", cmd_line]);
            Self::scrub_child_env(&mut c);
            c
        }
        #[cfg(not(windows))]
        {
            let mut c = Command::new("sh");
            c.args(["-c", cmd_line]);
            // audit-fix: 子进程独立进程组（pgid==pid），kill 路径可整组
            // SIGKILL，防 shell 派生的孙进程泄漏
            #[cfg(unix)]
            {
                use std::os::unix::process::CommandExt;
                c.process_group(0);
            }
            Self::scrub_child_env(&mut c);
            c
        }
    }

    fn scrub_child_env(cmd: &mut Command) {
        // Remove control-plane secrets from child env (defense in depth).
        const DROP_PREFIXES: &[&str] = &[
            "TEVARN_KERNEL_RPC_SECRET",
            "TEVARN_TOKEN_HMAC_SECRET",
            "TEVARN_JWT_SECRET",
            "SETTINGS_ENCRYPTION_KEY",
            "TEVARN_SETTINGS_ENCRYPTION",
        ];
        for k in DROP_PREFIXES {
            cmd.env_remove(k);
        }
        // Also drop any env key that looks like a secret (vars() is an iterator, not Result)
        for (k, _) in std::env::vars() {
            let ku = k.to_ascii_uppercase();
            if ku.contains("SECRET")
                || ku.ends_with("_API_KEY")
                || ku.ends_with("_TOKEN")
                || ku.contains("PASSWORD")
            {
                if ku.starts_with("TEVARN_")
                    || ku.starts_with("SETTINGS_")
                    || ku.contains("JWT")
                    || ku.contains("HMAC")
                {
                    cmd.env_remove(&k);
                }
            }
        }
    }

    pub fn attach_os_pid(&mut self, handle_id: &str, os_pid: u32) -> Option<IsolationHandle> {
        let h = self.handles.get_mut(handle_id)?;
        h.os_pid = Some(os_pid);
        Some(h.clone())
    }

    /// Non-blocking poll: try_wait on owned Child, update ledger.
    pub fn poll(&mut self, handle_id: &str) -> Option<Value> {
        if self.children.contains_key(handle_id) {
            let exited = {
                let child = self.children.get_mut(handle_id)?;
                match child.try_wait() {
                    Ok(Some(status)) => Some(status.code().unwrap_or(if status.success() {
                        0
                    } else {
                        1
                    })),
                    Ok(None) => None,
                    Err(e) => {
                        return Some(json!({
                            "ok": false,
                            "error": e.to_string(),
                            "handle_id": handle_id,
                        }));
                    }
                }
            };
            if let Some(code) = exited {
                self.children.remove(handle_id);
                let h = self.complete(handle_id, code)?;
                return Some(json!({
                    "ok": true,
                    "running": false,
                    "handle": h,
                }));
            }
            let h = self.handles.get(handle_id)?;
            return Some(json!({
                "ok": true,
                "running": true,
                "handle": h,
                "os_owned": true,
            }));
        }
        let h = self.handles.get(handle_id)?;
        Some(json!({
            "ok": true,
            "running": h.status == "running",
            "handle": h,
            "os_owned": false,
        }))
    }

    pub fn complete(&mut self, handle_id: &str, exit_code: i32) -> Option<IsolationHandle> {
        // drop Child if still held (zombie reaped via complete path)
        let _ = self.children.remove(handle_id);
        let h = self.handles.get_mut(handle_id)?;
        if h.status != "running" {
            return Some(h.clone());
        }
        h.status = "exited".into();
        h.exit_code = Some(exit_code);
        h.ended_at = Some(now_secs());
        h.reaped = true;
        self.reaped_total = self.reaped_total.saturating_add(1);
        if let Some(n) = self.live_by_process.get_mut(&h.process_id) {
            *n = n.saturating_sub(1);
        }
        Some(h.clone())
    }

    /// Kill OS child (if owned) then mark ledger killed.
    pub fn kill(&mut self, handle_id: &str) -> Option<IsolationHandle> {
        if let Some(mut child) = self.children.remove(handle_id) {
            // audit-fix: unix 下先整组 SIGKILL（pgid==child pid），再 kill+wait
            // 回收组长本体，防孙进程泄漏
            #[cfg(unix)]
            Self::kill_process_group(child.id());
            let _ = child.kill();
            let _ = child.wait();
        } else if let Some(h) = self.handles.get(handle_id) {
            if h.status == "running" {
                if let Some(pid) = h.os_pid {
                    Self::kill_pid_external(pid);
                }
            }
        }
        let h = self.handles.get_mut(handle_id)?;
        if h.status == "running" {
            h.status = "killed".into();
            h.ended_at = Some(now_secs());
            h.reaped = true;
            self.reaped_total = self.reaped_total.saturating_add(1);
            if let Some(n) = self.live_by_process.get_mut(&h.process_id) {
                *n = n.saturating_sub(1);
            }
        }
        Some(h.clone())
    }

    fn kill_pid_external(pid: u32) {
        if pid == 0 {
            return;
        }
        #[cfg(windows)]
        {
            let _ = Command::new("taskkill")
                .args(["/PID", &pid.to_string(), "/F", "/T"])
                .stdin(Stdio::null())
                .stdout(Stdio::null())
                .stderr(Stdio::null())
                .status();
        }
        #[cfg(unix)]
        {
            // audit-fix: 优先按进程组整树杀；组不存在（外部登记 pid 非
            // 进程组长）时回退单 pid
            let grouped = Command::new("kill")
                .args(["-9", &format!("-{pid}")])
                .stdin(Stdio::null())
                .stdout(Stdio::null())
                .stderr(Stdio::null())
                .status();
            match grouped {
                Ok(s) if s.success() => {}
                _ => {
                    let _ = Command::new("kill")
                        .args(["-9", &pid.to_string()])
                        .stdin(Stdio::null())
                        .stdout(Stdio::null())
                        .stderr(Stdio::null())
                        .status();
                }
            }
        }
    }

    /// audit-fix: unix 整组 SIGKILL（spawn 侧 process_group(0) 使 pgid==pid）。
    #[cfg(unix)]
    fn kill_process_group(pgid: u32) {
        if pgid == 0 {
            return;
        }
        let _ = Command::new("kill")
            .args(["-9", &format!("-{pgid}")])
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .status();
    }

    /// OS-level reaper: poll exited children + kill timed-out ones.
    pub fn reap_tick(&mut self, max_age_secs: f64) -> Value {
        let now = now_secs();
        let max_age = max_age_secs.max(1.0);
        let mut reaped = 0u64;
        let mut orphans = 0u64;
        let mut polled_exit = 0u64;
        let ids: Vec<_> = self
            .handles
            .values()
            .filter(|h| h.status == "running")
            .map(|h| h.id.clone())
            .collect();
        for id in ids {
            // first: try_wait real children that already exited
            if self.children.contains_key(&id) {
                if let Some(v) = self.poll(&id) {
                    if v.get("running") == Some(&json!(false)) {
                        polled_exit += 1;
                        reaped += 1;
                        continue;
                    }
                }
            }
            let age_ok = self
                .handles
                .get(&id)
                .map(|h| now - h.started_at > max_age)
                .unwrap_or(false);
            if !age_ok {
                continue;
            }
            let had_os = self
                .handles
                .get(&id)
                .map(|h| h.os_pid.is_some() || self.children.contains_key(&id))
                .unwrap_or(false);
            if let Some(h) = self.kill(&id) {
                if !had_os {
                    if let Some(hh) = self.handles.get_mut(&id) {
                        hh.status = "orphan".into();
                    }
                    orphans += 1;
                    self.orphan_kills = self.orphan_kills.saturating_add(1);
                }
                // silence unused warning on h when we only care about side-effect
                let _ = h;
                reaped += 1;
            }
        }
        json!({
            "reaped": reaped,
            "orphans": orphans,
            "polled_exit": polled_exit,
            "live": self.live_by_process.values().sum::<usize>(),
            "os_children": self.children.len(),
            "reaped_total": self.reaped_total,
            "orphan_kills": self.orphan_kills,
            "os_spawned_total": self.os_spawned_total,
            "max_children_per_process": self.max_children_per_process,
            "os_level": true,
            "authority": "rust",
        })
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

    pub fn status(&self) -> Value {
        json!({
            "handles": self.handles.len(),
            "live": self.live_by_process.values().sum::<usize>(),
            "os_children": self.children.len(),
            "os_spawned_total": self.os_spawned_total,
            "reaped_total": self.reaped_total,
            "orphan_kills": self.orphan_kills,
            "max_children_per_process": self.max_children_per_process,
            "default_profile": self.default_profile.as_str(),
            "os_level": true,
            "os_process_attach": true,
            "authority": "rust",
        })
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

    #[test]
    fn os_spawn_and_poll() {
        let mut s = IsolationSupervisor::new();
        s.set_process_profile("p1", IsolationProfile::Off);
        #[cfg(windows)]
        let cmd = "cmd /C exit 0";
        #[cfg(not(windows))]
        let cmd = "true";
        let h = s.spawn_os("p1", cmd, "os").expect("spawn");
        assert!(h.os_pid.is_some());
        // wait up to ~2s for exit
        let mut done = false;
        for _ in 0..40 {
            std::thread::sleep(std::time::Duration::from_millis(50));
            if let Some(v) = s.poll(&h.id) {
                if v.get("running") == Some(&json!(false)) {
                    done = true;
                    break;
                }
            }
        }
        assert!(done, "child should exit");
        assert_eq!(s.status()["os_spawned_total"], 1);
    }
}
