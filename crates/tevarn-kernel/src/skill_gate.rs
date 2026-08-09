//! Skill verification gate (P1-B G1–G4).
//! generate → hash → sandbox verify → activate; rollback; auto_apply always false.

use std::collections::HashMap;
use std::time::{SystemTime, UNIX_EPOCH};

use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};

fn now_secs() -> f64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs_f64())
        .unwrap_or(0.0)
}

fn short_id() -> String {
    uuid::Uuid::new_v4().simple().to_string()[..12].to_string()
}

fn content_hash(s: &str) -> String {
    let mut h = Sha256::new();
    h.update(s.as_bytes());
    hex::encode(h.finalize())
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SkillManifest {
    pub name: String,
    pub version: String,
    pub entry: String,
    pub permissions: Vec<String>,
    pub resources: Value,
    pub content_hash: String,
    pub tests: Vec<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SkillPackage {
    pub id: String,
    pub manifest: SkillManifest,
    pub content: String,
    pub status: String, // draft | verifying | active | failed | rolled_back
    pub gen: u32,
    pub created_at: f64,
    pub activated_at: Option<f64>,
    pub verify_log: Vec<String>,
    pub previous_id: Option<String>,
}

/// Evolution proposals never auto-apply live caps.
pub const EVOLUTION_AUTO_APPLY: bool = false;

pub struct SkillGate {
    packages: HashMap<String, SkillPackage>,
    /// name -> active package id
    active: HashMap<String, String>,
    /// name -> history of package ids
    history: HashMap<String, Vec<String>>,
}

impl Default for SkillGate {
    fn default() -> Self {
        Self {
            packages: HashMap::new(),
            active: HashMap::new(),
            history: HashMap::new(),
        }
    }
}

impl SkillGate {
    pub fn register(
        &mut self,
        name: &str,
        version: &str,
        content: &str,
        permissions: Vec<String>,
        tests: Vec<String>,
    ) -> SkillPackage {
        let ch = content_hash(content);
        let manifest = SkillManifest {
            name: name.to_string(),
            version: version.to_string(),
            entry: "main".into(),
            permissions,
            resources: json!({}),
            content_hash: ch,
            tests,
        };
        let gen = self
            .history
            .get(name)
            .map(|h| h.len() as u32)
            .unwrap_or(0);
        let pkg = SkillPackage {
            id: short_id(),
            manifest,
            content: content.to_string(),
            status: "draft".into(),
            gen,
            created_at: now_secs(),
            activated_at: None,
            verify_log: vec![],
            previous_id: self.active.get(name).cloned(),
        };
        self.history
            .entry(name.to_string())
            .or_default()
            .push(pkg.id.clone());
        self.packages.insert(pkg.id.clone(), pkg.clone());
        pkg
    }

    /// Simulated sandbox verify: require non-empty content, hash match, optional test tags pass.
    pub fn verify(&mut self, package_id: &str) -> Result<SkillPackage, String> {
        let pkg = self
            .packages
            .get_mut(package_id)
            .ok_or_else(|| format!("unknown package {package_id}"))?;
        pkg.status = "verifying".into();
        let mut log = vec![];
        if pkg.content.trim().is_empty() {
            pkg.status = "failed".into();
            log.push("empty content".into());
            pkg.verify_log = log;
            return Err("verify failed: empty content".into());
        }
        let h = content_hash(&pkg.content);
        if h != pkg.manifest.content_hash {
            pkg.status = "failed".into();
            log.push("content_hash mismatch".into());
            pkg.verify_log = log;
            return Err("verify failed: hash mismatch".into());
        }
        log.push("hash ok".into());
        // "sandbox tests": reject if test name contains "fail"
        for t in &pkg.manifest.tests {
            if t.contains("fail") || t.contains("FAIL") {
                pkg.status = "failed".into();
                log.push(format!("test failed: {t}"));
                pkg.verify_log = log;
                return Err(format!("verify failed: test {t}"));
            }
            log.push(format!("test ok: {t}"));
        }
        // security: no auto live caps expansion in content
        if pkg.content.contains("auto_apply:true") || pkg.content.contains("\"auto_apply\": true") {
            pkg.status = "failed".into();
            log.push("forbidden auto_apply in package".into());
            pkg.verify_log = log;
            return Err("verify failed: auto_apply forbidden".into());
        }
        log.push("sandbox gate passed".into());
        pkg.verify_log = log;
        pkg.status = "verified".into();
        Ok(pkg.clone())
    }

    pub fn activate(&mut self, package_id: &str) -> Result<SkillPackage, String> {
        let status = self
            .packages
            .get(package_id)
            .map(|p| p.status.clone())
            .ok_or_else(|| format!("unknown package {package_id}"))?;
        if status != "verified" && status != "active" {
            return Err(format!(
                "package not verified (status={status}); gate required before activate"
            ));
        }
        let name = self.packages[package_id].manifest.name.clone();
        // deactivate previous
        if let Some(prev) = self.active.get(&name).cloned() {
            if prev != package_id {
                if let Some(p) = self.packages.get_mut(&prev) {
                    if p.status == "active" {
                        p.status = "superseded".into();
                    }
                }
            }
        }
        let pkg = self.packages.get_mut(package_id).unwrap();
        pkg.status = "active".into();
        pkg.activated_at = Some(now_secs());
        self.active.insert(name, package_id.to_string());
        Ok(pkg.clone())
    }

    pub fn rollback(&mut self, name: &str) -> Result<SkillPackage, String> {
        let hist = self
            .history
            .get(name)
            .cloned()
            .ok_or_else(|| format!("no history for {name}"))?;
        let current = self.active.get(name).cloned();
        // find previous verified/active package before current
        let mut prev_id = None;
        for id in hist.iter().rev() {
            if Some(id.as_str()) == current.as_deref() {
                continue;
            }
            if let Some(p) = self.packages.get(id) {
                if p.status == "active"
                    || p.status == "verified"
                    || p.status == "superseded"
                {
                    prev_id = Some(id.clone());
                    break;
                }
            }
        }
        let prev_id = prev_id.ok_or_else(|| format!("no rollback target for {name}"))?;
        if let Some(cur) = current {
            if let Some(p) = self.packages.get_mut(&cur) {
                p.status = "rolled_back".into();
            }
        }
        // re-verify path not required if already was active/verified
        let pkg = self.packages.get_mut(&prev_id).unwrap();
        if pkg.status == "failed" {
            return Err("rollback target failed verification".into());
        }
        pkg.status = "active".into();
        pkg.activated_at = Some(now_secs());
        self.active.insert(name.to_string(), prev_id);
        Ok(pkg.clone())
    }

    pub fn get_active(&self, name: &str) -> Option<&SkillPackage> {
        self.active
            .get(name)
            .and_then(|id| self.packages.get(id))
    }

    pub fn get(&self, id: &str) -> Option<&SkillPackage> {
        self.packages.get(id)
    }

    pub fn list(&self) -> Vec<SkillPackage> {
        self.packages.values().cloned().collect()
    }

    pub fn is_loadable(&self, name: &str) -> bool {
        self.get_active(name)
            .map(|p| p.status == "active")
            .unwrap_or(false)
    }

    pub fn evolution_policy(&self) -> Value {
        json!({
            "auto_apply": EVOLUTION_AUTO_APPLY,
            "auto_apply_live_caps": false,
            "require_skill_gate": true,
            "require_human_confirm": true,
        })
    }

    pub fn status(&self) -> Value {
        json!({
            "packages": self.packages.len(),
            "active": self.active.len(),
            "evolution": self.evolution_policy(),
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn gate_blocks_unverified_activate() {
        let mut g = SkillGate::default();
        let p = g.register(
            "s1",
            "1.0",
            "print(1)",
            vec![],
            vec!["ok".into()],
        );
        assert!(g.activate(&p.id).is_err());
        g.verify(&p.id).unwrap();
        g.activate(&p.id).unwrap();
        assert!(g.is_loadable("s1"));
    }

    #[test]
    fn rollback_works() {
        let mut g = SkillGate::default();
        let a = g.register("s1", "1.0", "v1", vec![], vec![]);
        g.verify(&a.id).unwrap();
        g.activate(&a.id).unwrap();
        let b = g.register("s1", "1.1", "v2", vec![], vec![]);
        g.verify(&b.id).unwrap();
        g.activate(&b.id).unwrap();
        let rb = g.rollback("s1").unwrap();
        assert_eq!(rb.content, "v1");
    }

    #[test]
    fn auto_apply_hard_false() {
        assert!(!EVOLUTION_AUTO_APPLY);
    }
}
