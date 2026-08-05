//! Escape / light markdown
pub fn esc(s: &str) -> String {
    let mut out = String::with_capacity(s.len());
    for c in s.chars() {
        match c {
            '&' => out.push_str("&amp;"),
            '<' => out.push_str("&lt;"),
            '>' => out.push_str("&gt;"),
            '"' => out.push_str("&quot;"),
            '\'' => out.push_str("&#39;"),
            _ => out.push(c),
        }
    }
    out
}

pub fn md_basic(s: &str) -> String {
    esc(s).replace('\n', "<br/>")
}

pub fn now_hm() -> String {
    let d = js_sys::Date::new_0();
    let h = d.get_hours() as u32;
    let m = d.get_minutes() as u32;
    let mut s = String::new();
    if h < 10 { s.push('0'); }
    s.push_str(&h.to_string());
    s.push(':');
    if m < 10 { s.push('0'); }
    s.push_str(&m.to_string());
    s
}

pub fn storage_get(key: &str) -> Option<String> {
    web_sys::window()?
        .local_storage()
        .ok()
        .flatten()?
        .get_item(key)
        .ok()
        .flatten()
}

pub fn storage_set(key: &str, val: &str) {
    if let Some(w) = web_sys::window() {
        if let Ok(Some(ls)) = w.local_storage() {
            let _ = ls.set_item(key, val);
        }
    }
}
