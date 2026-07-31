//! Permission court — capability + tool policy layers (P0-D).
//!
//! Priority (fixed):
//!   disabled > secret_floor > user_deny > skill > path
//!   > steward > user_allow > profile > capability > default

use std::collections::BTreeSet;
use std::path::{Component, Path, PathBuf};

use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};

use crate::process::AgentProcess;
use crate::tool_catalog::capability_matches;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CourtDecision {
    pub tool: String,
    pub args_digest: String,
    pub verdict: String, // allow | deny | ask
    pub matched_rule: String,
    pub layer: String,
    pub reason: String,
    pub capability_checked: bool,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub extra: Option<Value>,
}

impl CourtDecision {
    pub fn to_audit(&self) -> Value {
        let mut d = json!({
            "tool": self.tool,
            "args_digest": self.args_digest,
            "verdict": self.verdict,
            "matched_rule": self.matched_rule,
            "layer": self.layer,
            "reason": self.reason,
            "capability_checked": self.capability_checked,
        });
        if let Some(ref e) = self.extra {
            d["extra"] = e.clone();
        }
        d
    }

    fn make(
        tool: impl Into<String>,
        digest: impl Into<String>,
        verdict: &str,
        rule: &str,
        layer: &str,
        reason: impl Into<String>,
        checked: bool,
        extra: Option<Value>,
    ) -> Self {
        Self {
            tool: tool.into(),
            args_digest: digest.into(),
            verdict: verdict.into(),
            matched_rule: rule.into(),
            layer: layer.into(),
            reason: reason.into(),
            capability_checked: checked,
            extra,
        }
    }
}

/// Runtime policy knobs loaded from host/Python.
#[derive(Debug, Clone)]
pub struct CourtPolicy {
    pub permission_enabled: bool,
    pub relax_secrets: bool,
    pub workspace_root: PathBuf,
    pub user_deny: Vec<String>,
    pub user_allow: Vec<String>,
    pub profile: String, // build | plan | cautious
    pub secret_globs: Vec<String>,
    pub secret_allow_globs: Vec<String>,
}

impl Default for CourtPolicy {
    fn default() -> Self {
        Self {
            permission_enabled: true,
            relax_secrets: false,
            workspace_root: std::env::current_dir().unwrap_or_else(|_| PathBuf::from(".")),
            user_deny: vec![],
            user_allow: vec![],
            profile: "build".into(),
            secret_globs: DEFAULT_SECRET_GLOBS.iter().map(|s| (*s).to_string()).collect(),
            secret_allow_globs: DEFAULT_SECRET_ALLOW
                .iter()
                .map(|s| (*s).to_string())
                .collect(),
        }
    }
}

pub static DEFAULT_SECRET_GLOBS: &[&str] = &[
    "*.env",
    "*.env.*",
    ".env",
    "**/.env",
    "**/.env.*",
    "**/secrets/**",
    "**/*secret*",
    "**/*credentials*",
    "**/id_rsa",
    "**/id_rsa.*",
    "**/id_ed25519",
    "**/id_ed25519.*",
    "**/*.pem",
    "**/*.p12",
    "**/*.pfx",
    "**/credentials.json",
    "**/service-account*.json",
    "**/.aws/credentials",
    "**/.ssh/**",
    // T1 deepen: common secret surfaces
    "**/kubeconfig",
    "**/config/gcloud/**",
    "**/.gnupg/**",
    "**/wallet.json",
    "**/*token*.json",
    "**/.netrc",
    "**/application-*.yml",
    "**/application-*.yaml",
    "**/auth.json",
];

pub static DEFAULT_SECRET_ALLOW: &[&str] = &[
    "*.env.example",
    "**/.env.example",
    "*.env.sample",
    "**/.env.sample",
];

static WRITE_TOOLS: &[&str] = &[
    "file_write",
    "edit",
    "apply_patch",
    "desktop_write_file",
    "write",
];
static COMMAND_TOOLS: &[&str] = &[
    "command",
    "bash",
    "shell",
    "python",
    "process",
    "terminal",
    "computer",
];

pub fn args_digest(tool: &str, arguments: Option<&Value>) -> String {
    let mut clean = serde_json::Map::new();
    if let Some(Value::Object(map)) = arguments {
        let mut keys: Vec<_> = map.keys().cloned().collect();
        keys.sort();
        for k in keys {
            if k.starts_with('_') {
                continue;
            }
            let v = &map[&k];
            let vv = if let Value::String(s) = v {
                if s.len() > 200 {
                    Value::String(format!("{}…", &s[..200]))
                } else {
                    v.clone()
                }
            } else {
                v.clone()
            };
            clean.insert(k, vv);
        }
    }
    let raw = json!({"tool": tool, "args": Value::Object(clean)});
    let text = serde_json::to_string(&raw).unwrap_or_default();
    let mut hasher = Sha256::new();
    hasher.update(text.as_bytes());
    format!("sha256:{}", &hex::encode(hasher.finalize())[..16])
}

pub fn decide_capability(
    process_id: &str,
    action: &str,
    target: &str,
    proc: Option<&AgentProcess>,
    args: Option<&Value>,
) -> CourtDecision {
    let tool = format!("{action}:{target}");
    let digest = args_digest(&tool, args);

    let Some(proc) = proc else {
        return CourtDecision::make(
            tool,
            digest,
            "deny",
            "capability:unknown_process",
            "capability",
            format!("未知进程 {process_id}"),
            true,
            None,
        );
    };

    if proc.is_terminal() {
        return CourtDecision::make(
            tool,
            digest,
            "deny",
            "capability:terminal",
            "capability",
            format!("进程已终止（{}）", proc.state),
            true,
            None,
        );
    }

    if let Some(ref token) = proc.token {
        if token.is_expired() {
            return CourtDecision::make(
                tool,
                digest,
                "deny",
                "capability:token_expired",
                "capability",
                "能力令牌已过期",
                true,
                Some(json!({"token_id": token.id})),
            );
        }
        if !token.allows(target) {
            return CourtDecision::make(
                tool,
                digest,
                "deny",
                "capability:token_scope",
                "capability",
                format!("令牌范围不含 '{target}'（action={action}）"),
                true,
                Some(json!({"token_id": token.id})),
            );
        }
        return CourtDecision::make(
            tool,
            digest,
            "allow",
            "capability:token_ok",
            "capability",
            "token mediated",
            true,
            Some(json!({"token_id": token.id})),
        );
    }

    if proc.capabilities.is_some() && !proc.has_capability(target) {
        return CourtDecision::make(
            tool,
            digest,
            "deny",
            "capability:set_miss",
            "capability",
            format!("能力集不含 '{target}'（action={action}）"),
            true,
            None,
        );
    }

    let checked = proc.capabilities.is_some();
    CourtDecision::make(
        tool,
        digest,
        "allow",
        if checked {
            "capability:set_ok"
        } else {
            "capability:compat"
        },
        "capability",
        "mediated",
        checked,
        None,
    )
}

/// Full tool decision (sync layers). Steward uses caps from args/meta.
pub fn decide_tool(
    name: &str,
    args: Option<&Value>,
    policy: &CourtPolicy,
    proc: Option<&AgentProcess>,
    skill_tools: Option<&[String]>,
    skill_deny: Option<&[String]>,
) -> CourtDecision {
    let digest = args_digest(name, args);
    if !policy.permission_enabled {
        return CourtDecision::make(
            name,
            digest,
            "allow",
            "disabled",
            "disabled",
            "agent_permission_enabled=false",
            false,
            None,
        );
    }

    // 1) secret floor
    if !policy.relax_secrets {
        if let Some(path) = extract_path(args) {
            if path_matches_any(&path, &policy.secret_allow_globs) {
                // allowed secret-adjacent
            } else if path_matches_any(&path, &policy.secret_globs) {
                return CourtDecision::make(
                    name,
                    digest,
                    "deny",
                    "secret_floor",
                    "secret_floor",
                    "secret floor deny",
                    false,
                    Some(json!({"path": path})),
                );
            }
        }
    }

    // 2) user deny patterns (tool name or path glob)
    for pat in &policy.user_deny {
        if pat == name || name.contains(pat) {
            return CourtDecision::make(
                name,
                digest,
                "deny",
                "user_deny",
                "user_deny",
                format!("user deny: {pat}"),
                false,
                None,
            );
        }
        if let Some(path) = extract_path(args) {
            if path_matches(&path, pat) {
                return CourtDecision::make(
                    name,
                    digest,
                    "deny",
                    "user_deny:path",
                    "user_deny",
                    format!("user deny path: {pat}"),
                    false,
                    Some(json!({"path": path})),
                );
            }
        }
    }

    // 3) skill contract
    if let Some(deny) = skill_deny {
        if deny.iter().any(|d| d == name) {
            return CourtDecision::make(
                name,
                digest,
                "deny",
                &format!("skill:deny:{name}"),
                "skill",
                "skill contract deny",
                false,
                None,
            );
        }
    }
    if let Some(allow) = skill_tools {
        if !allow.is_empty() && !allow.iter().any(|t| t == name) {
            return CourtDecision::make(
                name,
                digest,
                "deny",
                &format!("skill:tools!{name}"),
                "skill",
                format!("skill contract 不允许工具 {name}"),
                false,
                None,
            );
        }
    }

    // 4) path: outside workspace / path traversal
    if let Some(path) = extract_path(args) {
        if is_path_escape(&path) {
            return CourtDecision::make(
                name,
                digest,
                "deny",
                "path:traversal",
                "path",
                "path traversal denied",
                false,
                Some(json!({"path": path})),
            );
        }
        if is_outside_workspace(&path, &policy.workspace_root) {
            return CourtDecision::make(
                name,
                digest,
                "deny",
                "path:workspace",
                "path",
                "path outside workspace",
                false,
                Some(json!({"path": path, "workspace": policy.workspace_root.display().to_string()})),
            );
        }
    }

    // 5) steward (workforce) — if identity caps provided
    let is_wf = args_flag(args, "_workforce")
        || args_str(args, "_agent_key").is_some_and(|k| k.starts_with("wf:"));
    if is_wf && !is_human_strategy_surface(name) {
        if let Some(caps) = identity_caps(args, proc) {
            let set: BTreeSet<String> = caps.into_iter().collect();
            if capability_matches(name, &set) || set.contains("*") {
                return CourtDecision::make(
                    name,
                    digest,
                    "allow",
                    "steward:allow",
                    "steward",
                    "workforce identity cap",
                    true,
                    Some(json!({"caps": set})),
                );
            }
            return CourtDecision::make(
                name,
                digest,
                "deny",
                "steward:deny",
                "steward",
                format!("编制能力不含 '{name}'"),
                true,
                Some(json!({"caps": set})),
            );
        }
    }

    // 6) user allow
    for pat in &policy.user_allow {
        if pat == name || name.contains(pat) {
            return CourtDecision::make(
                name,
                digest,
                "allow",
                "user_allow",
                "user_allow",
                format!("user allow: {pat}"),
                false,
                None,
            );
        }
    }

    // 7) profile gate
    let profile = policy.profile.to_ascii_lowercase();
    if profile == "plan" || profile == "readonly" {
        if WRITE_TOOLS.contains(&name) || COMMAND_TOOLS.contains(&name) {
            return CourtDecision::make(
                name,
                digest,
                "deny",
                "profile:plan",
                "profile",
                "plan/readonly profile blocks write/command",
                false,
                Some(json!({"profile": profile})),
            );
        }
    }

    // 8) capability layer (process)
    if let Some(proc) = proc {
        let cap = decide_capability(&proc.id, "tool_call", name, Some(proc), args);
        if cap.verdict != "allow" {
            return CourtDecision {
                tool: name.into(),
                args_digest: digest,
                ..cap
            };
        }
        // write/command need ask unless session grant flag
        if WRITE_TOOLS.contains(&name) || COMMAND_TOOLS.contains(&name) {
            if args_flag(args, "_confirm_ok") || args_flag(args, "_session_grant") {
                return CourtDecision::make(
                    name,
                    digest,
                    "allow",
                    "session_grant",
                    "session_grant",
                    "本会话已授权",
                    cap.capability_checked,
                    Some(json!({"_confirm_ok": true})),
                );
            }
            return CourtDecision::make(
                name,
                digest,
                "ask",
                "profile:confirm",
                "profile",
                "high-risk tool requires confirmation",
                cap.capability_checked,
                Some(json!({"profile": policy.profile})),
            );
        }
        return CourtDecision::make(
            name,
            digest,
            "allow",
            &cap.matched_rule,
            "capability",
            cap.reason,
            cap.capability_checked,
            None,
        );
    }

    // no process — default ask for risky, allow for read-ish
    if WRITE_TOOLS.contains(&name) || COMMAND_TOOLS.contains(&name) {
        if args_flag(args, "_confirm_ok") {
            return CourtDecision::make(
                name,
                digest,
                "allow",
                "session_grant",
                "session_grant",
                "confirmed",
                false,
                Some(json!({"_confirm_ok": true})),
            );
        }
        return CourtDecision::make(
            name,
            digest,
            "ask",
            "default:ask",
            "default",
            "confirmation required",
            false,
            None,
        );
    }

    CourtDecision::make(
        name,
        digest,
        "allow",
        "default:allow",
        "default",
        "default allow",
        false,
        None,
    )
}

fn extract_path(args: Option<&Value>) -> Option<String> {
    let obj = args?.as_object()?;
    for key in ["path", "file", "filepath", "file_path", "target", "filename"] {
        if let Some(Value::String(s)) = obj.get(key) {
            if !s.is_empty() {
                return Some(s.clone());
            }
        }
    }
    None
}

fn args_flag(args: Option<&Value>, key: &str) -> bool {
    args.and_then(|a| a.get(key))
        .and_then(|v| v.as_bool())
        .unwrap_or(false)
        || args
            .and_then(|a| a.get(key))
            .and_then(|v| v.as_str())
            .is_some_and(|s| s == "1" || s.eq_ignore_ascii_case("true"))
}

fn args_str(args: Option<&Value>, key: &str) -> Option<String> {
    args.and_then(|a| a.get(key))
        .and_then(|v| v.as_str())
        .map(|s| s.to_string())
}

fn identity_caps(args: Option<&Value>, proc: Option<&AgentProcess>) -> Option<Vec<String>> {
    if let Some(Value::Array(arr)) = args.and_then(|a| a.get("_identity_capabilities")) {
        let caps: Vec<_> = arr
            .iter()
            .filter_map(|x| x.as_str().map(|s| s.to_string()))
            .collect();
        if !caps.is_empty() {
            return Some(caps);
        }
    }
    proc.and_then(|p| p.capabilities.clone())
}

fn is_human_strategy_surface(name: &str) -> bool {
    matches!(
        name,
        "crew_steward" | "notify" | "ask_user" | "message_user"
    )
}

fn path_matches_any(path: &str, globs: &[String]) -> bool {
    globs.iter().any(|g| path_matches(path, g))
}

/// Minimal glob: `*` segment, `**` anywhere, suffix/prefix.
fn path_matches(path: &str, pattern: &str) -> bool {
    let p = path.replace('\\', "/").to_ascii_lowercase();
    let g = pattern.replace('\\', "/").to_ascii_lowercase();
    let base = p.rsplit('/').next().unwrap_or(&p);
    if g == p || g == base || p.ends_with(&format!("/{g}")) {
        return true;
    }
    // .env / .env.local style
    if g == ".env" || g.ends_with("/.env") {
        if base == ".env" || base.starts_with(".env.") {
            return true;
        }
    }
    if let Some(suf) = g.strip_prefix("*.") {
        if base == format!(".{suf}") || base.ends_with(&format!(".{suf}")) {
            return true;
        }
        if suf == "env" && (base == ".env" || base.starts_with(".env.")) {
            return true;
        }
    }
    if let Some(rest) = g.strip_prefix("**/") {
        if rest.starts_with("*.") {
            let ext = &rest[2..];
            if base.ends_with(&format!(".{ext}")) || base == format!(".{ext}") {
                return true;
            }
            if ext.starts_with("env") && (base == ".env" || base.starts_with(".env.")) {
                return true;
            }
        }
        if base == rest || p.ends_with(&format!("/{rest}")) || p.contains(&format!("/{rest}/")) {
            return true;
        }
        if rest.contains('*') {
            let mid = rest.replace('*', "");
            if !mid.is_empty() && (base.contains(&mid) || p.contains(&mid)) {
                return true;
            }
        }
    }
    if g.starts_with("**/*") && g.ends_with('*') {
        let mid = g.trim_start_matches("**/").trim_matches('*');
        if !mid.is_empty() && p.contains(mid) {
            return true;
        }
    }
    p.ends_with(&g) || p.contains(&g)
}

fn is_path_escape(path: &str) -> bool {
    let path = Path::new(path);
    path.components().any(|c| matches!(c, Component::ParentDir))
}

fn is_outside_workspace(path: &str, workspace: &Path) -> bool {
    let p = Path::new(path);
    // absolute path outside workspace
    if p.is_absolute() {
        let ws = match workspace.canonicalize() {
            Ok(w) => w,
            Err(_) => workspace.to_path_buf(),
        };
        let full = p.canonicalize().unwrap_or_else(|_| p.to_path_buf());
        return !full.starts_with(&ws);
    }
    // relative with parent dir already caught
    false
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn secret_floor_denies_env() {
        let policy = CourtPolicy::default();
        let args = json!({"path": "/home/u/project/.env"});
        let d = decide_tool("file_read", Some(&args), &policy, None, None, None);
        assert_eq!(d.verdict, "deny");
        assert_eq!(d.layer, "secret_floor");
    }

    #[test]
    fn env_example_not_secret_denied() {
        let policy = CourtPolicy::default();
        let args = json!({"path": ".env.example"});
        let d = decide_tool("file_read", Some(&args), &policy, None, None, None);
        // allow path may match secret allow — should not be secret deny
        assert_ne!(d.matched_rule, "secret_floor");
    }

    #[test]
    fn write_tool_asks() {
        let policy = CourtPolicy::default();
        let proc = AgentProcess::new("main", None, None, Some(vec!["file_write".into()]), None, Default::default());
        let d = decide_tool(
            "file_write",
            Some(&json!({"path": "a.txt", "content": "x"})),
            &policy,
            Some(&proc),
            None,
            None,
        );
        assert_eq!(d.verdict, "ask");
    }

    #[test]
    fn steward_denies_missing_cap() {
        let policy = CourtPolicy::default();
        let args = json!({
            "_workforce": true,
            "_identity_capabilities": ["file_read"],
        });
        let d = decide_tool("command", Some(&args), &policy, None, None, None);
        assert_eq!(d.layer, "steward");
        assert_eq!(d.verdict, "deny");
    }
}
