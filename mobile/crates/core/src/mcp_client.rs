//! Lightweight MCP client (JSON-RPC 2.0 over HTTP).
//! Compatible with community MCP servers exposing streamable HTTP / simple POST.

use crate::error::{Error, Result};
use crate::storage::Store;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::sync::Arc;

const MCP_CFG: &str = "mcp_servers.json";

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct McpServerConfig {
    pub name: String,
    /// e.g. https://example.com/mcp
    pub url: String,
    #[serde(default)]
    pub headers: std::collections::HashMap<String, String>,
    #[serde(default = "default_true")]
    pub enabled: bool,
}

fn default_true() -> bool {
    true
}

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct McpConfigFile {
    #[serde(default)]
    pub servers: Vec<McpServerConfig>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct McpToolDef {
    pub server: String,
    pub name: String,
    pub description: String,
    pub input_schema: Value,
}

#[derive(Clone)]
pub struct McpHub {
    store: Store,
    http: reqwest::Client,
}

impl McpHub {
    pub fn new(store: Store) -> Self {
        Self {
            store,
            http: reqwest::Client::builder()
                .timeout(std::time::Duration::from_secs(40))
                .build()
                .unwrap_or_else(|_| reqwest::Client::new()),
        }
    }

    pub fn load_config(&self) -> McpConfigFile {
        self.store
            .load_json(MCP_CFG)
            .ok()
            .flatten()
            .unwrap_or_default()
    }

    pub fn save_config(&self, cfg: &McpConfigFile) -> Result<()> {
        self.store.save_json(MCP_CFG, cfg)
    }

    pub async fn list_all_tools(&self) -> Vec<McpToolDef> {
        let cfg = self.load_config();
        let mut out = Vec::new();
        for s in cfg.servers.iter().filter(|s| s.enabled && !s.url.is_empty()) {
            match self.list_tools(s).await {
                Ok(tools) => out.extend(tools),
                Err(_) => continue,
            }
        }
        out
    }

    pub async fn list_tools(&self, server: &McpServerConfig) -> Result<Vec<McpToolDef>> {
        let result = self.rpc(server, "tools/list", json!({})).await?;
        let arr = result
            .get("tools")
            .and_then(|t| t.as_array())
            .cloned()
            .unwrap_or_default();
        Ok(arr
            .into_iter()
            .map(|t| McpToolDef {
                server: server.name.clone(),
                name: t
                    .get("name")
                    .and_then(|x| x.as_str())
                    .unwrap_or("")
                    .to_string(),
                description: t
                    .get("description")
                    .and_then(|x| x.as_str())
                    .unwrap_or("")
                    .to_string(),
                input_schema: t
                    .get("inputSchema")
                    .cloned()
                    .unwrap_or(json!({"type":"object","properties":{}})),
            })
            .filter(|t| !t.name.is_empty())
            .collect())
    }

    pub async fn call_tool(
        &self,
        server_name: &str,
        tool_name: &str,
        arguments: Value,
    ) -> Result<String> {
        let cfg = self.load_config();
        let server = cfg
            .servers
            .iter()
            .find(|s| s.name == server_name)
            .ok_or_else(|| Error::Msg(format!("mcp server not found: {server_name}")))?
            .clone();
        let result = self
            .rpc(
                &server,
                "tools/call",
                json!({
                    "name": tool_name,
                    "arguments": arguments,
                }),
            )
            .await?;
        // content: [{type:text, text:...}]
        if let Some(arr) = result.get("content").and_then(|c| c.as_array()) {
            let mut texts = Vec::new();
            for c in arr {
                if let Some(t) = c.get("text").and_then(|x| x.as_str()) {
                    texts.push(t.to_string());
                } else {
                    texts.push(c.to_string());
                }
            }
            return Ok(texts.join("\n"));
        }
        Ok(result.to_string())
    }

    async fn rpc(&self, server: &McpServerConfig, method: &str, params: Value) -> Result<Value> {
        let body = json!({
            "jsonrpc": "2.0",
            "id": 1,
            "method": method,
            "params": params,
        });
        let mut req = self.http.post(&server.url).json(&body);
        for (k, v) in &server.headers {
            req = req.header(k, v);
        }
        req = req.header("Content-Type", "application/json");
        let resp = req
            .send()
            .await
            .map_err(|e| Error::Network(e.to_string()))?;
        let status = resp.status();
        let text = resp.text().await.unwrap_or_default();
        if !status.is_success() {
            return Err(Error::http(status.as_u16(), text));
        }
        let v: Value = serde_json::from_str(&text)
            .map_err(|e| Error::Msg(format!("mcp json: {e}; body={text}")))?;
        if let Some(err) = v.get("error") {
            return Err(Error::Msg(format!("mcp error: {err}")));
        }
        Ok(v.get("result").cloned().unwrap_or(Value::Null))
    }

    /// OpenAI tool schemas for enabled MCP tools (name: mcp__{server}__{tool})
    pub async fn tool_schemas(&self) -> Vec<Value> {
        let tools = self.list_all_tools().await;
        tools
            .into_iter()
            .map(|t| {
                let full = format!("mcp__{}__{}", sanitize(&t.server), sanitize(&t.name));
                json!({
                    "type": "function",
                    "function": {
                        "name": full,
                        "description": format!("[MCP:{}] {}", t.server, t.description),
                        "parameters": t.input_schema,
                    }
                })
            })
            .collect()
    }
}

fn sanitize(s: &str) -> String {
    s.chars()
        .map(|c| {
            if c.is_ascii_alphanumeric() || c == '_' || c == '-' {
                c
            } else {
                '_'
            }
        })
        .collect()
}

pub fn parse_mcp_tool_name(name: &str) -> Option<(String, String)> {
    // mcp__server__tool
    let rest = name.strip_prefix("mcp__")?;
    let (server, tool) = rest.split_once("__")?;
    if server.is_empty() || tool.is_empty() {
        return None;
    }
    Some((server.to_string(), tool.to_string()))
}

/// Shared handle type for agent.
pub type SharedMcp = Arc<McpHub>;
