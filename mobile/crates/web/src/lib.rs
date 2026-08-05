#![allow(non_snake_case)]
//! Takton Mobile — Dioxus-web UI (DOM renderer).
//! CSS: 100% reuse of `/pixel-console.css` (legacy pixel tokens).

mod api;
mod app;
mod media;
mod models;
mod util;

use wasm_bindgen::prelude::*;

#[wasm_bindgen(start)]
pub fn start() {
    console_error_panic_hook::set_once();
    dioxus::launch(app::App);
}
