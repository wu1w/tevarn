use serde::{Deserialize, Serialize};

/// Target platform — Android first; stubs ready for Apple / Harmony.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, Default)]
#[serde(rename_all = "snake_case")]
pub enum PlatformKind {
    #[default]
    Android,
    Ios,
    Ipad,
    Harmony,
    /// Desktop web host used for preview + E2E
    WebHost,
}

impl PlatformKind {
    pub fn detect() -> Self {
        if let Ok(p) = std::env::var("TAKTON_MOBILE_PLATFORM") {
            return match p.to_lowercase().as_str() {
                "ios" => Self::Ios,
                "ipad" => Self::Ipad,
                "harmony" | "ohos" | "hmos" => Self::Harmony,
                "android" => Self::Android,
                _ => Self::WebHost,
            };
        }
        #[cfg(target_os = "android")]
        {
            return Self::Android;
        }
        #[cfg(target_os = "ios")]
        {
            return Self::Ios;
        }
        Self::WebHost
    }

    pub fn as_str(self) -> &'static str {
        match self {
            Self::Android => "android",
            Self::Ios => "ios",
            Self::Ipad => "ipad",
            Self::Harmony => "harmony",
            Self::WebHost => "web_host",
        }
    }
}
