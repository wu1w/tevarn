# Tevarn v0.4.1

**发布日期**：2026-08-10  
**分支**：`main`

## 亮点

- **MCP 可用性闭环**：连接/热挂载/重连、包名规范化、`tools.include`/`exclude`、权限放行 `mcp_*`
- **精选商店**：Tavily / Firecrawl / **豆包搜索（Search Infinity）** 一键安装模板
- **manage_mcp 可发现**：list/get 返回 name/id；密钥写入 env 后热同步
- **配置意图纠偏**：配 MCP + API Key 时优先 `manage_mcp`，避免被「搜索」关键词带偏到 web_search

## 安装包

| 文件 | 说明 |
|------|------|
| `Tevarn-Setup-0.4.1-x64.exe` | Windows 安装版 |
| `Tevarn-0.4.1-win-portable.exe` | Windows 便携版 |
| `Tevarn-0.4.1-win-x64.zip` | 解压即用（若构建产出） |

## 升级说明

- 从 0.4.0 覆盖安装即可；MCP 配置在 `%APPDATA%\tevarn`
- 豆包搜索需 `ASK_ECHO_SEARCH_INFINITY_API_KEY` 与本机 `uvx`
