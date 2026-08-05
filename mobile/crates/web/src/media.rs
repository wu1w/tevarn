//! Real camera / microphone via web-sys (no mock).
use wasm_bindgen::JsCast;
use wasm_bindgen_futures::JsFuture;
use web_sys::{Blob, BlobPropertyBag, HtmlVideoElement, MediaStream};

pub async fn open_camera_stream() -> Result<MediaStream, String> {
    let window = web_sys::window().ok_or("no window")?;
    let devices = window
        .navigator()
        .media_devices()
        .map_err(|e| format!("{e:?}"))?;
    let mut constraints = web_sys::MediaStreamConstraints::new();
    constraints.set_video(&wasm_bindgen::JsValue::TRUE);
    constraints.set_audio(&wasm_bindgen::JsValue::FALSE);
    let p = devices
        .get_user_media_with_constraints(&constraints)
        .map_err(|e| format!("{e:?}"))?;
    let stream = JsFuture::from(p).await.map_err(|e| format!("{e:?}"))?;
    stream
        .dyn_into::<MediaStream>()
        .map_err(|_| "not a MediaStream".into())
}

pub async fn open_mic_stream() -> Result<MediaStream, String> {
    let window = web_sys::window().ok_or("no window")?;
    let devices = window
        .navigator()
        .media_devices()
        .map_err(|e| format!("{e:?}"))?;
    let mut constraints = web_sys::MediaStreamConstraints::new();
    constraints.set_audio(&wasm_bindgen::JsValue::TRUE);
    constraints.set_video(&wasm_bindgen::JsValue::FALSE);
    let p = devices
        .get_user_media_with_constraints(&constraints)
        .map_err(|e| format!("{e:?}"))?;
    let stream = JsFuture::from(p).await.map_err(|e| format!("{e:?}"))?;
    stream
        .dyn_into::<MediaStream>()
        .map_err(|_| "not a MediaStream".into())
}

pub fn stop_stream(stream: &MediaStream) {
    let tracks = stream.get_tracks();
    for i in 0..tracks.length() {
        if let Ok(t) = tracks.get(i).dyn_into::<web_sys::MediaStreamTrack>() {
            t.stop();
        }
    }
}

pub fn capture_video_to_file(video: &HtmlVideoElement) -> Result<web_sys::File, String> {
    let document = web_sys::window()
        .ok_or("no window")?
        .document()
        .ok_or("no document")?;
    let canvas = document
        .create_element("canvas")
        .map_err(|e| format!("{e:?}"))?
        .dyn_into::<web_sys::HtmlCanvasElement>()
        .map_err(|_| "canvas")?;
    let w = video.video_width().max(1);
    let h = video.video_height().max(1);
    canvas.set_width(w);
    canvas.set_height(h);
    let ctx = canvas
        .get_context("2d")
        .map_err(|e| format!("{e:?}"))?
        .ok_or("no 2d")?
        .dyn_into::<web_sys::CanvasRenderingContext2d>()
        .map_err(|_| "ctx")?;
    ctx.draw_image_with_html_video_element(video, 0.0, 0.0)
        .map_err(|e| format!("{e:?}"))?;
    let data_url = canvas
        .to_data_url_with_type("image/jpeg")
        .map_err(|e| format!("{e:?}"))?;
    data_url_to_file(
        &data_url,
        &format!("photo_{}.jpg", js_sys::Date::now() as u64),
        "image/jpeg",
    )
}

pub fn data_url_to_file(data_url: &str, name: &str, mime: &str) -> Result<web_sys::File, String> {
    use base64::Engine;
    let parts: Vec<&str> = data_url.splitn(2, ',').collect();
    if parts.len() != 2 {
        return Err("bad data url".into());
    }
    let bytes = base64::engine::general_purpose::STANDARD
        .decode(parts[1])
        .map_err(|e| e.to_string())?;
    let arr = js_sys::Uint8Array::new_with_length(bytes.len() as u32);
    arr.copy_from(&bytes);
    let seq = js_sys::Array::new();
    seq.push(&arr.buffer());
    let mut props = BlobPropertyBag::new();
    props.set_type(mime);
    let blob = Blob::new_with_u8_array_sequence_and_options(&seq, &props)
        .map_err(|e| format!("{e:?}"))?;
    let mut fprops = web_sys::FilePropertyBag::new();
    fprops.set_type(mime);
    web_sys::File::new_with_blob_sequence_and_options(&js_sys::Array::of1(&blob), name, &fprops)
        .map_err(|e| format!("{e:?}"))
}
