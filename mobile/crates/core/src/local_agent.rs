//! Phone-local agent loop — commercial-grade subset of Codex/Doubao-class agents:
//! tools · skills · MCP · context compression · multi-format tool calls · doom guard.

use crate::context_compress::{compress_messages, estimate_messages, CompressReport};
use crate::error::{Error, Result};
use crate::local_llm::{
    model_supports_vision, LocalChatHistory, LocalChatMessage, LocalImagePart, LocalLlmProfile,
    LocalLlmService,
};
use crate::local_tools::ToolRuntime;
use crate::tool_format::{
    parse_text_tool_calls, repair_json_args, strip_tool_markup, TEXT_TOOL_PROTOCOL,
};
use futures_util::StreamExt;
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::collections::HashMap;
use std::sync::Arc;

const SYSTEM_PROMPT: &str = r#"你是 Takton 手机本机 Agent（对标 Codex / 豆包助手的本机能力）。
你可以使用工具，并组合调用。规则：
1. 实时事实/新闻/不确定知识 → web_search；已知 URL → web_fetch 或 http_get(API)。
2. 多步大任务 → 先 task_plan.set 拆解，再逐步执行，完成后更新计划。
3. 若消息已含图片且你是视觉模型，请直接看图回答；无视觉时才调用 ocr_image。朗读 → voice_speak；算术 → calculator；时间 → get_datetime。
4. 可复用方法论 → list_skills / load_skill；远程扩展 → mcp_list / mcp_call 或 mcp__server__tool。
5. 短备忘 → memory_note。工具结果到了就直接答，禁止重复同一调用。
6. 不要编造工具结果或链接。中文优先，必要时附来源。
7. 上下文可能被压缩；压缩摘要中的内容是事实压缩，不是新推断。"#;

#[derive(Debug, Clone)]
pub enum AgentEvent {
    Status { detail: String },
    Delta { text: String },
    ToolStart { id: String, name: String, args: Value },
    ToolEnd {
        id: String,
        name: String,
        preview: String,
        ok: bool,
    },
    Compress { report: String },
    Done { content: String },
    Error { message: String },
}

#[derive(Debug, Clone)]
struct ToolCallAcc {
    id: String,
    name: String,
    arguments: String,
}

#[derive(Default)]
struct DoomGuard {
    last_fp: Option<String>,
    streak: u8,
    threshold: u8,
}

impl DoomGuard {
    fn new(threshold: u8) -> Self {
        Self {
            last_fp: None,
            streak: 0,
            threshold: threshold.max(2),
        }
    }
    fn record(&mut self, name: &str, args: &str) -> bool {
        let fp = fingerprint(name, args);
        if self.last_fp.as_deref() == Some(fp.as_str()) {
            self.streak = self.streak.saturating_add(1);
        } else {
            self.last_fp = Some(fp);
            self.streak = 1;
        }
        self.streak >= self.threshold
    }
}

pub struct LocalAgent {
    llm: Arc<LocalLlmService>,
    tools: ToolRuntime,
}

impl LocalAgent {
    pub fn new(llm: Arc<LocalLlmService>, tools: ToolRuntime) -> Self {
        Self { llm, tools }
    }

    pub fn tools(&self) -> &ToolRuntime {
        &self.tools
    }

    pub async fn run<F>(
        &self,
        profile: &LocalLlmProfile,
        hist: &mut LocalChatHistory,
        user_text: &str,
        images: &[LocalImagePart],
        mut on_event: F,
    ) -> Result<String>
    where
        F: FnMut(AgentEvent),
    {
        if !profile.is_ready() {
            return Err(Error::Msg("本机 LLM 未配置完整".into()));
        }
        // Clear previous stop only once per user turn (not per tool hop).
        self.llm.reset_cancel();

        let cfg = self.tools.load_config();
        let soft = cfg.context_soft_tokens as usize;
        let hard = cfg.context_hard_tokens as usize;
        let vision = model_supports_vision(profile);
        let img_note = if !images.is_empty() {
            if vision {
                on_event(AgentEvent::Status {
                    detail: format!("多模态看图 · {} 张", images.len()),
                });
                format!("

[本轮附带 {} 张图片，请直接视觉理解，无需 OCR 除非用户只要文字提取]", images.len())
            } else {
                on_event(AgentEvent::Status {
                    detail: "当前模型无视觉 · 可用 ocr_image".into(),
                });
                format!("

[本轮附带 {} 张图片；当前模型可能无视觉，如需读字请调用 ocr_image]", images.len())
            }
        } else {
            String::new()
        };
        let user_text_full = format!("{user_text}{img_note}");

        // Skills auto-match
        let mut skill_block = String::new();
        if cfg.enable_skills {
            let hits = self.tools.skills().match_for_prompt(&user_text_full);
            if !hits.is_empty() {
                skill_block = self.tools.skills().prompt_block(&hits);
                on_event(AgentEvent::Status {
                    detail: format!(
                        "Skills · {}",
                        hits.iter()
                            .map(|s| s.meta.name.as_str())
                            .collect::<Vec<_>>()
                            .join(", ")
                    ),
                });
            }
        }

        // ── ChatGPT OAuth / non-FC path with text tools ──
        if profile.is_chatgpt_oauth() {
            return self
                .run_text_tool_loop(
                    profile,
                    hist,
                    &user_text_full,
                    images,
                    &skill_block,
                    soft,
                    hard,
                    cfg.enable_text_tools,
                    &mut on_event,
                )
                .await;
        }

        let max_iter = cfg.max_iterations.clamp(2, 16) as usize;

        let mut messages: Vec<Value> = Vec::new();
        let mut sys = SYSTEM_PROMPT.to_string();
        if !skill_block.is_empty() {
            sys.push_str("\n\n");
            sys.push_str(&skill_block);
        }
        if cfg.enable_text_tools {
            sys.push_str(TEXT_TOOL_PROTOCOL);
        }
        messages.push(json!({"role": "system", "content": sys}));

        let hist_slice = if hist.messages.len() > 40 {
            &hist.messages[hist.messages.len() - 40..]
        } else {
            hist.messages.as_slice()
        };
        for m in hist_slice {
            messages.push(m.to_openai_json());
        }
        let user_msg = LocalChatMessage {
            role: "user".into(),
            content: user_text_full.clone(),
            images: if images.is_empty() {
                None
            } else {
                Some(images.to_vec())
            },
            ..Default::default()
        };
        messages.push(user_msg.to_openai_json());

        // History: keep text only (strip heavy base64 after clone)
        let mut hist_user = user_msg.clone();
        hist_user.strip_inline_images();
        hist.messages.push(hist_user);

        // Pre-search intent
        if let Some(q) = extract_search_query(&user_text_full) {
            self.push_tool_pair(
                &mut messages,
                hist,
                "pre_search",
                "web_search",
                json!({"query": q, "max_results": 6}),
                &mut on_event,
            )
            .await;
        }

        let schemas = self.tools.all_tool_schemas().await;
        let mut doom = DoomGuard::new(3);
        let mut force_final = false;
        let mut final_text = String::new();

        for turn in 0..max_iter {
            if self.llm.is_cancelled() {
                on_event(AgentEvent::Error {
                    message: "已停止".into(),
                });
                break;
            }

            // Compress before each model call when needed
            let rep = compress_messages(&mut messages, soft, hard);
            if rep.compressed {
                on_event(AgentEvent::Compress {
                    report: format_compress(&rep),
                });
                on_event(AgentEvent::Status {
                    detail: format_compress(&rep),
                });
            }

            on_event(AgentEvent::Status {
                detail: format!(
                    "思考中 · {}/{} · ~{}tok",
                    turn + 1,
                    max_iter,
                    estimate_messages(&messages)
                ),
            });

            let tools_arg = if force_final || turn + 1 == max_iter {
                None
            } else {
                Some(schemas.as_slice())
            };

            let (content, mut tool_calls) = self
                .complete_turn(profile, &messages, tools_arg, cfg.enable_text_tools, &mut on_event)
                .await?;

            // Also parse text tools from content if native FC empty
            if tool_calls.is_empty() && cfg.enable_text_tools {
                for p in parse_text_tool_calls(&content) {
                    tool_calls.push(ToolCallAcc {
                        id: p.id,
                        name: p.name,
                        arguments: p.arguments,
                    });
                }
            }

            // Normalize arguments
            for tc in &mut tool_calls {
                tc.arguments = repair_json_args(&tc.arguments);
            }

            if tool_calls.is_empty() {
                final_text = strip_tool_markup(&content);
                break;
            }

            let mut trip = false;
            for tc in &tool_calls {
                if doom.record(&tc.name, &tc.arguments) {
                    trip = true;
                }
            }
            if trip {
                on_event(AgentEvent::Status {
                    detail: "检测到重复工具调用 · 强制终答".into(),
                });
                force_final = true;
                messages.push(json!({
                    "role": "system",
                    "content": "【熔断】请停止重复调用工具，基于已有结果直接回答用户。不要编造未返回的数据。"
                }));
            }

            let visible = strip_tool_markup(&content);
            let assistant_tcs: Vec<Value> = tool_calls
                .iter()
                .map(|tc| {
                    json!({
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.name,
                            "arguments": tc.arguments,
                        }
                    })
                })
                .collect();
            messages.push(json!({
                "role": "assistant",
                "content": if visible.is_empty() { Value::Null } else { json!(visible) },
                "tool_calls": assistant_tcs,
            }));
            hist.messages.push(LocalChatMessage {
                role: "assistant".into(),
                content: visible,
                tool_calls: Some(json!(tool_calls
                    .iter()
                    .map(|tc| json!({
                        "id": tc.id,
                        "name": tc.name,
                        "arguments": tc.arguments,
                    }))
                    .collect::<Vec<_>>())),
                ..Default::default()
            });

            for tc in &tool_calls {
                let args: Value = serde_json::from_str(&tc.arguments).unwrap_or(json!({}));
                on_event(AgentEvent::ToolStart {
                    id: tc.id.clone(),
                    name: tc.name.clone(),
                    args: args.clone(),
                });
                let result = self.tools.dispatch(&tc.name, &args).await;
                let ok = !result.starts_with("[tool_error]");
                let preview: String = result.chars().take(200).collect();
                on_event(AgentEvent::ToolEnd {
                    id: tc.id.clone(),
                    name: tc.name.clone(),
                    preview,
                    ok,
                });
                // Bound each tool result in-flight (compression will refine later)
                let result_for_ctx = if result.chars().count() > 6000 {
                    let h: String = result.chars().take(5000).collect();
                    format!("{h}\n…[truncated]")
                } else {
                    result.clone()
                };
                messages.push(json!({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": result_for_ctx,
                }));
                hist.messages.push(LocalChatMessage {
                    role: "tool".into(),
                    content: result,
                    tool_call_id: Some(tc.id.clone()),
                    name: Some(tc.name.clone()),
                    ..Default::default()
                });
            }
        }

        if final_text.trim().is_empty() && !self.llm.is_cancelled() {
            on_event(AgentEvent::Status {
                detail: "宽限终答".into(),
            });
            let _ = compress_messages(&mut messages, soft, hard);
            let (content, _) = self
                .complete_turn(profile, &messages, None, false, &mut on_event)
                .await?;
            final_text = strip_tool_markup(&content);
        }
        if self.llm.is_cancelled() {
            if final_text.trim().is_empty() {
                final_text = "（已停止）".into();
            }
        }

        if final_text.trim().is_empty() {
            final_text = "（本轮无文本输出）工具已执行，请换个说法再试。".into();
        }

        hist.messages.push(LocalChatMessage {
            role: "assistant".into(),
            content: final_text.clone(),
            ..Default::default()
        });
        if hist.messages.len() > 100 {
            let drain = hist.messages.len() - 100;
            hist.messages.drain(0..drain);
        }
        let _ = self.llm.save_history(hist);
        on_event(AgentEvent::Done {
            content: final_text.clone(),
        });
        Ok(final_text)
    }

    /// Codex / non-FC: text tool protocol + optional pre-search.
    async fn run_text_tool_loop<F>(
        &self,
        profile: &LocalLlmProfile,
        hist: &mut LocalChatHistory,
        user_text: &str,
        images: &[LocalImagePart],
        skill_block: &str,
        soft: usize,
        hard: usize,
        enable_text_tools: bool,
        on_event: &mut F,
    ) -> Result<String>
    where
        F: FnMut(AgentEvent),
    {
        on_event(AgentEvent::Status {
            detail: if enable_text_tools {
                "文本工具协议 · Codex 兼容".into()
            } else {
                "直连对话".into()
            },
        });

        let mut working: Vec<LocalChatMessage> = Vec::new();
        let mut sys = SYSTEM_PROMPT.to_string();
        if !skill_block.is_empty() {
            sys.push_str("\n\n");
            sys.push_str(skill_block);
        }
        if enable_text_tools {
            sys.push_str(TEXT_TOOL_PROTOCOL);
            // include tool names for Codex
            let names: Vec<String> = ToolRuntime::tool_schemas()
                .iter()
                .filter_map(|t| {
                    t.pointer("/function/name")
                        .and_then(|n| n.as_str())
                        .map(|s| s.to_string())
                })
                .collect();
            sys.push_str(&format!("\n可用工具: {}\n", names.join(", ")));
        }
        working.push(LocalChatMessage {
            role: "system".into(),
            content: sys,
            ..Default::default()
        });
        let slice = if hist.messages.len() > 30 {
            &hist.messages[hist.messages.len() - 30..]
        } else {
            hist.messages.as_slice()
        };
        working.extend(slice.iter().cloned());
        let user_msg = LocalChatMessage {
            role: "user".into(),
            content: user_text.into(),
            images: if images.is_empty() {
                None
            } else {
                Some(images.to_vec())
            },
            ..Default::default()
        };
        working.push(user_msg.clone());

        let mut hist_user = user_msg;
        hist_user.strip_inline_images();
        hist.messages.push(hist_user);

        if let Some(q) = extract_search_query(user_text) {
            on_event(AgentEvent::ToolStart {
                id: "pre_search".into(),
                name: "web_search".into(),
                args: json!({"query": q}),
            });
            let result = self
                .tools
                .dispatch("web_search", &json!({"query": q, "max_results": 6}))
                .await;
            let ok = !result.starts_with("[tool_error]") && !result.contains("(no results)");
            on_event(AgentEvent::ToolEnd {
                id: "pre_search".into(),
                name: "web_search".into(),
                preview: result.chars().take(200).collect(),
                ok,
            });
            working.push(LocalChatMessage {
                role: "system".into(),
                content: format!("web_search 结果：\n{result}"),
                ..Default::default()
            });
        }

        let mut final_text = String::new();
        let max_rounds = 6usize;
        for round in 0..max_rounds {
            if self.llm.is_cancelled() {
                break;
            }
            // Convert to OpenAI values for compression estimate on parallel path
            let mut as_vals: Vec<Value> = working.iter().map(|m| m.to_openai_json()).collect();
            let rep = compress_messages(&mut as_vals, soft, hard);
            if rep.compressed {
                on_event(AgentEvent::Compress {
                    report: format_compress(&rep),
                });
                // rebuild working from compressed values (simplified: keep working, trim tool-like systems)
                if working.len() > 24 {
                    let keep = working.split_off(working.len() - 20);
                    let head = working[0].clone(); // system
                    working.clear();
                    working.push(head);
                    working.push(LocalChatMessage {
                        role: "system".into(),
                        content: format_compress(&rep),
                        ..Default::default()
                    });
                    working.extend(keep);
                }
            }

            on_event(AgentEvent::Status {
                detail: format!("生成中 · 文本工具轮 {}/{}", round + 1, max_rounds),
            });

            let full = self
                .llm
                .stream_chat(profile, &working, |d| {
                    on_event(AgentEvent::Delta {
                        text: d.to_string(),
                    });
                })
                .await?;

            let calls = if enable_text_tools {
                parse_text_tool_calls(&full)
            } else {
                vec![]
            };

            if calls.is_empty() || round + 1 == max_rounds {
                final_text = strip_tool_markup(&full);
                working.push(LocalChatMessage {
                    role: "assistant".into(),
                    content: final_text.clone(),
                    ..Default::default()
                });
                break;
            }

            working.push(LocalChatMessage {
                role: "assistant".into(),
                content: full.clone(),
                ..Default::default()
            });
            hist.messages.push(LocalChatMessage {
                role: "assistant".into(),
                content: full.clone(),
                ..Default::default()
            });

            let mut tool_reports = Vec::new();
            for (i, c) in calls.iter().enumerate() {
                let args: Value =
                    serde_json::from_str(&repair_json_args(&c.arguments)).unwrap_or(json!({}));
                on_event(AgentEvent::ToolStart {
                    id: c.id.clone(),
                    name: c.name.clone(),
                    args: args.clone(),
                });
                let result = self.tools.dispatch(&c.name, &args).await;
                let ok = !result.starts_with("[tool_error]");
                on_event(AgentEvent::ToolEnd {
                    id: c.id.clone(),
                    name: c.name.clone(),
                    preview: result.chars().take(200).collect(),
                    ok,
                });
                tool_reports.push(format!(
                    "[{i}] {} => {}",
                    c.name,
                    result.chars().take(4000).collect::<String>()
                ));
                hist.messages.push(LocalChatMessage {
                    role: "tool".into(),
                    content: result,
                    tool_call_id: Some(c.id.clone()),
                    name: Some(c.name.clone()),
                    ..Default::default()
                });
            }
            working.push(LocalChatMessage {
                role: "user".into(),
                content: format!(
                    "工具结果（请基于此继续；若已足够则直接给最终答案，勿再编造调用）：\n{}",
                    tool_reports.join("\n\n")
                ),
                ..Default::default()
            });
        }

        if final_text.trim().is_empty() {
            final_text = "（无模型输出）".into();
        }
        hist.messages.push(LocalChatMessage {
            role: "assistant".into(),
            content: final_text.clone(),
            ..Default::default()
        });
        let _ = self.llm.save_history(hist);
        on_event(AgentEvent::Done {
            content: final_text.clone(),
        });
        Ok(final_text)
    }

    async fn push_tool_pair<F>(
        &self,
        messages: &mut Vec<Value>,
        hist: &mut LocalChatHistory,
        id: &str,
        name: &str,
        args: Value,
        on_event: &mut F,
    ) where
        F: FnMut(AgentEvent),
    {
        on_event(AgentEvent::Status {
            detail: format!("工具 · {name}"),
        });
        on_event(AgentEvent::ToolStart {
            id: id.into(),
            name: name.into(),
            args: args.clone(),
        });
        let result = self.tools.dispatch(name, &args).await;
        let ok = !result.starts_with("[tool_error]") && !result.contains("(no results)");
        on_event(AgentEvent::ToolEnd {
            id: id.into(),
            name: name.into(),
            preview: result.chars().take(200).collect(),
            ok,
        });
        let args_s = args.to_string();
        messages.push(json!({
            "role": "assistant",
            "content": null,
            "tool_calls": [{
                "id": id,
                "type": "function",
                "function": { "name": name, "arguments": args_s }
            }]
        }));
        messages.push(json!({
            "role": "tool",
            "tool_call_id": id,
            "content": result,
        }));
        hist.messages.push(LocalChatMessage {
            role: "assistant".into(),
            content: String::new(),
            tool_calls: Some(json!([{
                "id": id,
                "name": name,
                "arguments": args.to_string(),
            }])),
            ..Default::default()
        });
        hist.messages.push(LocalChatMessage {
            role: "tool".into(),
            content: result,
            tool_call_id: Some(id.into()),
            name: Some(name.into()),
            ..Default::default()
        });
    }

    async fn complete_turn<F>(
        &self,
        profile: &LocalLlmProfile,
        messages: &[Value],
        tools: Option<&[Value]>,
        _enable_text_tools: bool,
        on_event: &mut F,
    ) -> Result<(String, Vec<ToolCallAcc>)>
    where
        F: FnMut(AgentEvent),
    {
        // Do NOT reset_cancel here — would clear user stop mid multi-turn loop.
        if self.llm.is_cancelled() {
            return Err(crate::error::Error::Msg("已停止".into()));
        }
        let url = profile.completions_url_pub()?;
        let mut body = json!({
            "model": profile.model,
            "messages": messages,
            "stream": true,
        });
        if let Some(t) = tools {
            body["tools"] = json!(t);
            body["tool_choice"] = json!("auto");
        }

        let resp = self
            .llm
            .http()
            .post(url)
            .bearer_auth(profile.api_key.trim())
            .header("Accept", "text/event-stream")
            .json(&body)
            .send()
            .await
            .map_err(|e| Error::Network(e.to_string()))?;
        let status = resp.status();
        if !status.is_success() {
            let text = resp.text().await.unwrap_or_default();
            if tools.is_some()
                && (text.contains("tools")
                    || text.contains("tool_choice")
                    || status.as_u16() == 400)
            {
                return Box::pin(self.complete_turn(profile, messages, None, true, on_event)).await;
            }
            return Err(Error::http(status.as_u16(), text));
        }

        let mut content = String::new();
        let mut tcs: HashMap<u32, ToolCallAcc> = HashMap::new();
        let mut stream = resp.bytes_stream();
        let mut buf = String::new();

        while let Some(item) = stream.next().await {
            if self.llm.is_cancelled() {
                break;
            }
            let chunk = item.map_err(|e| Error::Network(e.to_string()))?;
            buf.push_str(&String::from_utf8_lossy(&chunk));
            while let Some(pos) = buf.find('\n') {
                let line = buf[..pos].trim_end_matches('\r').to_string();
                buf = buf[pos + 1..].to_string();
                if line.is_empty() || line.starts_with(':') {
                    continue;
                }
                let data = if let Some(rest) = line.strip_prefix("data:") {
                    rest.trim()
                } else {
                    continue;
                };
                if data == "[DONE]" {
                    break;
                }
                let Ok(v) = serde_json::from_str::<Value>(data) else {
                    continue;
                };
                let choice = v.pointer("/choices/0").cloned().unwrap_or(Value::Null);
                let delta = choice.get("delta").cloned().unwrap_or(Value::Null);

                if let Some(t) = delta.get("content").and_then(|c| c.as_str()) {
                    if !t.is_empty() {
                        content.push_str(t);
                        on_event(AgentEvent::Delta {
                            text: t.to_string(),
                        });
                    }
                }
                if let Some(arr) = delta.get("tool_calls").and_then(|x| x.as_array()) {
                    for tc in arr {
                        let idx = tc.get("index").and_then(|x| x.as_u64()).unwrap_or(0) as u32;
                        let entry = tcs.entry(idx).or_insert_with(|| ToolCallAcc {
                            id: String::new(),
                            name: String::new(),
                            arguments: String::new(),
                        });
                        if let Some(id) = tc.get("id").and_then(|x| x.as_str()) {
                            if !id.is_empty() {
                                entry.id = id.to_string();
                            }
                        }
                        if let Some(fn_) = tc.get("function") {
                            if let Some(n) = fn_.get("name").and_then(|x| x.as_str()) {
                                if !n.is_empty() {
                                    entry.name.push_str(n);
                                }
                            }
                            if let Some(a) = fn_.get("arguments").and_then(|x| x.as_str()) {
                                entry.arguments.push_str(a);
                            }
                        }
                    }
                }
            }
        }

        let mut list: Vec<ToolCallAcc> = tcs.into_values().collect();
        list.retain(|t| !t.name.trim().is_empty());
        for (i, t) in list.iter_mut().enumerate() {
            if t.id.is_empty() {
                t.id = format!("call_{i}");
            }
            t.arguments = repair_json_args(&t.arguments);
        }
        Ok((content, list))
    }
}

fn format_compress(r: &CompressReport) -> String {
    format!(
        "上下文压缩 {}→{} tok · drop={} trim_tools={}",
        r.before_tokens, r.after_tokens, r.dropped_messages, r.tool_results_trimmed
    )
}

fn fingerprint(name: &str, args: &str) -> String {
    let raw = format!("{name}|{}", args.split_whitespace().collect::<String>());
    let h = Sha256::digest(raw.as_bytes());
    format!(
        "{name}:{:x}",
        u64::from_be_bytes(h[0..8].try_into().unwrap_or([0; 8]))
    )
}

fn extract_search_query(user_text: &str) -> Option<String> {
    let t = user_text.trim();
    if t.is_empty() {
        return None;
    }
    let lower = t.to_lowercase();
    let keywords = [
        "搜索", "搜一下", "搜下", "查一下", "查下", "检索", "联网", "上网查", "调研",
        "最新", "新闻", "实时", "今天", "今日", "现在", "目前", "近期",
        "search", "look up", "lookup", "google", "bing", "what is the latest",
        "news", "today's", "current ",
    ];
    let hit = keywords.iter().any(|k| lower.contains(k) || t.contains(k));
    if !hit {
        return None;
    }
    let mut q = t.to_string();
    for p in [
        "请帮我搜索", "帮我搜索", "请搜索", "搜索一下", "搜索下", "搜索：", "搜索:", "搜索",
        "搜一下", "搜下", "查一下", "查下", "请查", "search for", "search ", "look up ", "lookup ",
        "调研一下", "调研",
    ] {
        if q.to_lowercase().starts_with(&p.to_lowercase()) {
            q = q[p.len()..].trim().to_string();
            break;
        }
        if let Some(pos) = q.find(p) {
            if pos < 8 {
                q = q[pos + p.len()..].trim().to_string();
                break;
            }
        }
    }
    let q = q
        .trim()
        .trim_matches(|c: char| c == '?' || c == '？' || c == '。')
        .to_string();
    if q.chars().count() < 1 {
        Some(t.to_string())
    } else {
        Some(q)
    }
}
