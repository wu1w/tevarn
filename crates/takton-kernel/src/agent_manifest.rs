//! Minimal Agent SDK manifest validation (M-08) — pure Rust, no Python.

use serde::{Deserialize, Serialize};
use serde_json::{json, Value};

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AgentManifest {
    pub name: String,
    pub version: String,
    #[serde(default)]
    pub entry: String,
    #[serde(default)]
    pub capabilities: Vec<String>,
    #[serde(default)]
    pub permissions: Vec<String>,
    #[serde(default)]
    pub runtime: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ManifestValidation {
    pub ok: bool,
    pub errors: Vec<String>,
    pub warnings: Vec<String>,
    pub normalized: Option<AgentManifest>,
}

/// Validate agent.json-like JSON for SDK pack.
pub fn validate_agent_manifest(raw: &Value) -> ManifestValidation {
    let mut errors = vec![];
    let mut warnings = vec![];

    let name = raw
        .get("name")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .trim()
        .to_string();
    if name.is_empty() {
        errors.push("name is required".into());
    } else if name.len() > 64 {
        errors.push("name too long (max 64)".into());
    }

    let version = raw
        .get("version")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .trim()
        .to_string();
    if version.is_empty() {
        errors.push("version is required".into());
    }

    let entry = raw
        .get("entry")
        .and_then(|v| v.as_str())
        .unwrap_or("main")
        .to_string();

    let capabilities: Vec<String> = raw
        .get("capabilities")
        .and_then(|v| v.as_array())
        .map(|a| {
            a.iter()
                .filter_map(|x| x.as_str().map(|s| s.to_string()))
                .collect()
        })
        .unwrap_or_default();

    let permissions: Vec<String> = raw
        .get("permissions")
        .and_then(|v| v.as_array())
        .map(|a| {
            a.iter()
                .filter_map(|x| x.as_str().map(|s| s.to_string()))
                .collect()
        })
        .unwrap_or_default();

    let runtime = raw
        .get("runtime")
        .and_then(|v| v.as_str())
        .unwrap_or("python")
        .to_string();
    if !matches!(runtime.as_str(), "python" | "wasm" | "host") {
        warnings.push(format!("unusual runtime '{runtime}' (expected python|wasm|host)"));
    }

    if capabilities.is_empty() {
        warnings.push("capabilities empty — agent will get default grantable only".into());
    }
    if permissions.iter().any(|p| p == "*" || p == "root") {
        errors.push("permissions must not include * or root".into());
    }

    let ok = errors.is_empty();
    let normalized = if ok {
        Some(AgentManifest {
            name,
            version,
            entry,
            capabilities,
            permissions,
            runtime,
        })
    } else {
        None
    };

    ManifestValidation {
        ok,
        errors,
        warnings,
        normalized,
    }
}

pub fn validate_agent_manifest_str(s: &str) -> ManifestValidation {
    match serde_json::from_str::<Value>(s) {
        Ok(v) => validate_agent_manifest(&v),
        Err(e) => ManifestValidation {
            ok: false,
            errors: vec![format!("invalid json: {e}")],
            warnings: vec![],
            normalized: None,
        },
    }
}

pub fn pack_checklist() -> Value {
    json!({
        "files": ["agent.json", "README.md", "skills/ or main entry"],
        "rules": [
            "agent.json must pass validate_agent_manifest",
            "skills must pass skill_gate before activate",
            "evolution auto_apply is always false",
            "package content hash for market install",
        ],
        "rpc": ["agent_manifest_validate", "skill_register", "skill_verify", "skill_activate"],
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn accepts_minimal() {
        let v = json!({
            "name": "research-agent",
            "version": "0.1.0",
            "capabilities": ["file_read", "web_search"],
        });
        let r = validate_agent_manifest(&v);
        assert!(r.ok, "{:?}", r.errors);
    }

    #[test]
    fn rejects_star_permission() {
        let v = json!({
            "name": "bad",
            "version": "1",
            "permissions": ["*"],
        });
        let r = validate_agent_manifest(&v);
        assert!(!r.ok);
    }
}
