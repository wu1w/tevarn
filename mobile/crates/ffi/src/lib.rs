//! C ABI surface for Flutter FFI (`libtakton_mobile_ffi`).
//! Starts an embedded axum host and proxies method calls over loopback HTTP.
//! Streaming chat still uses HTTP/WS from Dart (Flutter opens it against the host
//! base returned by `takton_start_host`).

use once_cell::sync::OnceCell;
use serde_json::{json, Value};
use std::ffi::{CStr, CString};
use std::os::raw::c_char;
use std::path::PathBuf;
use std::sync::Mutex;
use takton_mobile_core::AppConfig;
use takton_mobile_host::{resolve_ui_dir, start_host, EngineHandle};

static RUNTIME: OnceCell<tokio::runtime::Runtime> = OnceCell::new();
static ENGINE: OnceCell<Mutex<Option<EngineHandle>>> = OnceCell::new();

fn runtime() -> &'static tokio::runtime::Runtime {
    RUNTIME.get_or_init(|| {
        tokio::runtime::Builder::new_multi_thread()
            .worker_threads(2)
            .enable_all()
            .build()
            .expect("tokio runtime")
    })
}

fn engine_slot() -> &'static Mutex<Option<EngineHandle>> {
    ENGINE.get_or_init(|| Mutex::new(None))
}

fn to_c_string(s: &str) -> *mut c_char {
    match CString::new(s) {
        Ok(c) => c.into_raw(),
        Err(_) => {
            let fallback = r#"{"ok":false,"error":"response contained interior NUL"}"#;
            CString::new(fallback)
                .unwrap_or_else(|_| CString::new("{}").expect("static"))
                .into_raw()
        }
    }
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

fn start_host_inner(preferred_port: i32, data_dir: Option<PathBuf>) -> *mut c_char {
    // Already running? Return existing base if we have one.
    {
        let g = match engine_slot().lock() {
            Ok(g) => g,
            Err(p) => p.into_inner(),
        };
        if let Some(eng) = g.as_ref() {
            return to_c_string(
                &json!({
                    "ok": true,
                    "base": *eng.base,
                    "reused": true,
                })
                .to_string(),
            );
        }
    }

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
        if let Some(d) = data_dir {
            config.data_dir = d;
        }
        // Ensure dir exists before host start
        let _ = std::fs::create_dir_all(&config.data_dir);
        let data_dir_s = config.data_dir.display().to_string();
        let ui = resolve_ui_dir();
        let result = start_host(config, ui).await;
        result.map(|(port, handle)| (port, handle, data_dir_s))
    }) {
        Ok((port, _handle, data_dir_s)) => {
            let eng = EngineHandle::new(port);
            let mut slot = match engine_slot().lock() {
                Ok(g) => g,
                Err(p) => p.into_inner(),
            };
            *slot = Some(eng.clone());
            to_c_string(
                &json!({
                    "ok": true,
                    "port": port,
                    "base": *eng.base,
                    "data_dir": data_dir_s,
                })
                .to_string(),
            )
        }
        Err(e) => err_json(e),
    }
}

/// Start embedded host on preferred port (0 = OS pick). Returns JSON `{ok, base, port}`.
#[no_mangle]
pub extern "C" fn takton_start_host(preferred_port: i32) -> *mut c_char {
    match std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
        start_host_inner(preferred_port, None)
    })) {
        Ok(ptr) => ptr,
        Err(_) => err_json("native panic in takton_start_host · 请重启应用"),
    }
}

/// Start host with an explicit writable data directory (required on Android).
/// `data_dir` is a UTF-8 C string path from Flutter path_provider.
#[no_mangle]
pub extern "C" fn takton_start_host2(
    preferred_port: i32,
    data_dir: *const c_char,
) -> *mut c_char {
    match std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
        let dir = match cstr(data_dir) {
            Ok(s) if !s.is_empty() => Some(PathBuf::from(s)),
            Ok(_) => None,
            Err(e) => return err_json(e),
        };
        start_host_inner(preferred_port, dir)
    })) {
        Ok(ptr) => ptr,
        Err(_) => err_json("native panic in takton_start_host2 · 请重启应用"),
    }
}

/// Generic method call: `method` name + JSON `args` object.
#[no_mangle]
pub extern "C" fn takton_call(method: *const c_char, args: *const c_char) -> *mut c_char {
    match std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
        takton_call_inner(method, args)
    })) {
        Ok(ptr) => ptr,
        Err(_) => err_json("native panic · 请重试或重启应用"),
    }
}

fn takton_call_inner(method: *const c_char, args: *const c_char) -> *mut c_char {
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
        let g = match engine_slot().lock() {
            Ok(g) => g,
            Err(p) => p.into_inner(),
        };
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
    match std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
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
    })) {
        Ok(ptr) => ptr,
        Err(_) => err_json("native panic in takton_mode_offline"),
    }
}

fn urlencoding_simple(s: &str) -> String {
    let mut out = String::with_capacity(s.len() * 2);
    for b in s.as_bytes() {
        match *b {
            b'A'..=b'Z' | b'a'..=b'z' | b'0'..=b'9' | b'-' | b'_' | b'.' | b'~' => {
                out.push(*b as char)
            }
            _ => out.push_str(&format!("%{:02X}", b)),
        }
    }
    out
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
        "session_stop" => {
            let id = sid();
            eng.invoke(
                "POST",
                &format!("/api/mobile/sessions/{id}/stop"),
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
        "local_config_clear" => eng
            .invoke("POST", "/api/mobile/local/config/clear", Some("{}"))
            .await,
        "local_agent_config_get" => eng
            .invoke("GET", "/api/mobile/local/agent_config", None)
            .await,
        "local_agent_config_set" => eng
            .invoke("POST", "/api/mobile/local/agent_config", Some(&body()))
            .await,
        "local_mcp_get" => eng.invoke("GET", "/api/mobile/local/mcp", None).await,
        "local_mcp_set" => eng
            .invoke("POST", "/api/mobile/local/mcp", Some(&body()))
            .await,
        "local_skills" => eng.invoke("GET", "/api/mobile/local/skills", None).await,
        "local_skills_install" => eng
            .invoke("POST", "/api/mobile/local/skills", Some(&body()))
            .await,
        "local_skills_install_pack" => eng
            .invoke("POST", "/api/mobile/local/skills/pack", Some(&body()))
            .await,
        "local_skills_uninstall" => eng
            .invoke("POST", "/api/mobile/local/skills/uninstall", Some(&body()))
            .await,
        "local_tools" => eng
            .invoke("POST", "/api/mobile/local/tools", Some(&body()))
            .await,
        "approvals" => eng.invoke("GET", "/api/mobile/approvals", None).await,
        "approvals_summary" => eng
            .invoke("GET", "/api/mobile/approvals/summary", None)
            .await,
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
        "pair_start" => eng
            .invoke("POST", "/api/mobile/pair/start", Some(&body()))
            .await,
        "pair_status" => {
            let id = args
                .get("pair_id")
                .or_else(|| args.get("id"))
                .and_then(|x| x.as_str())
                .unwrap_or("");
            eng.invoke("GET", &format!("/api/mobile/pair/status/{id}"), None)
                .await
        }
        "pair_confirm" => {
            let id = args
                .get("pair_id")
                .or_else(|| args.get("id"))
                .and_then(|x| x.as_str())
                .unwrap_or("");
            eng.invoke(
                "POST",
                &format!("/api/mobile/pair/confirm/{id}"),
                Some("{}"),
            )
            .await
        }
        "pair_cancel" => {
            let id = args
                .get("pair_id")
                .or_else(|| args.get("id"))
                .and_then(|x| x.as_str())
                .unwrap_or("");
            eng.invoke(
                "POST",
                &format!("/api/mobile/pair/cancel/{id}"),
                Some("{}"),
            )
            .await
        }
        "pair_claim" => eng
            .invoke("POST", "/api/mobile/pair/claim", Some(&body()))
            .await,
        "pair_apply" => eng
            .invoke("POST", "/api/mobile/pair/apply", Some(&body()))
            .await,
        "pair_devices" => eng.invoke("GET", "/api/mobile/pair/devices", None).await,
        "pair_pending" => eng.invoke("GET", "/api/mobile/pair/pending", None).await,
        "pair_revoke" => {
            let id = sid();
            eng.invoke(
                "POST",
                &format!("/api/mobile/pair/revoke/{id}"),
                Some("{}"),
            )
            .await
        }
        "mesh" | "mesh_status" => eng.invoke("GET", "/api/mobile/mesh", None).await,
        "mesh_set" => eng.invoke("POST", "/api/mobile/mesh", Some(&body())).await,
        "mesh_up" => eng.invoke("POST", "/api/mobile/mesh/up", Some(&body())).await,
        "mesh_down" => eng.invoke("POST", "/api/mobile/mesh/down", Some("{}")).await,
        "mesh_ifaces" => eng.invoke("POST", "/api/mobile/mesh/ifaces", Some(&body())).await,
        "mesh_auth" => eng.invoke("POST", "/api/mobile/mesh/auth", Some(&body())).await,
        "mesh_embed_start" => eng
            .invoke("POST", "/api/mobile/mesh/embed/start", Some(&body()))
            .await,
        "mesh_embed_stop" => eng
            .invoke("POST", "/api/mobile/mesh/embed/stop", Some("{}"))
            .await,
        "mesh_embed" | "mesh_embed_status" => {
            eng.invoke("GET", "/api/mobile/mesh/embed", None).await
        }
        "path" | "path_status" => eng.invoke("GET", "/api/mobile/path", None).await,
        "path_probe" => eng.invoke("POST", "/api/mobile/path/probe", Some(&body())).await,
        "path_reconnect" => eng
            .invoke("POST", "/api/mobile/path/reconnect", Some(&body()))
            .await,
        "path_refresh" => eng.invoke("POST", "/api/mobile/path/refresh", Some(&body())).await,
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
            let mut params: Vec<String> = Vec::new();
            if args.get("refresh").and_then(|x| x.as_bool()).unwrap_or(false) {
                params.push("refresh=true".into());
            }
            if let Some(q) = args
                .get("q")
                .and_then(|x| x.as_str())
                .filter(|s| !s.is_empty())
            {
                params.push(format!("q={}", urlencoding_simple(q)));
            }
            if let Some(pid) = args
                .get("provider_id")
                .and_then(|x| x.as_str())
                .filter(|s| !s.is_empty())
            {
                params.push(format!("provider_id={}", urlencoding_simple(pid)));
            }
            let path = if params.is_empty() {
                "/api/mobile/catalog".to_string()
            } else {
                format!("/api/mobile/catalog?{}", params.join("&"))
            };
            eng.invoke("GET", &path, None).await
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
        "notify" => eng.invoke("POST", "/api/mobile/notify", Some(&body())).await,
        "runtime" => eng.invoke("GET", "/api/mobile/runtime", None).await,
        "host_base" => return Ok(json!({ "ok": true, "base": *eng.base }).to_string()),
        other => return Err(format!("unknown method: {other}")),
    };
    result.map_err(|e| e.to_string())
}
