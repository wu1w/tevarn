use crate::models::{TokenResponse, UserInfo};
use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AuthSession {
    pub access_token: String,
    pub token_type: String,
    pub user: UserInfo,
    pub base_url: String,
}

impl AuthSession {
    pub fn from_token_response(base_url: String, tr: TokenResponse) -> Self {
        Self {
            access_token: tr.access_token,
            token_type: if tr.token_type.is_empty() {
                "bearer".into()
            } else {
                tr.token_type
            },
            user: tr.user,
            base_url,
        }
    }

    pub fn auth_header(&self) -> String {
        format!("Bearer {}", self.access_token)
    }
}
