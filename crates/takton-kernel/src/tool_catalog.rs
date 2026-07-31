//! Tool ↔ capability catalog (P0-B).
//!
//! Authority for TOOL_TO_CREW_CAP mapping. Used by:
//! - process.has_capability / token.allows (abstract caps like `file_rw`)
//! - filter_tools for LLM schema trimming

use std::collections::{BTreeMap, BTreeSet};

/// Tool name → abstract crew capability (parity with Python grant_store.TOOL_TO_CREW_CAP).
pub static TOOL_TO_CREW_CAP: &[(&str, &str)] = &[
    ("command", "command"),
    ("bash", "command"),
    ("shell", "command"),
    ("python", "command"),
    ("process", "command"),
    ("remote_exec", "command"),
    ("terminal", "command"),
    ("file_read", "file_rw"),
    ("file_write", "file_rw"),
    ("file_edit", "file_rw"),
    ("edit", "file_rw"),
    ("apply_patch", "file_rw"),
    ("read", "file_rw"),
    ("write", "file_rw"),
    ("glob", "file_rw"),
    ("grep", "file_rw"),
    ("doc_read", "file_rw"),
    ("web_search", "web_search"),
    ("search", "web_search"),
    ("web_fetch", "web_search"),
    ("web_extract", "web_search"),
    ("fetch_webpage", "web_search"),
    ("http", "web_search"),
    ("http_get", "web_search"),
    ("browser", "browser"),
    ("git", "git"),
    ("manage_git", "git"),
    ("calendar", "calendar"),
    ("calendar_read", "calendar"),
    ("notify", "notify"),
    ("send_email", "notify"),
    ("send_message", "notify"),
    ("current_time", "web_search"),
    ("session_search", "memory"),
    ("memory", "memory"),
    ("knowledge_search", "memory"),
    ("wiki_search", "memory"),
    ("delegate_task", "delegate_task"),
    ("cronjob", "cronjob"),
    ("computer", "computer"),
];

fn map() -> BTreeMap<&'static str, &'static str> {
    TOOL_TO_CREW_CAP.iter().copied().collect()
}

pub fn crew_cap_for_tool(tool: &str) -> Option<&'static str> {
    let m = map();
    if let Some(c) = m.get(tool) {
        return Some(*c);
    }
    // prefix before ':'
    if let Some(head) = tool.split(':').next() {
        return m.get(head).copied();
    }
    None
}

/// Whether `capabilities` allow tool/cap name `target`.
///
/// Matches:
/// - exact tool name or `*`
/// - abstract crew cap (e.g. file_rw covers file_read)
/// - grantable alias equality
pub fn capability_matches(target: &str, capabilities: &BTreeSet<String>) -> bool {
    if capabilities.contains("*") || capabilities.contains(target) {
        return true;
    }
    if let Some(abstract_cap) = crew_cap_for_tool(target) {
        if capabilities.contains(abstract_cap) {
            return true;
        }
    }
    // target itself may be abstract; allow any tool that maps to it
    for (tool, cap) in TOOL_TO_CREW_CAP {
        if *cap == target && capabilities.contains(*tool) {
            return true;
        }
    }
    false
}

/// Expand capability set to concrete tool names known in the catalog.
pub fn tools_for_capabilities(capabilities: Option<&[String]>) -> Option<Vec<String>> {
    let Some(caps) = capabilities else {
        return None; // compat mode: no filter
    };
    let set: BTreeSet<String> = caps.iter().cloned().collect();
    if set.contains("*") {
        return None;
    }
    let mut tools: BTreeSet<String> = BTreeSet::new();
    // include exact cap names that look like tools
    for c in &set {
        tools.insert(c.clone());
    }
    for (tool, abstract_cap) in TOOL_TO_CREW_CAP {
        if set.contains(*tool) || set.contains(*abstract_cap) {
            tools.insert((*tool).to_string());
        }
    }
    Some(tools.into_iter().collect())
}

/// Filter a proposed tool name list by capabilities. None caps = allow all.
pub fn filter_tool_names(tool_names: &[String], capabilities: Option<&[String]>) -> Vec<String> {
    let Some(allowed) = tools_for_capabilities(capabilities) else {
        return tool_names.to_vec();
    };
    let allow: BTreeSet<_> = allowed.into_iter().collect();
    tool_names
        .iter()
        .filter(|t| {
            allow.contains(*t)
                || allow.contains(t.split(':').next().unwrap_or(t))
                || capability_matches(t, &allow)
        })
        .cloned()
        .collect()
}

/// Catalog dump for Python clients (keep in sync without hardcoding twice).
pub fn catalog_as_json() -> serde_json::Value {
    let pairs: Vec<_> = TOOL_TO_CREW_CAP
        .iter()
        .map(|(t, c)| serde_json::json!({"tool": t, "cap": c}))
        .collect();
    serde_json::json!({
        "tool_to_crew_cap": pairs,
        "version": 1
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn file_rw_covers_read_tools() {
        let caps: BTreeSet<String> = ["file_rw".into()].into_iter().collect();
        assert!(capability_matches("file_read", &caps));
        assert!(capability_matches("grep", &caps));
        assert!(!capability_matches("terminal", &caps));
    }

    #[test]
    fn filter_by_grantable() {
        let caps = vec!["file_read".into(), "grep".into()];
        let tools = vec![
            "file_read".into(),
            "grep".into(),
            "terminal".into(),
            "file_write".into(),
        ];
        let out = filter_tool_names(&tools, Some(&caps));
        assert!(out.contains(&"file_read".into()));
        assert!(out.contains(&"grep".into()));
        assert!(!out.contains(&"terminal".into()));
    }

    #[test]
    fn abstract_expands_tools() {
        let tools = tools_for_capabilities(Some(&["file_rw".into()])).unwrap();
        assert!(tools.iter().any(|t| t == "file_read"));
        assert!(tools.iter().any(|t| t == "glob"));
    }
}
