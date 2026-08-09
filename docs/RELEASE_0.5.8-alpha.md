# Tevarn 0.5.8-alpha · 2026-08-08（重打包）

**分支**：`feature/agent-kernel`  
**版本权威**：`backend/VERSION` · `frontend/lib/appVersion.ts` · root/`frontend` `package.json`

## 本版要点（含 08-08 热修）

### 写文件 / 防复读（P0）
- **path 改写**：workspace 内绝对路径自动转相对，避免 `path:workspace` 拒绝 `file_write`
- **write_intent**：用户要落盘 spec/文档时，跨轮累计 explore streak；读过一次后**硬裁工具表**只留 `file_write`/`edit`
- 写意图下跳过 timid 读提示；禁止 text-only force_final 长文复读
- 附件正文上限 50k，减少「截断后反复 file_read」

### 超时 / 压缩 / 后台
- 工具超时 → 长命令 adopt 后台，不误杀；`[Timeout]` 计入错误
- 上下文 L3/L5 压缩与 history normalize；同文 8s 去重；超时后 auto_remember

### Web Search（桌面 · 一键包多环境）
- 补齐 `ddgs` + 嵌入式 Python `._pth` / `sitecustomize`
- **启动自愈**：外族 ABI / **残缺 primp**（无 `Client`）→ purge 并用当前解释器重装
- free_search：ddgs backend 改为 `auto/brave/duckduckgo`（去掉已移除的 bing）
- 原生依赖坏时 fail-fast 走 HTML/Wikipedia 瀑布

### 思考链 / 工具结果
- Grok `reasoning_delta` → 可折叠 `<thinking>`
- 工具大结果 spill 与 `result_load` 分页

### Kernel
- Rust `result_store` 阈值/预览上调

### 手机端（APK）
- 剥离 thinking、soft recover、取消流结束误 stop
- versionName `0.5.8` / versionCode 见 APK badging

## 安装包

| 产物 | 说明 |
|------|------|
| Windows NSIS / portable / zip | 桌面一键安装 |
| Android APK | 手机端（本机 LLM + 远端 Agent） |

## 升级注意
- 桌面：**重启一次**即可触发 python-packages ABI 自愈；无需手动 pip
- 若仍搜索失败：检查网络/代理；HTML/Wikipedia 兜底不依赖 API Key
- 推理模型建议 `reasoning_effort` 用 medium（max 会长时间「思考中」）
