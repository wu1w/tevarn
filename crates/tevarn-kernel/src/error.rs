//! Kernel error types — map 1:1 to Python exceptions where possible.

use thiserror::Error;

#[derive(Debug, Error)]
pub enum KernelError {
    #[error("{0}")]
    Permission(String),

    #[error("{0}")]
    BudgetExceeded(String),

    #[error("{0}")]
    CapabilityEscalation(String),

    #[error("{0}")]
    NotFound(String),

    #[error("{0}")]
    Invalid(String),

    #[error("{0}")]
    Internal(String),
}

impl KernelError {
    pub fn kind(&self) -> &'static str {
        match self {
            Self::Permission(_) => "permission",
            Self::BudgetExceeded(_) => "budget_exceeded",
            Self::CapabilityEscalation(_) => "capability_escalation",
            Self::NotFound(_) => "not_found",
            Self::Invalid(_) => "invalid",
            Self::Internal(_) => "internal",
        }
    }

    pub fn to_json(&self) -> serde_json::Value {
        serde_json::json!({
            "error": self.kind(),
            "message": self.to_string(),
        })
    }
}

pub type KernelResult<T> = Result<T, KernelError>;
