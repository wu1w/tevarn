//! Motion design tokens — single source of truth for mobile animation timing.
//! Host injects these as CSS custom properties; WASM UI only toggles classes.
//! Prefer transform/opacity (GPU compositor) — never animate layout properties from JS.

use serde::{Deserialize, Serialize};

/// Pixel-console motion profile (ms + cubic-bezier strings).
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct MotionProfile {
    pub drawer_ms: u32,
    pub sheet_ms: u32,
    pub toast_ms: u32,
    pub bubble_ms: u32,
    pub tab_ms: u32,
    pub press_ms: u32,
    pub island_ms: u32,
    /// Primary ease-out (drawers / sheets rising)
    pub ease_out: String,
    /// Snappy ease for buttons / toggles
    pub ease_snap: String,
    /// Soft spring-like for island / toast
    pub ease_soft: String,
    /// Long-press threshold (ms) — single source for gesture
    pub long_press_ms: u32,
    /// Swipe distance to reveal delete (px)
    pub swipe_delete_px: u32,
}

impl Default for MotionProfile {
    fn default() -> Self {
        Self {
            drawer_ms: 260,
            sheet_ms: 220,
            toast_ms: 280,
            bubble_ms: 180,
            tab_ms: 150,
            press_ms: 90,
            island_ms: 380,
            // Material-ish decelerate — smooth on 60/120Hz mobile
            ease_out: "cubic-bezier(0.22, 1, 0.36, 1)".into(),
            ease_snap: "cubic-bezier(0.2, 0.8, 0.2, 1)".into(),
            ease_soft: "cubic-bezier(0.32, 0.72, 0.24, 1)".into(),
            long_press_ms: 480,
            swipe_delete_px: 72,
        }
    }
}

impl MotionProfile {
    /// Emit `:root { --fx-*: ... }` block for injection into HTML/CSS.
    pub fn css_vars(&self) -> String {
        format!(
            r#":root{{
  --fx-drawer:{d}ms; --fx-sheet:{s}ms; --fx-toast:{t}ms; --fx-bubble:{b}ms;
  --fx-tab:{tab}ms; --fx-press:{p}ms; --fx-island:{i}ms;
  --fx-ease-out:{eo}; --fx-ease-snap:{es}; --fx-ease-soft:{ef};
  --fx-long-press:{lp}ms; --fx-swipe-del:{sw}px;
}}"#,
            d = self.drawer_ms,
            s = self.sheet_ms,
            t = self.toast_ms,
            b = self.bubble_ms,
            tab = self.tab_ms,
            p = self.press_ms,
            i = self.island_ms,
            eo = self.ease_out,
            es = self.ease_snap,
            ef = self.ease_soft,
            lp = self.long_press_ms,
            sw = self.swipe_delete_px,
        )
    }

    pub fn as_json(&self) -> serde_json::Value {
        serde_json::to_value(self).unwrap_or_default()
    }
}
