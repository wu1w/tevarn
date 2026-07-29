# Takton AIOS 互操作协议 0.2

**定位**：单用户数字班子 OS 的**本地互操作面**，不是多厂联邦 SaaS。  
**用户心智不变**：只讲 **员工 / 工单 / 审批**；协议名词不对主人弹。  
**0.2 增量**：领域事件多客户端订阅、`client_guide`、Runtime 心跳入口写进 manifest。

---

## 1. 端点

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/kernel/events/domain` | 领域事件快照；`since_ts` / `after_seq` 续订 |
| WS | `/api/ws/domain?token=` | 领域事件实时流（snapshot + live） |
| GET | `/api/runtime/status` | Kernel Host 心跳（loopback 含 badge） |
| GET | `/api/kernel/protocol/manifest` | 协议版本、心智、non-goals、入口索引 |
| GET | `/api/kernel/protocol/concepts` | 三词产品词典 + 工程名映射 |
| GET | `/api/kernel/protocol/governance` | 红线、策略预设、内核面 |
| GET | `/api/kernel/protocol/surface` | 可研究级 kernel surface |
| GET | `/api/kernel/protocol/agent-cards` | 全部员工 Agent Card |
| GET | `/api/kernel/protocol/agent-cards/{id}` | 单个员工 Card（id 或 name） |
| POST | `/api/kernel/protocol/a2a/tasks` | A2A-lite 任务 → Inbox 工单 |

---

## 2. Agent Card（员工可移植描述）

风格对齐业界 Agent Card / A2A 描述：`name`、`skills`、`capabilities`、`takton` 扩展。

```json
{
  "kind": "agent_card",
  "protocol_version": "0.1.0",
  "name": "研究员",
  "description": "research — …",
  "skills": [{ "id": "web_search", "name": "web_search", "tags": ["capability"] }],
  "takton": {
    "identity_id": "…",
    "status": "active",
    "token_budget": 40000,
    "product_concept": "employee"
  }
}
```

---

## 3. A2A-lite 任务信封 → 工单

最小字段：

```json
{
  "instruction": "调研今日新能源政策并写摘要",
  "identity_name": "研究员",
  "priority": 5,
  "metadata": { "caller": "external-script" }
}
```

或 parts 风格：

```json
{
  "message_id": "msg-1",
  "parts": [{ "type": "text", "text": "…" }],
  "metadata": { "identity_id": "<uuid>" }
}
```

映射：

- text → `AgentInboxItem.instruction`
- identity_* → 编制员工
- source 语义记入 `payload.a2a_*`；Inbox 投递源为 `api`（白名单）

**非目标（0.1）**：多跳 A2A 路由、跨站 push、完整 Google A2A 规范兼容测试套件。

---

## 4. 治理骨架

`GET /kernel/protocol/governance` 导出：

- **red_lines**：能力单调收窄、提权唯一扩权、进化人批、工单有界、哈希链…
- **policy_presets**：`relaxed_visible` / `locked`
- **kernel_surface**：kernel / identity / inbox / dispatcher / protocol 分层清单

与运行时 `approval_rules` 互补：预设描述意图，settings 里的规则控制开关。

---

## 5. 与「网上 AIOS」对齐到哪一步

| 业界需求 | 0.1 覆盖 |
|----------|----------|
| Agent 可描述 / 可移植契约 | Agent Card |
| 任务投递互操作 | A2A-lite → Inbox |
| Safety / governance 可列举 | red_lines + surface |
| Lifecycle / memory / tools 分层 | surface 文档化 |
| 全量 A2A 联邦 / 多租户 | **明确不做** |

实现：`backend/kernel/protocol_spec.py`、`governance.py`、`api/routes/protocol.py`。
