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
    ("session_search", "memory"),
    ("memory", "memory"),
    ("knowledge_search", "memory"),
    ("wiki_search", "memory"),
    ("delegate_task", "delegate_task"),
    ("cronjob", "cronjob"),
    ("computer", "computer"),
    // Main-chat orchestration (must stay in sync with tool_policy + coding profile)
    ("crew_steward", "crew_steward"),
    ("clarify", "crew_steward"),
    ("use_tool_pack", "use_tool_pack"),
    ("current_time", "current_time"),
    ("result_load", "file_read"),
    // Goals: O-KR (okr_goal) + session todos (manage_goal)
    ("okr_goal", "okr_goal"),
    ("manage_goal", "manage_goal"),
    ("autopilot", "manage_goal"),
    ("manage_skill", "manage_skill"),
    ("manage_mcp", "manage_mcp"),
    ("generate_ppt", "file_rw"),
    // Dialog config / status tools (token.allows uses tool name → abstract cap)
    ("configure_tevarn", "manage_skill"),
    ("update_config", "manage_skill"),
    ("get_system_status", "current_time"),
    ("list_available_models", "current_time"),
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
    // Dynamic MCP tools (mcp_*) — no catalog entry; allow under manage_mcp / mcp
    // so Python can mount runtime tools without rebuilding host for every server tool.
    if target.starts_with("mcp_") || target == "mcp_call" || target == "mcp" {
        if capabilities.contains("manage_mcp")
            || capabilities.contains("mcp")
            || capabilities.contains("integrations")
            || capabilities.contains("mcp_call")
        {
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

    #[test]
    fn catalog_keys_unique_and_time_mapping() {
        let mut seen = BTreeSet::new();
        for (t, _) in TOOL_TO_CREW_CAP {
            assert!(seen.insert(*t), "duplicate tool mapping: {t}");
        }
        assert_eq!(crew_cap_for_tool("current_time"), Some("current_time"));
        assert_eq!(crew_cap_for_tool("result_load"), Some("file_read"));
        assert_eq!(crew_cap_for_tool("generate_ppt"), Some("file_rw"));
        assert_eq!(crew_cap_for_tool("configure_tevarn"), Some("manage_skill"));
        assert_eq!(crew_cap_for_tool("update_config"), Some("manage_skill"));
        assert_eq!(crew_cap_for_tool("get_system_status"), Some("current_time"));
        assert_eq!(crew_cap_for_tool("list_available_models"), Some("current_time"));
    }

    #[test]
    fn manage_skill_covers_configure_tevarn() {
        let caps: BTreeSet<String> = ["manage_skill".into()].into_iter().collect();
        assert!(capability_matches("configure_tevarn", &caps));
        assert!(capability_matches("update_config", &caps));
        let time: BTreeSet<String> = ["current_time".into()].into_iter().collect();
        assert!(capability_matches("get_system_status", &time));
        assert!(capability_matches("list_available_models", &time));
    }
}
