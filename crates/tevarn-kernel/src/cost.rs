//! Token / billable / resource cost aggregation (P0.5 R5).
//!
//! Aggregates by process, provider family, and model (family/model).

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

fn model_key(family: &str, model: &str) -> String {
    let m = model.trim();
    if m.is_empty() {
        format!("{family}/(default)")
    } else {
        format!("{family}/{m}")
    }
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct ProcessCost {
    pub tokens: u64,
    pub billable: u64,
    pub llm_rounds: u64,
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct FamilyCost {
    pub tokens: u64,
    pub billable: u64,
    pub rounds: u64,
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct ModelCost {
    pub family: String,
    pub model: String,
    pub tokens: u64,
    pub billable: u64,
    pub rounds: u64,
}

#[derive(Default)]
pub struct CostLedger {
    by_process: HashMap<String, ProcessCost>,
    by_family: HashMap<String, FamilyCost>,
    by_model: HashMap<String, ModelCost>,
    token_total: u64,
    billable_total: u64,
    rounds_total: u64,
}

impl CostLedger {
    pub fn charge(
        &mut self,
        process_id: &str,
        family: &str,
        tokens: u64,
        billable: u64,
        model: Option<&str>,
    ) {
        let fam = if family.trim().is_empty() {
            "default"
        } else {
            family.trim()
        };
        self.token_total = self.token_total.saturating_add(tokens);
        self.billable_total = self.billable_total.saturating_add(billable);
        self.rounds_total = self.rounds_total.saturating_add(1);

        let pc = self.by_process.entry(process_id.to_string()).or_default();
        pc.tokens = pc.tokens.saturating_add(tokens);
        pc.billable = pc.billable.saturating_add(billable);
        pc.llm_rounds = pc.llm_rounds.saturating_add(1);

        let fc = self.by_family.entry(fam.to_string()).or_default();
        fc.tokens = fc.tokens.saturating_add(tokens);
        fc.billable = fc.billable.saturating_add(billable);
        fc.rounds = fc.rounds.saturating_add(1);

        let model_name = model.map(str::trim).filter(|s| !s.is_empty()).unwrap_or("");
        let key = model_key(fam, model_name);
        let mc = self.by_model.entry(key).or_insert_with(|| ModelCost {
            family: fam.to_string(),
            model: if model_name.is_empty() {
                "(default)".into()
            } else {
                model_name.to_string()
            },
            ..Default::default()
        });
        mc.tokens = mc.tokens.saturating_add(tokens);
        mc.billable = mc.billable.saturating_add(billable);
        mc.rounds = mc.rounds.saturating_add(1);
    }

    pub fn drop_process(&mut self, process_id: &str) {
        self.by_process.remove(process_id);
    }

    pub fn process_cost(&self, process_id: &str) -> Value {
        match self.by_process.get(process_id) {
            Some(c) => json!({
                "process_id": process_id,
                "tokens": c.tokens,
                "billable": c.billable,
                "llm_rounds": c.llm_rounds,
            }),
            None => json!({
                "process_id": process_id,
                "tokens": 0,
                "billable": 0,
                "llm_rounds": 0,
            }),
        }
    }

    pub fn panel(&self) -> Value {
        let mut processes = serde_json::Map::new();
        for (k, v) in &self.by_process {
            processes.insert(
                k.clone(),
                json!({
                    "tokens": v.tokens,
                    "billable": v.billable,
                    "llm_rounds": v.llm_rounds,
                }),
            );
        }
        let mut families = serde_json::Map::new();
        for (k, v) in &self.by_family {
            families.insert(
                k.clone(),
                json!({
                    "tokens": v.tokens,
                    "billable": v.billable,
                    "rounds": v.rounds,
                }),
            );
        }
        let mut models = serde_json::Map::new();
        for (k, v) in &self.by_model {
            models.insert(
                k.clone(),
                json!({
                    "family": v.family,
                    "model": v.model,
                    "tokens": v.tokens,
                    "billable": v.billable,
                    "rounds": v.rounds,
                }),
            );
        }
        json!({
            "totals": {
                "tokens": self.token_total,
                "billable": self.billable_total,
                "llm_rounds": self.rounds_total,
            },
            "by_process": processes,
            "by_family": families,
            "by_model": models,
            "ts": now_secs(),
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn aggregates() {
        let mut c = CostLedger::default();
        c.charge("p1", "openai", 100, 80, Some("gpt-4o"));
        c.charge("p1", "openai", 50, 40, Some("gpt-4o-mini"));
        c.charge("p2", "anthropic", 200, 100, Some("claude-sonnet"));
        let p = c.panel();
        assert_eq!(p["totals"]["tokens"], 350);
        assert_eq!(p["totals"]["billable"], 220);
        assert_eq!(p["by_process"]["p1"]["tokens"], 150);
        assert_eq!(p["by_family"]["openai"]["tokens"], 150);
        assert_eq!(p["by_model"]["openai/gpt-4o"]["tokens"], 100);
        assert_eq!(p["by_model"]["openai/gpt-4o-mini"]["tokens"], 50);
    }
}
