//! Pixel Console shell — DOM hierarchy mirrors legacy ui/index.html (1:1 migration).
//! CSS is 100% reused from pixel-console.css; do not invent new class names.

use crate::api;
use crate::media;
use crate::models::{AppStateDto, ChatMsg, ChatSurface, ModeSnap, Tab, LOCAL_SESSION_ID};
use crate::util::{esc, md_basic, now_hm, storage_get, storage_set};
use dioxus::prelude::*;
use serde_json::{json, Value};
use futures_util::StreamExt;
use wasm_bindgen::JsCast;

#[component]
pub fn App() -> Element {
    // ── core signals (mirror legacy globals) ──
    let mut tab = use_signal(|| Tab::Chat);
    let mut drawer_open = use_signal(|| false);
    let mut chat_surface = use_signal(|| {
        if storage_get("takton-chat-mode").as_deref() == Some("remote") {
            ChatSurface::Remote
        } else {
            ChatSurface::Local
        }
    });
    let mut app_state = use_signal(AppStateDto::default);
    let mut mode_snap = use_signal(ModeSnap::default);
    let mut messages = use_signal(Vec::<ChatMsg>::new);
    let mut input = use_signal(String::new);
    let mut streaming = use_signal(|| false);
    let mut toast_msg = use_signal(|| Option::<String>::None);
    let mut toast_show = use_signal(|| false);
    let mut clock = use_signal(now_hm);
    let mut theme_dark = use_signal(|| {
        storage_get("takton-theme").as_deref() == Some("dark")
    });
    let mut island_kind = use_signal(|| String::from("local"));
    let mut island_html = use_signal(|| String::from("<b>本机</b>&nbsp;…"));
    let mut island_live = use_signal(|| false);
    let mut media_sheet = use_signal(|| false);
    let mut cam_open = use_signal(|| false);
    let mut cam_stream: Signal<Option<web_sys::MediaStream>> = use_signal(|| None);
    let mut attach_names = use_signal(Vec::<String>::new);
    let mut ap_tab = use_signal(|| 1u8);
    let mut approvals = use_signal(Vec::<Value>::new);
    let mut evolutions = use_signal(Vec::<Value>::new);
    let mut processes = use_signal(Vec::<Value>::new);
    let mut pair_manual = use_signal(|| false);
    let mut form_base = use_signal(|| "http://127.0.0.1:8090".to_string());
    let mut form_email = use_signal(String::new);
    let mut form_pass = use_signal(String::new);
    let mut llm_base = use_signal(String::new);
    let mut llm_key = use_signal(String::new);
    let mut llm_model = use_signal(String::new);
    let mut active_session = use_signal(|| Option::<String>::None);
    let mut rec_on = use_signal(|| false);
    let mut notify_on = use_signal(|| true);
    // session long-press management
    let mut sess_sheet = use_signal(|| Option::<(String, String, bool)>::None); // id, title, pinned
    let mut sess_rename = use_signal(|| Option::<(String, String)>::None); // id, draft title
    let mut sess_confirm = use_signal(|| Option::<(String, String, bool)>::None); // id, title, is_local
    let mut hold_id = use_signal(|| Option::<String>::None);
    let mut hold_long = use_signal(|| false);
    let mut hold_gen = use_signal(|| 0u64); // invalidates late long-press timers
    let mut long_press_ms = use_signal(|| 480u32);
    let mut stream_cancel = use_signal(|| false);

    // toast helper
    let mut toast = {
        let mut toast_msg = toast_msg;
        let mut toast_show = toast_show;
        move |m: String| {
            toast_msg.set(Some(m));
            toast_show.set(true);
            spawn(async move {
                gloo_timers::future::TimeoutFuture::new(2200).await;
                toast_show.set(false);
            });
        }
    };

    // boot: refresh state + mode
    use_effect(move || {
        spawn(async move {
            loop {
                clock.set(now_hm());
                gloo_timers::future::TimeoutFuture::new(15_000).await;
            }
        });
        spawn(async move {
            // Rust motion tokens → CSS vars (GPU-only transforms in stylesheet)
            if let Ok(v) = api::get_json("/api/mobile/motion").await {
                if let Some(ms) = v.get("long_press_ms").and_then(|x| x.as_u64()) {
                    long_press_ms.set(ms as u32);
                }
                if let Some(css) = v.get("css_vars").and_then(|x| x.as_str()) {
                    if let Some(doc) = web_sys::window().and_then(|w| w.document()) {
                        if doc.get_element_by_id("fx-motion").is_none() {
                            if let Ok(style) = doc.create_element("style") {
                                style.set_id("fx-motion");
                                style.set_text_content(Some(css));
                                if let Some(head) = doc.head() {
                                    let _ = head.append_child(&style);
                                }
                            }
                        }
                    }
                }
            }
            if let Err(e) = bootstrap(
                app_state,
                mode_snap,
                chat_surface,
                messages,
                active_session,
                form_base,
                llm_base,
                llm_model,
                approvals,
                evolutions,
                processes,
                notify_on,
                island_kind,
                island_html,
            )
            .await
            {
                web_sys::console::warn_1(&format!("boot: {e}").into());
            }
        });
    });

    // theme attribute on documentElement
    use_effect(move || {
        let dark = theme_dark();
        if let Some(doc) = web_sys::window().and_then(|w| w.document()) {
            if let Some(el) = doc.document_element() {
                if dark {
                    let _ = el.set_attribute("data-theme", "dark");
                } else {
                    let _ = el.remove_attribute("data-theme");
                }
            }
        }
    });

    let pc = app_state().authenticated;
    let local_ready = app_state().local_llm_ready;
    let surface = chat_surface();
    let snap = mode_snap();
    let has_text = !input().trim().is_empty();
    let _can_send = snap.can_send && has_text && !streaming();
    let send_enabled = has_text || streaming(); // allow tap → toast when !ready

    let chat_title = if surface == ChatSurface::Remote {
        session_title(&app_state(), &active_session())
    } else {
        "本机对话".into()
    };
    let chat_sub = if !snap.subtitle.is_empty() {
        snap.subtitle.clone()
    } else if surface == ChatSurface::Remote {
        format!(
            "远端 Agent · {}",
            app_state().active_model.clone().unwrap_or_else(|| "—".into())
        )
    } else if local_ready {
        format!(
            "本机模型 · {}",
            app_state()
                .local_llm
                .as_ref()
                .and_then(|v| v.get("model"))
                .and_then(|m| m.as_str())
                .unwrap_or("—")
        )
    } else {
        "我的 → LLM 设置（API Key）".into()
    };
    let placeholder = if !snap.placeholder.is_empty() {
        snap.placeholder.clone()
    } else {
        "有什么可以帮忙的？".into()
    };
    let mode_hint = if !snap.reason.is_empty() {
        snap.reason.clone()
    } else {
        "选择对话通道：本机直连模型 · 或 PC Agent 工具链".into()
    };

    // connection strip
    let (conn_lamp, conn_name, conn_meta) = if pc {
        let name = app_state()
            .user
            .as_ref()
            .and_then(|u| {
                u.get("username")
                    .or_else(|| u.get("email"))
                    .and_then(|x| x.as_str())
            })
            .unwrap_or("PC")
            .to_string();
        (
            "lamp on pulse",
            format!("PC 已连接 · {}", esc(&name)),
            app_state()
                .runtime
                .get("processes_live")
                .map(|p| format!("进程 {p}"))
                .unwrap_or_else(|| "在线".into()),
        )
    } else {
        (
            "lamp warn",
            "未连 PC".into(),
            if local_ready {
                "本机模型可用".into()
            } else {
                "去「我的」配置 LLM".into()
            },
        )
    };

    rsx! {
        // #stage
        div { id: "stage",
            div { id: "side",
                h1 {
                    span { class: "px", "TAKTON MOBILE" }
                    "口袋里的 AI 公司"
                }
                p { "连接 PC 后可远程接管 agent / 审批 / 进程；LLM 设置与 PC 端一致。未连 PC 时也可直接配置模型对话。" }
                div { class: "tips",
                    b { "试试这些：" }
                    br {}
                    "1. 默认本机模型对话（我的 → LLM 设置）"
                    br {}
                    "2. 顶栏切换到「远端 Agent」使用 PC 工具链"
                    br {}
                    "3. 审批 tab 处理提权 / 进化"
                    br {}
                    "4. 连接 tab 管理 PC 与设备"
                    br {}
                    "5. 深色模式在「我的 → 主题」"
                }
            }

            div { id: "phone",
                // notch / dynamic island — same ids/classes
                div {
                    id: "notch",
                    class: if island_live() { "live" } else { "" },
                    title: "灵动岛 · 点击查看实时状态",
                    onclick: move |_| {
                        island_live.set(true);
                        island_html.set(if app_state().authenticated {
                            format!("<b>已连 PC</b>&nbsp;待办 {}", app_state().approvals_pending)
                        } else {
                            "<b>本地模式</b>".into()
                        });
                    },
                    span {
                        class: match island_kind().as_str() {
                            "stream" => "il-dot cy",
                            "alert" => "il-dot pk",
                            "local" => "il-dot lo",
                            _ => "il-dot on",
                        },
                        id: "il-dot",
                    }
                    span {
                        id: "il-txt",
                        class: "il-txt",
                        dangerous_inner_html: "{island_html()}",
                    }
                }

                div { id: "statusbar",
                    span { class: "num", id: "sb-time", "{clock()}" }
                    div { class: "sic",
                        svg {
                            width: "17", height: "11", view_box: "0 0 17 11",
                            fill: "currentColor",
                            style: "shape-rendering:crispEdges",
                            path { d: "M0 8h3v3H0zM4.5 5h3v6h-3zM9 3h3v8H9zM13.5 0h3v11h-3z" }
                        }
                        svg {
                            width: "15", height: "11", view_box: "0 0 15 11",
                            fill: "currentColor",
                            style: "shape-rendering:crispEdges",
                            path { d: "M7 8h1v3H7zM5 6h5v2H5zM3 4h9v2H3zM1 2h13v2H1zM0 0h15v1H0z", opacity: "0.9" }
                        }
                        svg {
                            width: "24", height: "11", view_box: "0 0 24 11",
                            fill: "none", stroke: "currentColor",
                            style: "shape-rendering:crispEdges",
                            rect { x: "0.5", y: "0.5", width: "20", height: "10" }
                            rect { x: "22", y: "3", width: "2", height: "5", fill: "currentColor", stroke: "none" }
                            rect { x: "2", y: "2", width: "14", height: "7", fill: "currentColor", stroke: "none" }
                        }
                    }
                }

                div { id: "screens",
                    // ═══ chat ═══
                    section {
                        class: if tab() == Tab::Chat { "screen act" } else { "screen" },
                        id: "s-chat",
                        div { class: "shead",
                            button {
                                class: "iconbtn",
                                title: "历史会话",
                                onclick: move |_| drawer_open.set(true),
                                svg { view_box: "0 0 24 24", fill: "currentColor",
                                    path { d: "M3 5h18v3H3zM3 11h18v3H3zM3 17h12v3H3z" }
                                }
                            }
                            div { style: "flex:1; min-width:0",
                                div { class: "tt", id: "chat-title", "{chat_title}" }
                                div { class: "sub", id: "chat-sub", "{chat_sub}" }
                            }
                            button {
                                class: "iconbtn",
                                title: "新建会话",
                                onclick: move |_| {
                                    let mut messages = messages;
                                    let mut toast = toast.clone();
                                    let surface = chat_surface();
                                    spawn(async move {
                                        if surface == ChatSurface::Local {
                                            let _ = api::post_empty("/api/mobile/local/history").await;
                                            messages.set(vec![welcome_local()]);
                                            toast("已新建本机对话".into());
                                        } else {
                                            match api::post_empty("/api/mobile/sessions").await {
                                                Ok(v) => {
                                                    if let Some(id) = v.pointer("/session/id").and_then(|x| x.as_str()) {
                                                        active_session.set(Some(id.to_string()));
                                                    }
                                                    messages.set(vec![welcome_remote()]);
                                                    toast("已新建远端会话".into());
                                                }
                                                Err(e) => toast(format!("新建失败: {e}")),
                                            }
                                        }
                                    });
                                },
                                svg { view_box: "0 0 24 24", fill: "currentColor",
                                    path { d: "M10 4h4v6h6v4h-6v6h-4v-6H4v-4h6z" }
                                }
                            }
                        }

                        div { id: "mode-bar",
                            button {
                                r#type: "button",
                                class: if surface == ChatSurface::Local { "mode-btn local act" } else { "mode-btn local" },
                                id: "mode-local",
                                onclick: move |_| {
                                    chat_surface.set(ChatSurface::Local);
                                    storage_set("takton-chat-mode", "local");
                                    let mut toast = toast.clone();
                                    spawn(async move {
                                        let _ = refresh_mode(chat_surface, mode_snap).await;
                                        load_local_msgs(messages).await;
                                        toast("已切换 · 本机对话".into());
                                    });
                                },
                                span { class: "dot" }
                                "本机对话"
                            }
                            button {
                                r#type: "button",
                                class: if surface == ChatSurface::Remote { "mode-btn act" } else { "mode-btn" },
                                id: "mode-remote",
                                disabled: !pc,
                                style: if pc { "opacity:1" } else { "opacity:.45" },
                                onclick: move |_| {
                                    if !app_state().authenticated {
                                        tab.set(Tab::Remote);
                                        toast("远端 Agent 需先连接 PC".into());
                                        return;
                                    }
                                    chat_surface.set(ChatSurface::Remote);
                                    storage_set("takton-chat-mode", "remote");
                                    let mut toast = toast.clone();
                                    spawn(async move {
                                        let _ = refresh_mode(chat_surface, mode_snap).await;
                                        ensure_remote_session(active_session, messages, app_state).await;
                                        toast("已切换 · 远端 Agent".into());
                                    });
                                },
                                span { class: "dot" }
                                "远端 Agent"
                            }
                        }
                        div { id: "mode-hint", "{mode_hint}" }

                        div { id: "chat-conn",
                            span { class: "{conn_lamp}", id: "conn-lamp" }
                            div { style: "min-width:0",
                                span {
                                    class: "nm",
                                    id: "conn-name",
                                    dangerous_inner_html: "{conn_name}",
                                }
                                span {
                                    class: "mut num",
                                    id: "conn-meta",
                                    style: "margin-left:6px",
                                    "{conn_meta}"
                                }
                            }
                            span {
                                class: "go",
                                id: "conn-go",
                                onclick: move |_| drawer_open.set(true),
                                "会话 →"
                            }
                        }

                        div { id: "msgs",
                            for m in messages() {
                                {
                                    let is_me = m.role == "user";
                                    rsx! {
                                        div {
                                            class: if is_me { "mrow me fx-enter" } else { "mrow fx-enter" },
                                            key: "{m.id}",
                                            if !is_me {
                                                div {
                                                    class: "avt avt-px",
                                                    style: "width:32px;height:32px",
                                                    "TK"
                                                }
                                            }
                                            div { class: "bub",
                                                if !is_me {
                                                    div { class: "who",
                                                        dangerous_inner_html: "{m.who}"
                                                    }
                                                }
                                                div {
                                                    dangerous_inner_html: "{m.html}",
                                                }
                                                if m.streaming {
                                                    span { class: "caret" }
                                                }
                                            }
                                        }
                                    }
                                }
                            }
                        }
                        div { id: "sugg" }

                        div { id: "composer",
                            div {
                                id: "attach-chips",
                                class: if attach_names().is_empty() { "" } else { "has" },
                                for (i, name) in attach_names().iter().enumerate() {
                                    {
                                        let name = name.clone();
                                        rsx! {
                                            span { class: "chip-file", key: "{i}-{name}",
                                                "{name}"
                                                button {
                                                    r#type: "button",
                                                    onclick: move |_| {
                                                        attach_names.write().remove(i);
                                                    },
                                                    "×"
                                                }
                                            }
                                        }
                                    }
                                }
                            }
                            div {
                                id: "rec-wave",
                                class: if rec_on() { "on" } else { "" },
                                "● 正在录音… 松开发送"
                            }
                            div { class: "composer-row",
                                button {
                                    r#type: "button",
                                    id: "attach-btn",
                                    title: "附件 / 相机",
                                    "aria-label": "附件",
                                    onclick: move |_| media_sheet.set(true),
                                    svg { view_box: "0 0 24 24", fill: "currentColor",
                                        path { d: "M11 5h2v6h6v2h-6v6h-2v-6H5v-2h6z" }
                                    }
                                }
                                button {
                                    r#type: "button",
                                    id: "cam-btn",
                                    title: "相机",
                                    onclick: move |_| {
                                        let mut cam_open = cam_open;
                                        let mut cam_stream = cam_stream;
                                        let mut toast = toast.clone();
                                        spawn(async move {
                                            match media::open_camera_stream().await {
                                                Ok(s) => {
                                                    // attach to video element after open
                                                    cam_stream.set(Some(s.clone()));
                                                    cam_open.set(true);
                                                    if let Some(doc) = web_sys::window().and_then(|w| w.document()) {
                                                        if let Some(v) = doc.get_element_by_id("cam-video") {
                                                            if let Ok(vid) = v.dyn_into::<web_sys::HtmlVideoElement>() {
                                                                vid.set_src_object(Some(&s));
                                                                let _ = vid.play();
                                                            }
                                                        }
                                                    }
                                                }
                                                Err(e) => toast(format!("无法打开相机：{e}")),
                                            }
                                        });
                                    },
                                    svg { view_box: "0 0 24 24", fill: "currentColor",
                                        path { d: "M3 7h4l2-2h6l2 2h4v12H3V7zm9 3a4 4 0 100 8 4 4 0 000-8zm0 2a2 2 0 110 4 2 2 0 010-4z" }
                                    }
                                }
                                textarea {
                                    class: "ta",
                                    id: "inp",
                                    rows: "1",
                                    placeholder: "{placeholder}",
                                    value: "{input}",
                                    oninput: move |e| input.set(e.value()),
                                    onkeydown: move |e| {
                                        // Enter 发送 · Shift+Enter 换行（对标主流聊天 App）
                                        let key = e.key();
                                        let is_enter = matches!(key, dioxus::html::input_data::keyboard_types::Key::Enter);
                                        if is_enter && !e.modifiers().contains(dioxus::html::input_data::keyboard_types::Modifiers::SHIFT) {
                                            e.prevent_default();
                                            if let Some(doc) = web_sys::window().and_then(|w| w.document()) {
                                                if let Some(el) = doc.get_element_by_id("sendbtn") {
                                                    if let Ok(btn) = el.dyn_into::<web_sys::HtmlButtonElement>() {
                                                        if !btn.disabled() {
                                                            btn.click();
                                                        }
                                                    }
                                                }
                                            }
                                        }
                                    },
                                }
                                button {
                                    r#type: "button",
                                    id: "voice-btn",
                                    class: if rec_on() { "rec" } else { "" },
                                    title: "按住说话",
                                    onpointerdown: move |_| {
                                        rec_on.set(true);
                                        toast("录音中…松开发送".into());
                                    },
                                    onpointerup: move |_| {
                                        if !rec_on() { return; }
                                        rec_on.set(false);
                                        let mut toast = toast.clone();
                                        spawn(async move {
                                            match record_short_voice().await {
                                                Ok(file) => {
                                                    let name = file.name();
                                                    match api::post_multipart_file("/api/mobile/media", "audio", &file).await {
                                                        Ok(_) => {
                                                            attach_names.write().push(name);
                                                            toast("语音已附加".into());
                                                        }
                                                        Err(e) => toast(format!("语音上传失败: {e}")),
                                                    }
                                                }
                                                Err(e) => toast(format!("录音失败: {e}")),
                                            }
                                        });
                                    },
                                    svg { view_box: "0 0 24 24", fill: "currentColor",
                                        path { d: "M12 2a3 3 0 00-3 3v6a3 3 0 006 0V5a3 3 0 00-3-3zm-7 9h2a5 5 0 0010 0h2a7 7 0 01-6 6.9V21h-2v-3.1A7 7 0 015 11z" }
                                    }
                                }
                                button {
                                    r#type: "button",
                                    id: "sendbtn",
                                    class: {
                                        if streaming() {
                                            "stop"
                                        } else if has_text && !mode_snap().can_send {
                                            "warn"
                                        } else {
                                            "go"
                                        }
                                    },
                                    disabled: !send_enabled,
                                    "aria-label": "发送",
                                    onclick: move |_| {
                                        if streaming() {
                                            stream_cancel.set(true);
                                            streaming.set(false);
                                            island_live.set(false);
                                            let surface = chat_surface();
                                            spawn(async move {
                                                if surface == ChatSurface::Local {
                                                    let _ = api::post_empty("/api/mobile/local/stop").await;
                                                }
                                            });
                                            return;
                                        }
                                        let txt = input().trim().to_string();
                                        if txt.is_empty() { return; }
                                        if !mode_snap().can_send {
                                            let snap = mode_snap();
                                            let msg = if snap.fix_hint.is_empty() {
                                                snap.reason.clone()
                                            } else {
                                                format!("{} · {}", snap.reason, snap.fix_hint)
                                            };
                                            toast(msg);
                                            match snap.fix_tab.as_str() {
                                                "me" => tab.set(Tab::Me),
                                                "remote" => tab.set(Tab::Remote),
                                                _ => {}
                                            }
                                            return;
                                        }
                                        input.set(String::new());
                                        let surface = chat_surface();
                                        let mut messages = messages;
                                        let mut streaming = streaming;
                                        let mut toast = toast.clone();
                                        let mut island_kind = island_kind;
                                        let mut island_html = island_html;
                                        let session = active_session();
                                        let attach = attach_names();
                                        spawn(async move {
                                            let mut user_txt = txt.clone();
                                            if !attach.is_empty() {
                                                user_txt = format!("{user_txt}\n\n[附件: {}]", attach.join("、"));
                                                attach_names.set(vec![]);
                                            }
                                            let uid = format!("u{}", js_sys::Date::now());
                                            messages.write().push(ChatMsg {
                                                id: uid,
                                                role: "user".into(),
                                                html: esc(&user_txt),
                                                who: String::new(),
                                                streaming: false,
                                            });
                                            let aid = format!("a{}", js_sys::Date::now());
                                            messages.write().push(ChatMsg {
                                                id: aid.clone(),
                                                role: "assistant".into(),
                                                html: String::new(),
                                                who: if surface == ChatSurface::Remote {
                                                    "远端 Agent · <span class=\"num\">流式</span>".into()
                                                } else {
                                                    "本机 · <span class=\"num\">LLM</span>".into()
                                                },
                                                streaming: true,
                                            });
                                            streaming.set(true);
                                            stream_cancel.set(false);
                                            island_live.set(true);
                                            island_kind.set("stream".into());
                                            island_html.set("<b>生成</b>&nbsp;中".into());

                                            let result = if surface == ChatSurface::Local {
                                                stream_local(&user_txt, aid.clone(), messages, stream_cancel).await
                                            } else {
                                                stream_remote(&user_txt, session, aid.clone(), messages, stream_cancel).await
                                            };
                                            streaming.set(false);
                                            stream_cancel.set(false);
                                            island_live.set(false);
                                            // mark not streaming
                                            let mut empty_out = false;
                                            {
                                                let mut ms = messages.write();
                                                if let Some(m) = ms.iter_mut().find(|m| m.id == aid) {
                                                    m.streaming = false;
                                                    if m.html.is_empty() {
                                                        empty_out = true;
                                                        m.html = esc(match &result {
                                                            Ok(()) => {
                                                                if surface == ChatSurface::Local {
                                                                    "（无模型输出）请检查本机 LLM 配置或切换远端 Agent"
                                                                } else {
                                                                    "（无模型输出）PC Agent 可能未就绪 · 请确认内核/会话"
                                                                }
                                                            }
                                                            Err(e) => e.as_str(),
                                                        });
                                                    }
                                                }
                                            }
                                            if surface == ChatSurface::Remote {
                                                island_kind.set("conn".into());
                                                island_html.set("<b>已连 PC</b>&nbsp;就绪".into());
                                            } else {
                                                island_kind.set("local".into());
                                                island_html.set("<b>本机</b>&nbsp;就绪".into());
                                            }
                                            match result {
                                                Err(e) => toast(e),
                                                Ok(()) if empty_out => {
                                                    let snap = mode_snap();
                                                    let hint = if snap.fix_hint.is_empty() {
                                                        if surface == ChatSurface::Local {
                                                            "无模型输出 · 检查 LLM 配置".into()
                                                        } else {
                                                            "无模型输出 · 检查 PC Agent".into()
                                                        }
                                                    } else {
                                                        format!("无模型输出 · {}", snap.fix_hint)
                                                    };
                                                    toast(hint);
                                                }
                                                Ok(()) => {}
                                            }
                                        });
                                    },
                                    if streaming() {
                                        svg {
                                            id: "stopico",
                                            view_box: "0 0 24 24",
                                            fill: "currentColor",
                                            path { d: "M7 7h10v10H7z" }
                                        }
                                    } else {
                                        svg {
                                            id: "sendico",
                                            view_box: "0 0 24 24",
                                            fill: "currentColor",
                                            path { d: "M3 10h11v4H3zM13 8h3v8h-3zM16 10h5l-2.5 2 2.5 2z" }
                                        }
                                    }
                                }
                            }
                        }

                        div {
                            id: "media-sheet",
                            class: if media_sheet() { "show" } else { "" },
                            button {
                                r#type: "button",
                                onclick: move |_| {
                                    media_sheet.set(false);
                                    // trigger hidden file input via DOM
                                    if let Some(doc) = web_sys::window().and_then(|w| w.document()) {
                                        if let Some(el) = doc.get_element_by_id("file-input") {
                                            if let Ok(inp) = el.dyn_into::<web_sys::HtmlInputElement>() {
                                                inp.click();
                                            }
                                        }
                                    }
                                },
                                span { class: "ms-ico", "📎" }
                                span {
                                    div { "文件" }
                                    div { class: "ms-sub", "文档 / 图片 · 远端上传到 PC" }
                                }
                            }
                            button {
                                r#type: "button",
                                onclick: move |_| {
                                    media_sheet.set(false);
                                    // reuse cam button logic by setting flag
                                    let mut cam_open = cam_open;
                                    let mut cam_stream = cam_stream;
                                    let mut toast = toast.clone();
                                    spawn(async move {
                                        match media::open_camera_stream().await {
                                            Ok(s) => {
                                                cam_stream.set(Some(s.clone()));
                                                cam_open.set(true);
                                                if let Some(doc) = web_sys::window().and_then(|w| w.document()) {
                                                    if let Some(v) = doc.get_element_by_id("cam-video") {
                                                        if let Ok(vid) = v.dyn_into::<web_sys::HtmlVideoElement>() {
                                                            vid.set_src_object(Some(&s));
                                                            let _ = vid.play();
                                                        }
                                                    }
                                                }
                                            }
                                            Err(e) => toast(format!("无法打开相机：{e}")),
                                        }
                                    });
                                },
                                span { class: "ms-ico", "📷" }
                                span {
                                    div { "相机拍照" }
                                    div { class: "ms-sub", "实时预览拍摄 · 真实摄像头" }
                                }
                            }
                            button {
                                r#type: "button",
                                onclick: move |_| {
                                    media_sheet.set(false);
                                    if let Some(doc) = web_sys::window().and_then(|w| w.document()) {
                                        if let Some(el) = doc.get_element_by_id("gallery-input") {
                                            if let Ok(inp) = el.dyn_into::<web_sys::HtmlInputElement>() {
                                                inp.click();
                                            }
                                        }
                                    }
                                },
                                span { class: "ms-ico", "🖼" }
                                span {
                                    div { "相册" }
                                    div { class: "ms-sub", "从相册选择图片" }
                                }
                            }
                            button {
                                r#type: "button",
                                style: "justify-content:center;color:var(--ink3)",
                                onclick: move |_| media_sheet.set(false),
                                "取消"
                            }
                        }
                    }

                    // camera overlay — sibling inside screens? legacy places after chat section
                    div {
                        id: "cam-overlay",
                        class: if cam_open() { "show" } else { "" },
                        video {
                            id: "cam-video",
                            playsinline: true,
                            autoplay: true,
                            muted: true,
                        }
                        canvas { id: "cam-canvas", style: "display:none" }
                        div { class: "cam-bar",
                            button {
                                r#type: "button",
                                class: "cam-cancel",
                                onclick: move |_| {
                                    if let Some(s) = cam_stream() {
                                        media::stop_stream(&s);
                                    }
                                    cam_stream.set(None);
                                    cam_open.set(false);
                                },
                                "取消"
                            }
                            button {
                                r#type: "button",
                                id: "cam-shutter",
                                title: "拍照",
                                onclick: move |_| {
                                    let mut toast = toast.clone();
                                    let mut attach_names = attach_names;
                                    spawn(async move {
                                        if let Some(doc) = web_sys::window().and_then(|w| w.document()) {
                                            if let Some(v) = doc.get_element_by_id("cam-video") {
                                                if let Ok(vid) = v.dyn_into::<web_sys::HtmlVideoElement>() {
                                                    match media::capture_video_to_file(&vid) {
                                                        Ok(file) => {
                                                            let name = file.name();
                                                            match api::post_multipart_file("/api/mobile/media", "image", &file).await {
                                                                Ok(_) => {
                                                                    attach_names.write().push(name);
                                                                    toast("照片已就绪".into());
                                                                }
                                                                Err(e) => toast(e),
                                                            }
                                                        }
                                                        Err(e) => toast(e),
                                                    }
                                                }
                                            }
                                        }
                                        if let Some(s) = cam_stream() {
                                            media::stop_stream(&s);
                                        }
                                        cam_stream.set(None);
                                        cam_open.set(false);
                                    });
                                },
                                "●"
                            }
                        }
                    }

                    // ═══ approve ═══
                    section {
                        class: if tab() == Tab::Approve { "screen act" } else { "screen" },
                        id: "s-approve",
                        div { class: "shead",
                            div { style: "flex:1",
                                div { class: "tt", "审批中心" }
                                div { class: "sub", "提权与进化，路上也能拍板" }
                            }
                            button {
                                class: "btn sm",
                                onclick: move |_| {
                                    let list = approvals();
                                    let mut toast = toast.clone();
                                    spawn(async move {
                                        for a in list {
                                            if let Some(id) = a.get("id").and_then(|x| x.as_str()) {
                                                let _ = api::post_json(
                                                    &format!("/api/mobile/approvals/{id}/decide"),
                                                    &json!({"approved": true, "kind": "escalation", "scope": "once"}),
                                                )
                                                .await;
                                            }
                                        }
                                        toast("已批量处理".into());
                                    });
                                },
                                "全部通过"
                            }
                        }
                        div { class: "sbody",
                            div { class: "seg",
                                button {
                                    class: if ap_tab() == 1 { "act" } else { "" },
                                    id: "ap-t1",
                                    onclick: move |_| ap_tab.set(1),
                                    "员工扩权 "
                                    if !approvals().is_empty() {
                                        span { class: "badge", id: "ap-b1", "{approvals().len()}" }
                                    }
                                }
                                button {
                                    class: if ap_tab() == 2 { "act" } else { "" },
                                    id: "ap-t2",
                                    onclick: move |_| ap_tab.set(2),
                                    "进化提案 "
                                    if !evolutions().is_empty() {
                                        span { class: "badge cy", id: "ap-b2", "{evolutions().len()}" }
                                    }
                                }
                            }
                            div { id: "ap-list",
                                if ap_tab() == 1 {
                                    if approvals().is_empty() {
                                        div { class: "mut", style: "text-align:center;padding:20px",
                                            if pc { "暂无待审批提权" } else { "连接 PC 后显示真实审批" }
                                        }
                                    } else {
                                        for a in approvals() {
                                            {
                                                let id = a.get("id").and_then(|x| x.as_str()).unwrap_or("").to_string();
                                                let title = a.get("title").or_else(|| a.get("reason")).and_then(|x| x.as_str()).unwrap_or("提权请求").to_string();
                                                let id_key = id.clone();
                                                let id_a = id.clone();
                                                let id_b = id.clone();
                                                rsx! {
                                                    div { class: "card2", key: "{id_key}", style: "margin-bottom:10px",
                                                        div { style: "font-weight:700", "{title}" }
                                                        div { class: "mut", style: "margin-top:6px", "{id_key}" }
                                                        div { style: "display:flex;gap:8px;margin-top:10px",
                                                            button {
                                                                class: "btn sm pri",
                                                                onclick: move |_| {
                                                                    let id = id_a.clone();
                                                                    let mut toast = toast.clone();
                                                                    spawn(async move {
                                                                        match api::post_json(
                                                                            &format!("/api/mobile/approvals/{id}/decide"),
                                                                            &json!({"approved": true, "kind": "escalation", "scope": "once"}),
                                                                        ).await {
                                                                            Ok(_) => toast("已通过".into()),
                                                                            Err(e) => toast(e),
                                                                        }
                                                                    });
                                                                },
                                                                "通过"
                                                            }
                                                            button {
                                                                class: "btn sm dan",
                                                                onclick: move |_| {
                                                                    let id = id_b.clone();
                                                                    let mut toast = toast.clone();
                                                                    spawn(async move {
                                                                        match api::post_json(
                                                                            &format!("/api/mobile/approvals/{id}/decide"),
                                                                            &json!({"approved": false, "kind": "escalation", "scope": "deny"}),
                                                                        ).await {
                                                                            Ok(_) => toast("已拒绝".into()),
                                                                            Err(e) => toast(e),
                                                                        }
                                                                    });
                                                                },
                                                                "拒绝"
                                                            }
                                                        }
                                                    }
                                                }
                                            }
                                        }
                                    }
                                } else {
                                    if evolutions().is_empty() {
                                        div { class: "mut", style: "text-align:center;padding:20px",
                                            if pc { "暂无进化提案" } else { "连接 PC 后显示真实提案" }
                                        }
                                    } else {
                                        for evo in evolutions() {
                                            {
                                                let id = evo.get("id").and_then(|x| x.as_str()).unwrap_or("").to_string();
                                                let title = evo.get("title").or_else(|| evo.get("summary")).and_then(|x| x.as_str()).unwrap_or("进化提案").to_string();
                                                let id_key = id.clone();
                                                let id_a = id.clone();
                                                let id_b = id.clone();
                                                rsx! {
                                                    div { class: "card2", key: "{id_key}", style: "margin-bottom:10px",
                                                        div { style: "font-weight:700", "{title}" }
                                                        div { class: "mut", style: "margin-top:6px", "{id_key}" }
                                                        div { style: "display:flex;gap:8px;margin-top:10px",
                                                            button {
                                                                class: "btn sm pri",
                                                                onclick: move |_| {
                                                                    let id = id_a.clone();
                                                                    let mut toast = toast.clone();
                                                                    spawn(async move {
                                                                        match api::post_json(
                                                                            &format!("/api/mobile/approvals/{id}/decide"),
                                                                            &json!({"approved": true, "kind": "evolution"}),
                                                                        ).await {
                                                                            Ok(_) => toast("已批准进化".into()),
                                                                            Err(e) => toast(e),
                                                                        }
                                                                    });
                                                                },
                                                                "批准"
                                                            }
                                                            button {
                                                                class: "btn sm dan",
                                                                onclick: move |_| {
                                                                    let id = id_b.clone();
                                                                    let mut toast = toast.clone();
                                                                    spawn(async move {
                                                                        match api::post_json(
                                                                            &format!("/api/mobile/approvals/{id}/decide"),
                                                                            &json!({"approved": false, "kind": "evolution"}),
                                                                        ).await {
                                                                            Ok(_) => toast("已拒绝进化".into()),
                                                                            Err(e) => toast(e),
                                                                        }
                                                                    });
                                                                },
                                                                "拒绝"
                                                            }
                                                        }
                                                    }
                                                }
                                            }
                                        }
                                    }
                                }
                            }
                            div { class: "sect",
                                "RUNNING "
                                span { class: "mut num", id: "proc-count", "{processes().len()}" }
                            }
                            div { class: "card2", style: "padding:6px 14px", id: "proc-list",
                                if processes().is_empty() {
                                    div { class: "mut", style: "text-align:center;padding:14px",
                                        "连接 PC 后显示真实进程"
                                    }
                                } else {
                                    for p in processes() {
                                        {
                                            let id = p.get("id").and_then(|x| x.as_str()).unwrap_or("").to_string();
                                            let name = p.get("name").or_else(|| p.get("title")).and_then(|x| x.as_str()).unwrap_or("process").to_string();
                                            let id_key = id.clone();
                                            let id_stop = id.clone();
                                            rsx! {
                                                div { class: "row", key: "{id_key}",
                                                    span { class: "rt", "{name}" }
                                                    button {
                                                        class: "btn sm",
                                                        onclick: move |_| {
                                                            let id = id_stop.clone();
                                                            spawn(async move {
                                                                let _ = api::post_empty(&format!("/api/mobile/processes/{id}/stop")).await;
                                                            });
                                                        },
                                                        "停止"
                                                    }
                                                }
                                            }
                                        }
                                    }
                                }
                            }
                            div { class: "mut", style: "text-align:center", "数据来自 PC 内核 · 无演示条目" }
                        }
                    }

                    // ═══ remote ═══
                    section {
                        class: if tab() == Tab::Remote { "screen act" } else { "screen" },
                        id: "s-remote",
                        div { class: "shead",
                            div { style: "flex:1",
                                div { class: "tt", "远程连接" }
                                div { class: "sub", "接管 PC 上的 Takton 运行时" }
                            }
                            span {
                                class: if pc { "badge" } else { "badge am" },
                                id: "rm-state",
                                if pc { "ONLINE" } else { "LOCAL" }
                            }
                        }
                        div { class: "sbody",
                            div { class: "card2 conn-hero",
                                svg {
                                    width: "56", height: "56", view_box: "0 0 24 24",
                                    fill: "var(--purple)",
                                    style: "shape-rendering:crispEdges; margin:0 auto; display:block",
                                    path { d: "M7 7h10v2H7zM7 15h10v2H7zM7 7h2v10H7zM15 7h2v10h-2zM10 10h4v4h-4zM9 3h2v4H9zM13 3h2v4h-2zM9 17h2v4H9zM13 17h2v4h-2zM3 9h4v2H3zM3 13h4v2H3zM17 9h4v2h-4zM17 13h4v2h-4z" }
                                }
                                div { class: "big", id: "rm-title",
                                    {
                                        if pc {
                                            format!("已连接 · {}", app_state().base_url)
                                        } else {
                                            "未连接".to_string()
                                        }
                                    }
                                }
                                div { class: "mut num", id: "rm-meta",
                                    {
                                        if pc {
                                            app_state().user.as_ref().and_then(|u| u.get("email")).and_then(|e| e.as_str()).unwrap_or("").to_string()
                                        } else {
                                            "填写地址登录你的 PC Takton".to_string()
                                        }
                                    }
                                }
                                div { style: "display:flex; gap:8px; justify-content:center; margin-top:14px",
                                    button {
                                        class: "btn sm",
                                        id: "rm-disc",
                                        onclick: move |_| {
                                            let mut toast = toast.clone();
                                            spawn(async move {
                                                if app_state().authenticated {
                                                    let _ = api::post_empty("/api/mobile/disconnect").await;
                                                    let _ = refresh_all(app_state, mode_snap, chat_surface, approvals, evolutions, processes, notify_on, form_base).await;
                                                    toast("已断开 · 降级为本地模式".into());
                                                } else {
                                                    tab.set(Tab::Me);
                                                }
                                            });
                                        },
                                        if pc { "断开连接" } else { "连接 PC" }
                                    }
                                    button {
                                        class: "btn sm cy",
                                        onclick: move |_| {
                                            let mut toast = toast.clone();
                                            spawn(async move {
                                                match api::get_json("/api/mobile/runtime").await {
                                                    Ok(v) => {
                                                        let live = v.pointer("/runtime/processes_live");
                                                        toast(format!("运行时 OK · {:?}", live));
                                                    }
                                                    Err(e) => toast(e),
                                                }
                                            });
                                        },
                                        "心跳测试"
                                    }
                                }
                            }
                            div { class: "sect", "DEVICES" }
                            div { id: "dev-list",
                                if app_state().devices.is_empty() {
                                    div { class: "mut", style: "padding:8px", "暂无设备" }
                                } else {
                                    for d in app_state().devices.clone() {
                                        {
                                            let name = d.get("name").and_then(|x| x.as_str()).unwrap_or("device");
                                            rsx! {
                                                div { class: "dev",
                                                    div { class: "di",
                                                        div { class: "dn", "{name}" }
                                                        div { class: "dm", "{d.get(\"id\").and_then(|x| x.as_str()).unwrap_or(\"\")}" }
                                                    }
                                                }
                                            }
                                        }
                                    }
                                }
                            }
                            div { class: "sect", "PAIR" }
                            div { class: "card2",
                                div { style: "display:flex; gap:8px",
                                    button {
                                        class: "btn pri",
                                        style: "flex:1",
                                        onclick: move |_| pair_manual.set(true),
                                        "手动配对"
                                    }
                                    button {
                                        class: "btn",
                                        style: "flex:1",
                                        onclick: move |_| pair_manual.set(true),
                                        "展开表单"
                                    }
                                }
                                if pair_manual() {
                                    div { id: "manual", style: "margin-top:12px",
                                        div { class: "fg",
                                            span { class: "lb", "PC Base URL" }
                                            input {
                                                class: "inp num",
                                                id: "pair-base",
                                                value: "{form_base}",
                                                oninput: move |e| form_base.set(e.value()),
                                            }
                                        }
                                        div { class: "fg",
                                            span { class: "lb", "登录邮箱" }
                                            input {
                                                class: "inp num",
                                                id: "pair-email",
                                                placeholder: "admin@takton.dev",
                                                value: "{form_email}",
                                                oninput: move |e| form_email.set(e.value()),
                                            }
                                        }
                                        div { class: "fg",
                                            span { class: "lb", "密码" }
                                            input {
                                                class: "inp num",
                                                id: "pair-pass",
                                                r#type: "password",
                                                placeholder: "留空则 loopback auto-login",
                                                value: "{form_pass}",
                                                oninput: move |e| form_pass.set(e.value()),
                                            }
                                        }
                                        button {
                                            class: "btn pri blk",
                                            onclick: move |_| {
                                                let mut toast = toast.clone();
                                                let base = form_base();
                                                let email = form_email();
                                                let pass = form_pass();
                                                spawn(async move {
                                                    let body = json!({
                                                        "base_url": base,
                                                        "email": if email.is_empty() { Value::Null } else { json!(email) },
                                                        "password": if pass.is_empty() { Value::Null } else { json!(pass) },
                                                        "auto": email.is_empty() && pass.is_empty(),
                                                    });
                                                    match api::post_json("/api/mobile/connect", &body).await {
                                                        Ok(_) => {
                                                            let _ = refresh_all(app_state, mode_snap, chat_surface, approvals, evolutions, processes, notify_on, form_base).await;
                                                            toast("已连接 PC".into());
                                                        }
                                                        Err(e) => toast(format!("连接失败: {e}")),
                                                    }
                                                });
                                            },
                                            "连接 PC"
                                        }
                                    }
                                }
                            }
                            div { class: "sect", "CAPABILITIES" }
                            div { class: "card2", style: "padding-top:8px; padding-bottom:8px", id: "cap-list",
                                div { class: "cap",
                                    span { class: if pc { "lamp on" } else { "lamp off" }, id: "cap-chat" }
                                    "对话 · 会话与 PC 实时同步"
                                }
                                div { class: "cap",
                                    span { class: if pc { "lamp on" } else { "lamp off" }, id: "cap-appr" }
                                    "审批提权 / 进化提案"
                                }
                                div { class: "cap",
                                    span { class: if pc { "lamp on" } else { "lamp off" }, id: "cap-proc" }
                                    "进程查看 / 停止"
                                }
                                div { class: "cap",
                                    span { class: if pc { "lamp on" } else { "lamp off" }, id: "cap-dev" }
                                    "L1 设备配对"
                                }
                                div { class: "cap",
                                    span { class: if pc { "lamp on" } else { "lamp off" }, id: "cap-file" }
                                    "附件上传（随消息）"
                                }
                            }
                        }
                    }

                    // ═══ me ═══
                    section {
                        class: if tab() == Tab::Me { "screen act" } else { "screen" },
                        id: "s-me",
                        div { class: "shead",
                            div { class: "tt", style: "flex:1", "我的" }
                        }
                        div { class: "sbody",
                            div { class: "card2 me-card",
                                canvas {
                                    class: "avt",
                                    width: "10", height: "10",
                                    style: "width:44px;height:44px;background:var(--purple)",
                                }
                                div {
                                    div { class: "mn", id: "me-name",
                                        {
                                            app_state().user.as_ref()
                                                .and_then(|u| u.get("display_name").or_else(|| u.get("email")))
                                                .and_then(|x| x.as_str())
                                                .unwrap_or("未登录")
                                                .to_string()
                                        }
                                    }
                                    div { class: "md num", id: "me-meta",
                                        {
                                            if pc {
                                                format!("已连接 · {}", app_state().base_url)
                                            } else {
                                                "未连接".to_string()
                                            }
                                        }
                                    }
                                }
                            }

                            div { class: "sect", "PC 连接" }
                            div { class: "card2",
                                div { class: "mut", style: "margin-bottom:10px; line-height:1.6",
                                    "连接 Takton 后端以使用 agent / 审批 / 进程。"
                                }
                                div { class: "fg",
                                    span { class: "lb", "Takton Base URL" }
                                    input {
                                        class: "inp num", id: "api-base",
                                        value: "{form_base}",
                                        oninput: move |e| form_base.set(e.value()),
                                    }
                                }
                                div { class: "fg",
                                    span { class: "lb", "邮箱" }
                                    input {
                                        class: "inp num", id: "api-email",
                                        placeholder: "admin@takton.dev",
                                        value: "{form_email}",
                                        oninput: move |e| form_email.set(e.value()),
                                    }
                                }
                                div { class: "fg",
                                    span { class: "lb", "登录密码" }
                                    input {
                                        class: "inp num", id: "api-key",
                                        r#type: "password",
                                        value: "{form_pass}",
                                        oninput: move |e| form_pass.set(e.value()),
                                    }
                                }
                                div { style: "display:flex; gap:8px",
                                    button {
                                        class: "btn pri", style: "flex:1",
                                        onclick: move |_| {
                                            let mut toast = toast.clone();
                                            let base = form_base();
                                            let email = form_email();
                                            let pass = form_pass();
                                            spawn(async move {
                                                let body = json!({
                                                    "base_url": base,
                                                    "auto": email.is_empty() && pass.is_empty(),
                                                    "email": email,
                                                    "password": pass,
                                                });
                                                match api::post_json("/api/mobile/connect", &body).await {
                                                    Ok(_) => {
                                                        let _ = refresh_all(app_state, mode_snap, chat_surface, approvals, evolutions, processes, notify_on, form_base).await;
                                                        toast("已连接 PC".into());
                                                    }
                                                    Err(e) => toast(format!("连接失败: {e}")),
                                                }
                                            });
                                        },
                                        "连接"
                                    }
                                    button {
                                        class: "btn",
                                        onclick: move |_| {
                                            form_email.set(String::new());
                                            form_pass.set(String::new());
                                        },
                                        "清除"
                                    }
                                }
                            }

                            div { class: "sect", "LLM 设置 · 本机" }
                            div { class: "card2",
                                div { class: "mut", style: "margin-bottom:10px;line-height:1.6",
                                    "API Key 供应商用于本机对话。ChatGPT OAuth 请在连接 PC 后于远端 Agent 使用。"
                                }
                                div { class: "fg",
                                    span { class: "lb", "Base URL" }
                                    input {
                                        class: "inp num",
                                        placeholder: "https://api.openai.com/v1",
                                        value: "{llm_base}",
                                        oninput: move |e| llm_base.set(e.value()),
                                    }
                                }
                                div { class: "fg",
                                    span { class: "lb", "API Key" }
                                    input {
                                        class: "inp num",
                                        r#type: "password",
                                        placeholder: "sk-…",
                                        value: "{llm_key}",
                                        oninput: move |e| llm_key.set(e.value()),
                                    }
                                }
                                div { class: "fg",
                                    span { class: "lb", "Model" }
                                    input {
                                        class: "inp num",
                                        placeholder: "model-id",
                                        value: "{llm_model}",
                                        oninput: move |e| llm_model.set(e.value()),
                                    }
                                }
                                button {
                                    class: "btn pri blk",
                                    onclick: move |_| {
                                        let mut toast = toast.clone();
                                        let body = json!({
                                            "base_url": llm_base(),
                                            "api_key": llm_key(),
                                            "model": llm_model(),
                                        });
                                        spawn(async move {
                                            match api::post_json("/api/mobile/local/config", &body).await {
                                                Ok(_) => {
                                                    let _ = refresh_mode(chat_surface, mode_snap).await;
                                                    if let Ok(s) = api::get_json("/api/mobile/state").await {
                                                        if let Ok(dto) = serde_json::from_value::<AppStateDto>(s) {
                                                            app_state.set(dto);
                                                        }
                                                    }
                                                    if mode_snap().can_send || mode_snap().local_llm_ready {
                                                        toast("本机模型已就绪".into());
                                                    } else {
                                                        toast("已保存 · 请补全 base_url / api_key / model".into());
                                                    }
                                                }
                                                Err(e) => toast(e),
                                            }
                                        });
                                    },
                                    "应用本机模型"
                                }
                            }

                            div { class: "sect", "主题" }
                            div { class: "card2",
                                div { class: "tseg",
                                    button {
                                        class: if !theme_dark() { "act" } else { "" },
                                        onclick: move |_| {
                                            theme_dark.set(false);
                                            storage_set("takton-theme", "light");
                                        },
                                        "浅色"
                                    }
                                    button {
                                        class: if theme_dark() { "act" } else { "" },
                                        onclick: move |_| {
                                            theme_dark.set(true);
                                            storage_set("takton-theme", "dark");
                                        },
                                        "深色"
                                    }
                                }
                            }

                            div { class: "sect", "关于" }
                            div { class: "card2",
                                div { class: "row",
                                    span { class: "rt", "引擎" }
                                    span { class: "rd", "Dioxus-web · 纯 Rust UI" }
                                }
                                div { class: "row",
                                    span { class: "rt", "样式" }
                                    span { class: "rd", "pixel-console.css 1:1 复用" }
                                }
                                div { class: "row",
                                    span { class: "rt", "版本" }
                                    span { class: "rd num", "mobile v0.2-dx" }
                                }
                            }
                        }
                    }
                }

                // tabbar — demo pixel icons + crispEdges
                div { id: "tabbar",
                    div {
                        class: if tab() == Tab::Chat { "tab act" } else { "tab" },
                        "data-tab": "chat",
                        onclick: move |_| tab.set(Tab::Chat),
                        svg { view_box: "0 0 24 24", fill: "currentColor", style: "shape-rendering:crispEdges",
                            path { d: "M3 3h18v2H3zM3 13h18v2H3zM3 3h2v12H3zM19 3h2v12h-2zM6 15h3v3H6zM5 18h2v2H5zM7 7h2v3H7zM11 7h2v3h-2zM15 7h2v3h-2z" }
                        }
                        span { "对话" }
                    }
                    div {
                        class: if tab() == Tab::Approve { "tab act" } else { "tab" },
                        "data-tab": "approve",
                        onclick: move |_| {
                            tab.set(Tab::Approve);
                            spawn(async move {
                                let _ = load_approvals(approvals, evolutions, processes).await;
                            });
                        },
                        svg { view_box: "0 0 24 24", fill: "currentColor", style: "shape-rendering:crispEdges",
                            path { d: "M4 2h14v2H4zM4 20h14v2H4zM4 2h2v20H4zM16 2h2v20h-2zM8 11h2v2H8zM10 13h2v2h-2zM12 11h2v2h-2zM14 9h2v2h-2z" }
                        }
                        span { "审批" }
                        if app_state().approvals_pending > 0 {
                            span { class: "bdg", "{app_state().approvals_pending}" }
                        }
                    }
                    div {
                        class: if tab() == Tab::Remote { "tab act" } else { "tab" },
                        "data-tab": "remote",
                        onclick: move |_| tab.set(Tab::Remote),
                        svg { view_box: "0 0 24 24", fill: "currentColor", style: "shape-rendering:crispEdges",
                            path { d: "M9 2h6v2H9zM9 20h6v2H9zM7 4h2v16H7zM15 4h2v16h-2zM10 9h4v4h-4zM3 7h2v2H3zM1 5h2v2H1zM19 7h2v2h-2zM21 5h2v2h-2zM3 15h2v2H3zM1 17h2v2H1zM19 15h2v2h-2zM21 17h2v2h-2z" }
                        }
                        span { "连接" }
                    }
                    div {
                        class: if tab() == Tab::Me { "tab act" } else { "tab" },
                        "data-tab": "me",
                        onclick: move |_| tab.set(Tab::Me),
                        svg { view_box: "0 0 24 24", fill: "currentColor", style: "shape-rendering:crispEdges",
                            path { d: "M9 3h6v6H9zM6 11h12v3H6zM4 14h16v7H4z" }
                        }
                        span { "我的" }
                    }
                }
                div { id: "homebar" }

                // drawer (history) + long-press session management
                if drawer_open() {
                    div {
                        id: "drawer-bg",
                        onclick: move |_| drawer_open.set(false),
                    }
                    div {
                        id: "drawer",
                        div { class: "dh",
                            div { style: "flex:1;min-width:0",
                                div { class: "tt", style: "font-size:15px;font-weight:700", "会话" }
                                div { class: "sub", "轻触进入 · 长按管理" }
                            }
                            button {
                                class: "iconbtn",
                                title: "关闭",
                                onclick: move |_| drawer_open.set(false),
                                svg {
                                    view_box: "0 0 24 24",
                                    fill: "none",
                                    stroke: "currentColor",
                                    style: "width:18px;height:18px",
                                    path {
                                        d: "M6 6l12 12M18 6L6 18",
                                        stroke_width: "2.2",
                                        stroke_linecap: "square",
                                    }
                                }
                            }
                        }
                        div { class: "dlist",
                            button {
                                r#type: "button",
                                class: "btn pri blk fx-press",
                                style: "margin:4px 0 10px",
                                onclick: move |_| {
                                    let surface = chat_surface();
                                    let mut toast = toast.clone();
                                    spawn(async move {
                                        if surface == ChatSurface::Local {
                                            let _ = api::post_empty("/api/mobile/local/history").await;
                                            messages.set(vec![welcome_local()]);
                                            drawer_open.set(false);
                                            toast("已新建本机对话".into());
                                        } else if app_state().authenticated {
                                            match api::post_empty("/api/mobile/sessions").await {
                                                Ok(v) => {
                                                    if let Some(id) = v.pointer("/session/id").and_then(|x| x.as_str()) {
                                                        active_session.set(Some(id.to_string()));
                                                    }
                                                    messages.set(vec![welcome_remote()]);
                                                    drawer_open.set(false);
                                                    toast("已新建远端会话".into());
                                                    let _ = refresh_all(app_state, mode_snap, chat_surface, approvals, evolutions, processes, notify_on, form_base).await;
                                                }
                                                Err(e) => toast(format!("新建失败: {e}")),
                                            }
                                        } else {
                                            toast("远端新建需先连接 PC".into());
                                        }
                                    });
                                },
                                "+ 新对话"
                            }
                            div { class: "dsect", "对话通道" }
                            {
                                let local_title = app_state()
                                    .local_session
                                    .as_ref()
                                    .and_then(|v| v.get("title"))
                                    .and_then(|x| x.as_str())
                                    .unwrap_or("本机对话")
                                    .to_string();
                                let local_pinned = app_state()
                                    .local_session
                                    .as_ref()
                                    .and_then(|v| v.get("pinned"))
                                    .and_then(|x| x.as_bool())
                                    .unwrap_or(false);
                                let local_act = surface == ChatSurface::Local;
                                let lt = local_title.clone();
                                let cls = if local_act && local_pinned {
                                    "hitem act pinned"
                                } else if local_act {
                                    "hitem act"
                                } else if local_pinned {
                                    "hitem pinned"
                                } else {
                                    "hitem"
                                };
                                rsx! {
                                    div {
                                        class: "{cls} fx-press",
                                        onpointerdown: {
                                            let lt = lt.clone();
                                            move |_| {
                                                let gen = hold_gen() + 1;
                                                hold_gen.set(gen);
                                                hold_id.set(Some(LOCAL_SESSION_ID.into()));
                                                hold_long.set(false);
                                                let lt = lt.clone();
                                                let wait = long_press_ms();
                                                spawn(async move {
                                                    gloo_timers::future::TimeoutFuture::new(wait).await;
                                                    if hold_gen() == gen && hold_id().as_deref() == Some(LOCAL_SESSION_ID) {
                                                        hold_long.set(true);
                                                        sess_sheet.set(Some((LOCAL_SESSION_ID.into(), lt, local_pinned)));
                                                    }
                                                });
                                            }
                                        },
                                        onpointerup: move |_| {
                                            hold_id.set(None);
                                            let was_long = hold_long();
                                            hold_long.set(false);
                                            if was_long || sess_sheet().is_some() {
                                                return;
                                            }
                                            chat_surface.set(ChatSurface::Local);
                                            storage_set("takton-chat-mode", "local");
                                            drawer_open.set(false);
                                            spawn(async move {
                                                let _ = refresh_mode(chat_surface, mode_snap).await;
                                                load_local_msgs(messages).await;
                                            });
                                        },
                                        onpointercancel: move |_| {
                                            hold_id.set(None);
                                            hold_gen.set(hold_gen() + 1);
                                            hold_long.set(false);
                                        },
                                        oncontextmenu: move |e: dioxus::prelude::Event<dioxus::html::MouseData>| {
                                            e.prevent_default();
                                            hold_long.set(true);
                                            sess_sheet.set(Some((LOCAL_SESSION_ID.into(), local_title.clone(), local_pinned)));
                                        },
                                        div { class: "t1",
                                            if local_pinned { span { class: "pin-mark", "PIN" } }
                                            "{local_title}"
                                            span { class: "mode-tag lo", "本机" }
                                        }
                                        div { class: "t2", "直连本机模型 · 单线程 · 长按管理" }
                                    }
                                }
                            }
                            div { class: "dsect", "远端会话" }
                            if !pc {
                                div { class: "mut", "连接 PC 后显示远端会话" }
                            } else if app_state().sessions.is_empty() {
                                div { class: "mut", "暂无远端会话 · 点右上角 + 新建" }
                            } else {
                                for s in app_state().sessions.clone() {
                                    {
                                        let id = s.get("id").and_then(|x| x.as_str()).unwrap_or("").to_string();
                                        let title = s.get("title").and_then(|x| x.as_str()).unwrap_or("会话").to_string();
                                        let pinned = s.get("pinned").and_then(|x| x.as_bool()).unwrap_or(false);
                                        let id_key = id.clone();
                                        let id_short = id.chars().take(8).collect::<String>();
                                        let id_up = id.clone();
                                        let title_up = title.clone();
                                        let act = active_session().as_deref() == Some(id.as_str()) && surface == ChatSurface::Remote;
                                        let cls = {
                                            let mut c = String::from("hitem");
                                            if act { c.push_str(" act"); }
                                            if pinned { c.push_str(" pinned"); }
                                            c
                                        };
                                        rsx! {
                                            div {
                                                class: "{cls} fx-press",
                                                key: "{id_key}",
                                                onpointerdown: {
                                                    let id = id.clone();
                                                    let title = title.clone();
                                                    move |_| {
                                                        let gen = hold_gen() + 1;
                                                        hold_gen.set(gen);
                                                        hold_id.set(Some(id.clone()));
                                                        hold_long.set(false);
                                                        let id2 = id.clone();
                                                        let title2 = title.clone();
                                                        let wait = long_press_ms();
                                                        spawn(async move {
                                                            gloo_timers::future::TimeoutFuture::new(wait).await;
                                                            if hold_gen() == gen && hold_id().as_deref() == Some(id2.as_str()) {
                                                                hold_long.set(true);
                                                                sess_sheet.set(Some((id2, title2, pinned)));
                                                            }
                                                        });
                                                    }
                                                },
                                                onpointerup: {
                                                    let id_up = id_up.clone();
                                                    move |_| {
                                                        hold_id.set(None);
                                                        let was_long = hold_long();
                                                        hold_long.set(false);
                                                        if was_long || sess_sheet().is_some() {
                                                            return;
                                                        }
                                                        active_session.set(Some(id_up.clone()));
                                                        chat_surface.set(ChatSurface::Remote);
                                                        storage_set("takton-chat-mode", "remote");
                                                        drawer_open.set(false);
                                                        let id = id_up.clone();
                                                        spawn(async move {
                                                            let _ = refresh_mode(chat_surface, mode_snap).await;
                                                            load_remote_msgs(&id, messages).await;
                                                        });
                                                    }
                                                },
                                                onpointercancel: move |_| {
                                                    hold_id.set(None);
                                                    hold_gen.set(hold_gen() + 1);
                                                    hold_long.set(false);
                                                },
                                                oncontextmenu: {
                                                    let id = id.clone();
                                                    let title = title.clone();
                                                    move |e: dioxus::prelude::Event<dioxus::html::MouseData>| {
                                                        e.prevent_default();
                                                        hold_long.set(true);
                                                        sess_sheet.set(Some((id.clone(), title.clone(), pinned)));
                                                    }
                                                },
                                                div { class: "t1",
                                                    if pinned { span { class: "pin-mark", "PIN" } }
                                                    "{title}"
                                                    span { class: "mode-tag rm", "远端" }
                                                }
                                                div { class: "t2", "{id_short} · 长按管理" }
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    }
                }

                // session action sheet (长按管理)
                if let Some((sid, stitle, pinned)) = sess_sheet() {
                    div {
                        id: "sess-sheet-bg",
                        onclick: move |_| sess_sheet.set(None),
                    }
                    div { id: "sess-sheet",
                        div { class: "ss-title", "管理 · {stitle}" }
                        button {
                            r#type: "button",
                            onclick: {
                                let sid = sid.clone();
                                move |_| {
                                    let pinned_next = !pinned;
                                    let sid = sid.clone();
                                    sess_sheet.set(None);
                                    spawn(async move {
                                        let body = json!({ "pinned": pinned_next });
                                        let _ = api::post_json(&format!("/api/mobile/sessions/{sid}/pin"), &body).await;
                                        let _ = refresh_all(app_state, mode_snap, chat_surface, approvals, evolutions, processes, notify_on, form_base).await;
                                    });
                                }
                            },
                            if pinned { "取消置顶" } else { "置顶" }
                        }
                        button {
                            r#type: "button",
                            onclick: {
                                let sid = sid.clone();
                                let stitle = stitle.clone();
                                move |_| {
                                    sess_sheet.set(None);
                                    sess_rename.set(Some((sid.clone(), stitle.clone())));
                                }
                            },
                            "编辑名称"
                        }
                        button {
                            r#type: "button",
                            class: "dan",
                            onclick: {
                                let sid = sid.clone();
                                let stitle = stitle.clone();
                                move |_| {
                                    let is_local = sid == LOCAL_SESSION_ID;
                                    sess_sheet.set(None);
                                    sess_confirm.set(Some((sid.clone(), stitle.clone(), is_local)));
                                }
                            },
                            if sid == LOCAL_SESSION_ID { "清空本机历史" } else { "删除远端会话" }
                        }
                        button {
                            r#type: "button",
                            class: "cancel",
                            onclick: move |_| sess_sheet.set(None),
                            "取消"
                        }
                    }
                }

                // delete confirm
                if let Some((sid, _stitle, is_local)) = sess_confirm() {
                    div {
                        id: "sess-sheet-bg",
                        onclick: move |_| sess_confirm.set(None),
                    }
                    div { id: "sess-confirm",
                        div { class: "ss-title",
                            if is_local { "清空本机历史？" } else { "删除远端会话？" }
                        }
                        div { class: "ss-body",
                            if is_local {
                                "将清空本机消息记录。本机为单线程通道，清空后无法撤销。"
                            } else {
                                "将从 PC 删除该远端会话。此操作不可撤销。"
                            }
                        }
                        div { class: "ops",
                            button {
                                class: "btn",
                                onclick: move |_| sess_confirm.set(None),
                                "取消"
                            }
                            button {
                                class: "btn pri dan",
                                onclick: {
                                    let sid = sid.clone();
                                    move |_| {
                                        sess_confirm.set(None);
                                        let sid = sid.clone();
                                        let is_local = sid == LOCAL_SESSION_ID;
                                        let mut toast = toast.clone();
                                        spawn(async move {
                                            match api::post_empty(&format!("/api/mobile/sessions/{sid}/delete")).await {
                                                Ok(_) => {
                                                    if is_local {
                                                        messages.set(vec![welcome_local()]);
                                                        chat_surface.set(ChatSurface::Local);
                                                        toast("已清空本机历史".into());
                                                    } else {
                                                        if active_session().as_deref() == Some(sid.as_str()) {
                                                            active_session.set(None);
                                                            messages.set(vec![welcome_remote()]);
                                                        }
                                                        toast("已删除远端会话".into());
                                                    }
                                                    let _ = refresh_all(app_state, mode_snap, chat_surface, approvals, evolutions, processes, notify_on, form_base).await;
                                                }
                                                Err(e) => toast(format!("删除失败: {e}")),
                                            }
                                        });
                                    }
                                },
                                if is_local { "确认清空" } else { "确认删除" }
                            }
                        }
                    }
                }

                // rename dialog
                if let Some((sid, draft)) = sess_rename() {
                    div {
                        id: "sess-sheet-bg",
                        onclick: move |_| sess_rename.set(None),
                    }
                    div { id: "sess-rename",
                        div { class: "lb", "会话名称" }
                        input {
                            class: "inp",
                            value: "{draft}",
                            oninput: move |e| {
                                if let Some((id, _)) = sess_rename() {
                                    sess_rename.set(Some((id, e.value())));
                                }
                            },
                        }
                        div { class: "ops",
                            button {
                                class: "btn",
                                onclick: move |_| sess_rename.set(None),
                                "取消"
                            }
                            button {
                                class: "btn pri",
                                onclick: move |_| {
                                    if let Some((id, title)) = sess_rename() {
                                        sess_rename.set(None);
                                        let mut toast = toast.clone();
                                        spawn(async move {
                                            let body = json!({ "title": title });
                                            match api::post_json(&format!("/api/mobile/sessions/{id}/rename"), &body).await {
                                                Ok(v) => {
                                                    let note = v.get("note").and_then(|x| x.as_str()).unwrap_or("已改名");
                                                    toast(note.into());
                                                    let _ = refresh_all(app_state, mode_snap, chat_surface, approvals, evolutions, processes, notify_on, form_base).await;
                                                }
                                                Err(e) => toast(format!("改名失败: {e}")),
                                            }
                                        });
                                    }
                                },
                                "保存"
                            }
                        }
                    }
                }

                // hidden file inputs — same ids
                input {
                    r#type: "file",
                    id: "file-input",
                    multiple: true,
                    accept: "image/*,audio/*,.pdf,.txt,.md,.json,.csv,.zip,.py,.ts,.tsx,.js,.rs",
                    style: "display:none",
                    onchange: move |e| {
                        // Dioxus file engine
                        if let Some(fe) = e.files() {
                            let files = fe.files();
                            let mut toast = toast.clone();
                            spawn(async move {
                                for name in files {
                                    attach_names.write().push(name.clone());
                                }
                                toast("已添加附件".into());
                            });
                        }
                    },
                }
                input {
                    r#type: "file",
                    id: "gallery-input",
                    accept: "image/*",
                    style: "display:none",
                    onchange: move |e| {
                        if let Some(fe) = e.files() {
                            let files = fe.files();
                            let mut toast = toast.clone();
                            spawn(async move {
                                for name in files {
                                    attach_names.write().push(name.clone());
                                }
                                toast("已添加相册图片".into());
                            });
                        }
                    },
                }

                div {
                    id: "toast",
                    class: if toast_show() { "show" } else { "" },
                    { toast_msg().unwrap_or_default() }
                }
            }
        }
    }
}

// ───────── data loaders ─────────

fn welcome_local() -> ChatMsg {
    ChatMsg {
        id: "w-local".into(),
        role: "assistant".into(),
        html: "<p><b>本机对话</b></p><p>在「我的 → LLM 设置」配置 <b>API Key 供应商</b> 后即可直接流式聊天。</p><p class=\"mut\">ChatGPT OAuth 请切换到顶栏「远端 Agent」。</p>".into(),
        who: "本机 · <span class=\"num\">LLM</span>".into(),
        streaming: false,
    }
}

fn welcome_remote() -> ChatMsg {
    ChatMsg {
        id: "w-remote".into(),
        role: "assistant".into(),
        html: "<p><b>远端 Agent</b></p><p>当前为 PC 上的 agent 会话：支持工具、审批与附件。</p><p class=\"mut\">返回本机请点顶栏「本机对话」。</p>".into(),
        who: "远端 Agent · <span class=\"num\">ready</span>".into(),
        streaming: false,
    }
}

fn session_title(st: &AppStateDto, active: &Option<String>) -> String {
    if let Some(id) = active {
        if let Some(s) = st.sessions.iter().find(|s| s.get("id").and_then(|x| x.as_str()) == Some(id.as_str())) {
            if let Some(t) = s.get("title").and_then(|x| x.as_str()) {
                return t.to_string();
            }
        }
        return "Takton 会话".into();
    }
    "Takton 会话".into()
}

async fn refresh_mode(surface: Signal<ChatSurface>, mut mode_snap: Signal<ModeSnap>) -> Result<(), String> {
    let s = surface().as_str();
    let v = api::post_json("/api/mobile/mode", &json!({ "surface": s })).await?;
    if let Some(m) = v.get("mode") {
        if let Ok(snap) = serde_json::from_value::<ModeSnap>(m.clone()) {
            mode_snap.set(snap);
        }
    }
    Ok(())
}

async fn load_approvals(
    mut approvals: Signal<Vec<Value>>,
    mut evolutions: Signal<Vec<Value>>,
    mut processes: Signal<Vec<Value>>,
) -> Result<(), String> {
    if let Ok(v) = api::get_json("/api/mobile/approvals").await {
        // Host returns { escalations, evolution } — map both (no invent split).
        let esc = v
            .get("escalations")
            .or_else(|| v.get("approvals"))
            .or_else(|| v.get("items"))
            .cloned()
            .unwrap_or(json!([]));
        let evo = v
            .get("evolution")
            .or_else(|| v.get("evolutions"))
            .cloned()
            .unwrap_or(json!([]));
        if let Some(arr) = esc.as_array() {
            approvals.set(arr.clone());
        }
        if let Some(arr) = evo.as_array() {
            evolutions.set(arr.clone());
        }
    }
    if let Ok(v) = api::get_json("/api/mobile/processes").await {
        let list = v
            .get("processes")
            .or_else(|| v.get("items"))
            .cloned()
            .unwrap_or(json!([]));
        if let Some(arr) = list.as_array() {
            processes.set(arr.clone());
        }
    }
    Ok(())
}

async fn refresh_all(
    mut app_state: Signal<AppStateDto>,
    mode_snap: Signal<ModeSnap>,
    chat_surface: Signal<ChatSurface>,
    approvals: Signal<Vec<Value>>,
    evolutions: Signal<Vec<Value>>,
    processes: Signal<Vec<Value>>,
    mut notify_on: Signal<bool>,
    mut form_base: Signal<String>,
) -> Result<(), String> {
    let v = api::get_json("/api/mobile/state").await?;
    let dto: AppStateDto = serde_json::from_value(v).map_err(|e| e.to_string())?;
    if !dto.base_url.is_empty() {
        form_base.set(dto.base_url.clone());
    }
    notify_on.set(dto.notify_approvals);
    app_state.set(dto);
    let _ = refresh_mode(chat_surface, mode_snap).await;
    let _ = load_approvals(approvals, evolutions, processes).await;
    Ok(())
}

async fn bootstrap(
    mut app_state: Signal<AppStateDto>,
    mode_snap: Signal<ModeSnap>,
    mut chat_surface: Signal<ChatSurface>,
    mut messages: Signal<Vec<ChatMsg>>,
    mut active_session: Signal<Option<String>>,
    mut form_base: Signal<String>,
    mut llm_base: Signal<String>,
    mut llm_model: Signal<String>,
    approvals: Signal<Vec<Value>>,
    evolutions: Signal<Vec<Value>>,
    processes: Signal<Vec<Value>>,
    notify_on: Signal<bool>,
    mut island_kind: Signal<String>,
    mut island_html: Signal<String>,
) -> Result<(), String> {
    let _ = refresh_all(
        app_state,
        mode_snap,
        chat_surface,
        approvals,
        evolutions,
        processes,
        notify_on,
        form_base,
    )
    .await;
    // local config
    if let Ok(v) = api::get_json("/api/mobile/local/config").await {
        if let Some(c) = v.get("config") {
            if let Some(b) = c.get("base_url").and_then(|x| x.as_str()) {
                llm_base.set(b.into());
            }
            if let Some(m) = c.get("model").and_then(|x| x.as_str()) {
                llm_model.set(m.into());
            }
        }
    }
    // default surface
    let want_remote = storage_get("takton-chat-mode").as_deref() == Some("remote")
        && app_state().authenticated;
    if want_remote {
        chat_surface.set(ChatSurface::Remote);
        ensure_remote_session(active_session, messages, app_state).await;
        island_kind.set("conn".into());
        island_html.set("<b>远端</b>&nbsp;Agent".into());
    } else {
        chat_surface.set(ChatSurface::Local);
        load_local_msgs(messages).await;
        island_kind.set("local".into());
        let m = app_state()
            .local_llm
            .as_ref()
            .and_then(|v| v.get("model"))
            .and_then(|x| x.as_str())
            .unwrap_or("未配置")
            .to_string();
        island_html.set(format!("<b>本机</b>&nbsp;{}", esc(&m)));
    }
    let _ = refresh_mode(chat_surface, mode_snap).await;
    Ok(())
}

async fn load_local_msgs(mut messages: Signal<Vec<ChatMsg>>) {
    match api::get_json("/api/mobile/local/history").await {
        Ok(v) => {
            let msgs = v
                .get("messages")
                .and_then(|m| m.as_array())
                .cloned()
                .unwrap_or_default();
            if msgs.is_empty() {
                messages.set(vec![welcome_local()]);
                return;
            }
            let mut out = vec![];
            for (i, m) in msgs.iter().enumerate() {
                let role = m.get("role").and_then(|x| x.as_str()).unwrap_or("assistant");
                let content = m.get("content").and_then(|x| x.as_str()).unwrap_or("");
                out.push(ChatMsg {
                    id: format!("h{i}"),
                    role: role.into(),
                    html: if role == "user" {
                        esc(content)
                    } else {
                        md_basic(content)
                    },
                    who: "本机 · <span class=\"num\">LLM</span>".into(),
                    streaming: false,
                });
            }
            messages.set(out);
        }
        Err(_) => messages.set(vec![welcome_local()]),
    }
}

async fn load_remote_msgs(id: &str, mut messages: Signal<Vec<ChatMsg>>) {
    let _ = api::post_empty(&format!("/api/mobile/sessions/{id}/open")).await;
    match api::get_json(&format!("/api/mobile/sessions/{id}/messages")).await {
        Ok(v) => {
            let msgs = v
                .get("messages")
                .and_then(|m| m.as_array())
                .cloned()
                .unwrap_or_default();
            if msgs.is_empty() {
                messages.set(vec![welcome_remote()]);
                return;
            }
            let mut out = vec![];
            for (i, m) in msgs.iter().enumerate() {
                let role = m.get("role").and_then(|x| x.as_str()).unwrap_or("assistant");
                let content = m
                    .get("content")
                    .or_else(|| m.get("text"))
                    .and_then(|x| x.as_str())
                    .unwrap_or("");
                out.push(ChatMsg {
                    id: format!("r{i}"),
                    role: role.into(),
                    html: if role == "user" {
                        esc(content)
                    } else {
                        md_basic(content)
                    },
                    who: "远端 Agent".into(),
                    streaming: false,
                });
            }
            messages.set(out);
        }
        Err(_) => messages.set(vec![welcome_remote()]),
    }
}

async fn ensure_remote_session(
    mut active_session: Signal<Option<String>>,
    mut messages: Signal<Vec<ChatMsg>>,
    app_state: Signal<AppStateDto>,
) {
    if let Some(id) = active_session.clone()() {
        load_remote_msgs(&id, messages).await;
        return;
    }
    if let Some(s) = app_state().sessions.first() {
        if let Some(id) = s.get("id").and_then(|x| x.as_str()) {
            active_session.set(Some(id.to_string()));
            load_remote_msgs(id, messages).await;
            return;
        }
    }
    if let Ok(v) = api::post_empty("/api/mobile/sessions").await {
        if let Some(id) = v.pointer("/session/id").and_then(|x| x.as_str()) {
            active_session.set(Some(id.to_string()));
            messages.write().clear();
            messages.write().push(welcome_remote());
        }
    }
}

async fn stream_local(
    text: &str,
    aid: String,
    mut messages: Signal<Vec<ChatMsg>>,
    stream_cancel: Signal<bool>,
) -> Result<(), String> {
    // Use fetch streaming via web-sys for SSE from host
    let window = web_sys::window().ok_or("no window")?;
    // Host LocalChatBody expects `content` (also accepts legacy `message`)
    let body = json!({ "content": text }).to_string();

    let mut opts = web_sys::RequestInit::new();
    opts.set_method("POST");
    opts.set_body(&wasm_bindgen::JsValue::from_str(&body));
    let headers = web_sys::Headers::new().map_err(|e| format!("{e:?}"))?;
    headers
        .set("Content-Type", "application/json")
        .map_err(|e| format!("{e:?}"))?;
    opts.set_headers(&headers);

    let req = web_sys::Request::new_with_str_and_init("/api/mobile/local/chat", &opts)
        .map_err(|e| format!("{e:?}"))?;
    let resp_val = wasm_bindgen_futures::JsFuture::from(window.fetch_with_request(&req))
        .await
        .map_err(|e| format!("{e:?}"))?;
    let resp: web_sys::Response = resp_val.dyn_into().map_err(|_| "resp")?;
    if !resp.ok() {
        return Err(format!("local chat HTTP {}", resp.status()));
    }
    // Read full text then parse SSE lines. Host emits:
    //   event: delta  data: {"text":"..."}
    //   event: done   data: {"text": full}
    //   event: error  data: {"error":"..."}
    let text_val = wasm_bindgen_futures::JsFuture::from(resp.text().map_err(|e| format!("{e:?}"))?)
        .await
        .map_err(|e| format!("{e:?}"))?;
    if stream_cancel() {
        return Err("已停止".into());
    }
    let raw = text_val.as_string().unwrap_or_default();
    let mut acc = String::new();
    let mut cur_event = String::new();
    for line in raw.lines() {
        if stream_cancel() {
            return Err("已停止".into());
        }
        let line = line.trim_end();
        if line.is_empty() {
            cur_event.clear();
            continue;
        }
        if let Some(ev) = line.strip_prefix("event:") {
            cur_event = ev.trim().to_string();
            continue;
        }
        if let Some(data) = line.strip_prefix("data:") {
            let data = data.trim();
            if data == "[DONE]" || data.is_empty() {
                continue;
            }
            if let Ok(v) = serde_json::from_str::<Value>(data) {
                if cur_event == "error" || v.get("error").is_some() {
                    let err = v
                        .get("error")
                        .and_then(|x| x.as_str())
                        .unwrap_or("local LLM error");
                    return Err(err.into());
                }
                if let Some(d) = v
                    .get("text")
                    .or_else(|| v.pointer("/choices/0/delta/content"))
                    .or_else(|| v.get("content"))
                    .or_else(|| v.get("delta"))
                    .and_then(|x| x.as_str())
                {
                    if cur_event == "done" {
                        acc = d.to_string();
                    } else {
                        // delta events carry incremental text
                        if cur_event == "delta" || cur_event.is_empty() {
                            // Host sends full-delta chunks as plain text pieces
                            acc.push_str(d);
                        } else {
                            acc.push_str(d);
                        }
                    }
                    let html = md_basic(&acc);
                    let mut ms = messages.write();
                    if let Some(m) = ms.iter_mut().find(|m| m.id == aid) {
                        m.html = html;
                    }
                }
            }
        }
    }
    if acc.is_empty() && !raw.is_empty() {
        // non-SSE JSON fallback
        if let Ok(v) = serde_json::from_str::<Value>(&raw) {
            if let Some(err) = v.get("error").and_then(|x| x.as_str()) {
                return Err(err.into());
            }
            if let Some(c) = v
                .get("text")
                .or_else(|| v.pointer("/choices/0/message/content"))
                .or_else(|| v.get("content"))
                .and_then(|x| x.as_str())
            {
                acc = c.to_string();
            }
        }
        let mut ms = messages.write();
        if let Some(m) = ms.iter_mut().find(|m| m.id == aid) {
            m.html = md_basic(&acc);
        }
    }
    Ok(())
}

async fn stream_remote(
    text: &str,
    session: Option<String>,
    aid: String,
    mut messages: Signal<Vec<ChatMsg>>,
    stream_cancel: Signal<bool>,
) -> Result<(), String> {
    let sid = match session {
        Some(s) => s,
        None => return Err("无远端会话".into()),
    };
    // Prefer WS if available; fallback: open session + note
    let _ = api::post_empty(&format!("/api/mobile/sessions/{sid}/open")).await;

    // Use browser WebSocket to host /api/mobile/ws
    let loc = web_sys::window()
        .ok_or("no window")?
        .location();
    let proto = if loc.protocol().unwrap_or_default().starts_with("https") {
        "wss"
    } else {
        "ws"
    };
    let host = loc.host().map_err(|e| format!("{e:?}"))?;
    let url = format!("{proto}://{host}/api/mobile/ws");
    let ws = web_sys::WebSocket::new(&url).map_err(|e| format!("{e:?}"))?;
    ws.set_binary_type(web_sys::BinaryType::Arraybuffer);

    // Wait open
    {
        let (tx, rx) = futures::channel::oneshot::channel::<Result<(), String>>();
        let mut tx = Some(tx);
        let onopen = wasm_bindgen::closure::Closure::wrap(Box::new(move || {
            if let Some(tx) = tx.take() {
                let _ = tx.send(Ok(()));
            }
        }) as Box<dyn FnMut()>);
        ws.set_onopen(Some(onopen.as_ref().unchecked_ref()));
        onopen.forget();
        // timeout
        let t = gloo_timers::future::TimeoutFuture::new(5000);
        futures::pin_mut!(t);
        futures::pin_mut!(rx);
        match futures::future::select(rx, t).await {
            futures::future::Either::Left((Ok(Ok(())), _)) => {}
            _ => return Err("WS 连接超时".into()),
        }
    }

    let payload = json!({
        "type": "chat",
        "session_id": sid,
        "content": text,
    });
    ws.send_with_str(&payload.to_string())
        .map_err(|e| format!("{e:?}"))?;

    // Collect messages for a while
    let (tx, mut rx) = futures::channel::mpsc::unbounded::<String>();
    let onmessage = {
        let tx = tx.clone();
        wasm_bindgen::closure::Closure::wrap(Box::new(move |e: web_sys::MessageEvent| {
            if let Some(s) = e.data().as_string() {
                let _ = tx.unbounded_send(s);
            }
        }) as Box<dyn FnMut(_)>)
    };
    ws.set_onmessage(Some(onmessage.as_ref().unchecked_ref()));
    onmessage.forget();

    let mut acc = String::new();
    let deadline = gloo_timers::future::TimeoutFuture::new(90_000);
    futures::pin_mut!(deadline);
    loop {
        if stream_cancel() {
            let _ = ws.close();
            return Err("已停止".into());
        }
        let next = futures::future::select(rx.next(), &mut deadline);
        match next.await {
            futures::future::Either::Left((Some(s), _)) => {
                if let Ok(v) = serde_json::from_str::<Value>(&s) {
                    let ty = v.get("type").and_then(|x| x.as_str()).unwrap_or("");
                    if ty == "stream_delta" || ty == "delta" || ty == "assistant_delta" {
                        if let Some(d) = v
                            .get("delta")
                            .or_else(|| v.get("content"))
                            .or_else(|| v.get("text"))
                            .and_then(|x| x.as_str())
                        {
                            acc.push_str(d);
                            let html = md_basic(&acc);
                            let mut ms = messages.write();
                            if let Some(m) = ms.iter_mut().find(|m| m.id == aid) {
                                m.html = html;
                            }
                        }
                    } else if ty == "done" || ty == "chat_done" || ty == "error" {
                        if ty == "error" {
                            let err = v
                                .get("error")
                                .or_else(|| v.get("message"))
                                .and_then(|x| x.as_str())
                                .unwrap_or("远端错误");
                            return Err(err.into());
                        }
                        break;
                    } else if let Some(d) = v.get("content").and_then(|x| x.as_str()) {
                        acc.push_str(d);
                        let html = md_basic(&acc);
                        let mut ms = messages.write();
                        if let Some(m) = ms.iter_mut().find(|m| m.id == aid) {
                            m.html = html;
                        }
                    }
                }
            }
            futures::future::Either::Left((None, _)) => break,
            futures::future::Either::Right(_) => break,
        }
    }
    let _ = ws.close();
    Ok(())
}

async fn record_short_voice() -> Result<web_sys::File, String> {
    let stream = media::open_mic_stream().await?;
    // Record ~1.2s with MediaRecorder
    let recorder = web_sys::MediaRecorder::new_with_media_stream(&stream)
        .map_err(|e| format!("{e:?}"))?;
    let chunks: std::rc::Rc<std::cell::RefCell<Vec<web_sys::Blob>>> =
        std::rc::Rc::new(std::cell::RefCell::new(vec![]));
    {
        let chunks = chunks.clone();
        let ondata = wasm_bindgen::closure::Closure::wrap(Box::new(move |e: web_sys::BlobEvent| {
            if let Some(b) = e.data() {
                chunks.borrow_mut().push(b);
            }
        }) as Box<dyn FnMut(_)>);
        recorder.set_ondataavailable(Some(ondata.as_ref().unchecked_ref()));
        ondata.forget();
    }
    recorder.start_with_time_slice(100).map_err(|e| format!("{e:?}"))?;
    gloo_timers::future::TimeoutFuture::new(1200).await;
    recorder.stop().map_err(|e| format!("{e:?}"))?;
    gloo_timers::future::TimeoutFuture::new(200).await;
    media::stop_stream(&stream);
    let parts = js_sys::Array::new();
    for b in chunks.borrow().iter() {
        parts.push(b);
    }
    let mut props = web_sys::BlobPropertyBag::new();
    props.set_type("audio/webm");
    let blob = web_sys::Blob::new_with_blob_sequence_and_options(&parts, &props)
        .map_err(|e| format!("{e:?}"))?;
    let mut fprops = web_sys::FilePropertyBag::new();
    fprops.set_type("audio/webm");
    web_sys::File::new_with_blob_sequence_and_options(
        &js_sys::Array::of1(&blob),
        &format!("voice_{}.webm", js_sys::Date::now() as u64),
        &fprops,
    )
    .map_err(|e| format!("{e:?}"))
}
