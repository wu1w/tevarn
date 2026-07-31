//! Provider cache hit metrics aggregation (P0.5 E5).

use std::collections::HashMap;
use std::time::{SystemTime, UNIX_EPOCH};

use serde::{Deserialize, Serialize};
use serde_json::{json, Value};

fn now_secs() -> f64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs_f64())
        .unwrap_or(0.0)
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct FamilyStats {
    pub hits: u64,
    pub misses: u64,
    pub bytes_saved: u64,
}

impl FamilyStats {
    pub fn hit_rate(&self) -> f64 {
        let t = self.hits + self.misses;
        if t == 0 {
            0.0
        } else {
            self.hits as f64 / t as f64
        }
    }
}

#[derive(Default)]
pub struct CacheMetrics {
    by_family: HashMap<String, FamilyStats>,
}

impl CacheMetrics {
    pub fn record(&mut self, family: &str, hit: bool, bytes_saved: u64) {
        let st = self.by_family.entry(family.to_string()).or_default();
        if hit {
            st.hits += 1;
            st.bytes_saved = st.bytes_saved.saturating_add(bytes_saved);
        } else {
            st.misses += 1;
        }
    }

    pub fn status(&self) -> Value {
        let mut families = serde_json::Map::new();
        let mut total_hits = 0u64;
        let mut total_misses = 0u64;
        for (k, v) in &self.by_family {
            total_hits += v.hits;
            total_misses += v.misses;
            families.insert(
                k.clone(),
                json!({
                    "hits": v.hits,
                    "misses": v.misses,
                    "bytes_saved": v.bytes_saved,
                    "hit_rate": v.hit_rate(),
                }),
            );
        }
        let total = total_hits + total_misses;
        let overall = if total == 0 {
            0.0
        } else {
            total_hits as f64 / total as f64
        };
        json!({
            "families": families,
            "totals": {
                "hits": total_hits,
                "misses": total_misses,
                "hit_rate": overall,
            },
            "ts": now_secs(),
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn hit_rate() {
        let mut m = CacheMetrics::default();
        m.record("openai", true, 100);
        m.record("openai", false, 0);
        m.record("openai", true, 50);
        let s = m.status();
        assert!((s["families"]["openai"]["hit_rate"].as_f64().unwrap() - 2.0 / 3.0).abs() < 1e-9);
    }
}
