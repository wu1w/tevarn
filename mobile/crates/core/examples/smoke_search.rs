//! Smoke-test phone web_search backends (Bing RSS / Google News / Wiki / HN).
use serde_json::json;
use std::path::PathBuf;
use tevarn_mobile_core::local_tools::ToolRuntime;
use tevarn_mobile_core::storage::Store;

#[tokio::main]
async fn main() {
    let dir = std::env::temp_dir().join(format!("tevarn-smoke-{}", std::process::id()));
    let _ = std::fs::create_dir_all(&dir);
    let store = Store::open(&dir).expect("store");
    let tools = ToolRuntime::new(store);

    let queries = [
        "xAI Grok 新闻",
        "Rust async runtime",
        "人工智能",
    ];
    let mut failed = 0usize;
    for q in queries {
        println!("\n======== web_search: {q} ========");
        let out = tools
            .dispatch("web_search", &json!({"query": q, "max_results": 5}))
            .await;
        let ok = !out.contains("(no results)")
            && !out.starts_with("[tool_error]")
            && out.lines().count() >= 3;
        println!("ok={ok} chars={} lines={}", out.len(), out.lines().count());
        // print first 800 chars
        let preview: String = out.chars().take(800).collect();
        println!("{preview}");
        if !ok {
            failed += 1;
        }
    }
    let _ = std::fs::remove_dir_all(PathBuf::from(&dir));
    if failed > 0 {
        eprintln!("\nSMOKE FAILED: {failed}/{} queries empty", queries.len());
        std::process::exit(1);
    }
    println!("\nSMOKE OK: all queries returned results");
}
