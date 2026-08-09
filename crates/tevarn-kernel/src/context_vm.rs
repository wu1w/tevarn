//! Context virtual memory: quota + swap in/out (P1 M-04).

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
pub struct ContextPage {
    pub id: String,
    pub process_id: String,
    pub label: String,
    pub tokens: u32,
    pub content: String,
    pub resident: bool,
    /// pinned pages skip LRU eviction
    pub pinned: bool,
    pub isolation: String, // process | identity | shared
    pub last_access: f64,
}

pub struct ContextVm {
    pages: HashMap<String, ContextPage>,
    /// process -> page ids
    by_process: HashMap<String, Vec<String>>,
    default_quota_tokens: u32,
    quotas: HashMap<String, u32>,
    /// process isolation profile: process (default) | identity | shared
    isolation: HashMap<String, String>,
    schedule_ticks: u64,
    evictions: u64,
}

impl Default for ContextVm {
    fn default() -> Self {
        Self::new(32_000)
    }
}

impl ContextVm {
    pub fn new(default_quota_tokens: u32) -> Self {
        Self {
            pages: HashMap::new(),
            by_process: HashMap::new(),
            default_quota_tokens: default_quota_tokens.max(1024),
            quotas: HashMap::new(),
            isolation: HashMap::new(),
            schedule_ticks: 0,
            evictions: 0,
        }
    }

    pub fn set_quota(&mut self, process_id: &str, tokens: u32) {
        self.quotas
            .insert(process_id.to_string(), tokens.max(64));
    }

    /// Isolation: process pages are private; identity/shared allow controlled share.
    pub fn set_isolation(&mut self, process_id: &str, mode: &str) {
        let m = match mode {
            "identity" | "shared" => mode,
            _ => "process",
        };
        self.isolation
            .insert(process_id.to_string(), m.to_string());
    }

    pub fn isolation(&self, process_id: &str) -> &str {
        self.isolation
            .get(process_id)
            .map(|s| s.as_str())
            .unwrap_or("process")
    }

    pub fn quota(&self, process_id: &str) -> u32 {
        self.quotas
            .get(process_id)
            .copied()
            .unwrap_or(self.default_quota_tokens)
    }

    pub fn resident_tokens(&self, process_id: &str) -> u32 {
        self.by_process
            .get(process_id)
            .into_iter()
            .flatten()
            .filter_map(|id| self.pages.get(id))
            .filter(|p| p.resident)
            .map(|p| p.tokens)
            .sum()
    }

    /// Deny cross-process page access under process isolation.
    pub fn assert_page_access(&self, page_id: &str, caller_process: &str) -> Result<(), String> {
        let p = self
            .pages
            .get(page_id)
            .ok_or_else(|| format!("unknown page {page_id}"))?;
        if p.process_id == caller_process {
            return Ok(());
        }
        let iso = self.isolation(caller_process);
        if iso == "shared" || self.isolation(&p.process_id) == "shared" {
            return Ok(());
        }
        Err(format!(
            "context:isolation_denied page={page_id} owner={} caller={caller_process}",
            p.process_id
        ))
    }

    pub fn put_page(
        &mut self,
        process_id: &str,
        label: &str,
        content: &str,
    ) -> ContextPage {
        let tokens = (content.len() as u32 / 4).max(1);
        let iso = self.isolation(process_id).to_string();
        let page = ContextPage {
            id: short_id(),
            process_id: process_id.to_string(),
            label: label.to_string(),
            tokens,
            content: content.chars().take(100_000).collect(),
            resident: true,
            pinned: false,
            isolation: iso,
            last_access: now_secs(),
        };
        self.by_process
            .entry(process_id.to_string())
            .or_default()
            .push(page.id.clone());
        self.pages.insert(page.id.clone(), page.clone());
        self.evict_if_needed(process_id);
        self.pages.get(&page.id).cloned().unwrap_or(page)
    }

    pub fn pin_page(&mut self, page_id: &str, pinned: bool) -> Result<ContextPage, String> {
        let p = self
            .pages
            .get_mut(page_id)
            .ok_or_else(|| format!("unknown page {page_id}"))?;
        p.pinned = pinned;
        if pinned {
            p.resident = true;
        }
        Ok(p.clone())
    }

    /// Scheduler tick: enforce quotas and age out cold unpinned pages.
    pub fn schedule_tick(&mut self, process_id: Option<&str>) -> Value {
        self.schedule_ticks = self.schedule_ticks.saturating_add(1);
        let mut processes: Vec<String> = if let Some(pid) = process_id {
            vec![pid.to_string()]
        } else {
            self.by_process.keys().cloned().collect()
        };
        processes.sort();
        let mut evicted = 0u64;
        for pid in processes {
            let before = self.evictions;
            self.evict_if_needed(&pid);
            // age cold pages (not accessed in 300s) if over 50% quota
            let quota = self.quota(&pid);
            let res = self.resident_tokens(&pid);
            if res > quota / 2 {
                let now = now_secs();
                let cold: Vec<String> = self
                    .by_process
                    .get(&pid)
                    .into_iter()
                    .flatten()
                    .filter_map(|id| self.pages.get(id))
                    .filter(|p| p.resident && !p.pinned && now - p.last_access > 300.0)
                    .map(|p| p.id.clone())
                    .collect();
                for id in cold {
                    if let Some(p) = self.pages.get_mut(&id) {
                        p.resident = false;
                        self.evictions = self.evictions.saturating_add(1);
                        evicted += 1;
                    }
                }
            }
            evicted += self.evictions.saturating_sub(before);
        }
        json!({
            "ticks": self.schedule_ticks,
            "evicted_this_tick": evicted,
            "evictions_total": self.evictions,
        })
    }

    fn evict_if_needed(&mut self, process_id: &str) {
        let quota = self.quota(process_id);
        for _ in 0..64 {
            if self.resident_tokens(process_id) <= quota {
                return;
            }
            // LRU among resident unpinned pages
            let mut best: Option<(String, f64)> = None;
            if let Some(ids) = self.by_process.get(process_id) {
                for id in ids {
                    if let Some(p) = self.pages.get(id) {
                        if p.resident && !p.pinned {
                            match best {
                                None => best = Some((id.clone(), p.last_access)),
                                Some((_, ts)) if p.last_access < ts => {
                                    best = Some((id.clone(), p.last_access));
                                }
                                _ => {}
                            }
                        }
                    }
                }
            }
            let Some((vid, _)) = best else {
                return; // only pinned remain over quota
            };
            if let Some(p) = self.pages.get_mut(&vid) {
                p.resident = false;
                self.evictions = self.evictions.saturating_add(1);
            } else {
                return;
            }
        }
    }

    pub fn swap_in(
        &mut self,
        page_id: &str,
        caller_process: Option<&str>,
    ) -> Result<ContextPage, String> {
        if let Some(caller) = caller_process {
            self.assert_page_access(page_id, caller)?;
        }
        let process_id = self
            .pages
            .get(page_id)
            .map(|p| p.process_id.clone())
            .ok_or_else(|| format!("unknown page {page_id}"))?;
        {
            let p = self.pages.get_mut(page_id).unwrap();
            p.resident = true;
            p.last_access = now_secs();
        }
        self.evict_if_needed(&process_id);
        Ok(self.pages.get(page_id).unwrap().clone())
    }

    pub fn swap_out(
        &mut self,
        page_id: &str,
        caller_process: Option<&str>,
    ) -> Result<ContextPage, String> {
        if let Some(caller) = caller_process {
            self.assert_page_access(page_id, caller)?;
        }
        let p = self
            .pages
            .get_mut(page_id)
            .ok_or_else(|| format!("unknown page {page_id}"))?;
        if p.pinned {
            return Err("cannot swap_out pinned page".into());
        }
        p.resident = false;
        Ok(p.clone())
    }

    pub fn list_pages(&self, process_id: &str) -> Vec<Value> {
        self.by_process
            .get(process_id)
            .into_iter()
            .flatten()
            .filter_map(|id| self.pages.get(id))
            .map(|p| {
                json!({
                    "id": p.id,
                    "label": p.label,
                    "tokens": p.tokens,
                    "resident": p.resident,
                    "pinned": p.pinned,
                    "isolation": p.isolation,
                    "last_access": p.last_access,
                })
            })
            .collect()
    }

    pub fn drop_process(&mut self, process_id: &str) {
        if let Some(ids) = self.by_process.remove(process_id) {
            for id in ids {
                self.pages.remove(&id);
            }
        }
        self.quotas.remove(process_id);
        self.isolation.remove(process_id);
    }

    pub fn status(&self, process_id: Option<&str>) -> Value {
        if let Some(pid) = process_id {
            return json!({
                "process_id": pid,
                "quota": self.quota(pid),
                "resident_tokens": self.resident_tokens(pid),
                "isolation": self.isolation(pid),
                "pages": self.list_pages(pid),
                "kernel_scheduled": true,
            });
        }
        json!({
            "processes": self.by_process.len(),
            "pages": self.pages.len(),
            "schedule_ticks": self.schedule_ticks,
            "evictions": self.evictions,
            "isolation_enforced": true,
            "default_quota": self.default_quota_tokens,
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn evicts_when_over_quota() {
        let mut vm = ContextVm::new(100);
        vm.set_quota("p1", 40);
        let _a = vm.put_page("p1", "a", &"x".repeat(200)); // 50 tokens
        let _b = vm.put_page("p1", "b", &"y".repeat(200));
        // must not exceed quota after eviction (allow single-page oversize)
        assert!(vm.resident_tokens("p1") <= 50);
        let residents = vm
            .list_pages("p1")
            .iter()
            .filter(|p| p["resident"] == true)
            .count();
        assert!(residents <= 1);
    }
}
