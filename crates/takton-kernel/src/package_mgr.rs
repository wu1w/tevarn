//! Package manager: install / sign / verify / deps (P2 I3).

use std::collections::HashMap;
use std::time::{SystemTime, UNIX_EPOCH};

use hmac::{Hmac, Mac};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};

type HmacSha256 = Hmac<Sha256>;

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

fn sign_hmac(key: &[u8], msg: &str) -> String {
    let mut mac = HmacSha256::new_from_slice(key).expect("hmac key");
    mac.update(msg.as_bytes());
    hex::encode(mac.finalize().into_bytes())
}

fn verify_hmac(key: &[u8], msg: &str, sig: &str) -> bool {
    match HmacSha256::new_from_slice(key) {
        Ok(mut mac) => {
            mac.update(msg.as_bytes());
            let expected = hex::encode(mac.finalize().into_bytes());
            expected == sig
        }
        Err(_) => false,
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PackageManifest {
    pub name: String,
    pub version: String,
    pub content_hash: String,
    pub dependencies: Vec<String>, // name@version or name
    pub permissions: Vec<String>,
    pub entry: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct InstalledPackage {
    pub id: String,
    pub manifest: PackageManifest,
    pub content: String,
    pub signature: String,
    pub status: String, // installed | verified | active | quarantined
    pub security: Value,
    pub installed_at: f64,
}

pub struct PackageManager {
    packages: HashMap<String, InstalledPackage>, // name -> latest
    by_id: HashMap<String, InstalledPackage>,
    signing_key: Vec<u8>,
    /// env | derived_jwt | insecure_default | set
    key_source: String,
}

/// Resolve signing key: env TAKTON_PKG_SIGNING_KEY → derive JWT → insecure dev fallback.
pub fn resolve_signing_key_from_env() -> (Vec<u8>, String) {
    if let Ok(k) = std::env::var("TAKTON_PKG_SIGNING_KEY") {
        let t = k.trim();
        if !t.is_empty() && t.len() >= 16 {
            return (t.as_bytes().to_vec(), "env".into());
        }
    }
    // Prefer JWT / app secret so production never shares a public constant
    for env_name in ["TAKTON_JWT_SECRET", "JWT_SECRET", "TAKTON_API_KEY"] {
        if let Ok(k) = std::env::var(env_name) {
            let t = k.trim();
            if t.len() >= 16 {
                let mut h = Sha256::new();
                h.update(b"takton-pkg-signing-v2|");
                h.update(t.as_bytes());
                return (h.finalize().to_vec(), format!("derived:{env_name}"));
            }
        }
    }
    // Dev-only constant — anyone who knows the source can forge; status marks insecure
    (
        b"takton-package-signing-v1-INSECURE-DEV-ONLY".to_vec(),
        "insecure_default".into(),
    )
}

impl Default for PackageManager {
    fn default() -> Self {
        let (key, source) = resolve_signing_key_from_env();
        Self {
            packages: HashMap::new(),
            by_id: HashMap::new(),
            signing_key: key,
            key_source: source,
        }
    }
}

impl PackageManager {
    pub fn set_signing_key(&mut self, key: Vec<u8>) {
        if !key.is_empty() {
            self.signing_key = key;
            self.key_source = "set".into();
        }
    }

    pub fn key_source(&self) -> &str {
        &self.key_source
    }

    pub fn is_insecure_default_key(&self) -> bool {
        self.key_source == "insecure_default"
    }

    pub fn sign_content(&self, content: &str) -> String {
        sign_hmac(&self.signing_key, content)
    }

    /// Security scan: secrets patterns, auto_apply, huge size, dangerous code.
    pub fn security_scan(content: &str, permissions: &[String]) -> Value {
        let mut findings = Vec::new();
        let lower = content.to_lowercase();
        for pat in [
            "api_key",
            "sk-",
            "secret",
            "password=",
            "private_key",
            "aws_secret",
            "begin rsa private",
            "begin openssh private",
        ] {
            if lower.contains(pat) {
                findings.push(json!({"severity": "high", "rule": pat}));
            }
        }
        if lower.contains("auto_apply") && lower.contains("true") {
            findings.push(json!({"severity": "high", "rule": "auto_apply_true"}));
        }
        // dangerous runtime patterns in packaged skill content
        for pat in [
            "subprocess.call",
            "os.system(",
            "eval(",
            "exec(",
            "__import__('os')",
            "rm -rf /",
            "powershell -enc",
        ] {
            if lower.contains(pat) {
                findings.push(json!({"severity": "high", "rule": format!("dangerous_code:{pat}")}));
            }
        }
        if content.len() > 500_000 {
            findings.push(json!({"severity": "medium", "rule": "oversized"}));
        }
        if permissions.iter().any(|p| p == "*" || p == "terminal" || p == "command") {
            findings.push(json!({"severity": "low", "rule": "broad_permissions"}));
        }
        let ok = !findings
            .iter()
            .any(|f| f.get("severity").and_then(|v| v.as_str()) == Some("high"));
        json!({"ok": ok, "findings": findings})
    }

    pub fn install(
        &mut self,
        name: &str,
        version: &str,
        content: &str,
        dependencies: Vec<String>,
        permissions: Vec<String>,
        signature: Option<&str>,
    ) -> Result<InstalledPackage, String> {
        if name.is_empty() || version.is_empty() {
            return Err("name and version required".into());
        }
        let ch = content_hash(content);
        let sig = signature
            .map(|s| s.to_string())
            .unwrap_or_else(|| self.sign_content(content));
        if !verify_hmac(&self.signing_key, content, &sig) {
            // allow install as quarantined if signature mismatch
            let scan = Self::security_scan(content, &permissions);
            let pkg = InstalledPackage {
                id: short_id(),
                manifest: PackageManifest {
                    name: name.to_string(),
                    version: version.to_string(),
                    content_hash: ch,
                    dependencies,
                    permissions,
                    entry: "main".into(),
                },
                content: content.to_string(),
                signature: sig,
                status: "quarantined".into(),
                security: json!({"ok": false, "findings": [{"severity":"high","rule":"bad_signature"}], "scan": scan}),
                installed_at: now_secs(),
            };
            self.by_id.insert(pkg.id.clone(), pkg.clone());
            return Ok(pkg);
        }
        let scan = Self::security_scan(content, &permissions);
        // insecure_default key: never mark verified (prevents forged "verified" with public key)
        let status = if self.is_insecure_default_key() {
            "quarantined"
        } else if scan.get("ok") == Some(&json!(true)) {
            "verified"
        } else {
            "quarantined"
        };
        // check deps present
        for dep in &dependencies {
            let dep_name = dep.split('@').next().unwrap_or(dep);
            if !self.packages.contains_key(dep_name) {
                return Err(format!("missing dependency: {dep_name}"));
            }
        }
        let pkg = InstalledPackage {
            id: short_id(),
            manifest: PackageManifest {
                name: name.to_string(),
                version: version.to_string(),
                content_hash: ch,
                dependencies,
                permissions,
                entry: "main".into(),
            },
            content: content.to_string(),
            signature: sig,
            status: status.into(),
            security: scan,
            installed_at: now_secs(),
        };
        self.packages.insert(name.to_string(), pkg.clone());
        self.by_id.insert(pkg.id.clone(), pkg.clone());
        Ok(pkg)
    }

    pub fn activate(&mut self, name: &str) -> Result<InstalledPackage, String> {
        let pkg = self
            .packages
            .get_mut(name)
            .ok_or_else(|| format!("package not installed: {name}"))?;
        if pkg.status == "quarantined" {
            return Err("cannot activate quarantined package".into());
        }
        pkg.status = "active".into();
        Ok(pkg.clone())
    }

    /// Re-scan content; clean + valid signature → verified (not yet active).
    ///
    /// `force=true` only marks findings as reviewed and **keeps quarantine**.
    pub fn promote(&mut self, name: &str, force: bool) -> Result<InstalledPackage, String> {
        let mut pkg = self
            .packages
            .get(name)
            .cloned()
            .ok_or_else(|| format!("package not installed: {name}"))?;
        if pkg.status == "active" || pkg.status == "verified" {
            return Ok(pkg);
        }
        let scan = Self::security_scan(&pkg.content, &pkg.manifest.permissions);
        let sig_ok = verify_hmac(&self.signing_key, &pkg.content, &pkg.signature);
        if !sig_ok {
            return Err("cannot promote: bad signature (re-install with valid pkg_sign)".into());
        }
        if scan.get("ok") != Some(&json!(true)) {
            pkg.security = json!({
                "ok": false,
                "findings": scan.get("findings").cloned().unwrap_or(json!([])),
                "reviewed": force,
            });
            pkg.status = "quarantined".into();
            self.packages.insert(name.to_string(), pkg.clone());
            self.by_id.insert(pkg.id.clone(), pkg.clone());
            return Err(format!(
                "cannot promote: security findings remain (quarantined, reviewed={force})"
            ));
        }
        pkg.security = scan;
        pkg.status = "verified".into();
        self.packages.insert(name.to_string(), pkg.clone());
        self.by_id.insert(pkg.id.clone(), pkg.clone());
        Ok(pkg)
    }

    /// Scan without install (market pre-check).
    pub fn scan_only(&self, content: &str, permissions: &[String]) -> Value {
        let findings = Self::security_scan(content, permissions);
        json!({
            "scan": findings,
            "content_hash": content_hash(content),
            "size": content.len(),
        })
    }

    /// Market catalog view (installed packages as local market entries).
    pub fn catalog(&self) -> Value {
        let items: Vec<Value> = self
            .packages
            .values()
            .map(|p| {
                json!({
                    "name": p.manifest.name,
                    "version": p.manifest.version,
                    "status": p.status,
                    "permissions": p.manifest.permissions,
                    "dependencies": p.manifest.dependencies,
                    "security_ok": p.security.get("ok").and_then(|v| v.as_bool()).unwrap_or(false),
                    "findings": p.security.get("findings").cloned().unwrap_or(json!([])),
                    "content_hash": p.manifest.content_hash,
                    "installed_at": p.installed_at,
                    "source": "local_kernel",
                })
            })
            .collect();
        json!({
            "market": "local",
            "items": items,
            "count": items.len(),
        })
    }

    pub fn get(&self, name: &str) -> Option<&InstalledPackage> {
        self.packages.get(name)
    }

    pub fn list(&self) -> Vec<InstalledPackage> {
        self.packages.values().cloned().collect()
    }

    pub fn uninstall(&mut self, name: &str) -> bool {
        if let Some(p) = self.packages.remove(name) {
            self.by_id.remove(&p.id);
            true
        } else {
            false
        }
    }

    pub fn status(&self) -> Value {
        json!({
            "packages": self.packages.len(),
            "active": self.packages.values().filter(|p| p.status == "active").count(),
            "verified": self.packages.values().filter(|p| p.status == "verified").count(),
            "quarantined": self.packages.values().filter(|p| p.status == "quarantined").count(),
            "market": "local",
            "signing": "hmac-sha256",
            "key_source": self.key_source,
            "insecure_default_key": self.is_insecure_default_key(),
            "warning": if self.is_insecure_default_key() {
                "using insecure_default signing key; set TAKTON_PKG_SIGNING_KEY or TAKTON_JWT_SECRET"
            } else {
                ""
            },
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn install_sign_activate() {
        let mut pm = PackageManager::default();
        // force strong key so verified path is exercised (default may be insecure)
        pm.set_signing_key(b"unit-test-signing-key-32bytes!!".to_vec());
        let content = "print('hi')";
        let sig = pm.sign_content(content);
        let p = pm
            .install("demo", "1.0.0", content, vec![], vec!["file_read".into()], Some(&sig))
            .unwrap();
        assert_eq!(p.status, "verified");
        pm.activate("demo").unwrap();
        assert_eq!(pm.get("demo").unwrap().status, "active");
    }

    #[test]
    fn insecure_default_never_verified() {
        let mut pm = PackageManager {
            packages: Default::default(),
            by_id: Default::default(),
            signing_key: b"takton-package-signing-v1-INSECURE-DEV-ONLY".to_vec(),
            key_source: "insecure_default".into(),
        };
        let content = "print('safe')";
        let sig = pm.sign_content(content);
        let p = pm
            .install("x", "1.0", content, vec![], vec!["file_read".into()], Some(&sig))
            .unwrap();
        assert_eq!(p.status, "quarantined");
        assert!(pm.status()["insecure_default_key"].as_bool().unwrap());
    }

    #[test]
    fn quarantine_secrets() {
        let mut pm = PackageManager::default();
        let content = "api_key=sk-abcdefghijklmnop";
        let sig = pm.sign_content(content);
        let p = pm
            .install("bad", "0.1", content, vec![], vec![], Some(&sig))
            .unwrap();
        assert_eq!(p.status, "quarantined");
    }

    #[test]
    fn scan_promote_catalog() {
        let mut pm = PackageManager::default();
        pm.set_signing_key(b"unit-test-signing-key-32bytes!!".to_vec());
        let content = "print('safe skill')";
        let sig = pm.sign_content(content);
        let p = pm
            .install(
                "safe",
                "1.0",
                content,
                vec![],
                vec!["file_read".into()],
                Some(&sig),
            )
            .unwrap();
        assert_eq!(p.status, "verified");
        let scan = pm.scan_only(content, &["file_read".into()]);
        assert_eq!(scan["scan"]["ok"], true);
        let cat = pm.catalog();
        assert_eq!(cat["count"], 1);
        // quarantine then fail promote
        let bad = "password=supersecret";
        let sig2 = pm.sign_content(bad);
        pm.install("x", "0.1", bad, vec![], vec![], Some(&sig2))
            .unwrap();
        assert!(pm.promote("x", false).is_err());
    }
}
