//! C ABI surface for Flutter FFI (`libtakton_mobile_ffi`).
//! Starts an embedded axum host and proxies method calls over loopback HTTP.
//! Streaming chat still uses HTTP/WS from Dart (Flutter opens it against the host
//! base returned by `takton_start_host`).

use once_cell::sync::OnceCell;
use serde_json::{json, Value};
use std::ffi::{CStr, CString};
use std::os::raw::c_char;
use std::sync::Mutex;
use takton_mobile_core::AppConfig;
use takton_mobile_host::{resolve_ui_dir, start_host, EngineHandle};

static RUNTIME: OnceCell<tokio::runtime::Runtime> = OnceCell::new();
static ENGINE: OnceCell<Mutex<Option<EngineHandle>>> = OnceCell::new();

fn runtime() -> &'static tokio::runtime::Runtime {
    RUNTIME.get_or_init(|| {
        tokio::runtime::Builder::new_multi_thread()
            .enable_all()
            .build()
            .expect("tokio runtime")
    })
}

fn engine_slot() -> &'static Mutex<Option<EngineHandle>> {
    ENGINE.get_or_init(|| Mutex::new(None))
}

fn to_c_string(s: &str) -> *mut c_char {
    CString::new(s).unwrap_or_default().into_raw()
}

fn err_json(e: impl ToString) -> *mut c_char {
    to_c_string(&json!({ "ok": false, "error": e.to_string() }).to_string())
}

fn cstr(p: *const c_char) -> Result<String, String> {
    if p.is_null() {
        return Err("null pointer".into());
    }
    unsafe { CStr::from_ptr(p) }
        .to_str()
        .map(|s| s.to_string())
        .map_err(|e| e.to_string())
}

/// Start embedded host on preferred port (0 = OS pick). Returns JSON `{ok, base, port}`.
#[no_mangle]
pub extern "C" fn takton_start_host(preferred_port: i32) -> *mut c_char {
    let rt = runtime();
    match rt.block_on(async {
        let mut config = AppConfig::default();
        // Prefer loopback for embedded native host
        config.host_bind = "127.0.0.1".into();
        config.host_port = if preferred_port > 0 {
            preferred_port as u16
        } else {
            0
        };
        let ui = resolve_ui_dir();
        start_host(config, ui).await
    }) {
        Ok((port, _handle)) => {
            let eng = EngineHandle::new(port);
            *engine_slot().lock().unwrap() = Some(eng.clone());
            to_c_string(
                &json!({
                    "ok": true,
                    "port": port,
                    "base": *eng.base,
                })
                .to_string(),
            )
        }
        Err(e) => err_json(e),
    }
}

/// Generic method call: `method` name + JSON `args` object.
#[no_mangle]
pub extern "C" fn takton_call(method: *const c_char, args: *const c_char) -> *mut c_char {
    let method = match cstr(method) {
        Ok(s) => s,
        Err(e) => return err_json(e),
    };
    let args_raw = match cstr(args) {
        Ok(s) => s,
        Err(_) => "{}".into(),
    };
    let args_val: Value = serde_json::from_str(&args_raw).unwrap_or(json!({}));
    let eng = {
        let g = engine_slot().lock().unwrap();
        match g.clone() {
            Some(e) => e,
            None => return err_json("host not started · call takton_start_host first"),
        }
    };
    let rt = runtime();
    match rt.block_on(dispatch(&eng, &method, &args_val)) {
        Ok(s) => to_c_string(&s),
        Err(e) => err_json(e),
    }
}

/// Free a string returned by takton_start_host / takton_call.
#[no_mangle]
pub extern "C" fn takton_free(ptr: *mut c_char) {
    if ptr.is_null() {
        return;
    }
    unsafe {
        drop(CString::from_raw(ptr));
    }
}

/// Offline motion profile (no host needed).
#[no_mangle]
pub extern "C" fn takton_mode_offline() -> *mut c_char {
    let m = takton_mobile_core::MotionProfile::default();
    match serde_json::to_string(&json!({
        "ok": true,
        "motion": m,
        "css_vars": m.css_vars(),
        "long_press_ms": m.long_press_ms
    })) {
        Ok(s) => to_c_string(&s),
        Err(e) => err_json(e.to_string()),
    }
}

async fn dispatch(eng: &EngineHandle, method: &str, args: &Value) -> Result<String, String> {
    let sid = || args.get("id").and_then(|x| x.as_str()).unwrap_or("").to_string();
    let body = || args.to_string();

    let result = match method {
        "health" => eng.invoke("GET", "/api/mobile/health", None).await,
        "state" => eng.invoke("GET", "/api/mobile/state", None).await,
        "mode" => eng.invoke("POST", "/api/mobile/mode", Some(&body())).await,
        "switch_surface" => eng
            .invoke("POST", "/api/mobile/switch_surface", Some(&body()))
            .await,

        "connect" => eng.invoke("POST", "/api/mobile/connect", Some(&body())).await,
        "disconnect" => eng.invoke("POST", "/api/mobile/disconnect", Some("{}")).await,
        "auto_login" => eng.invoke("POST", "/api/mobile/auto-login", Some("{}")).await,
        "sessions" => eng.invoke("GET", "/api/mobile/sessions", None).await,
        "session_create" => eng.invoke("POST", "/api/mobile/sessions", Some("{}")).await,
        "session_open" => {
            let id = sid();
            eng.invoke("POST", &format!("/api/mobile/sessions/{id}/open"), Some("{}"))
                .await
        }
        "session_pin" => {
            let id = sid();
            eng.invoke(
                "POST",
                &format!("/api/mobile/sessions/{id}/pin"),
                Some(&body()),
            )
            .await
        }
        "session_rename" => {
            let id = sid();
            eng.invoke(
                "POST",
                &format!("/api/mobile/sessions/{id}/rename"),
                Some(&body()),
            )
            .await
        }
        "session_delete" => {
            let id = sid();
            eng.invoke(
                "POST",
                &format!("/api/mobile/sessions/{id}/delete"),
                Some("{}"),
            )
            .await
        }
        "messages" => {
            let id = sid();
            eng.invoke("GET", &format!("/api/mobile/sessions/{id}/messages"), None)
                .await
        }
        "local_history" => eng.invoke("GET", "/api/mobile/local/history", None).await,
        "local_history_clear" => eng
            .invoke("POST", "/api/mobile/local/history", Some("{}"))
            .await,
        "local_config_get" => eng.invoke("GET", "/api/mobile/local/config", None).await,
        "local_config_set" => eng
            .invoke("POST", "/api/mobile/local/config", Some(&body()))
            .await,
        "local_test" => eng
            .invoke("POST", "/api/mobile/local/test", Some(&body()))
            .await,
        "local_chat" => eng
            .invoke("POST", "/api/mobile/local/chat", Some(&body()))
            .await,
        "local_stop" => eng
            .invoke("POST", "/api/mobile/local/stop", Some("{}"))
            .await,
        "approvals" => eng.invoke("GET", "/api/mobile/approvals", None).await,
        "decide" => {
            let id = sid();
            eng.invoke(
                "POST",
                &format!("/api/mobile/approvals/{id}/decide"),
                Some(&body()),
            )
            .await
        }
        "devices" => eng.invoke("GET", "/api/mobile/devices", None).await,
        "processes" => eng.invoke("GET", "/api/mobile/processes", None).await,
        "process_stop" => {
            let id = sid();
            eng.invoke(
                "POST",
                &format!("/api/mobile/processes/{id}/stop"),
                Some("{}"),
            )
            .await
        }
        "process_resume" => {
            let id = sid();
            eng.invoke(
                "POST",
                &format!("/api/mobile/processes/{id}/resume"),
                Some("{}"),
            )
            .await
        }
        "motion" => eng.invoke("GET", "/api/mobile/motion", None).await,
        "kernel" => eng.invoke("GET", "/api/mobile/kernel", None).await,
        "catalog" => {
            let refresh = args.get("refresh").and_then(|x| x.as_bool()).unwrap_or(false);
            let path = if refresh {
                "/api/mobile/catalog?refresh=true"
            } else {
                "/api/mobile/catalog"
            };
            eng.invoke("GET", path, None).await
        }
        "presets" => eng.invoke("GET", "/api/mobile/presets", None).await,
        "catalog_select" => eng
            .invoke("POST", "/api/mobile/catalog/select", Some(&body()))
            .await,
        "catalog_register" => eng
            .invoke("POST", "/api/mobile/catalog/register", Some(&body()))
            .await,
        "set_credentials" => eng
            .invoke("POST", "/api/mobile/settings/credentials", Some(&body()))
            .await,
        "test_llm" => eng.invoke("POST", "/api/mobile/test-llm", Some(&body())).await,
        "oauth_openai_start" => eng
            .invoke("POST", "/api/mobile/oauth/openai/start", Some("{}"))
            .await,
        "oauth_openai_poll" => eng
            .invoke("POST", "/api/mobile/oauth/openai/poll", Some(&body()))
            .await,
        "oauth_openai_complete" => eng
            .invoke(
                "POST",
                "/api/mobile/oauth/openai/complete",
                Some(&body()),
            )
            .await,
        "oauth_xai_start" => eng
            .invoke("POST", "/api/mobile/oauth/xai/start", Some("{}"))
            .await,
        "oauth_xai_poll" => eng
            .invoke("POST", "/api/mobile/oauth/xai/poll", Some(&body()))
            .await,
        "host_base" => return Ok(json!({ "ok": true, "base": *eng.base }).to_string()),
        other => return Err(format!("unknown method: {other}")),
    };
    result.map_err(|e| e.to_string())
}
