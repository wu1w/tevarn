//! Context compression for long agent sessions.
//! Goals: stay under phone token budgets, keep tool_call pairs valid, avoid hallucinated
//! "summary of tools that never ran".

use serde_json::{json, Value};

/// Rough token estimate: CJK ~1 tok/char, Latin ~1 tok/4 chars.
pub fn estimate_tokens_text(s: &str) -> usize {
    let ascii = s.chars().filter(|c| c.is_ascii()).count();
    let non = s.chars().count().saturating_sub(ascii);
    (ascii / 4) + non + 8
}

pub fn estimate_tokens_value(v: &Value) -> usize {
    estimate_tokens_text(&v.to_string())
}

pub fn estimate_messages(msgs: &[Value]) -> usize {
    msgs.iter().map(estimate_tokens_value).sum::<usize>() + 24
}

#[derive(Debug, Clone, Default)]
pub struct CompressReport {
    pub before_tokens: usize,
    pub after_tokens: usize,
    pub compressed: bool,
    pub dropped_messages: usize,
    pub tool_results_trimmed: usize,
}

/// Hard rules for safe compression:
/// 1. Never orphan a `tool` message without its preceding assistant tool_calls.
/// 2. Prefer truncating old tool *results* before dropping dialogue.
/// 3. When still over hard budget, collapse the oldest contiguous block into one system note.
pub fn compress_messages(
    msgs: &mut Vec<Value>,
    soft_tokens: usize,
    hard_tokens: usize,
) -> CompressReport {
    let soft = soft_tokens.max(800);
    let hard = hard_tokens.max(soft + 200);
    let before = estimate_messages(msgs);
    let mut report = CompressReport {
        before_tokens: before,
        after_tokens: before,
        ..Default::default()
    };
    if before <= soft {
        return report;
    }

    // Pass 1: trim oversized tool results (keep head+tail)
    for m in msgs.iter_mut() {
        if m.get("role").and_then(|r| r.as_str()) != Some("tool") {
            continue;
        }
        let Some(content) = m.get("content").and_then(|c| c.as_str()) else {
            continue;
        };
        if content.chars().count() > 1800 {
            let head: String = content.chars().take(900).collect();
            let tail: String = content
                .chars()
                .rev()
                .take(400)
                .collect::<String>()
                .chars()
                .rev()
                .collect();
            m["content"] = json!(format!(
                "{head}\n…[tool result truncated, {} chars]…\n{tail}",
                content.chars().count()
            ));
            report.tool_results_trimmed += 1;
        }
    }

    let mid = estimate_messages(msgs);
    if mid <= soft {
        report.after_tokens = mid;
        report.compressed = report.tool_results_trimmed > 0;
        return report;
    }

    // Pass 2: drop oldest non-system turns in pairs, preserving tool_call integrity.
    // Keep: first system + last `keep_tail` messages (adjusted to not split tool pairs).
    let keep_tail = 16usize;
    if msgs.len() > keep_tail + 2 {
        let mut cut = msgs.len() - keep_tail;
        // Don't start cut in the middle of tool results — walk back to user/assistant boundary
        while cut > 1 {
            let role = msgs[cut]
                .get("role")
                .and_then(|r| r.as_str())
                .unwrap_or("");
            if role == "tool" {
                cut -= 1;
                continue;
            }
            // if previous is assistant with tool_calls and this is tool — already handled
            break;
        }
        // If msgs[cut] is assistant with tool_calls, include following tools in kept region
        // i.e. move cut earlier only if we'd orphan — actually cut is start of kept region
        // Ensure msgs[cut..] doesn't start with role=tool
        while cut < msgs.len()
            && msgs[cut].get("role").and_then(|r| r.as_str()) == Some("tool")
        {
            cut = cut.saturating_sub(1);
            if cut == 0 {
                break;
            }
        }

        let system: Vec<Value> = msgs
            .iter()
            .filter(|m| m.get("role").and_then(|r| r.as_str()) == Some("system"))
            .cloned()
            .collect();
        let dropped: Vec<Value> = msgs[..cut]
            .iter()
            .filter(|m| m.get("role").and_then(|r| r.as_str()) != Some("system"))
            .cloned()
            .collect();
        let kept: Vec<Value> = msgs[cut..].to_vec();
        report.dropped_messages = dropped.len();

        let summary = summarize_dropped(&dropped);
        let mut out = Vec::new();
        if system.is_empty() {
            out.push(json!({"role":"system","content": summary}));
        } else {
            for (i, s) in system.into_iter().enumerate() {
                if i == 0 {
                    let mut c = s
                        .get("content")
                        .and_then(|x| x.as_str())
                        .unwrap_or("")
                        .to_string();
                    c.push_str("\n\n");
                    c.push_str(&summary);
                    out.push(json!({"role":"system","content": c}));
                } else {
                    out.push(s);
                }
            }
        }
        out.extend(kept);
        *msgs = out;
        report.compressed = true;
    }

    // Pass 3: if still over hard, aggressively shrink tool results again
    if estimate_messages(msgs) > hard {
        for m in msgs.iter_mut() {
            if m.get("role").and_then(|r| r.as_str()) != Some("tool") {
                continue;
            }
            if let Some(c) = m.get("content").and_then(|x| x.as_str()) {
                if c.chars().count() > 600 {
                    let head: String = c.chars().take(400).collect();
                    m["content"] = json!(format!("{head}\n…[hard-truncated]"));
                    report.tool_results_trimmed += 1;
                }
            }
        }
        report.compressed = true;
    }

    // Pass 4: repair any orphan tool messages (drop tools without preceding assistant tool_calls)
    repair_tool_pairs(msgs);

    report.after_tokens = estimate_messages(msgs);
    report
}

fn summarize_dropped(dropped: &[Value]) -> String {
    let mut users = Vec::new();
    let mut tools = Vec::new();
    let mut assistants = Vec::new();
    for m in dropped {
        let role = m.get("role").and_then(|r| r.as_str()).unwrap_or("");
        let content = m
            .get("content")
            .and_then(|c| c.as_str())
            .unwrap_or("")
            .chars()
            .take(120)
            .collect::<String>();
        match role {
            "user" if !content.is_empty() => users.push(content),
            "assistant" if !content.is_empty() => assistants.push(content),
            "tool" => {
                let name = m.get("name").and_then(|n| n.as_str()).unwrap_or("tool");
                tools.push(format!("{name}: {content}"));
            }
            _ => {
                // tool_calls only assistant
                if let Some(tcs) = m.get("tool_calls").and_then(|t| t.as_array()) {
                    for tc in tcs {
                        let n = tc
                            .pointer("/function/name")
                            .or_else(|| tc.get("name"))
                            .and_then(|x| x.as_str())
                            .unwrap_or("tool");
                        tools.push(format!("called {n}"));
                    }
                }
            }
        }
    }
    let mut s = String::from(
        "【会话压缩摘要 · 以下为较早轮次的事实压缩，非新推断。禁止编造未出现的工具结果】\n",
    );
    if !users.is_empty() {
        s.push_str("用户要点: ");
        s.push_str(&users.into_iter().take(4).collect::<Vec<_>>().join(" | "));
        s.push('\n');
    }
    if !tools.is_empty() {
        s.push_str("已执行工具: ");
        s.push_str(&tools.into_iter().take(8).collect::<Vec<_>>().join(" · "));
        s.push('\n');
    }
    if !assistants.is_empty() {
        s.push_str("助手结论片段: ");
        s.push_str(
            &assistants
                .into_iter()
                .take(3)
                .collect::<Vec<_>>()
                .join(" | "),
        );
        s.push('\n');
    }
    s
}

/// Drop orphan `role=tool` messages; ensure assistant tool_calls arguments are valid JSON strings.
pub fn repair_tool_pairs(msgs: &mut Vec<Value>) {
    let mut out = Vec::with_capacity(msgs.len());
    let mut pending_ids: std::collections::HashSet<String> = std::collections::HashSet::new();
    for m in msgs.drain(..) {
        let role = m.get("role").and_then(|r| r.as_str()).unwrap_or("");
        if role == "assistant" {
            pending_ids.clear();
            if let Some(arr) = m.get("tool_calls").and_then(|t| t.as_array()) {
                for tc in arr {
                    if let Some(id) = tc.get("id").and_then(|x| x.as_str()) {
                        pending_ids.insert(id.to_string());
                    }
                    // normalize arguments to string
                }
            }
            // normalize
            let mut mm = m;
            if let Some(arr) = mm.get_mut("tool_calls").and_then(|t| t.as_array_mut()) {
                for tc in arr.iter_mut() {
                    if let Some(args) = tc.pointer_mut("/function/arguments") {
                        if !args.is_string() {
                            *args = json!(args.to_string());
                        } else if let Some(s) = args.as_str() {
                            // repair near-json
                            if serde_json::from_str::<Value>(s).is_err() {
                                *args = json!(crate::tool_format::repair_json_args(s));
                            }
                        }
                    }
                }
            }
            out.push(mm);
        } else if role == "tool" {
            let id = m
                .get("tool_call_id")
                .and_then(|x| x.as_str())
                .unwrap_or("")
                .to_string();
            if id.is_empty() || pending_ids.contains(&id) {
                out.push(m);
            }
            // else drop orphan
        } else {
            if role == "user" || role == "system" {
                pending_ids.clear();
            }
            out.push(m);
        }
    }
    *msgs = out;
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn compress_trims_tool_results() {
        let mut msgs = vec![
            json!({"role":"system","content":"sys"}),
            json!({"role":"user","content":"hi"}),
            json!({"role":"assistant","content":null,"tool_calls":[{
                "id":"c1","type":"function","function":{"name":"web_search","arguments":"{\"query\":\"a\"}"}
            }]}),
            json!({"role":"tool","tool_call_id":"c1","content": "x".repeat(5000)}),
            json!({"role":"assistant","content":"done"}),
        ];
        let r = compress_messages(&mut msgs, 800, 1200);
        assert!(r.tool_results_trimmed >= 1 || r.compressed, "{r:?}");
        assert!(estimate_messages(&msgs) < estimate_tokens_text(&"x".repeat(5000)));
    }
}
