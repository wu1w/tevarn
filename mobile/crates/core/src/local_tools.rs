//! Phone-local tools — pruned from PC web_search / free_search + device helpers.
//! All execution in Rust.

use crate::error::{Error, Result};
use crate::storage::Store;
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::time::{SystemTime, UNIX_EPOCH};

const NOTES_FILE: &str = "local_agent_notes.json";
const AGENT_CFG: &str = "local_agent_config.json";
const UA: &str = "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 Chrome/122.0.0.0 Mobile Safari/537.36 TaktonMobile/1.0";
const EDGE_TTS_TOKEN: &str = "6A5AA1D4EAFF4E9FB37E23D68491D6F4";

#[derive(Clone)]
pub struct ToolRuntime {
    store: Store,
    http: reqwest::Client,
    skills: std::sync::Arc<crate::skills::SkillStore>,
    mcp: crate::mcp_client::SharedMcp,
}

#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
pub struct AgentConfig {
    #[serde(default)]
    pub azure_speech_key: String,
    #[serde(default = "default_region")]
    pub azure_speech_region: String,
    #[serde(default)]
    pub azure_vision_key: String,
    #[serde(default)]
    pub azure_vision_endpoint: String,
    #[serde(default = "default_max_iter")]
    pub max_iterations: u32,
    #[serde(default = "default_voice")]
    pub tts_voice: String,
    #[serde(default)]
    pub tavily_api_key: String,
    /// Soft context budget (tokens). Compress when exceeded.
    #[serde(default = "default_soft_tokens")]
    pub context_soft_tokens: u32,
    #[serde(default = "default_hard_tokens")]
    pub context_hard_tokens: u32,
    #[serde(default = "default_true")]
    pub enable_skills: bool,
    #[serde(default = "default_true")]
    pub enable_mcp: bool,
    #[serde(default = "default_true")]
    pub enable_text_tools: bool,
}

impl Default for AgentConfig {
    fn default() -> Self {
        Self {
            azure_speech_key: String::new(),
            azure_speech_region: default_region(),
            azure_vision_key: String::new(),
            azure_vision_endpoint: String::new(),
            max_iterations: default_max_iter(),
            tts_voice: default_voice(),
            tavily_api_key: String::new(),
            context_soft_tokens: default_soft_tokens(),
            context_hard_tokens: default_hard_tokens(),
            enable_skills: true,
            enable_mcp: true,
            enable_text_tools: true,
        }
    }
}

fn default_region() -> String {
    "eastasia".into()
}
fn default_max_iter() -> u32 {
    8
}
fn default_voice() -> String {
    "zh-CN-XiaoxiaoNeural".into()
}
fn default_soft_tokens() -> u32 {
    18_000
}
fn default_hard_tokens() -> u32 {
    28_000
}
fn default_true() -> bool {
    true
}

impl ToolRuntime {
    pub fn new(store: Store) -> Self {
        let skills = std::sync::Arc::new(crate::skills::SkillStore::new(&store));
        let mcp = std::sync::Arc::new(crate::mcp_client::McpHub::new(store.clone()));
        Self {
            store,
            http: reqwest::Client::builder()
                .timeout(std::time::Duration::from_secs(45))
                .user_agent(UA)
                .redirect(reqwest::redirect::Policy::limited(6))
                .build()
                .unwrap_or_else(|_| reqwest::Client::new()),
            skills,
            mcp,
        }
    }

    pub fn skills(&self) -> &crate::skills::SkillStore {
        &self.skills
    }

    pub fn mcp(&self) -> &crate::mcp_client::McpHub {
        &self.mcp
    }

    pub fn load_config(&self) -> AgentConfig {
        self.store
            .load_json(AGENT_CFG)
            .ok()
            .flatten()
            .unwrap_or_default()
    }

    pub fn save_config(&self, cfg: &AgentConfig) -> Result<()> {
        self.store.save_json(AGENT_CFG, cfg)
    }

    pub fn tool_schemas() -> Vec<Value> {
        vec![
            fn_tool(
                "web_search",
                "搜索互联网获取最新信息、新闻、事实。需要实时或外部知识时必须调用。",
                json!({
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "搜索关键词"},
                        "max_results": {"type": "integer", "description": "条数 1-8", "default": 5}
                    },
                    "required": ["query"]
                }),
            ),
            fn_tool(
                "web_fetch",
                "抓取并阅读指定 URL 的网页正文。在已知链接时使用。",
                json!({
                    "type": "object",
                    "properties": {
                        "url": {"type": "string"},
                        "max_chars": {"type": "integer", "default": 6000}
                    },
                    "required": ["url"]
                }),
            ),
            fn_tool(
                "get_datetime",
                "获取本机当前日期与时间。",
                json!({"type": "object", "properties": {}, "required": []}),
            ),
            fn_tool(
                "calculator",
                "计算数学表达式（四则运算与括号）。",
                json!({
                    "type": "object",
                    "properties": {
                        "expression": {"type": "string"}
                    },
                    "required": ["expression"]
                }),
            ),
            fn_tool(
                "ocr_image",
                "从图片识别文字（OCR）。DeepSeek/GLM 等无视觉模型处理截图/单据时必须调用。参数 media_path 或 image_base64。",
                json!({
                    "type": "object",
                    "properties": {
                        "media_path": {"type": "string"},
                        "image_base64": {"type": "string"},
                        "hint": {"type": "string"}
                    },
                    "required": []
                }),
            ),
            fn_tool(
                "voice_speak",
                "微软语音合成朗读文本（默认 Edge 中文音色；可配 Azure Speech Key）。返回音频路径。",
                json!({
                    "type": "object",
                    "properties": {
                        "text": {"type": "string"},
                        "voice": {"type": "string"}
                    },
                    "required": ["text"]
                }),
            ),
            fn_tool(
                "memory_note",
                "读写本机短备忘。action=list|get|set|delete。",
                json!({
                    "type": "object",
                    "properties": {
                        "action": {"type": "string", "enum": ["list", "get", "set", "delete"]},
                        "key": {"type": "string"},
                        "value": {"type": "string"}
                    },
                    "required": ["action"]
                }),
            ),
            fn_tool(
                "list_skills",
                "列出已安装的 Skills（SKILL.md 体系）。",
                json!({"type":"object","properties":{},"required":[]}),
            ),
            fn_tool(
                "load_skill",
                "加载某个 skill 的完整指引正文。",
                json!({
                    "type":"object",
                    "properties":{"id":{"type":"string","description":"skill id 或 name"}},
                    "required":["id"]
                }),
            ),
            fn_tool(
                "mcp_list",
                "列出已配置 MCP 服务器上的远程工具。",
                json!({"type":"object","properties":{},"required":[]}),
            ),
            fn_tool(
                "mcp_call",
                "调用 MCP 远程工具。server=服务器名, tool=工具名, arguments=JSON对象。",
                json!({
                    "type":"object",
                    "properties":{
                        "server":{"type":"string"},
                        "tool":{"type":"string"},
                        "arguments":{"type":"object"}
                    },
                    "required":["server","tool"]
                }),
            ),
            fn_tool(
                "task_plan",
                "创建或更新多步骤任务计划（大任务拆解）。action=set|get|clear。",
                json!({
                    "type":"object",
                    "properties":{
                        "action":{"type":"string","enum":["set","get","clear"]},
                        "plan":{"type":"string","description":"Markdown 计划正文"}
                    },
                    "required":["action"]
                }),
            ),
            fn_tool(
                "http_get",
                "对公开 HTTPS API 发 GET（非 HTML 页面优先用；网页请用 web_fetch）。",
                json!({
                    "type":"object",
                    "properties":{
                        "url":{"type":"string"},
                        "max_chars":{"type":"integer","default":8000}
                    },
                    "required":["url"]
                }),
            ),
        ]
    }

    /// Builtin + dynamic MCP schemas (async).
    pub async fn all_tool_schemas(&self) -> Vec<Value> {
        let mut s = Self::tool_schemas();
        let cfg = self.load_config();
        if cfg.enable_mcp {
            let mut mcp = self.mcp.tool_schemas().await;
            s.append(&mut mcp);
        }
        s
    }

    pub async fn dispatch(&self, name: &str, args: &Value) -> String {
        // Dynamic MCP tools: mcp__server__tool
        if let Some((server, tool)) = crate::mcp_client::parse_mcp_tool_name(name) {
            let r = self
                .mcp
                .call_tool(&server, &tool, args.clone())
                .await;
            return match r {
                Ok(s) => truncate(&s, 12_000),
                Err(e) => format!("[tool_error] {e}"),
            };
        }
        let r = match name {
            "web_search" => self.web_search(args).await,
            "web_fetch" => self.web_fetch(args).await,
            "get_datetime" => Ok(self.get_datetime()),
            "calculator" => self.calculator(args),
            "ocr_image" => self.ocr_image(args).await,
            "voice_speak" => self.voice_speak(args).await,
            "memory_note" => self.memory_note(args),
            "list_skills" => Ok(self.skills.list_json().to_string()),
            "load_skill" => self.load_skill(args),
            "mcp_list" => self.mcp_list().await,
            "mcp_call" => self.mcp_call(args).await,
            "task_plan" => self.task_plan(args),
            "http_get" => self.http_get(args).await,
            _ => Err(Error::Msg(format!("unknown tool: {name}"))),
        };
        match r {
            Ok(s) => truncate(&s, 12_000),
            Err(e) => format!("[tool_error] {e}"),
        }
    }

    async fn web_search(&self, args: &Value) -> Result<String> {
        let q = args
            .get("query")
            .or_else(|| args.get("q"))
            .and_then(|x| x.as_str())
            .unwrap_or("")
            .trim();
        if q.is_empty() {
            return Err(Error::Msg("query required".into()));
        }
        let n = args
            .get("max_results")
            .or_else(|| args.get("num_results"))
            .and_then(|x| x.as_u64())
            .unwrap_or(5)
            .clamp(1, 8) as usize;

        let mut blocks: Vec<String> = Vec::new();
        let mut errors: Vec<String> = Vec::new();

        // 1) Tavily (optional keyed)
        let cfg = self.load_config();
        if !cfg.tavily_api_key.trim().is_empty() {
            match self.tavily_search(q, n, &cfg.tavily_api_key).await {
                Ok(s) if !s.is_empty() => blocks.push(s),
                Ok(_) => {}
                Err(e) => errors.push(format!("tavily: {e}")),
            }
        }

        // 2) Bing web RSS — works without key, resists bot walls better than HTML
        match self.bing_rss_search(q, n).await {
            Ok(s) if !s.is_empty() => blocks.push(s),
            Ok(_) => errors.push("bing-rss: empty".into()),
            Err(e) => errors.push(format!("bing-rss: {e}")),
        }

        // 3) Google News RSS (great for 新闻/最新)
        match self.google_news_rss(q, n).await {
            Ok(s) if !s.is_empty() => blocks.push(s),
            Ok(_) => {}
            Err(e) => errors.push(format!("gnews: {e}")),
        }

        // 4) Wikipedia (knowledge)
        match self.wikipedia_search(q, n).await {
            Ok(s) if !s.contains("(no results)") => blocks.push(s),
            Ok(_) => {}
            Err(e) => errors.push(format!("wiki: {e}")),
        }

        // 5) Hacker News Algolia (tech)
        match self.hn_search(q, n).await {
            Ok(s) if !s.is_empty() => blocks.push(s),
            Ok(_) => {}
            Err(e) => errors.push(format!("hn: {e}")),
        }

        // 6) DDG lite last (often blocked/timeout)
        match tokio::time::timeout(
            std::time::Duration::from_secs(8),
            self.ddg_lite_search(q, n),
        )
        .await
        {
            Ok(Ok(s)) if !s.is_empty() => blocks.push(s),
            Ok(Ok(_)) => {}
            Ok(Err(e)) => errors.push(format!("ddg: {e}")),
            Err(_) => errors.push("ddg: timeout".into()),
        }

        if blocks.is_empty() {
            return Ok(format!(
                "# Search: {q}\n(no results)\nerrors: {}",
                errors.join("; ")
            ));
        }

        // Merge, de-dupe by URL-ish lines, cap total
        let mut out = format!("# Search: {q}\n");
        out.push_str(&blocks.join("\n\n"));
        Ok(truncate(&out, 10_000))
    }

    async fn bing_rss_search(&self, q: &str, n: usize) -> Result<String> {
        let has_cjk = q.chars().any(|c| ('\u{4e00}'..='\u{9fff}').contains(&c));
        let url = if has_cjk {
            format!(
                "https://www.bing.com/search?q={}&format=rss&setlang=zh-hans&mkt=zh-CN&cc=CN",
                urlencoding::encode(q)
            )
        } else {
            format!(
                "https://www.bing.com/search?q={}&format=rss&setlang=en-us&mkt=en-US",
                urlencoding::encode(q)
            )
        };

        let resp = self
            .http
            .get(&url)
            .header("Accept", "application/rss+xml, application/xml, text/xml, */*")
            .header("Accept-Language", "zh-CN,zh;q=0.9,en;q=0.8")
            .send()
            .await
            .map_err(|e| Error::Network(e.to_string()))?;
        if !resp.status().is_success() {
            return Err(Error::Msg(format!("bing rss http {}", resp.status())));
        }
        let xml = resp.text().await.unwrap_or_default();
        let items = parse_rss_items(&xml, n);
        if items.is_empty() {
            return Ok(String::new());
        }
        let mut lines = vec![format!("## Bing ({})", items.len())];
        for (i, it) in items.iter().enumerate() {
            lines.push(format!(
                "{}. {}\n   {}\n   {}",
                i + 1,
                it.title,
                it.link,
                it.desc.chars().take(220).collect::<String>()
            ));
        }
        Ok(lines.join("\n"))
    }

    async fn google_news_rss(&self, q: &str, n: usize) -> Result<String> {
        let has_cjk = q.chars().any(|c| ('\u{4e00}'..='\u{9fff}').contains(&c));
        let (hl, gl, ceid) = if has_cjk {
            ("zh-CN", "CN", "CN:zh-Hans")
        } else {
            ("en-US", "US", "US:en")
        };
        let url = format!(
            "https://news.google.com/rss/search?q={}&hl={hl}&gl={gl}&ceid={ceid}",
            urlencoding::encode(q)
        );
        let resp = self
            .http
            .get(&url)
            .header("Accept", "application/rss+xml, application/xml, text/xml, */*")
            .send()
            .await
            .map_err(|e| Error::Network(e.to_string()))?;
        if !resp.status().is_success() {
            return Err(Error::Msg(format!("gnews http {}", resp.status())));
        }
        let xml = resp.text().await.unwrap_or_default();
        let items = parse_rss_items(&xml, n);
        if items.is_empty() {
            return Ok(String::new());
        }
        let mut lines = vec![format!("## Google News ({})", items.len())];
        for (i, it) in items.iter().enumerate() {
            lines.push(format!(
                "{}. {}\n   {}\n   {}",
                i + 1,
                it.title,
                it.link,
                it.desc.chars().take(180).collect::<String>()
            ));
        }
        Ok(lines.join("\n"))
    }

    async fn hn_search(&self, q: &str, n: usize) -> Result<String> {
        let url = format!(
            "https://hn.algolia.com/api/v1/search?query={}&hitsPerPage={n}&tags=story",
            urlencoding::encode(q)
        );
        let resp = self
            .http
            .get(&url)
            .send()
            .await
            .map_err(|e| Error::Network(e.to_string()))?;
        if !resp.status().is_success() {
            return Err(Error::Msg(format!("hn http {}", resp.status())));
        }
        let v: Value = resp.json().await.unwrap_or(json!({}));
        let hits = v.get("hits").and_then(|x| x.as_array()).cloned().unwrap_or_default();
        if hits.is_empty() {
            return Ok(String::new());
        }
        let mut lines = vec![format!("## Hacker News ({})", hits.len().min(n))];
        for (i, h) in hits.iter().take(n).enumerate() {
            let title = h
                .get("title")
                .or_else(|| h.get("story_title"))
                .and_then(|x| x.as_str())
                .unwrap_or("(no title)");
            let link = h
                .get("url")
                .or_else(|| h.get("story_url"))
                .and_then(|x| x.as_str())
                .unwrap_or("");
            let pts = h.get("points").and_then(|x| x.as_i64()).unwrap_or(0);
            lines.push(format!("{}. {title}\n   {link}\n   points={pts}", i + 1));
        }
        Ok(lines.join("\n"))
    }

    async fn ddg_lite_search(&self, q: &str, n: usize) -> Result<String> {
        let url = format!(
            "https://lite.duckduckgo.com/lite/?q={}",
            urlencoding::encode(q)
        );
        let resp = self
            .http
            .get(&url)
            .header("Accept-Language", "zh-CN,zh;q=0.9,en;q=0.8")
            .send()
            .await
            .map_err(|e| Error::Network(e.to_string()))?;
        let html = resp.text().await.unwrap_or_default();
        let results = extract_http_links(&html, n);
        if results.is_empty() {
            return Ok(String::new());
        }
        let mut lines = vec![format!("## DuckDuckGo ({})", results.len())];
        for (i, (t, u)) in results.iter().enumerate() {
            lines.push(format!("{}. {t}\n   {u}", i + 1));
        }
        Ok(lines.join("\n"))
    }

    async fn wikipedia_search(&self, q: &str, n: usize) -> Result<String> {
        let lang = if q.chars().any(|c| ('\u{4e00}'..='\u{9fff}').contains(&c)) {
            "zh"
        } else {
            "en"
        };
        let url = format!(
            "https://{lang}.wikipedia.org/w/api.php?action=opensearch&search={}&limit={n}&namespace=0&format=json",
            urlencoding::encode(q)
        );
        let resp = self
            .http
            .get(&url)
            .send()
            .await
            .map_err(|e| Error::Network(e.to_string()))?;
        let v: Value = resp.json().await.unwrap_or(json!([]));
        let titles = v.get(1).and_then(|x| x.as_array()).cloned().unwrap_or_default();
        let descs = v.get(2).and_then(|x| x.as_array()).cloned().unwrap_or_default();
        let links = v.get(3).and_then(|x| x.as_array()).cloned().unwrap_or_default();
        if titles.is_empty() {
            return Ok(format!("# Search: {q}\n(no results)"));
        }
        let mut lines = vec![format!("## Wikipedia/{lang} ({})", titles.len().min(n))];
        for i in 0..titles.len().min(n) {
            lines.push(format!(
                "{}. {}\n   {}\n   {}",
                i + 1,
                titles[i].as_str().unwrap_or(""),
                links.get(i).and_then(|x| x.as_str()).unwrap_or(""),
                descs.get(i).and_then(|x| x.as_str()).unwrap_or("")
            ));
        }
        Ok(lines.join("\n"))
    }

    async fn tavily_search(&self, q: &str, n: usize, key: &str) -> Result<String> {
        let body = json!({
            "api_key": key,
            "query": q,
            "max_results": n,
            "include_answer": true,
            "search_depth": "basic",
        });
        let resp = self
            .http
            .post("https://api.tavily.com/search")
            .json(&body)
            .send()
            .await
            .map_err(|e| Error::Network(e.to_string()))?;
        if !resp.status().is_success() {
            return Ok(String::new());
        }
        let v: Value = resp.json().await.unwrap_or(json!({}));
        let mut lines = vec![format!("## Tavily")];
        if let Some(a) = v.get("answer").and_then(|x| x.as_str()) {
            if !a.is_empty() {
                lines.push(format!("Answer: {a}"));
            }
        }
        if let Some(arr) = v.get("results").and_then(|x| x.as_array()) {
            for (i, r) in arr.iter().take(n).enumerate() {
                lines.push(format!(
                    "{}. {}\n   {}\n   {}",
                    i + 1,
                    r.get("title").and_then(|x| x.as_str()).unwrap_or(""),
                    r.get("url").and_then(|x| x.as_str()).unwrap_or(""),
                    r.get("content")
                        .and_then(|x| x.as_str())
                        .unwrap_or("")
                        .chars()
                        .take(240)
                        .collect::<String>()
                ));
            }
        }
        Ok(if lines.len() > 1 {
            lines.join("\n")
        } else {
            String::new()
        })
    }

    async fn web_fetch(&self, args: &Value) -> Result<String> {
        let url = args
            .get("url")
            .and_then(|x| x.as_str())
            .unwrap_or("")
            .trim();
        if !url.starts_with("http://") && !url.starts_with("https://") {
            return Err(Error::Msg("url must be http(s)".into()));
        }
        let max = args
            .get("max_chars")
            .and_then(|x| x.as_u64())
            .unwrap_or(6000)
            .clamp(500, 20_000) as usize;
        let resp = self
            .http
            .get(url)
            .send()
            .await
            .map_err(|e| Error::Network(e.to_string()))?;
        let status = resp.status().as_u16();
        let ct = resp
            .headers()
            .get(reqwest::header::CONTENT_TYPE)
            .and_then(|v| v.to_str().ok())
            .unwrap_or("")
            .to_string();
        let body = resp.text().await.unwrap_or_default();
        let text = if ct.contains("html") || body.trim_start().starts_with('<') {
            html_to_text(&body)
        } else {
            body
        };
        let text = text.trim();
        let clipped: String = text.chars().take(max).collect();
        Ok(format!(
            "# Fetch {url}\nHTTP {status}\n\n{clipped}{}",
            if text.chars().count() > max { "\n…" } else { "" }
        ))
    }

    fn get_datetime(&self) -> String {
        let now = chrono::Local::now();
        format!(
            "local: {}\niso: {}\nunix: {}",
            now.format("%Y-%m-%d %H:%M:%S %Z"),
            now.to_rfc3339(),
            now.timestamp()
        )
    }

    fn calculator(&self, args: &Value) -> Result<String> {
        let expr = args
            .get("expression")
            .or_else(|| args.get("expr"))
            .and_then(|x| x.as_str())
            .unwrap_or("")
            .replace(' ', "");
        if expr.is_empty()
            || !expr
                .chars()
                .all(|c| c.is_ascii_digit() || matches!(c, '+' | '-' | '*' | '/' | '(' | ')' | '.'))
        {
            return Err(Error::Msg("invalid expression".into()));
        }
        let v = eval_expr(&expr).ok_or_else(|| Error::Msg("eval failed".into()))?;
        Ok(format!("{expr} = {v}"))
    }

    async fn ocr_image(&self, args: &Value) -> Result<String> {
        let bytes = self.load_image_bytes(args)?;
        if bytes.is_empty() {
            return Err(Error::Msg("provide media_path or image_base64".into()));
        }
        let cfg = self.load_config();
        if !cfg.azure_vision_key.trim().is_empty() {
            if let Ok(t) = self.azure_ocr(&cfg, &bytes).await {
                if !t.trim().is_empty() {
                    return Ok(format!("# OCR (Azure Vision)\n{t}"));
                }
            }
        }
        if let Ok(t) = self.ocr_space(&bytes).await {
            if !t.trim().is_empty() {
                return Ok(format!("# OCR (ocr.space)\n{t}"));
            }
        }
        Err(Error::Msg(
            "OCR 失败。可在 local_agent_config 填写 azure_vision_key + azure_vision_endpoint。"
                .into(),
        ))
    }

    fn load_image_bytes(&self, args: &Value) -> Result<Vec<u8>> {
        if let Some(p) = args.get("media_path").and_then(|x| x.as_str()) {
            let p = p.trim();
            if !p.is_empty() {
                return std::fs::read(p).map_err(Error::Io);
            }
        }
        if let Some(b64) = args.get("image_base64").and_then(|x| x.as_str()) {
            let s = b64.trim().split(',').last().unwrap_or(b64).trim();
            return base64::Engine::decode(&base64::engine::general_purpose::STANDARD, s.as_bytes())
                .or_else(|_| {
                    base64::Engine::decode(
                        &base64::engine::general_purpose::URL_SAFE,
                        s.as_bytes(),
                    )
                })
                .map_err(|e| Error::Msg(format!("base64 decode: {e}")));
        }
        Ok(vec![])
    }

    async fn azure_ocr(&self, cfg: &AgentConfig, bytes: &[u8]) -> Result<String> {
        let ep = cfg.azure_vision_endpoint.trim().trim_end_matches('/');
        if ep.is_empty() {
            return Err(Error::Msg("azure_vision_endpoint empty".into()));
        }
        let url = format!("{ep}/vision/v3.2/read/analyze");
        let resp = self
            .http
            .post(&url)
            .header("Ocp-Apim-Subscription-Key", cfg.azure_vision_key.trim())
            .header("Content-Type", "application/octet-stream")
            .body(bytes.to_vec())
            .send()
            .await
            .map_err(|e| Error::Network(e.to_string()))?;
        if !resp.status().is_success() && resp.status().as_u16() != 202 {
            let t = resp.text().await.unwrap_or_default();
            return Err(Error::Msg(format!("azure ocr start: {t}")));
        }
        let op = resp
            .headers()
            .get("Operation-Location")
            .or_else(|| resp.headers().get("operation-location"))
            .and_then(|v| v.to_str().ok())
            .unwrap_or("")
            .to_string();
        if op.is_empty() {
            return Err(Error::Msg("no Operation-Location".into()));
        }
        for _ in 0..20 {
            tokio::time::sleep(std::time::Duration::from_millis(400)).await;
            let r = self
                .http
                .get(&op)
                .header("Ocp-Apim-Subscription-Key", cfg.azure_vision_key.trim())
                .send()
                .await
                .map_err(|e| Error::Network(e.to_string()))?;
            let v: Value = r.json().await.unwrap_or(json!({}));
            let st = v.get("status").and_then(|x| x.as_str()).unwrap_or("");
            if st == "succeeded" {
                let mut lines = Vec::new();
                if let Some(arr) = v
                    .pointer("/analyzeResult/readResults")
                    .and_then(|x| x.as_array())
                {
                    for page in arr {
                        if let Some(ls) = page.get("lines").and_then(|x| x.as_array()) {
                            for line in ls {
                                if let Some(t) = line.get("text").and_then(|x| x.as_str()) {
                                    lines.push(t.to_string());
                                }
                            }
                        }
                    }
                }
                return Ok(lines.join("\n"));
            }
            if st == "failed" {
                return Err(Error::Msg("azure ocr failed".into()));
            }
        }
        Err(Error::Msg("azure ocr timeout".into()))
    }

    async fn ocr_space(&self, bytes: &[u8]) -> Result<String> {
        let b64 = base64::Engine::encode(&base64::engine::general_purpose::STANDARD, bytes);
        let body = format!(
            "base64Image={}&language=chs&isOverlayRequired=false&OCREngine=2&apikey=helloworld",
            urlencoding::encode(&format!("data:image/jpeg;base64,{b64}"))
        );
        let resp = self
            .http
            .post("https://api.ocr.space/parse/image")
            .header("Content-Type", "application/x-www-form-urlencoded")
            .body(body)
            .send()
            .await
            .map_err(|e| Error::Network(e.to_string()))?;
        let v: Value = resp.json().await.unwrap_or(json!({}));
        if let Some(arr) = v.get("ParsedResults").and_then(|x| x.as_array()) {
            let mut out = String::new();
            for r in arr {
                if let Some(t) = r.get("ParsedText").and_then(|x| x.as_str()) {
                    out.push_str(t);
                    out.push('\n');
                }
            }
            return Ok(out);
        }
        Err(Error::Msg("ocr.space empty".into()))
    }

    async fn voice_speak(&self, args: &Value) -> Result<String> {
        let text = args
            .get("text")
            .and_then(|x| x.as_str())
            .unwrap_or("")
            .trim();
        if text.is_empty() {
            return Err(Error::Msg("text required".into()));
        }
        let text: String = text.chars().take(800).collect();
        let cfg = self.load_config();
        let voice = args
            .get("voice")
            .and_then(|x| x.as_str())
            .unwrap_or("")
            .trim();
        let voice = if voice.is_empty() {
            cfg.tts_voice.as_str()
        } else {
            voice
        };

        let audio = if !cfg.azure_speech_key.trim().is_empty() {
            self.azure_tts(&cfg, &text, voice).await?
        } else {
            self.edge_tts_via_auth(&text, voice).await?
        };

        let path = self.store.path(&format!("media/tts_{}.mp3", uuid::Uuid::new_v4()));
        if let Some(parent) = path.parent() {
            std::fs::create_dir_all(parent).map_err(Error::Io)?;
        }
        std::fs::write(&path, &audio).map_err(Error::Io)?;
        Ok(format!(
            "ok · Microsoft TTS voice={voice} · bytes={} · path={}",
            audio.len(),
            path.display()
        ))
    }

    async fn azure_tts(&self, cfg: &AgentConfig, text: &str, voice: &str) -> Result<Vec<u8>> {
        let region = cfg.azure_speech_region.trim();
        let url = format!("https://{region}.tts.speech.microsoft.com/cognitiveservices/v1");
        let ssml = format!(
            r#"<speak version='1.0' xml:lang='zh-CN'><voice name='{voice}'>{}</voice></speak>"#,
            xml_escape(text)
        );
        let resp = self
            .http
            .post(&url)
            .header("Ocp-Apim-Subscription-Key", cfg.azure_speech_key.trim())
            .header("Content-Type", "application/ssml+xml")
            .header(
                "X-Microsoft-OutputFormat",
                "audio-16khz-128kbitrate-mono-mp3",
            )
            .header("User-Agent", "TaktonMobile")
            .body(ssml)
            .send()
            .await
            .map_err(|e| Error::Network(e.to_string()))?;
        if !resp.status().is_success() {
            let t = resp.text().await.unwrap_or_default();
            return Err(Error::Msg(format!("Azure TTS failed: {t}")));
        }
        Ok(resp
            .bytes()
            .await
            .map_err(|e| Error::Network(e.to_string()))?
            .to_vec())
    }

    async fn edge_tts_via_auth(&self, text: &str, voice: &str) -> Result<Vec<u8>> {
        let auth = self
            .http
            .get("https://edge.microsoft.com/translate/auth")
            .send()
            .await
            .map_err(|e| Error::Network(e.to_string()))?
            .text()
            .await
            .unwrap_or_default();
        if auth.len() < 20 {
            return Err(Error::Msg(
                "Edge TTS 令牌获取失败。请配置 azure_speech_key。".into(),
            ));
        }
        let ssml = format!(
            r#"<speak version='1.0' xml:lang='zh-CN'><voice name='{voice}'>{}</voice></speak>"#,
            xml_escape(text)
        );
        for host in [
            "https://eastus.tts.speech.microsoft.com/cognitiveservices/v1",
            "https://eastasia.tts.speech.microsoft.com/cognitiveservices/v1",
        ] {
            let resp = self
                .http
                .post(host)
                .header("Authorization", format!("Bearer {auth}"))
                .header("Content-Type", "application/ssml+xml")
                .header(
                    "X-Microsoft-OutputFormat",
                    "audio-16khz-32kbitrate-mono-mp3",
                )
                .header("User-Agent", "TaktonMobile")
                .body(ssml.clone())
                .send()
                .await;
            if let Ok(resp) = resp {
                if resp.status().is_success() {
                    if let Ok(b) = resp.bytes().await {
                        if b.len() > 100 {
                            return Ok(b.to_vec());
                        }
                    }
                }
            }
        }
        let _ = (EDGE_TTS_TOKEN, edge_sec_ms_gec());
        Err(Error::Msg(
            "微软免费 TTS 不可用。请在 local_agent_config 配置 azure_speech_key + azure_speech_region。"
                .into(),
        ))
    }

    fn memory_note(&self, args: &Value) -> Result<String> {
        let action = args
            .get("action")
            .and_then(|x| x.as_str())
            .unwrap_or("list");
        let mut map: std::collections::BTreeMap<String, String> = self
            .store
            .load_json(NOTES_FILE)
            .ok()
            .flatten()
            .unwrap_or_default();
        match action {
            "list" => {
                if map.is_empty() {
                    return Ok("(empty notes)".into());
                }
                let mut lines = Vec::new();
                for (k, v) in map.iter().take(50) {
                    lines.push(format!(
                        "- {k}: {}",
                        v.chars().take(120).collect::<String>()
                    ));
                }
                Ok(lines.join("\n"))
            }
            "get" => {
                let key = args.get("key").and_then(|x| x.as_str()).unwrap_or("");
                Ok(map.get(key).cloned().unwrap_or_else(|| "(missing)".into()))
            }
            "set" => {
                let key = args
                    .get("key")
                    .and_then(|x| x.as_str())
                    .unwrap_or("")
                    .trim();
                let val = args
                    .get("value")
                    .and_then(|x| x.as_str())
                    .unwrap_or("")
                    .to_string();
                if key.is_empty() {
                    return Err(Error::Msg("key required".into()));
                }
                map.insert(key.to_string(), val);
                while map.len() > 200 {
                    if let Some(k) = map.keys().next().cloned() {
                        map.remove(&k);
                    } else {
                        break;
                    }
                }
                self.store.save_json(NOTES_FILE, &map)?;
                Ok(format!("saved {key}"))
            }
            "delete" => {
                let key = args.get("key").and_then(|x| x.as_str()).unwrap_or("");
                map.remove(key);
                self.store.save_json(NOTES_FILE, &map)?;
                Ok(format!("deleted {key}"))
            }
            _ => Err(Error::Msg("action must be list|get|set|delete".into())),
        }
    }

    fn load_skill(&self, args: &Value) -> Result<String> {
        let id = args.get("id").and_then(|x| x.as_str()).unwrap_or("").trim();
        if id.is_empty() {
            return Err(Error::Msg("id required".into()));
        }
        let sk = self.skills.get(id)?;
        Ok(format!(
            "# {}\n{}\n\n{}",
            sk.meta.name, sk.meta.description, sk.body
        ))
    }

    async fn mcp_list(&self) -> Result<String> {
        let tools = self.mcp.list_all_tools().await;
        if tools.is_empty() {
            let cfg = self.mcp.load_config();
            return Ok(format!(
                "暂无 MCP 工具。已配置服务器: {}。在 Agent 设置里添加 mcp_servers。",
                cfg.servers
                    .iter()
                    .map(|s| s.name.as_str())
                    .collect::<Vec<_>>()
                    .join(", ")
            ));
        }
        let mut lines = vec![format!("# MCP tools ({})", tools.len())];
        for t in tools {
            lines.push(format!(
                "- mcp__{}__{} — {}",
                t.server, t.name, t.description
            ));
        }
        Ok(lines.join("\n"))
    }

    async fn mcp_call(&self, args: &Value) -> Result<String> {
        let server = args
            .get("server")
            .and_then(|x| x.as_str())
            .unwrap_or("")
            .trim();
        let tool = args
            .get("tool")
            .and_then(|x| x.as_str())
            .unwrap_or("")
            .trim();
        if server.is_empty() || tool.is_empty() {
            return Err(Error::Msg("server and tool required".into()));
        }
        let a = args.get("arguments").cloned().unwrap_or(json!({}));
        self.mcp.call_tool(server, tool, a).await
    }

    fn task_plan(&self, args: &Value) -> Result<String> {
        const PLAN: &str = "task_plan.md";
        let action = args.get("action").and_then(|x| x.as_str()).unwrap_or("get");
        match action {
            "clear" => {
                let _ = self.store.delete(PLAN);
                Ok("plan cleared".into())
            }
            "set" => {
                let plan = args
                    .get("plan")
                    .and_then(|x| x.as_str())
                    .unwrap_or("")
                    .trim();
                if plan.is_empty() {
                    return Err(Error::Msg("plan required".into()));
                }
                let path = self.store.path(PLAN);
                std::fs::write(&path, plan).map_err(|e| Error::Msg(e.to_string()))?;
                Ok(format!("plan saved ({} chars)", plan.chars().count()))
            }
            _ => {
                let path = self.store.path(PLAN);
                if !path.exists() {
                    return Ok("(no plan)".into());
                }
                let s = std::fs::read_to_string(path).unwrap_or_default();
                Ok(s)
            }
        }
    }

    async fn http_get(&self, args: &Value) -> Result<String> {
        let url = args.get("url").and_then(|x| x.as_str()).unwrap_or("").trim();
        if !url.starts_with("https://") {
            return Err(Error::Msg("only https:// URLs allowed".into()));
        }
        let lower = url.to_lowercase();
        for ban in ["169.254.", "localhost", "127.0.0.1", "0.0.0.0", "[::1]"] {
            if lower.contains(ban) {
                return Err(Error::Msg("url not allowed".into()));
            }
        }
        let max = args
            .get("max_chars")
            .and_then(|x| x.as_u64())
            .unwrap_or(8000)
            .clamp(500, 20_000) as usize;
        let resp = self
            .http
            .get(url)
            .send()
            .await
            .map_err(|e| Error::Network(e.to_string()))?;
        let status = resp.status().as_u16();
        let body = resp.text().await.unwrap_or_default();
        let clipped: String = body.chars().take(max).collect();
        Ok(format!("HTTP {status}\n{clipped}"))
    }
}

fn fn_tool(name: &str, description: &str, parameters: Value) -> Value {
    json!({
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": parameters
        }
    })
}

fn truncate(s: &str, max: usize) -> String {
    if s.chars().count() <= max {
        s.to_string()
    } else {
        format!("{}…", s.chars().take(max).collect::<String>())
    }
}


#[derive(Debug, Clone)]
struct RssItem {
    title: String,
    link: String,
    desc: String,
}

fn parse_rss_items(xml: &str, n: usize) -> Vec<RssItem> {
    let mut out = Vec::new();
    let lower = xml; // keep original for slice
    let mut rest = lower;
    while out.len() < n {
        let Some(item_start) = rest.find("<item") else { break };
        let after = &rest[item_start..];
        let Some(gt) = after.find('>') else { break };
        let body_start = item_start + gt + 1;
        let Some(end_rel) = rest[body_start..].find("</item>") else { break };
        let body = &rest[body_start..body_start + end_rel];
        rest = &rest[body_start + end_rel + 7..];

        let title = rss_tag(body, "title");
        let link = rss_tag(body, "link");
        let desc = rss_tag(body, "description");
        if title.is_empty() && link.is_empty() {
            continue;
        }
        // unwrap bing news apiclick redirect when present
        let link = unwrap_bing_news_url(&link);
        out.push(RssItem {
            title: strip_cdata(&html_unescape(&title)),
            link,
            desc: strip_tags(&html_unescape(&desc)),
        });
    }
    out
}

fn rss_tag(body: &str, tag: &str) -> String {
    let open = format!("<{tag}");
    let close = format!("</{tag}>");
    let Some(s) = body.find(&open) else { return String::new() };
    let after = &body[s + open.len()..];
    let Some(gt) = after.find('>') else { return String::new() };
    let content = &after[gt + 1..];
    let Some(e) = content.find(&close) else { return String::new() };
    content[..e].trim().to_string()
}

fn strip_cdata(s: &str) -> String {
    let s = s.trim();
    if let Some(inner) = s.strip_prefix("<![CDATA[") {
        if let Some(i) = inner.find("]]>") {
            return inner[..i].to_string();
        }
    }
    s.to_string()
}

fn unwrap_bing_news_url(link: &str) -> String {
    // http://www.bing.com/news/apiclick.aspx?...&url=https%3a%2f%2f...
    if !link.contains("bing.com/news/apiclick") && !link.contains("url=") {
        return link.to_string();
    }
    if let Some(pos) = link.find("url=") {
        let rest = &link[pos + 4..];
        let end = rest.find('&').unwrap_or(rest.len());
        let enc = &rest[..end];
        if let Ok(decoded) = urlencoding::decode(enc) {
            let d = decoded.to_string();
            if d.starts_with("http") {
                return d;
            }
        }
    }
    link.to_string()
}

fn extract_http_links(html: &str, n: usize) -> Vec<(String, String)> {
    let mut out = Vec::new();
    let mut seen = std::collections::HashSet::new();
    let mut i = 0;
    while i + 10 < html.len() && out.len() < n {
        if let Some(rel) = html[i..].find("href=") {
            i += rel + 5;
            if i >= html.len() {
                break;
            }
            let quote = html.as_bytes().get(i).copied().unwrap_or(0) as char;
            if quote != '"' && quote != '\'' {
                continue;
            }
            i += 1;
            if let Some(end) = html[i..].find(quote) {
                let href = &html[i..i + end];
                i += end + 1;
                if !href.starts_with("http") || href.contains("duckduckgo.com") {
                    continue;
                }
                if !seen.insert(href.to_string()) {
                    continue;
                }
                let title = if let Some(gt) = html[i..].find('>') {
                    let start = i + gt + 1;
                    if let Some(close) = html[start..].find("</a>") {
                        strip_tags(&html[start..start + close])
                    } else {
                        href.chars().take(60).collect()
                    }
                } else {
                    href.chars().take(60).collect()
                };
                let title = title.trim().to_string();
                if title.is_empty() {
                    continue;
                }
                out.push((title, href.to_string()));
            }
        } else {
            break;
        }
    }
    out
}

fn strip_tags(s: &str) -> String {
    let mut out = String::new();
    let mut in_tag = false;
    for c in s.chars() {
        match c {
            '<' => in_tag = true,
            '>' => in_tag = false,
            _ if !in_tag => out.push(c),
            _ => {}
        }
    }
    html_unescape(&out.split_whitespace().collect::<Vec<_>>().join(" "))
}

fn html_to_text(html: &str) -> String {
    let mut s = html.to_string();
    for tag in ["script", "style", "noscript"] {
        loop {
            let lower = s.to_lowercase();
            let open = format!("<{tag}");
            let close = format!("</{tag}>");
            if let Some(start) = lower.find(&open) {
                if let Some(end_rel) = lower[start..].find(&close) {
                    let end = start + end_rel + close.len();
                    s.replace_range(start..end.min(s.len()), " ");
                } else {
                    break;
                }
            } else {
                break;
            }
        }
    }
    strip_tags(&s)
}

fn html_unescape(s: &str) -> String {
    s.replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", "\"")
        .replace("&#39;", "'")
        .replace("&nbsp;", " ")
}

fn xml_escape(s: &str) -> String {
    s.replace('&', "&amp;")
        .replace('<', "&lt;")
        .replace('>', "&gt;")
        .replace('"', "&quot;")
}

fn edge_sec_ms_gec() -> String {
    let unix = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0);
    let mut ticks = (unix + 11644473600) * 10_000_000;
    ticks -= ticks % 3_000_000_000;
    let s = format!("{ticks}{EDGE_TTS_TOKEN}");
    let hash = Sha256::digest(s.as_bytes());
    hash.iter().map(|b| format!("{b:02X}")).collect()
}

fn eval_expr(s: &str) -> Option<f64> {
    let chars: Vec<char> = s.chars().collect();
    let mut i = 0usize;
    fn parse_expr(chars: &[char], i: &mut usize) -> Option<f64> {
        let mut v = parse_term(chars, i)?;
        while *i < chars.len() {
            match chars[*i] {
                '+' => {
                    *i += 1;
                    v += parse_term(chars, i)?;
                }
                '-' => {
                    *i += 1;
                    v -= parse_term(chars, i)?;
                }
                _ => break,
            }
        }
        Some(v)
    }
    fn parse_term(chars: &[char], i: &mut usize) -> Option<f64> {
        let mut v = parse_factor(chars, i)?;
        while *i < chars.len() {
            match chars[*i] {
                '*' => {
                    *i += 1;
                    v *= parse_factor(chars, i)?;
                }
                '/' => {
                    *i += 1;
                    let d = parse_factor(chars, i)?;
                    if d == 0.0 {
                        return None;
                    }
                    v /= d;
                }
                _ => break,
            }
        }
        Some(v)
    }
    fn parse_factor(chars: &[char], i: &mut usize) -> Option<f64> {
        if *i < chars.len() && chars[*i] == '(' {
            *i += 1;
            let v = parse_expr(chars, i)?;
            if *i < chars.len() && chars[*i] == ')' {
                *i += 1;
            }
            return Some(v);
        }
        if *i < chars.len() && chars[*i] == '-' {
            *i += 1;
            return Some(-parse_factor(chars, i)?);
        }
        let start = *i;
        while *i < chars.len() && (chars[*i].is_ascii_digit() || chars[*i] == '.') {
            *i += 1;
        }
        if start == *i {
            return None;
        }
        let s: String = chars[start..*i].iter().collect();
        s.parse().ok()
    }
    let v = parse_expr(&chars, &mut i)?;
    if i != chars.len() {
        return None;
    }
    Some(v)
}
