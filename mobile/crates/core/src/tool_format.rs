//! Multi-provider tool-call parsing & normalization.
//! Supports OpenAI FC, XML/text tool blocks (Codex / local models), and loose JSON repair.

use serde_json::{json, Value};

#[derive(Debug, Clone)]
pub struct ParsedToolCall {
    pub id: String,
    pub name: String,
    pub arguments: String, // always a JSON object string
}

/// Normalize a raw arguments string into a JSON object string.
pub fn repair_json_args(raw: &str) -> String {
    let s = raw.trim();
    if s.is_empty() {
        return "{}".into();
    }
    if let Ok(v) = serde_json::from_str::<Value>(s) {
        return match v {
            Value::Object(_) => v.to_string(),
            Value::String(inner) => {
                if let Ok(v2) = serde_json::from_str::<Value>(&inner) {
                    if v2.is_object() {
                        return v2.to_string();
                    }
                }
                json!({"value": inner}).to_string()
            }
            other => json!({"value": other}).to_string(),
        };
    }
    // single quotes → double
    let alt = s.replace('\'', "\"");
    if let Ok(v) = serde_json::from_str::<Value>(&alt) {
        if v.is_object() {
            return v.to_string();
        }
    }
    // key: value lines
    if s.contains(':') && !s.contains('{') {
        // try query= form
        if let Some((k, v)) = s.split_once('=') {
            return json!({k.trim(): v.trim()}).to_string();
        }
    }
    // bare query string for web_search
    json!({"query": s.trim_matches('"')}).to_string()
}

/// Extract tool calls from assistant free-text (non-FC models / Codex).
pub fn parse_text_tool_calls(content: &str) -> Vec<ParsedToolCall> {
    let mut out = Vec::new();
    // <tool_call name="web_search">{"query":"..."}</tool_call>
    // <tool_call name="web_search">query is ...</tool_call>
    let mut rest = content;
    let mut i = 0u32;
    while let Some(start) = rest.find("<tool_call") {
        let after = &rest[start..];
        let Some(gt) = after.find('>') else { break };
        let open = &after[..gt];
        let name = attr(open, "name").unwrap_or_else(|| attr(open, "tool").unwrap_or_default());
        let body_start = start + gt + 1;
        let close_tag = "</tool_call>";
        let Some(end_rel) = rest[body_start..].find(close_tag) else { break };
        let body = rest[body_start..body_start + end_rel].trim();
        rest = &rest[body_start + end_rel + close_tag.len()..];
        if name.is_empty() {
            continue;
        }
        out.push(ParsedToolCall {
            id: format!("txt_call_{i}"),
            name: name.trim().to_string(),
            arguments: repair_json_args(body),
        });
        i += 1;
    }

    // ```tool_call
    // web_search
    // {"query":"..."}
    // ```
    if out.is_empty() {
        let mut r = content;
        while let Some(start) = r.find("```tool") {
            let after = &r[start + 3..];
            let Some(nl) = after.find('\n') else { break };
            let body = &after[nl + 1..];
            let Some(end) = body.find("```") else { break };
            let block = body[..end].trim();
            r = &body[end + 3..];
            let mut lines = block.lines();
            let name = lines.next().unwrap_or("").trim().to_string();
            let args = lines.collect::<Vec<_>>().join("\n");
            if !name.is_empty() {
                out.push(ParsedToolCall {
                    id: format!("fence_call_{i}"),
                    name,
                    arguments: repair_json_args(args.trim()),
                });
                i += 1;
            }
        }
    }

    // invoke tool web_search with {"query":"..."}
    if out.is_empty() {
        for (idx, line) in content.lines().enumerate() {
            let l = line.trim();
            let lower = l.to_lowercase();
            if lower.starts_with("invoke tool ") || lower.starts_with("call tool ") {
                let rest = if lower.starts_with("invoke tool ") {
                    &l["invoke tool ".len()..]
                } else {
                    &l["call tool ".len()..]
                };
                let rest = rest.trim();
                let (name, args) = if let Some((n, a)) = rest.split_once(" with ") {
                    (n.trim(), a.trim())
                } else if let Some((n, a)) = rest.split_once(' ') {
                    (n.trim(), a.trim())
                } else {
                    (rest, "{}")
                };
                if !name.is_empty() {
                    out.push(ParsedToolCall {
                        id: format!("invoke_{idx}"),
                        name: name.to_string(),
                        arguments: repair_json_args(args),
                    });
                }
            }
        }
    }

    out
}

fn attr(open: &str, key: &str) -> Option<String> {
    // name="..." or name='...'
    let pat1 = format!("{key}=\"");
    if let Some(p) = open.find(&pat1) {
        let s = &open[p + pat1.len()..];
        if let Some(e) = s.find('"') {
            return Some(s[..e].to_string());
        }
    }
    let pat2 = format!("{key}='");
    if let Some(p) = open.find(&pat2) {
        let s = &open[p + pat2.len()..];
        if let Some(e) = s.find('\'') {
            return Some(s[..e].to_string());
        }
    }
    None
}

/// Strip tool XML from user-visible content.
pub fn strip_tool_markup(content: &str) -> String {
    let mut s = content.to_string();
    while let Some(start) = s.find("<tool_call") {
        if let Some(end) = s[start..].find("</tool_call>") {
            let end = start + end + "</tool_call>".len();
            s.replace_range(start..end, "");
        } else {
            break;
        }
    }
    // fences
    while let Some(start) = s.find("```tool") {
        if let Some(rel) = s[start + 3..].find("```") {
            let end = start + 3 + rel + 3;
            s.replace_range(start..end, "");
        } else {
            break;
        }
    }
    s.trim().to_string()
}

/// Prompt appendix for models without native FC (Codex / some CN models).
pub const TEXT_TOOL_PROTOCOL: &str = r#"
当你需要调用工具时，用以下 XML（可多次）：
<tool_call name="TOOL_NAME">
{"arg":"value"}
</tool_call>
不要编造工具结果；等待系统返回后再回答用户。
"#;

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parse_xml_tool() {
        let c = r#"先搜一下
<tool_call name="web_search">
{"query":"rust async"}
</tool_call>"#;
        let t = parse_text_tool_calls(c);
        assert_eq!(t.len(), 1);
        assert_eq!(t[0].name, "web_search");
        assert!(t[0].arguments.contains("rust"));
    }

    #[test]
    fn repair_bare_query() {
        let a = repair_json_args("hello world");
        assert!(a.contains("query"));
    }
}
