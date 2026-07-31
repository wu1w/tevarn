//! Eval harness ledger in-kernel (M-07 productization).
//! Records suite runs, computes trends, and gate checks for weekly CI.

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

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct EvalRun {
    pub id: String,
    pub suite: String,
    /// overall score 0.0–1.0
    pub overall: f64,
    pub parts: HashMap<String, f64>,
    pub ts: f64,
    pub meta: Value,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct EvalGate {
    pub min_overall: f64,
    pub min_parts: HashMap<String, f64>,
}

impl Default for EvalGate {
    fn default() -> Self {
        let mut min_parts = HashMap::new();
        min_parts.insert("coding".into(), 0.4);
        min_parts.insert("safety".into(), 0.7);
        min_parts.insert("long".into(), 0.3);
        Self {
            min_overall: 0.5,
            min_parts,
        }
    }
}

pub struct EvalSuite {
    runs: Vec<EvalRun>,
    gate: EvalGate,
    max_runs: usize,
}

impl Default for EvalSuite {
    fn default() -> Self {
        Self {
            runs: Vec::new(),
            gate: EvalGate::default(),
            max_runs: 256,
        }
    }
}

impl EvalSuite {
    pub fn set_gate(&mut self, gate: EvalGate) {
        self.gate = gate;
    }

    pub fn record(
        &mut self,
        suite: &str,
        overall: f64,
        parts: HashMap<String, f64>,
        meta: Value,
    ) -> EvalRun {
        let run = EvalRun {
            id: short_id(),
            suite: suite.to_string(),
            overall: overall.clamp(0.0, 1.0),
            parts,
            ts: now_secs(),
            meta,
        };
        self.runs.push(run.clone());
        if self.runs.len() > self.max_runs {
            let drop_n = self.runs.len() - self.max_runs;
            self.runs.drain(0..drop_n);
        }
        run
    }

    pub fn latest(&self, suite: Option<&str>) -> Option<&EvalRun> {
        self.runs
            .iter()
            .rev()
            .find(|r| suite.map(|s| r.suite == s).unwrap_or(true))
    }

    pub fn trend(&self, suite: &str, last_n: usize) -> Value {
        let n = last_n.clamp(1, 64);
        let mut scores: Vec<f64> = self
            .runs
            .iter()
            .filter(|r| r.suite == suite)
            .map(|r| r.overall)
            .collect();
        if scores.len() > n {
            scores = scores.split_off(scores.len() - n);
        }
        let avg = if scores.is_empty() {
            0.0
        } else {
            scores.iter().sum::<f64>() / scores.len() as f64
        };
        let delta = if scores.len() >= 2 {
            scores[scores.len() - 1] - scores[0]
        } else {
            0.0
        };
        json!({
            "suite": suite,
            "n": scores.len(),
            "scores": scores,
            "avg": avg,
            "delta": delta,
            "improving": delta > 0.0,
        })
    }

    pub fn check_gate(&self, suite: Option<&str>) -> Value {
        let Some(run) = self.latest(suite) else {
            return json!({
                "ok": false,
                "reason": "no_eval_runs",
                "gate": self.gate,
            });
        };
        let mut failed = vec![];
        if run.overall < self.gate.min_overall {
            failed.push(format!(
                "overall {:.3} < min {:.3}",
                run.overall, self.gate.min_overall
            ));
        }
        for (k, min_v) in &self.gate.min_parts {
            let got = run.parts.get(k).copied().unwrap_or(0.0);
            if got < *min_v {
                failed.push(format!("{k} {got:.3} < min {min_v:.3}"));
            }
        }
        json!({
            "ok": failed.is_empty(),
            "run_id": run.id,
            "suite": run.suite,
            "overall": run.overall,
            "failed": failed,
            "gate": {
                "min_overall": self.gate.min_overall,
                "min_parts": self.gate.min_parts,
            },
        })
    }

    pub fn status(&self) -> Value {
        json!({
            "runs": self.runs.len(),
            "latest": self.latest(None),
            "gate": {
                "min_overall": self.gate.min_overall,
                "min_parts": self.gate.min_parts,
            },
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn record_trend_and_gate() {
        let mut e = EvalSuite::default();
        let mut parts = HashMap::new();
        parts.insert("coding".into(), 0.8);
        parts.insert("safety".into(), 0.9);
        parts.insert("long".into(), 0.5);
        e.record("default", 0.7, parts.clone(), json!({}));
        e.record("default", 0.75, parts, json!({}));
        let t = e.trend("default", 8);
        assert_eq!(t["n"], 2);
        assert!(t["improving"].as_bool().unwrap());
        let g = e.check_gate(Some("default"));
        assert_eq!(g["ok"], true);
    }
}
