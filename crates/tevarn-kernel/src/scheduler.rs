//! Multi-agent priority scheduler with aging fairness.

use std::cmp::Ordering;
use std::collections::{BinaryHeap, HashMap};
use std::time::{SystemTime, UNIX_EPOCH};

use serde::{Deserialize, Serialize};
use serde_json::{json, Value};

const AGE_THRESHOLD_SECONDS: f64 = 30.0;
const AGE_BOOST: i32 = 1;

/// Named priority classes (lower number = higher priority for heap).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum PriorityClass {
    System = 0,
    Foreground = 5,
    Interactive = 10,
    WorkforceHigh = 20,
    Workforce = 30,
    Background = 50,
}

impl PriorityClass {
    pub fn as_i32(self) -> i32 {
        self as i32
    }

    pub fn parse(s: &str) -> Self {
        match s.to_ascii_lowercase().as_str() {
            "system" => Self::System,
            "foreground" | "owner" | "chat" => Self::Foreground,
            "interactive" => Self::Interactive,
            "workforce_high" | "high" => Self::WorkforceHigh,
            "workforce" | "normal" => Self::Workforce,
            "background" | "cron" | "low" => Self::Background,
            _ => Self::Workforce,
        }
    }
}

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
pub struct ScheduledTask {
    pub id: String,
    pub process_id: String,
    pub payload: Value,
    pub base_priority: i32,
    pub effective_priority: i32,
    pub seq: u64,
    pub submitted_at: f64,
    pub state: String, // queued | running | done | cancelled
}

#[derive(Eq, PartialEq)]
struct HeapEntry {
    effective_priority: i32,
    seq: u64,
    id: String,
}

impl Ord for HeapEntry {
    fn cmp(&self, other: &Self) -> Ordering {
        // min-heap by priority then seq
        other
            .effective_priority
            .cmp(&self.effective_priority)
            .then_with(|| other.seq.cmp(&self.seq))
    }
}

impl PartialOrd for HeapEntry {
    fn partial_cmp(&self, other: &Self) -> Option<Ordering> {
        Some(self.cmp(other))
    }
}

#[derive(Default)]
pub struct AgentScheduler {
    heap: BinaryHeap<HeapEntry>,
    tasks: HashMap<String, ScheduledTask>,
    seq: u64,
    age_threshold: f64,
    /// global max concurrent running tasks (OS-level gate, independent of session locks)
    max_running: usize,
    running_count: usize,
    /// session_id → running count (fair share, not a hard session mutex)
    running_by_session: HashMap<String, usize>,
    max_per_session: usize,
    completed: u64,
    starved_skips: u64,
}

impl AgentScheduler {
    pub fn new() -> Self {
        Self {
            age_threshold: AGE_THRESHOLD_SECONDS,
            max_running: 16,
            max_per_session: 2,
            ..Default::default()
        }
    }

    pub fn with_age_threshold(mut self, t: f64) -> Self {
        self.age_threshold = t;
        self
    }

    pub fn set_limits(&mut self, max_running: usize, max_per_session: usize) {
        self.max_running = max_running.max(1);
        self.max_per_session = max_per_session.max(1);
    }

    fn session_of(payload: &Value) -> String {
        payload
            .get("session_id")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_string()
    }

    pub fn submit(&mut self, process_id: impl Into<String>, payload: Value, priority: i32) -> ScheduledTask {
        let priority = priority.max(0);
        let seq = self.seq;
        self.seq += 1;
        let task = ScheduledTask {
            id: short_id(),
            process_id: process_id.into(),
            payload,
            base_priority: priority,
            effective_priority: priority,
            seq,
            submitted_at: now_secs(),
            state: "queued".into(),
        };
        self.heap.push(HeapEntry {
            effective_priority: task.effective_priority,
            seq: task.seq,
            id: task.id.clone(),
        });
        self.tasks.insert(task.id.clone(), task.clone());
        task
    }

    fn apply_aging(&mut self) {
        let now = now_secs();
        let mut dirty = false;
        for t in self.tasks.values_mut() {
            if t.state != "queued" {
                continue;
            }
            let waited = now - t.submitted_at;
            let boost = ((waited / self.age_threshold).floor() as i32) * AGE_BOOST;
            let new_prio = (t.base_priority - boost).max(0);
            if new_prio != t.effective_priority {
                t.effective_priority = new_prio;
                dirty = true;
            }
        }
        if dirty {
            self.heap.clear();
            for t in self.tasks.values() {
                if t.state == "queued" {
                    self.heap.push(HeapEntry {
                        effective_priority: t.effective_priority,
                        seq: t.seq,
                        id: t.id.clone(),
                    });
                }
            }
        }
    }

    pub fn next(&mut self) -> Option<ScheduledTask> {
        self.apply_aging();
        if self.running_count >= self.max_running {
            return None;
        }
        let mut deferred: Vec<HeapEntry> = Vec::new();
        let mut result = None;
        while let Some(entry) = self.heap.pop() {
            let Some(task) = self.tasks.get(&entry.id) else {
                continue;
            };
            if task.state != "queued" {
                continue;
            }
            let sid = Self::session_of(&task.payload);
            if !sid.is_empty() {
                let srun = *self.running_by_session.get(&sid).unwrap_or(&0);
                if srun >= self.max_per_session {
                    // fair share: skip this session's extra work, try next
                    deferred.push(entry);
                    self.starved_skips = self.starved_skips.saturating_add(1);
                    continue;
                }
            }
            if let Some(task) = self.tasks.get_mut(&entry.id) {
                task.state = "running".into();
                self.running_count = self.running_count.saturating_add(1);
                if !sid.is_empty() {
                    *self.running_by_session.entry(sid).or_insert(0) += 1;
                }
                result = Some(task.clone());
            }
            break;
        }
        // put deferred back
        for e in deferred {
            self.heap.push(e);
        }
        result
    }

    pub fn complete(&mut self, task_id: &str, cancelled: bool) {
        if let Some(task) = self.tasks.get_mut(task_id) {
            if task.state == "running" {
                self.running_count = self.running_count.saturating_sub(1);
                let sid = Self::session_of(&task.payload);
                if !sid.is_empty() {
                    if let Some(n) = self.running_by_session.get_mut(&sid) {
                        *n = n.saturating_sub(1);
                    }
                }
            }
            task.state = if cancelled {
                "cancelled".into()
            } else {
                "done".into()
            };
            self.completed = self.completed.saturating_add(1);
        }
        let terminal: Vec<_> = self
            .tasks
            .values()
            .filter(|t| t.state == "done" || t.state == "cancelled")
            .map(|t| (t.seq, t.id.clone()))
            .collect();
        if terminal.len() > 1000 {
            let mut sorted = terminal;
            sorted.sort_by_key(|(seq, _)| *seq);
            let drop_n = sorted.len() - 1000;
            for (_, id) in sorted.into_iter().take(drop_n) {
                self.tasks.remove(&id);
            }
        }
    }

    pub fn cancel_process(&mut self, process_id: &str) -> usize {
        let mut n = 0;
        for t in self.tasks.values_mut() {
            if t.process_id == process_id && t.state == "queued" {
                t.state = "cancelled".into();
                n += 1;
            }
        }
        n
    }

    pub fn queued(&mut self) -> Vec<ScheduledTask> {
        self.apply_aging();
        let mut out: Vec<_> = self
            .tasks
            .values()
            .filter(|t| t.state == "queued")
            .cloned()
            .collect();
        out.sort_by(|a, b| {
            a.effective_priority
                .cmp(&b.effective_priority)
                .then(a.seq.cmp(&b.seq))
        });
        out
    }

    pub fn stats(&self) -> HashMap<String, usize> {
        let mut out = HashMap::from([
            ("queued".into(), 0usize),
            ("running".into(), 0),
            ("done".into(), 0),
            ("cancelled".into(), 0),
            ("max_running".into(), self.max_running),
            ("max_per_session".into(), self.max_per_session),
            ("running_count".into(), self.running_count),
            ("completed_total".into(), self.completed as usize),
            ("starved_skips".into(), self.starved_skips as usize),
            ("sessions_active".into(), self.running_by_session.len()),
        ]);
        for t in self.tasks.values() {
            *out.entry(t.state.clone()).or_insert(0) += 1;
        }
        out
    }

    pub fn status(&self) -> Value {
        json!({
            "stats": self.stats(),
            "fair_share": true,
            "session_lock_decoupled": true,
            "global_cap": self.max_running,
            "per_session_cap": self.max_per_session,
            "authority": "rust",
            "os_level": true,
        })
    }
}
