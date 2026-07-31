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
}

impl Default for LayeredMemory {
    fn default() -> Self {
        Self {
            entries: HashMap::new(),
            by_identity: HashMap::new(),
            consolidations: 0,
        }
    }
}

impl LayeredMemory {
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
        e
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
        })
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
