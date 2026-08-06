//! Kernel smoke: search + skills + compress + text tool parse (no LLM key required).
use serde_json::json;
use takton_mobile_core::context_compress::{compress_messages, estimate_messages};
use takton_mobile_core::local_tools::ToolRuntime;
use takton_mobile_core::storage::Store;
use takton_mobile_core::tool_format::{parse_text_tool_calls, repair_json_args};

#[tokio::main]
async fn main() {
    let dir = std::env::temp_dir().join(format!("takton-kernel-{}", std::process::id()));
    let _ = std::fs::create_dir_all(&dir);
    let store = Store::open(&dir).expect("store");
    let tools = ToolRuntime::new(store);

    let mut failed = 0usize;

    // 1 tools list
    let schemas = tools.all_tool_schemas().await;
    println!("schemas={}", schemas.len());
    if schemas.len() < 10 {
        eprintln!("FAIL: expected >=10 tools, got {}", schemas.len());
        failed += 1;
    }

    // 2 skills seeded
    let skills = tools.skills().list();
    println!("skills={:?}", skills.iter().map(|s| s.id.as_str()).collect::<Vec<_>>());
    if skills.len() < 3 {
        eprintln!("FAIL skills");
        failed += 1;
    }

    // 3 web_search
    let s = tools
        .dispatch("web_search", &json!({"query": "xAI Grok", "max_results": 3}))
        .await;
    let ok = !s.contains("(no results)") && s.lines().count() >= 3;
    println!("web_search ok={ok} chars={}", s.len());
    if !ok {
        failed += 1;
    }

    // 4 calculator + datetime + task_plan + memory
    for (name, args) in [
        ("calculator", json!({"expression": "2+3*4"})),
        ("get_datetime", json!({})),
        ("task_plan", json!({"action": "set", "plan": "1. search\n2. summarize"})),
        ("task_plan", json!({"action": "get"})),
        ("memory_note", json!({"action": "set", "key": "k", "value": "v"})),
        ("memory_note", json!({"action": "get", "key": "k"})),
        ("list_skills", json!({})),
        ("load_skill", json!({"id": "research"})),
    ] {
        let r = tools.dispatch(name, &args).await;
        let bad = r.starts_with("[tool_error]");
        println!("tool {name} bad={bad} preview={}", r.chars().take(80).collect::<String>());
        if bad {
            failed += 1;
        }
    }

    // 5 text tool parse
    let calls = parse_text_tool_calls(
        r#"need search
<tool_call name="web_search">
{"query":"rust"}
</tool_call>"#,
    );
    println!("text_tools={}", calls.len());
    if calls.len() != 1 || calls[0].name != "web_search" {
        failed += 1;
    }
    assert!(repair_json_args("q=hello").contains("q") || repair_json_args("q=hello").contains("query"));

    // 6 compress keeps tool pairs
    let mut msgs = vec![
        json!({"role":"system","content":"sys"}),
    ];
    for i in 0..30 {
        msgs.push(json!({"role":"user","content": format!("u{i} {}", "word ".repeat(40))}));
        msgs.push(json!({
            "role":"assistant","content":null,
            "tool_calls":[{"id": format!("c{i}"), "type":"function",
                "function":{"name":"web_search","arguments":"{\"query\":\"x\"}"}}]
        }));
        msgs.push(json!({"role":"tool","tool_call_id": format!("c{i}"), "content": "R".repeat(3000)}));
        msgs.push(json!({"role":"assistant","content": format!("ans{i}")}));
    }
    let before = estimate_messages(&msgs);
    let rep = compress_messages(&mut msgs, 2000, 4000);
    let after = estimate_messages(&msgs);
    println!(
        "compress before={before} after={after} compressed={} trimmed={}",
        rep.compressed, rep.tool_results_trimmed
    );
    // no orphan tools
    let mut pending = std::collections::HashSet::new();
    let mut orphan = 0;
    for m in &msgs {
        let role = m.get("role").and_then(|r| r.as_str()).unwrap_or("");
        if role == "assistant" {
            pending.clear();
            if let Some(arr) = m.get("tool_calls").and_then(|t| t.as_array()) {
                for tc in arr {
                    if let Some(id) = tc.get("id").and_then(|x| x.as_str()) {
                        pending.insert(id.to_string());
                    }
                }
            }
        } else if role == "tool" {
            let id = m.get("tool_call_id").and_then(|x| x.as_str()).unwrap_or("");
            if !id.is_empty() && !pending.contains(id) {
                orphan += 1;
            }
        }
    }
    println!("orphan_tools={orphan}");
    if after >= before || orphan > 0 {
        eprintln!("FAIL compress");
        failed += 1;
    }

    let _ = std::fs::remove_dir_all(&dir);
    if failed > 0 {
        eprintln!("KERNEL SMOKE FAILED ({failed})");
        std::process::exit(1);
    }
    println!("KERNEL SMOKE OK");
}
