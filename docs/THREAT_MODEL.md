# Takton 威胁模型（H2 · 0.5.0-alpha）

**范围**：本地优先、单用户工作站上的 **Agent 控制平面**（Capability Token · Permission Court · mediate · 审计）。  
**非范围**：多租户 SaaS、对抗国家级 APT、防物理接触本机。

---

## 1. 资产

| 资产 | 说明 |
|------|------|
| CapabilityToken | 进程能力边界；伪造 = 提权 |
| 工具执行路径 | 读写文件、命令、网络 |
| 审计哈希链 | 可检测篡改，**非**防删除 |
| JWT / HMAC 密钥 | 会话与 token 签名 |
| 用户工作区文件 | 经 path 策略与沙箱约束 |

---

## 2. 信任边界

```
[用户 UI / 本地进程]
        │  JWT
        ▼
[Backend API] ── RPC ──► [Rust Kernel Host 127.0.0.1]
        │
        ├── Agent loop (Python 脑)
        └── Computer backends / skills
```

- **Kernel Host** 仅监听本机回环（默认 `127.0.0.1:17890`）。
- 密钥与数据 **同机**：能读本机配置的人可签发 token / 关闭治理（见旁路）。

---

## 3. 攻击者画像

| 角色 | 能力 | 目标 |
|------|------|------|
| 恶意提示 / 工具输出 | 控制 LLM 输出与部分 args | 越权工具、读密钥文件 |
| 本地恶意 skill 包 | 安装未签名内容 | 代码执行、数据外泄 |
| 配置失误操作者 | 关 kernel / DEV_UNSAFE | 无意去掉治理 |

**不假设**：远程未认证攻击者直连 kernel host（应保持 bind 本机 + 防火墙）。

---

## 4. 控制与残留风险

| 控制 | 状态 | 残留风险 |
|------|------|----------|
| Capability 单调收窄 + HMAC | ✅ | 密钥与 JWT 同机；优先 `TAKTON_TOKEN_HMAC_SECRET` 解耦 |
| Permission Court + mediate | ✅ | 路径 key 不全时的旁路（H2 已扩展 key） |
| Intent → schema 裁剪 | ✅ H2 | DEV_UNSAFE 仍可全开 |
| 生产禁止 Python fallback | ✅ H2 | 显式 `TAKTON_KERNEL_BACKEND=python` 可绕过 |
| 审计哈希链 + rotation | ✅ H2 | `TAKTON_AUDIT_WORM=1` 永不删段；外部 `*.anchor.json` 锚定 tip |
| Context/Memory 隔离 | ✅ | process/identity namespace；跨身份 deny |
| 多设备 sync | ✅ LWW | 需双方在线/传 envelope；非端到端加密通道 |
| 工具结果 spill | ✅ 激进默认 | 句柄可 `result_load`；磁盘在 `~/.takton/tool_results` |
| 包签名 / require_secure | ✅ | insecure_default 仅开发 |

---

## 5. 明确旁路（必须知情同意）

| 开关 | 效果 |
|------|------|
| `TAKTON_DEV_UNSAFE=1` | 允许 Python kernel fallback、capabilities=None schema、关 kernel |
| `TAKTON_KERNEL_BACKEND=python` | 强制废弃 Python 权威 |
| `agent_kernel_enabled=False` | 生产被忽略；仅 DEV_UNSAFE 生效 |
| `agent_budget_hard_cap_only=True` | 关闭 soft renew，硬顶语义 |
| `single_user_mode=True`（**默认**） | 进程/confirm 归属放宽；开多用户必须显式 `False`，否则等于没有进程归属 |
| `agent_run_snapshot_persist` | 聊天 partial/live_tools 可落盘 `~/.takton/run_snapshots/`；默认截断 tool result（`agent_run_snapshot_disk_full_tools=False`）。共享机注意 HOME 权限 |

### 多 worker / Redis

- 进程 busy 门：优先 Redis；Redis 挂掉时**退化为本机锁**——多 worker 部署须把 Redis 当硬依赖，或接受跨 worker 并发放宽。
- run snapshot 落盘用 `.tmp` + `replace`（单进程安全）；多 worker 写同一 session 文件可能互相覆盖（建议 session sticky）。
- `list_processes` 多用户过滤失败 → **503**，不退回全量列表。

### Confirm 归属

- `request_confirmation` 在 `single_user_mode=False` 时**必须**带 `user_id`。
- `resolve`：pending 有 owner 必须匹配；无 owner 且多用户 → 拒绝。

---

## 6. 密钥建议

1. 设置 **独立** `TAKTON_TOKEN_HMAC_SECRET`（≥16， entropic）。  
2. JWT secret 与 token HMAC **分离**；泄露 JWT 不应自动可伪造 capability。  
3. 包签名：`TAKTON_PKG_SIGNING_KEY` 或 JWT 派生；生产 `TAKTON_PKG_REQUIRE_SECURE=1`。  
4. 轮换：换 secret 后旧 token 失效 → 进程需 re-issue（预期行为）。

---

## 7. 对外表述

> Takton 0.5.0-alpha 是 **可治理的本地 Agent 控制平面（alpha）**，不是完整 AIOS，也不是 hardened multi-tenant OS。  
> 默认安全基线在 H2 后变硬；开发逃生舱仍存在且必须显式开启。
