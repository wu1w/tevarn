//! Marathon long-run resume metrics (P0.5 E6 / R2).

use std::time::{SystemTime, UNIX_EPOCH};

use serde_json::{json, Value};

fn now_secs() -> f64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs_f64())
        .unwrap_or(0.0)
}

#[derive(Default)]
pub struct MarathonMetrics {
    pub attempts: u64,
    pub resume_success: u64,
    pub resume_fail: u64,
    pub snapshot_ok: u64,
    pub last_reason: String,
}

impl MarathonMetrics {
    pub fn record_attempt(&mut self) {
        self.attempts = self.attempts.saturating_add(1);
    }

    pub fn record_resume(&mut self, ok: bool, reason: &str) {
        if ok {
            self.resume_success = self.resume_success.saturating_add(1);
        } else {
            self.resume_fail = self.resume_fail.saturating_add(1);
        }
        self.last_reason = reason.chars().take(200).collect();
    }

    pub fn record_snapshot(&mut self, ok: bool) {
        if ok {
            self.snapshot_ok = self.snapshot_ok.saturating_add(1);
        }
    }

    pub fn resume_success_rate(&self) -> f64 {
        let t = self.resume_success + self.resume_fail;
        if t == 0 {
            0.0
        } else {
            self.resume_success as f64 / t as f64
        }
    }

    pub fn status(&self) -> Value {
        json!({
            "attempts": self.attempts,
            "resume_success": self.resume_success,
            "resume_fail": self.resume_fail,
            "snapshot_ok": self.snapshot_ok,
            "marathon_resume_success": self.resume_success_rate(),
            "last_reason": self.last_reason,
            "ts": now_secs(),
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn rate() {
        let mut m = MarathonMetrics::default();
        m.record_resume(true, "ok");
        m.record_resume(true, "ok");
        m.record_resume(false, "no snap");
        assert!((m.resume_success_rate() - 2.0 / 3.0).abs() < 1e-9);
    }
}
