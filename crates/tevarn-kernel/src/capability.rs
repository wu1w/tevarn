//! CapabilityToken — monotonic narrowing + optional HMAC signing.

use std::collections::BTreeSet;
use std::time::{SystemTime, UNIX_EPOCH};

use hmac::{Hmac, Mac};
use serde::{Deserialize, Serialize};
use sha2::Sha256;

use crate::error::{KernelError, KernelResult};

type HmacSha256 = Hmac<Sha256>;

/// HMAC info label — must match Python `signing._HMAC_INFO` for v1 interop when key is provided.
pub const HMAC_INFO: &[u8] = b"tevarn-kernel-token-hmac-v1";

fn now_secs() -> f64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs_f64())
        .unwrap_or(0.0)
}

fn short_id() -> String {
    uuid::Uuid::new_v4().simple().to_string()[..16].to_string()
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CapabilityToken {
    pub id: String,
    pub process_id: String,
    pub parent_token_id: Option<String>,
    pub capabilities: BTreeSet<String>,
    pub issued_at: f64,
    pub expires_at: Option<f64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub signature: Option<String>,
}

impl CapabilityToken {
    pub fn new(
        capabilities: impl IntoIterator<Item = impl Into<String>>,
        process_id: impl Into<String>,
        expires_at: Option<f64>,
    ) -> Self {
        Self {
            id: short_id(),
            process_id: process_id.into(),
            parent_token_id: None,
            capabilities: capabilities.into_iter().map(|c| c.into()).collect(),
            issued_at: now_secs(),
            expires_at,
            signature: None,
        }
    }

    pub fn is_expired(&self) -> bool {
        match self.expires_at {
            Some(exp) => now_secs() >= exp,
            None => false,
        }
    }

    pub fn allows(&self, capability: &str) -> bool {
        if self.is_expired() {
            return false;
        }
        use crate::tool_catalog::capability_matches;
        capability_matches(capability, &self.capabilities)
    }

    /// Produce a stricter child token. Subset must be ⊆ parent (unless parent has `*`).
    pub fn narrow(
        &self,
        subset: impl IntoIterator<Item = impl Into<String>>,
        process_id: impl Into<String>,
        expires_at: Option<f64>,
    ) -> KernelResult<Self> {
        let requested: BTreeSet<String> = subset.into_iter().map(|c| c.into()).collect();
        if !self.capabilities.contains("*") {
            let extra: Vec<_> = requested
                .difference(&self.capabilities)
                .cloned()
                .collect();
            if !extra.is_empty() {
                let mut sorted = extra;
                sorted.sort();
                return Err(KernelError::CapabilityEscalation(format!(
                    "narrowing 不允许扩大能力：{sorted:?} 不在父 Token 能力集中"
                )));
            }
        }
        let effective_expiry = match (self.expires_at, expires_at) {
            (Some(p), Some(c)) => Some(p.min(c)),
            (Some(p), None) => Some(p),
            (None, c) => c,
        };
        Ok(Self {
            id: short_id(),
            process_id: process_id.into(),
            parent_token_id: Some(self.id.clone()),
            capabilities: requested,
            issued_at: now_secs(),
            expires_at: effective_expiry,
            signature: None,
        })
    }

    pub fn to_dict(&self, sign_key: Option<&[u8]>) -> serde_json::Value {
        let mut v = serde_json::json!({
            "id": self.id,
            "process_id": self.process_id,
            "parent_token_id": self.parent_token_id,
            "capabilities": self.capabilities.iter().cloned().collect::<Vec<_>>(),
            "issued_at": self.issued_at,
            "expires_at": self.expires_at,
        });
        if let Some(key) = sign_key {
            if let Ok(sig) = sign_token_payload(&v, key) {
                v["signature"] = serde_json::Value::String(sig);
            }
        } else if let Some(ref s) = self.signature {
            v["signature"] = serde_json::Value::String(s.clone());
        }
        v
    }

    pub fn from_dict(data: &serde_json::Value, verify_key: Option<&[u8]>) -> KernelResult<Self> {
        if let Some(key) = verify_key {
            if !verify_token_payload(data, key) {
                return Err(KernelError::Permission(
                    "Token 签名验证失败——拒绝反序列化不可信来源的能力令牌".into(),
                ));
            }
        }
        let caps = data
            .get("capabilities")
            .and_then(|c| c.as_array())
            .map(|arr| {
                arr.iter()
                    .filter_map(|x| x.as_str().map(|s| s.to_string()))
                    .collect()
            })
            .unwrap_or_default();
        Ok(Self {
            id: data
                .get("id")
                .and_then(|v| v.as_str())
                .unwrap_or_default()
                .to_string()
                .if_empty_then(short_id),
            process_id: data
                .get("process_id")
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .to_string(),
            parent_token_id: data
                .get("parent_token_id")
                .and_then(|v| v.as_str())
                .map(|s| s.to_string()),
            capabilities: caps,
            issued_at: data
                .get("issued_at")
                .and_then(|v| v.as_f64())
                .unwrap_or_else(now_secs),
            expires_at: data.get("expires_at").and_then(|v| v.as_f64()),
            signature: data
                .get("signature")
                .and_then(|v| v.as_str())
                .map(|s| s.to_string()),
        })
    }
}

trait IfEmpty {
    fn if_empty_then(self, f: fn() -> String) -> String;
}

impl IfEmpty for String {
    fn if_empty_then(self, f: fn() -> String) -> String {
        if self.is_empty() {
            f()
        } else {
            self
        }
    }
}

fn canonical_payload(data: &serde_json::Value) -> Vec<u8> {
    let payload = serde_json::json!({
        "id": data.get("id"),
        "process_id": data.get("process_id"),
        "parent_token_id": data.get("parent_token_id"),
        "capabilities": data.get("capabilities").and_then(|c| c.as_array()).map(|arr| {
            let mut v: Vec<_> = arr.iter().filter_map(|x| x.as_str()).collect();
            v.sort();
            v
        }),
        "issued_at": data.get("issued_at"),
        "expires_at": data.get("expires_at"),
    });
    // Python uses sort_keys=True ensure_ascii=False — serde_json Map is sorted for object keys
    serde_json::to_vec(&payload).unwrap_or_default()
}

pub fn sign_token_payload(data: &serde_json::Value, key: &[u8]) -> KernelResult<String> {
    let mut mac =
        HmacSha256::new_from_slice(key).map_err(|e| KernelError::Internal(e.to_string()))?;
    mac.update(&canonical_payload(data));
    Ok(hex::encode(mac.finalize().into_bytes()))
}

pub fn verify_token_payload(data: &serde_json::Value, key: &[u8]) -> bool {
    let sig = match data.get("signature").and_then(|v| v.as_str()) {
        Some(s) if !s.is_empty() => s,
        _ => return false,
    };
    match sign_token_payload(data, key) {
        Ok(expected) => {
            // constant-time-ish compare
            if expected.len() != sig.len() {
                return false;
            }
            expected
                .bytes()
                .zip(sig.bytes())
                .fold(0u8, |acc, (a, b)| acc | (a ^ b))
                == 0
        }
        Err(_) => false,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn narrow_monotonic() {
        let parent = CapabilityToken::new(["file_read", "grep", "glob"], "", None);
        let child = parent.narrow(["file_read"], "p1", None).unwrap();
        assert!(child.allows("file_read"));
        assert!(!child.allows("grep"));
        assert!(parent.narrow(["file_read", "terminal"], "", None).is_err());
    }

    #[test]
    fn wild_allows_any_narrow() {
        let wild = CapabilityToken::new(["*"], "", None);
        assert!(wild.narrow(["file_read"], "", None).unwrap().allows("file_read"));
    }

    #[test]
    fn expiry_monotonic() {
        let parent = CapabilityToken::new(["*"], "", Some(1000.0));
        let child = parent.narrow(["file_read"], "", Some(500.0)).unwrap();
        assert_eq!(child.expires_at, Some(500.0));
        let later = parent.narrow(["file_read"], "", Some(2000.0)).unwrap();
        assert_eq!(later.expires_at, Some(1000.0));
    }

    #[test]
    fn expired_denies() {
        let tok = CapabilityToken::new(["*"], "", Some(1.0));
        assert!(tok.is_expired());
        assert!(!tok.allows("file_read"));
    }
}
