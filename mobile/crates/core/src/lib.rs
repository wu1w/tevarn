pub mod auth;
pub mod chat;
pub mod client;
pub mod config;
pub mod error;
pub mod models;
pub mod platform;
pub mod storage;
pub mod local_llm;
pub mod local_oauth;
pub mod chat_mode;
pub mod media;
pub mod session_meta;
pub mod motion;
pub mod catalog_filter;
pub mod pair;
pub mod mesh;
pub mod tsnet_embed;
pub mod path;

pub use auth::AuthSession;
pub use client::TaktonClient;
pub use config::{parse_base_url_parts, AppConfig};

pub use error::{Error, Result};
pub use models::*;
pub use platform::PlatformKind;
pub use local_llm::{LocalChatHistory, LocalChatMessage, LocalLlmProfile, LocalLlmService};
pub use local_oauth::LocalOauth;
pub use chat_mode::{
    normalize_ui_messages, ChatSurface, ModeSnapshot, SendPath, UiChatMessage,
};
pub use media::{MediaItem, MediaStore};
pub use session_meta::{SessionMetaStore, LOCAL_ID as LOCAL_SESSION_ID};
pub use motion::MotionProfile;
pub use catalog_filter::filter_catalog;
pub use pair::{MeshMode, PairPayload, PairService, PairedDevice, PendingPair};
pub use mesh::{MeshConfig, MeshService, MeshStatus};
pub use tsnet_embed::{TsnetEmbed, TsnetRole};
pub use path::{
    claim_urls, probe_endpoint, select_best, DeferredClaim, Endpoint, EndpointKind, PathProfile,
    PathService, ProbeResult,
};
