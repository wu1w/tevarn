# 记忆写入权威（0.5.1 / 0.6 冻结新后端）

## 原则

| 层 | 写什么 | 谁写 | 读优先级 |
|----|--------|------|----------|
| **Identity Memory** | 人格 / 职责 / 经验 / 偏好 / 方法论 | hire、述职批准、system | 员工工单 prompt **最高** |
| **Session / CtxItem** | 本会话工作记忆 | loop 压缩与用户消息 | 当前对话 |
| **Entities** | 可命名事实实体 | 显式工具 / 导入 | 场景 rich 时 |
| **Wiki** | 文档型知识 | 知识库导入 | 检索命中时 |
| **Memory Graph** | 图投影（召回） | remember 工具 | 辅助，非编制真源 |

## 禁止（0.6 前）

- 再加第五套记忆存储
- 员工工单只读 graph 不读 Identity Memory
- 进化静默改 Identity Memory 不经审批

## 实现锚点

- 写入：`IdentityRegistry` memory APIs、`crew_steward.hire`
- **统一读写收口**：`backend/kernel/crew_memory.py`
  - `CrewMemoryAssembler` — 注入块（workforce / chat / preview / compact）
  - `CrewMemoryWriter` — 沉淀门禁（失败不写、auto_distill 默认关、distilled 需 approved_by）
- 读取：`dispatcher._build_memory_block` → Assembler；`POST .../memory/preview`
- 废止：`POST .../memory/{id}/retire`（tombstone，Assembler 跳过）
- 手动沉淀：`POST .../memory/distill-from-item`
- UI：员工资料卡「记忆」tab
- 配置键：`crew_memory_experience_max_inject`、`crew_memory_auto_distill`、
  `crew_memory_require_approve_distill`、`crew_memory_auto_distill_min_chars`

## 与 RAG 兼容

| 层 | 角色 |
|----|------|
| Identity Memory (SQLite) | **唯一编制真源** |
| Qdrant `identity_memory` | 写入时 best-effort 投影；`CrewMemoryAssembler` 在 experience 超 cap 时 `search_identity_memory` top-k |
| knowledge / wiki | 知识问答，**不**顶替 persona/duty |

硬约束：向量命中必须对齐 `current_memory` 且非 tombstone；向量不可用 → 关键词 → 最新 N 条。  
联调脚本：`python -m backend.scripts.smoke_vector_crew_memory`（需 Embedding + Qdrant）。
