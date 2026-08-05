pub mod auth;
pub mod chat;
pub mod client;
pub mod config;
pub mod error;
pub mod models;
pub mod platform;
pub mod storage;
pub mod local_llm;
pub mod chat_mode;
pub mod media;
pub mod session_meta;
pub mod motion;
pub mod catalog_filter;

pub use auth::AuthSession;
pub use client::TaktonClient;
pub use config::AppConfig;
pub use error::{Error, Result};
pub use models::*;
pub use platform::PlatformKind;
pub use local_llm::{LocalChatHistory, LocalChatMessage, LocalLlmProfile, LocalLlmService};
pub use chat_mode::{
    normalize_ui_messages, ChatSurface, ModeSnapshot, SendPath, UiChatMessage,
};
pub use media::{MediaItem, MediaStore};
pub use session_meta::{SessionMetaStore, LOCAL_ID as LOCAL_SESSION_ID};
pub use motion::MotionProfile;
pub use catalog_filter::filter_catalog;
