//! HTTP helpers — talk only to same-origin mobile host (no mock).
use gloo_net::http::Request;
use serde::de::DeserializeOwned;
use serde_json::Value;
use wasm_bindgen::JsCast;
use wasm_bindgen_futures::JsFuture;

fn map_api_error(status: u16, v: &Value) -> Option<String> {
    // Host returns HTTP 200 + { ok:false, error } for most failures.
    let ok_false = v.get("ok") == Some(&Value::Bool(false));
    if status >= 400 || ok_false {
        let err = v
            .get("error")
            .and_then(|x| x.as_str())
            .or_else(|| v.get("message").and_then(|x| x.as_str()))
            .unwrap_or("request failed");
        Some(if status >= 400 && !ok_false {
            format!("HTTP {status}: {err}")
        } else {
            err.to_string()
        })
    } else {
        None
    }
}

pub async fn get_json(path: &str) -> Result<Value, String> {
    let resp = Request::get(path)
        .send()
        .await
        .map_err(|e| e.to_string())?;
    let status = resp.status();
    let v: Value = resp.json().await.map_err(|e| e.to_string())?;
    if let Some(err) = map_api_error(status, &v) {
        return Err(err);
    }
    if status >= 400 {
        return Err(format!("HTTP {status}"));
    }
    Ok(v)
}

pub async fn post_json(path: &str, body: &Value) -> Result<Value, String> {
    let resp = Request::post(path)
        .header("Content-Type", "application/json")
        .json(body)
        .map_err(|e| e.to_string())?
        .send()
        .await
        .map_err(|e| e.to_string())?;
    let status = resp.status();
    let v: Value = resp.json().await.map_err(|e| e.to_string())?;
    if let Some(err) = map_api_error(status, &v) {
        return Err(err);
    }
    Ok(v)
}

pub async fn post_empty(path: &str) -> Result<Value, String> {
    post_json(path, &serde_json::json!({})).await
}

/// Multipart upload via browser FormData + fetch (gloo multipart is limited).
pub async fn post_multipart_file(
    path: &str,
    kind: &str,
    file: &web_sys::File,
) -> Result<Value, String> {
    let window = web_sys::window().ok_or("no window")?;
    let form = web_sys::FormData::new().map_err(|e| format!("{e:?}"))?;
    form.append_with_str("kind", kind)
        .map_err(|e| format!("{e:?}"))?;
    form.append_with_blob_and_filename("file", file, &file.name())
        .map_err(|e| format!("{e:?}"))?;

    let opts = web_sys::RequestInit::new();
    opts.set_method("POST");
    opts.set_body(&form);

    let request = web_sys::Request::new_with_str_and_init(path, &opts)
        .map_err(|e| format!("{e:?}"))?;
    let resp_val = JsFuture::from(window.fetch_with_request(&request))
        .await
        .map_err(|e| format!("{e:?}"))?;
    let resp: web_sys::Response = resp_val.dyn_into().map_err(|_| "bad response")?;
    let status = resp.status();
    let text = JsFuture::from(resp.text().map_err(|e| format!("{e:?}"))?)
        .await
        .map_err(|e| format!("{e:?}"))?;
    let s = text.as_string().unwrap_or_default();
    let v: Value = serde_json::from_str(&s).map_err(|e| e.to_string())?;
    if let Some(err) = map_api_error(status, &v) {
        return Err(err);
    }
    if status >= 400 {
        return Err(format!("HTTP {status}: {s}"));
    }
    Ok(v)
}

pub async fn post_json_typed<T: DeserializeOwned>(path: &str, body: &Value) -> Result<T, String> {
    let v = post_json(path, body).await?;
    serde_json::from_value(v).map_err(|e| e.to_string())
}
