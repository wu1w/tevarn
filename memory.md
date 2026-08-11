# Memory Index

## 🎯 当前焦点
- [takton-kernel] feature/agent-kernel **0.5.4-alpha**：ChatGPT OAuth · CEO 全开令牌 · 持久化用量 ledger · prompt cache 稳定化 · okr_goal
- 知识/契约与真实落盘路径对齐（见下方「路径权威」）

## 📂 路径权威（Windows · 以实际磁盘为准，勿信过期文档）

| 角色 | 实际路径 | 说明 |
|------|----------|------|
| **源码 / 沙箱 workspace 根** | `C:\Users\wuyw\Documents\kimi\workspace\takton` | Job 沙箱 cwd；git 仓库根 |
| **桌面 userData workspace（契约/记忆默认查找）** | `%APPDATA%\takton\data\workspace` | Electron/桌面侧会话契约与 memory.md |
| **Computer 虚拟 home** | `<repo>\.computers\main\home` | 沙箱 HOME；其下 `.takton` 为运行态 |
| **Skills（实际）** | `<repo>\.computers\main\home\.takton\skills` | 用户/clawhub 技能落盘处 |
| **Skills（别名）** | `%USERPROFILE%\.takton\skills` | 若存在则为目录或链接；**以 computer home 为准** |
| **Kernel / grants / tool_results** | `%USERPROFILE%\.takton\` | session_grants.json、tool_results、kernel 旁路状态 |
| **桌面 DB / 上传** | `%APPDATA%\takton\` | takton.db、uploads、Preferences |

> 自查常见误报：`AppData\...\workspace` 或 `~\.takton\skills`「不存在」时，先看 **repo 根** 与 **`.computers\main\home\.takton\skills`**。  
> **禁止**为演示往 cost/cache ledger 灌 mock 数据。

## 🖥 执行环境（本机）
- OS：**Windows**；命令工具默认 **cmd.exe**（不是 bash）
- 命令串联：用 `&`，**不要** bash 的 `;`
- 列目录：用 `dir`，**不要** `ls`（除非明确在 Git Bash/WSL）
- 路径分隔：优先 `\\` 或 pathlib；写 shell 时注意引号
- 源码开发后端：`127.0.0.1:8090`；前端 Next：`127.0.0.1:3000`（rewrites → 8090）
- Kernel host：`127.0.0.1:17890`，二进制 `target\\release\\takton-kernel-host.exe`

## 📂 项目清单
| 项目 | 路径 | 状态 | 最后更新 |
|------|------|------|----------|
| takton-kernel (agent-kernel) | 仓库 `feature/agent-kernel` | 进行中 | 2026-08-02 |
| takton-optimization | projects/takton-optimization.md | 归档/并入 kernel 线 | 2026-07-22 |

## 📋 跨会话待办
- [ ] GitHub：`gh` 已装（约 2.96.x）→ **待 `gh auth status` 非交互确认**（勿在会话里盲触交互 login）
- [ ] Qdrant 向量库（老板：可后置；本地模式用 memory/Wiki）
- [ ] SMTP（老板：可后置）
- [x] 用量 UI：侧栏「用量」`/usage`，按供应商/模型筛选 cost + cache
- [x] force 删会话 cancel_agent + clear grants；result_load 绑 process_id

## 🚫 安全红线（公司电脑 · 2026-08-07 老板明令）
- 本机为**公司电脑**，有严格安全限制
- **禁止使用 PowerShell**
- **禁止扫描本地环境**（不做环境探测/信息收集式扫描）
- **禁止在内网中横向移动**（不主动连接/探测内网其他主机）
- 违反即停手，先向老板确认

## ⚠️ 活跃风险
- Qdrant 未启 → 向量 RAG 降级（本地 memory/Wiki 仍可用）
- **加密 salt/JWT 不一致** 时 settings 里 LLM Key 可能解不出来（日志 key mismatch）→ 设置页重配
- 路径漂移：契约写 APPDATA workspace、技能在 computer home —— **改文件前先 resolve 实际路径**
- cost/cache 为 **kernel 进程内累计**，host 重启清零（非持久账单）

## 📝 近期决策 / 交付
- 2026-08-02: 知识与契约按真实 Windows 路径重写；IconRail 增加用量；审计 P1/P2 合入 feature/agent-kernel
- 2026-07-22: coding_pipeline 线性 DAG；长记忆用 memory_pref/knowledge/workspace 文件
