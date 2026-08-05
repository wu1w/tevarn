//! Catalog / provider / model filtering in Rust so Flutter only binds lists.

use serde_json::{json, Value};

/// Filter a catalog JSON by optional free-text query and/or provider id.
/// Returns a catalog-shaped value plus convenience fields:
/// - `providers` (filtered)
/// - `models` (flat model id list for the selected provider, if any)
/// - `match_count`
pub fn filter_catalog(
    mut catalog: Value,
    q: Option<&str>,
    provider_id: Option<&str>,
) -> Value {
    let q = q.map(str::trim).filter(|s| !s.is_empty());
    let q_lower = q.map(|s| s.to_lowercase());
    let provider_id = provider_id.map(str::trim).filter(|s| !s.is_empty());

    let providers = catalog
        .get_mut("providers")
        .and_then(|v| v.as_array_mut())
        .map(std::mem::take)
        .unwrap_or_default();

    let mut filtered: Vec<Value> = Vec::new();
    let mut flat_models: Vec<String> = Vec::new();

    for mut p in providers {
        let id = p
            .get("id")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_string();
        if id.is_empty() {
            continue;
        }
        if let Some(pid) = provider_id {
            if id != pid {
                continue;
            }
        }

        let name = p
            .get("name")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_string();

        // Filter model lists in-place when query present
        if let Some(ref ql) = q_lower {
            let provider_hit =
                id.to_lowercase().contains(ql) || name.to_lowercase().contains(ql);
            filter_model_lists(&mut p, ql, provider_hit);
            // Drop provider if neither name/id nor any model matches
            if !provider_hit && !provider_has_any_model(&p) {
                continue;
            }
        }

        // Collect flat models for this provider (post-filter)
        for m in collect_models(&p) {
            if !flat_models.contains(&m) {
                flat_models.push(m);
            }
        }

        filtered.push(p);
    }

    let match_count = filtered.len();
    if let Some(obj) = catalog.as_object_mut() {
        obj.insert("providers".into(), Value::Array(filtered));
        obj.insert("models".into(), json!(flat_models));
        obj.insert("match_count".into(), json!(match_count));
        if let Some(q) = q {
            obj.insert("query".into(), json!(q));
        }
        if let Some(pid) = provider_id {
            obj.insert("filtered_provider_id".into(), json!(pid));
        }
    }

    catalog
}

fn filter_model_lists(p: &mut Value, ql: &str, keep_all_if_provider_hit: bool) {
    for key in ["models", "cached_models", "available_models"] {
        if let Some(arr) = p.get_mut(key).and_then(|v| v.as_array_mut()) {
            if keep_all_if_provider_hit {
                continue;
            }
            arr.retain(|e| model_matches(e, ql));
        }
    }
    if let Some(llm) = p.get_mut("llm").and_then(|v| v.as_object_mut()) {
        if let Some(arr) = llm.get_mut("models").and_then(|v| v.as_array_mut()) {
            if !keep_all_if_provider_hit {
                arr.retain(|e| model_matches(e, ql));
            }
        }
    }
}

fn model_matches(e: &Value, ql: &str) -> bool {
    let s = if let Some(m) = e.as_object() {
        m.get("id")
            .or_else(|| m.get("model"))
            .or_else(|| m.get("name"))
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_string()
    } else {
        e.as_str().unwrap_or("").to_string()
    };
    s.to_lowercase().contains(ql)
}

fn provider_has_any_model(p: &Value) -> bool {
    !collect_models(p).is_empty()
}

fn collect_models(p: &Value) -> Vec<String> {
    let mut out = Vec::new();
    for key in ["models", "cached_models", "available_models"] {
        if let Some(arr) = p.get(key).and_then(|v| v.as_array()) {
            for e in arr {
                let s = if let Some(m) = e.as_object() {
                    m.get("id")
                        .or_else(|| m.get("model"))
                        .or_else(|| m.get("name"))
                        .and_then(|v| v.as_str())
                        .unwrap_or("")
                        .to_string()
                } else {
                    e.as_str().unwrap_or("").to_string()
                };
                if !s.is_empty() && !out.contains(&s) {
                    out.push(s);
                }
            }
        }
    }
    if let Some(llm) = p.get("llm") {
        if let Some(m) = llm
            .get("llm_model")
            .or_else(|| llm.get("model"))
            .and_then(|v| v.as_str())
        {
            if !m.is_empty() && !out.contains(&m.to_string()) {
                out.push(m.to_string());
            }
        }
        if let Some(arr) = llm.get("models").and_then(|v| v.as_array()) {
            for e in arr {
                let s = e.as_str().unwrap_or("").to_string();
                if !s.is_empty() && !out.contains(&s) {
                    out.push(s);
                }
            }
        }
    }
    if let Some(m) = p
        .get("llm_model")
        .or_else(|| p.get("model"))
        .and_then(|v| v.as_str())
    {
        if !m.is_empty() && !out.contains(&m.to_string()) {
            out.push(m.to_string());
        }
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn filters_by_provider_and_query() {
        let cat = json!({
            "providers": [
                {"id":"openai","name":"OpenAI","models":["gpt-4o","gpt-4.1"]},
                {"id":"xai","name":"xAI","models":["grok-3","grok-2"]},
            ]
        });
        let only = filter_catalog(cat.clone(), None, Some("xai"));
        assert_eq!(only["match_count"], 1);
        assert_eq!(only["providers"][0]["id"], "xai");
        assert!(only["models"].as_array().unwrap().contains(&json!("grok-3")));

        let q = filter_catalog(cat, Some("grok"), None);
        assert_eq!(q["match_count"], 1);
        assert_eq!(q["providers"][0]["id"], "xai");
    }
}
