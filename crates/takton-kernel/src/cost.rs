//! Token / billable / resource cost aggregation (P0.5 R5).

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

#[derive(Default)]
pub struct CostLedger {
    by_process: HashMap<String, ProcessCost>,
    by_family: HashMap<String, FamilyCost>,
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
    ) {
        self.token_total = self.token_total.saturating_add(tokens);
        self.billable_total = self.billable_total.saturating_add(billable);
        self.rounds_total = self.rounds_total.saturating_add(1);

        let pc = self.by_process.entry(process_id.to_string()).or_default();
        pc.tokens = pc.tokens.saturating_add(tokens);
        pc.billable = pc.billable.saturating_add(billable);
        pc.llm_rounds = pc.llm_rounds.saturating_add(1);

        let fc = self.by_family.entry(family.to_string()).or_default();
        fc.tokens = fc.tokens.saturating_add(tokens);
        fc.billable = fc.billable.saturating_add(billable);
        fc.rounds = fc.rounds.saturating_add(1);
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
        json!({
            "totals": {
                "tokens": self.token_total,
                "billable": self.billable_total,
                "llm_rounds": self.rounds_total,
            },
            "by_process": processes,
            "by_family": families,
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
        c.charge("p1", "openai", 100, 80);
        c.charge("p1", "openai", 50, 40);
        c.charge("p2", "anthropic", 200, 100);
        let p = c.panel();
        assert_eq!(p["totals"]["tokens"], 350);
        assert_eq!(p["totals"]["billable"], 220);
        assert_eq!(p["by_process"]["p1"]["tokens"], 150);
    }
}
