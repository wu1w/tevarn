//! Layered memory + consolidation (P1 M-03 / G7).
//! working / episodic / semantic / skill

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

fn short_id() -> String {
    uuid::Uuid::new_v4().simple().to_string()[..12].to_string()
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum MemoryLayer {
    Working,
    Episodic,
    Semantic,
    Skill,
}

impl MemoryLayer {
    pub fn parse(s: &str) -> Self {
        match s {
            "episodic" => Self::Episodic,
            "semantic" => Self::Semantic,
            "skill" => Self::Skill,
            _ => Self::Working,
        }
    }

    pub fn as_str(self) -> &'static str {
        match self {
            Self::Working => "working",
            Self::Episodic => "episodic",
            Self::Semantic => "semantic",
            Self::Skill => "skill",
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct MemoryEntry {
    pub id: String,
    pub identity: String,
    pub layer: String,
    pub content: String,
    pub score: f64,
    pub created_at: f64,
    pub consolidated_at: Option<f64>,
}

pub struct LayeredMemory {
    entries: HashMap<String, MemoryEntry>,
    by_identity: HashMap<String, Vec<String>>,
    consolidations: u64,
    /// max working-layer entries per identity (isolation hard cap)
    working_cap: usize,
    schedule_ticks: u64,
    gc_dropped: u64,
}

impl Default for LayeredMemory {
    fn default() -> Self {
        Self {
            entries: HashMap::new(),
            by_identity: HashMap::new(),
            consolidations: 0,
            working_cap: 64,
            schedule_ticks: 0,
            gc_dropped: 0,
        }
    }
}

impl LayeredMemory {
    pub fn set_working_cap(&mut self, n: usize) {
        self.working_cap = n.max(4);
    }

    /// Kernel isolation: entry belongs only to its identity namespace.
    pub fn assert_identity_access(&self, entry_id: &str, identity: &str) -> Result<(), String> {
        let e = self
            .entries
            .get(entry_id)
            .ok_or_else(|| format!("unknown memory entry {entry_id}"))?;
        if e.identity != identity {
            return Err(format!(
                "memory:isolation_denied entry={entry_id} owner={} caller={identity}",
                e.identity
            ));
        }
        Ok(())
    }

    pub fn put(
        &mut self,
        identity: &str,
        layer: MemoryLayer,
        content: &str,
        score: f64,
    ) -> MemoryEntry {
        let e = MemoryEntry {
            id: short_id(),
            identity: identity.to_string(),
            layer: layer.as_str().to_string(),
            content: content.chars().take(4000).collect(),
            score,
            created_at: now_secs(),
            consolidated_at: None,
        };
        self.by_identity
            .entry(identity.to_string())
            .or_default()
            .push(e.id.clone());
        self.entries.insert(e.id.clone(), e.clone());
        if layer == MemoryLayer::Working {
            self.gc_working(identity);
        }
        e
    }

    /// Drop lowest-score working entries over cap (isolation hard limit).
    pub fn gc_working(&mut self, identity: &str) -> u64 {
        let mut working: Vec<(String, f64, f64)> = self
            .list(identity, Some(MemoryLayer::Working))
            .into_iter()
            .map(|e| (e.id, e.score, e.created_at))
            .collect();
        if working.len() <= self.working_cap {
            return 0;
        }
        // drop lowest score then oldest
        working.sort_by(|a, b| {
            a.1.partial_cmp(&b.1)
                .unwrap_or(std::cmp::Ordering::Equal)
                .then(a.2.partial_cmp(&b.2).unwrap_or(std::cmp::Ordering::Equal))
        });
        let drop_n = working.len() - self.working_cap;
        let mut dropped = 0u64;
        for (id, _, _) in working.into_iter().take(drop_n) {
            self.entries.remove(&id);
            if let Some(ids) = self.by_identity.get_mut(identity) {
                ids.retain(|x| x != &id);
            }
            dropped += 1;
            self.gc_dropped = self.gc_dropped.saturating_add(1);
        }
        dropped
    }

    /// Scheduler: consolidate + gc for one or all identities.
    pub fn schedule_tick(&mut self, identity: Option<&str>) -> Value {
        self.schedule_ticks = self.schedule_ticks.saturating_add(1);
        let ids: Vec<String> = if let Some(i) = identity {
            vec![i.to_string()]
        } else {
            self.by_identity.keys().cloned().collect()
        };
        let mut consolidated = 0u64;
        let mut gced = 0u64;
        for id in ids {
            let r = self.consolidate(&id);
            consolidated += r
                .get("promoted_to_episodic")
                .and_then(|v| v.as_u64())
                .unwrap_or(0);
            gced += self.gc_working(&id);
        }
        json!({
            "ticks": self.schedule_ticks,
            "consolidated": consolidated,
            "gc_dropped": gced,
            "gc_dropped_total": self.gc_dropped,
            "isolation": "identity_namespace",
            "working_cap": self.working_cap,
        })
    }

    pub fn list(&self, identity: &str, layer: Option<MemoryLayer>) -> Vec<MemoryEntry> {
        self.by_identity
            .get(identity)
            .into_iter()
            .flatten()
            .filter_map(|id| self.entries.get(id))
            .filter(|e| {
                layer
                    .map(|l| e.layer == l.as_str())
                    .unwrap_or(true)
            })
            .cloned()
            .collect()
    }

    /// Promote high-score working → episodic; episodic summaries → semantic.
    pub fn consolidate(&mut self, identity: &str) -> Value {
        let mut promoted_ep = 0u32;
        let mut promoted_sem = 0u32;
        let now = now_secs();
        let working: Vec<_> = self
            .list(identity, Some(MemoryLayer::Working))
            .into_iter()
            .filter(|e| e.score >= 0.7)
            .collect();
        for w in working {
            if let Some(e) = self.entries.get_mut(&w.id) {
                e.layer = MemoryLayer::Episodic.as_str().to_string();
                e.consolidated_at = Some(now);
                promoted_ep += 1;
            }
        }
        let episodic: Vec<_> = self
            .list(identity, Some(MemoryLayer::Episodic))
            .into_iter()
            .filter(|e| e.score >= 0.85)
            .take(5)
            .collect();
        if !episodic.is_empty() {
            let summary: String = episodic
                .iter()
                .map(|e| e.content.chars().take(80).collect::<String>())
                .collect::<Vec<_>>()
                .join(" | ");
            self.put(
                identity,
                MemoryLayer::Semantic,
                &format!("[consolidated] {summary}"),
                0.9,
            );
            promoted_sem += 1;
            for e in episodic {
                if let Some(ent) = self.entries.get_mut(&e.id) {
                    ent.consolidated_at = Some(now);
                }
            }
        }
        self.consolidations = self.consolidations.saturating_add(1);
        json!({
            "identity": identity,
            "promoted_to_episodic": promoted_ep,
            "promoted_to_semantic": promoted_sem,
            "consolidations_total": self.consolidations,
        })
    }

    pub fn status(&self) -> Value {
        let mut layers = HashMap::new();
        for e in self.entries.values() {
            *layers.entry(e.layer.clone()).or_insert(0u64) += 1;
        }
        json!({
            "entries": self.entries.len(),
            "identities": self.by_identity.len(),
            "layers": layers,
            "consolidations": self.consolidations,
            "working_cap": self.working_cap,
            "schedule_ticks": self.schedule_ticks,
            "gc_dropped": self.gc_dropped,
            "isolation": "identity_namespace",
            "kernel_scheduled": true,
        })
    }

    /// Export all layers for multi-device sync.
    pub fn export_identity(&self, identity: &str) -> Value {
        json!({
            "identity": identity,
            "entries": self.list(identity, None),
        })
    }

    /// Import entries (draft merge; never overwrite higher layers blindly).
    pub fn import_identity(&mut self, identity: &str, entries: &[Value]) -> usize {
        let mut n = 0usize;
        for ent in entries {
            let content = ent
                .get("content")
                .and_then(|v| v.as_str())
                .unwrap_or("");
            if content.is_empty() {
                continue;
            }
            let layer = MemoryLayer::parse(
                ent.get("layer").and_then(|v| v.as_str()).unwrap_or("working"),
            );
            let score = ent.get("score").and_then(|v| v.as_f64()).unwrap_or(0.5);
            self.put(identity, layer, content, score);
            n += 1;
        }
        n
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn consolidate_promotes() {
        let mut m = LayeredMemory::default();
        m.put("a", MemoryLayer::Working, "important fact", 0.9);
        let r = m.consolidate("a");
        assert!(r["promoted_to_episodic"].as_u64().unwrap() >= 1);
    }
}
