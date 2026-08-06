---
name: codex-security
description: OpenAI Codex Security — find, validate, and fix security vulnerabilities with the official CLI/SDK (@openai/codex-security). Use when the user asks for security scan, AppSec review, vulnerability hunt, dependency risk, or codex-security.
license: MIT-derived playbook wrapping OpenAI Codex Security
source: openai/codex-security
homepage: https://github.com/openai/codex-security
triggers: [security, 安全扫描, 漏洞, AppSec, codex-security, vulnerability, 威胁建模, OWASP]
version: "1.0"
---

# Codex Security（openai/codex-security）

本 skill 指导 Agent 使用 OpenAI 官方 **Codex Security** CLI / SDK  
仓库：https://github.com/openai/codex-security  
文档：https://developers.openai.com/codex/security · https://learn.chatgpt.com/docs/security/cli

## 何时启用

用户明确或隐含要求：

- 安全扫描 / 漏洞挖掘 / AppSec review
- 修安全问题、对比两次扫描结果
- 提到 `codex-security`、`@openai/codex-security`、Trusted Access for Cyber

**不要**在普通代码改动里默认全仓 deep scan（昂贵且慢）。先确认范围。

## 前置条件

- Node.js **22.13+（22.x）/ 24.x / 26.x**
- Python **3.10+**
- 凭证二选一：
  - 交互：`npx @openai/codex-security login`
  - CI / 非交互：`OPENAI_API_KEY` 或 `CODEX_API_KEY`（不落盘到 keyring）
- 部分网络安全类请求需 [Trusted Access for Cyber](https://chatgpt.com/cyber)

## 推荐命令（优先用 shell / command 工具执行）

快速扫当前工作区：

```bash
npx -y @openai/codex-security scan .
```

指定模型 / 力度：

```bash
npx -y @openai/codex-security scan . --model gpt-5.6-terra --effort high
```

深度扫描（更贵，需用户同意）：

```bash
npx -y @openai/codex-security scan . --mode deep --workers 2 --subagents 0 --stop-after-no-new 3 --max-discovery-runs 10
```

显式凭证：

```bash
npx -y @openai/codex-security scan . --auth api-key
npx -y @openai/codex-security scan . --auth chatgpt
```

其它 provider（需对应 API Key）：

```bash
npx -y @openai/codex-security scan . --provider openrouter --model anthropic/claude-sonnet-4.5
```

诊断：

```bash
npx -y @openai/codex-security scan . --verbose
# 或 CODEX_SECURITY_LOG_LEVEL=debug
```

对比两次扫描（finding 按根因匹配）：

```bash
npx -y @openai/codex-security scans compare BEFORE_SCAN_ID AFTER_SCAN_ID
```

## Agent 工作流

1. **定范围**：路径、语言栈、是否允许 deep mode、是否可联网。
2. **检查环境**：`node -v`、`python3 --version`；缺依赖先提示安装。
3. **鉴权**：无 key 且未 login → 提示用户配置 `OPENAI_API_KEY` / `CODEX_API_KEY` 或执行 login，**不要编造扫描结果**。
4. **先 quick scan**（默认 `.` 或用户给的路径）；deep 需二次确认。
5. **解读 stdout JSON / reportPath**：按严重度排序，给出：
   - 发现摘要（高/中/低计数）
   - 可复现路径与根因
   - 最小安全修复建议（补丁级，避免无关重构）
6. **修复后**：在用户同意下再扫一次，或 `scans compare` 看是否 resolved。
7. **禁止**：把密钥写进仓库、日志或对话明文复述完整 key。

## 安全与合规

- 只在用户授权的目录扫描；尊重 `.gitignore` / 密钥文件。
- 输出里对 secret 做脱敏。
- 不把扫描报告上传到未授权第三方。
- 若 CLI 不可用，降级为**手工安全审查清单**（OWASP Top 10 / 注入 / 鉴权 / SSRF / 路径穿越 / 依赖 CVE），并标明「未运行 codex-security」。

## 与其它 skill 协作

- 大改前可先 `code-review`；安全专项用本 skill。
- 需要威胁建模叙事时，可结合 threat-model 类 skill；落地扫描仍以 codex-security 为准。
