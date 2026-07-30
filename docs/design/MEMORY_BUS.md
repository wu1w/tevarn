# 记忆总线（Phase 3.1）

> 一页纸。**业务写入只走总线；RAG 不是权威。**

与 `docs/internal/MEMORY_AUTHORITY.md` 对齐并扩展到 graph/entity/wiki。

---

## 一句话

**一切长期记忆写入经 `memory_bus`；Identity Memory 是编制真源；Graph/Entity 是事实投影；Wiki 是文档知识；Qdrant 只做索引。**

---

## 权威表

| 记忆类型 | kind 示例 | 权威存储 | 谁能写 | 冲突裁决 | 向量 |
|----------|-----------|----------|--------|----------|------|
| 人格/经验 | persona, duty, methodology, preference, experience | `identity_memory` | 本 Identity + 审批 | supersede 版本链 | 随权威同步 |
| 事实/实体 | entity / fact | `entities` | 任何 Run（经总线） | supersede / archive | 可选 |
| 图记忆 | knowledge, decision, preference, experience | `memory_nodes` | agent 工具 / 总线 | supersede 节点 | 本期不做独立写 |
| 文档知识 | wiki | wiki 表 | 用户 + 审批 agent | 人工 | 索引层 |
| RAG | — | Qdrant | **禁止业务直写** | n/a | 仅索引 |

---

## API

```text
remember(kind, content, *, source_run_id?, confidence=1.0,
         identity_id?, user_id?, title?, tags?, meta?) -> MemoryWriteResult

supersede(ref, new_content, *, approved_by, source_run_id?) -> MemoryWriteResult

recall(query, kinds?=None, top_k=8, identity_id?, user_id?)
  -> list[MemoryHit{source, id, kind, title?, content, score, freshness, version?}]
```

### kind 路由

| 输入 kind | 路由 |
|-----------|------|
| persona / duty / methodology / preference / experience | identity（需 `identity_id`） |
| knowledge / decision | memory graph |
| entity / fact | entities |
| wiki | wiki_entities 真写（同名更新 version；人类导入 API 仍可用） |
| graph 别名 preference/experience 无 identity_id 时 | memory graph |

---

## 禁止

- 工具/技能/进化路径绕过总线直接 `repo.add_node` / `Entity()` 业务写  
- 把 Qdrant 命中当「可改真源」  
- 再发明第五套记忆表  

## 实现

- 模块：`backend/services/memory_bus.py`  
- 底层仍复用：`IdentityRegistry`、`AsyncMemoryGraphRepository`、entity service  
- 编制注入：仍走 `CrewMemoryAssembler`（读侧）；沉淀写走 Writer → 总线  

## 验收

1. 业务写点（memory_graph 工具、crew record_manual）经总线。  
2. supersede 后 `recall` 不返回旧版本。  
3. 回归：`test_memory_bus` + 既有 crew/graph/authority 测试绿。  
