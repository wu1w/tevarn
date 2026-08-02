# AGENTS.md — 工作区协作与运行契约

> 本文件由会话启动契约自动注入。用户本轮明确指令优先。

## 环境事实（2026-08-02）
- 平台：**Windows + cmd**（非默认 bash）
- 仓库/沙箱根：`Documents\\kimi\\workspace\\takton`
- 会话记忆/契约文件优先：`%APPDATA%\\takton\\data\\workspace\\`
- Skills 实际：`<repo>\\.computers\\main\\home\\.takton\\skills`
- 开发 API：`http://127.0.0.1:8090` · 前端：`http://127.0.0.1:3000`

## Shell 纪律（Windows）
| 目的 | 正确 | 错误 |
|------|------|------|
| 串联命令 | `cmd1 & cmd2` | `cmd1; cmd2`（bash） |
| 列目录 | `dir` | `ls`（除非 WSL/Git Bash） |
| 读文件 | `type file` | 盲目假设 POSIX 工具齐全 |
| 路径 | 带空格用引号；优先绝对路径 | 混用未转义路径导致失败后误判「工具坏了」 |

## 数据纪律
- **禁止 mock / 演示造数**（含 kernel cost_charge、cache_record 灌水）
- 密钥只进 DB settings / 系统密钥环，**禁止**写入 workspace 明文
- 一次性探针脚本用完删除，勿长期堆在 workspace
- 结论须有日志/DB/命令输出依据

## 产品主路径（IconRail）
工作台 `/` · 员工 `/agents` · 审批 `/approvals` · 联系员工 `/chat` · **用量 `/usage`** · 内核 `/kernel` · 设置 `/settings`

## 子代理协作模式

### 该委派
- 耗时批处理、深度审查、可并行独立子任务、需隔离上下文

### 自己干
- 一两步能完、需与老板多轮确认、依赖全局对话史、老板要求「你亲自来」

### 委派模板
```
任务：[目标]
输入：[上下文]
输出要求：[格式]
约束：[时间/质量]
验证标准：[完成定义]
```

### 协作类型
- **Researcher**：多源调研 + 引用 + 置信度
- **Reviewer**：找问题优先，严重度分级
- **Executor**：按指令精准执行 + 异常报告

### 质量
关键产出走 初稿 → 审查 → 修正；不跳过审查直接上线。
