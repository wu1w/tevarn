# Takton 自主进化 + HAEE 集成方案

> 版本：v0.3（已拍板：B + auto_apply）  
> 日期：2026-07-16  
> 状态：**已拍板，实施中**  
> 决策：  
> - 方案 **B**（Phase 1 验收环 + Phase 2 改进/门禁/进化中心 UI）  
> - **`auto_apply_skills: true`** — 过五道安全门后自动 active，无需再点采纳（失败仍只留 draft/日志）  
> - 可视可管理硬性：清单 / use_count / 删除  
> 范围：Hermes 学习环 + HAEE 验收 + 进化资产可视可管  
> 非目标：整包 fork Hermes PR #61645；自动改 backend 业务代码；黑盒堆不可见知识

---

## 1. 问题与目标

### 1.1 现状（Takton）

| 已有 | 缺口 |
|------|------|
| `SkillRegistry` + builtins / DB dynamic skills | skill **创建/改写无验收** |
| system_prompt 写了 memory/skill 纪律 | **无强制** post-task 落盘 |
| Goal / todo / checkpoint / 多轮工具 | 目标「完成」≈ 模型说 done，**无任务准则** |
| 多信源聚合、远程 L1、`configure_takton` | 无 **失败分类 / 回归门 / 进化指标** |
| 会话与 DB | 无 evolution 轨迹表 |

### 1.2 Hermes 侧可借鉴的两层

| 层 | 是什么 | 价值 | 风险 |
|----|--------|------|------|
| **学习环**（已合入 Hermès 的方向） | 复杂任务后写 skill；使用中 patch；memory 分层 | 越用越顺 | 只写不验 → skill 腐化 |
| **HAEE**（PR #61645，未合 main，P3） | 评估 5 法 + 失败 10 类 + 5 安全门 + 聚类 + skill 代数进化 | 把「self-improving」做成可测 | 体量大、作者自报基准、合入未定 |

### 1.3 产品目标（Takton 版命名建议）

**TEE — Takton Evolution Engine**（对内模块名；对外可叫「自主进化」）

一句话：

> **正常对话不打断** → 可选开启进化 → **验收结果** → **安全改进 skill/提示** → **清单可见、次数可查、无用可删、默认可关**。

成功标准（方案级）：

1. 关 `evolution.enabled` 时 **零开销**（不加载 evolution 子树也可）。  
2. 开之后：至少能对「可定义任务」打分，失败不静默。  
3. skill 自动写入须过 **安全门**；高风险改动要确认。  
4. 与多设备 `@device`、Goal、通道 thinking-only **不打架**。  
5. **可视可管理（硬性）**：用户能看到 Agent 自主归纳了哪些东西、每样用了多少次，并能手动删除无用项；禁止黑盒越学越多。

---

## 1.4 可视可管理（一等公民）

自主进化若不可见，对用户是风险：skill / 任务准则 / 草稿会越积越多，占上下文、占磁盘、行为难预期。  
**铁律：凡 Agent 自主写入的资产，必须进入「进化中心」清单，可计数、可筛选、可删除。**

### 用户要看见什么

| 展示项 | 说明 | 示例 |
|--------|------|------|
| 资产类型 | 分类标签 | 技能草稿 / 已启用技能 / 验收任务 / 记忆候选 |
| 标题与摘要 | 人话一句 | 「上海天气多源合并话术」 |
| 来源 | 怎么来的 | `auto` 自主 / `user` 手建 / `seed` 预置 |
| 状态 | 生命周期 | draft · active · disabled · rejected |
| **使用次数** | 实调用次数 | `use_count` |
| 最近使用 | 时间 | `last_used_at` |
| 创建/更新 | 时间 | `created_at` / `updated_at` |
| 版本 | skill 代数 | Gen0 / Gen1 |
| 最近验收分 | 0–1 或 — | `last_score` |

**页顶汇总条：**

- 自主归纳总数（`source=auto`）
- 生效中 / 待审草稿 / **从未使用**（`use_count=0`）
- 本周新增 · 本周使用 Top

### 用户能做什么

| 动作 | 说明 |
|------|------|
| 查看详情 | 全文、门禁结果、轨迹摘要、来源会话 |
| 启用 / 停用 | active ↔ disabled（停用不删历史） |
| 采纳 / 拒绝草稿 | draft → gate → active，或 rejected |
| **删除** | 无用项手动删；确认弹窗 |
| **批量删未使用** | 筛选 `use_count=0` 且 `source=auto` |
| 导出清单 | 可选 JSON/CSV 审计 |

**删除规则：**

- `seed` / 系统内置：不可删（可隐藏）
- `auto` / `user`：可删；删后 agent 不再加载
- 近 7 天高频 `active` 项：二次确认

### 使用次数怎么计（可解释）

| 事件 | 计数 |
|------|------|
| skill/tool **成功执行** | `use_count + 1`，更新 `last_used_at` |
| 仅 skill_view 读入 | `view_count + 1`（高级列，默认不主展示） |
| 任务准则被评估 | task `run_count + 1` |

UI 默认只 prioritise **实调用 use_count**，避免「看过」虚高。

### UI：`/evolution` 进化中心

- 侧栏名称：**自主进化**（靠近「技能」）
- 待审草稿角标数字
- 结构：总览卡 → 筛选（类型/状态/来源/**仅未使用**/排序）→ 列表（次数+删除）→ 详情抽屉
- Skills 页角标「待审 N」链到本页
- 对话：`进化里有哪些没用过的` / `删掉从未用过的草稿`（`evolution.list|stats|delete`，删前 confirm）

### 管理 API

| Method | Path | 说明 |
|--------|------|------|
| GET | `/api/evolution/assets` | 清单；`unused_only` / `sort=use_count` |
| GET | `/api/evolution/assets/{id}` | 详情 |
| GET | `/api/evolution/stats` | 汇总条 |
| POST | `/api/evolution/assets/{id}/enable` | 启用 |
| POST | `/api/evolution/assets/{id}/disable` | 停用 |
| DELETE | `/api/evolution/assets/{id}` | 删除 |
| POST | `/api/evolution/assets/bulk_delete` | `ids[]` 或 `filter=unused_auto` |
| POST | `/api/evolution/drafts/{id}/apply` | 采纳 |
| POST | `/api/evolution/drafts/{id}/reject` | 拒绝 |

### 资产统一字段

```
id, kind, name, summary, source, status,
use_count, view_count, last_used_at,
created_at, updated_at, session_id_origin,
content_ref, gen, last_score, meta_json
```

### 可视可管理验收

1. 自主产生 1 条 draft → 进化中心 **立刻可见**  
2. 调用该 skill 3 次 → **use_count=3**  
3. 用户删除 → 列表消失且 agent **不再加载**  
4. 筛选未使用 + 批量删除可预览条数  
5. seed/builtin 无删除或禁用删除  

| Phase | 可视交付 |
|-------|----------|
| P1 | assets/stats API + use_count 挂钩；可 curl 清单 |
| P2 | 完整 `/evolution` 页：筛选、删除、采纳、次数 |
| P3 | 批量清未使用、角标、对话删除、Top 使用 |

**无论选型 A/B/C，最小「清单 + 次数 + 删除」不砍。**

---

## 2. 能力对照与裁剪

### 2.1 HAEE 组件 → Takton 取舍

| HAEE 组件 | 是否引入 | Takton 落点 | 说明 |
|-----------|----------|-------------|------|
| TaskDefinition（YAML 准则） | **P0** | `backend/evolution/tasks/` + DB | 验收的根 |
| Evaluator（5 法） | **P0** | `evaluator.py` | 先 4 硬 + 1 软（llm_judge） |
| TrajectoryCollector | **P0** | 会话/工具轨迹摘要 | 复用 loop 已有 tool 事件 |
| FailureAnalyzer（规则层） | **P0** | 规则优先 | LLM 层 P1 |
| FailureAnalyzer（LLM 层） | P1 | 辅助调用 | 控成本 |
| ImprovementProposer（skill） | **P0** | 生成/patch `SKILL.md` 或 DB skill | 默认 draft |
| RegressionGate（5 门） | **P0** | 强制 | seesaw 必须 |
| Skill 代数进化 GenN | P1 | skill 版本表 | 可回滚 |
| ConversationObserver / 聚类 | P1 | 使用模式 | 小白「你常做的事」 |
| Auto-trigger 对话内抓失败 | P1 | loop 钩子 | 不刷屏 |
| PR proposer / HyperAgents | **P2 / 不做默认** | — | 与「真机 agent」定位弱相关；可后接 GitHub |
| Atropos 导出 | P2 | 与用户语料 jsonl 目标对齐 | 训练友好 |
| CLI `hermes evolution *` | P1 | `takton evolution` + **对话 configure** | 对齐「对话框搞定一切」 |
| 改 4 处 runtime 钩子 | **P0** | `loop.py` 少量钩子 | 镜像 HAEE 接入方式 |

### 2.2 Hermes 学习环 → Takton

| Hermes 行为 | Takton 映射 |
|-------------|-------------|
| 难任务后建议 skill_manage | 进化开：自动 **提议** skill；关：保持现有提示 |
| skill_view 使用中发现错就 patch | ImprovementProposer + Gate 后 patch |
| memory 存事实不存过程 | 不变；过程进 trajectory，不进 memory |
| curator 不验 skill | **TEE 补验**（这是 HAEE 精华） |

---

## 3. 目标架构

```
                    ┌─────────────────────────────────────┐
                    │           Agent Loop (现有)          │
                    │  run → tools → aggregate → final    │
                    └──────────────┬──────────────────────┘
                                   │ hooks (可选, evolution.enabled)
           ┌───────────────────────┼───────────────────────┐
           ▼                       ▼                       ▼
   TrajectoryCollector      AutoTrigger(轻量)        TurnFinalizer
   (工具/命令/文件痕迹)      (失败启发式)              (会话末结算)
           │                       │                       │
           └───────────────────────┴───────────────────────┘
                                   ▼
                        EvolutionManager (单例编排)
                                   │
        ┌──────────────┬───────────┼───────────┬──────────────┐
        ▼              ▼           ▼           ▼              ▼
   TaskBank      Evaluator   FailureAnalyzer  Improver   RegressionGate
   (准则库)       (打分)        (归因)         (skill稿)    (放行/拒)
        │              │           │           │              │
        └──────────────┴───────────┴───────────┴──────────────┘
                                   ▼
                         EvolutionStore (SQLite)
                         trajectories / scores / skill_versions
                                   │
                    ┌──────────────┴──────────────┐
                    ▼                             ▼
            Skills (draft→active)          UI / 对话 configure
            + 可选 KB 手册条目              evolution status
```

### 3.1 设计不变量（必须写进实现）

1. **默认关闭**：`evolution.enabled: false`。  
2. **只改 skill / 任务库 / 提示片段**，默认 **不** 自动改 `backend/` 业务代码。  
3. **Gate 失败 = 不落地**；草稿可进 `skill_versions` 供人审。  
4. **不向通道刷进化过程**（QQ/企微保持 thinking-only 产品纪律）；进化日志走后端 + 可选「进化」页。  
5. **密钥/内网/设备 token** 永不写入 skill 正文（扫描门）。  
6. 与 **多信源聚合** 顺序：先 aggregate 出用户答复，再异步/同轮末尾跑评估（不阻塞首包可配置）。

---

## 4. 数据与配置

### 4.1 配置（`settings` / `.env` / configure_takton）

```yaml
evolution:
  enabled: false                 # 总开关
  mode: on_failure               # off | on_failure | always | manual
  max_iterations: 3              # 单任务改进轮次上限
  llm_judge: true                # 是否允许软评估
  auto_apply_skills: false       # true=过门后自动 active；false=仅 draft
  safety:
    require_confirm_high_risk: true
    max_skill_bytes: 32000
    ban_patterns: ["ghp_", "sk-", "192.168.", "token="]  # 可扩展
  store_path: null               # 默认 data/evolution.db 或并入主库
  export_training: false         # 对齐语料 jsonl
```

对话配置键建议挂到 `configure_takton`：  
`evolution.enabled` / `evolution.mode` / `evolution.auto_apply_skills`（高风险需 confirm）。

### 4.2 表结构（可并入主 SQLite 或独立）

| 表 | 用途 |
|----|------|
| `evo_tasks` | 任务定义：name, domain, criteria_json, enabled |
| `evo_runs` | 一次执行：session_id, task_id?, score, status, failure_codes |
| `evo_trajectories` | 压缩轨迹 YAML/JSON（工具名序列、关键 stdout 摘要） |
| `evo_skill_versions` | skill_name, gen, content, parent_gen, gate_result, state(draft/active/rejected) |
| `evo_metrics` | 聚合：任务均分、改进 delta（后期 Wilcoxon 可选） |

### 4.3 任务准则（Task criteria）— P0 五种

与 HAEE 对齐，便于以后对照论文/PR：

| type | 含义 | Takton 实现要点 |
|------|------|-----------------|
| `test_pass` | 跑测试退出码 0 | 本机 pytest / 项目脚本；远程则 `@device` exec |
| `file_exists` | 路径存在 | 本地或 remote file.list/stat |
| `content_match` | 文件/输出含正则或子串 | 注意敏感信息截断 |
| `command_output` | 命令输出匹配 | 白名单命令 + 超时 |
| `llm_judge` | 结构化打分 0–1 | 用现有 LLM；rubric 短 |

预置任务包（P0 先 4 个，可扩到 10）：

1. `smoke-health` — `/api/health` ok  
2. `skill-weather-shape` — weather skill 返回含温度字段  
3. `remote-ping` — 已配对设备 ping 成功（无设备则 skip）  
4. `kb-search-nonempty` — 知识库检索非空（有 seed 时）

---

## 5. 运行时接入点（最小改 loop）

仿 HAEE「4 钩子」，Takton 建议：

| 钩子 | 位置 | 行为 |
|------|------|------|
| `on_session_start` | `loop.run` 入口 | 若 enabled：装 EvolutionManager、挂 observer |
| `on_tool_result` | 每轮 tool 后 | Trajectory 追加；AutoTrigger 规则扫描 |
| `on_turn_final` | final_content 落库前/后 | 若匹配 task 或 trigger：Evaluate →（可选）Improve 草稿 |
| `on_session_end` | 会话结束/超时 | flush metrics；清理临时状态 |

**延迟策略（产品）：**

- `mode=on_failure`：仅启发式失败或 score&lt;阈值 才评估改进  
- 评估默认 **同进程 async**，超时 15–30s；失败只记日志不影响用户答复  
- 可选 `evolution.defer=true`：用户答复先返回，后台评估（推荐 QQ 通道）

---

## 6. 安全门（RegressionGate）— 必须有

任一不通过 → **禁止 active**，只保留 draft + 原因：

| 门 | 检查 |
|----|------|
| G1 Manifest | skill 必有 name/description/parameters 或 SKILL 结构完整 |
| G2 Content | 无 ban_patterns；无明显可执行破坏指令（`rm -rf /` 等） |
| G3 Smoke | 对绑定 task 重跑 criteria，score ≥ 基线 |
| G4 Size | 不超过 `max_skill_bytes`；diff 行数上限 |
| G5 Seesaw | 关联回归集任务均分不得下降超过 ε（如 0.05） |

`auto_apply_skills=false` 时：过门后状态=`pending_review`，UI/对话「采纳进化草稿」。

---

## 7. 分阶段交付

### Phase 0 — 方案冻结（0.5 天）

- [ ] 用户确认：模块名、默认关、是否允许 auto_apply  
- [ ] 确认：**进化中心**必做（清单 / use_count / 删除）  
- [ ] 确认不做：自动改 backend 代码 / 自动提 GitHub PR（P2 再议）

### Phase 1 — 验收环 MVP（约 3–5 天）**【铃推荐先做】**

**交付：**

1. `backend/evolution/` 包：config, store, task_definition, evaluator, trajectory, manager  
2. loop 两钩子：`on_tool_result` 轨迹 + `on_turn_final` 可选评估  
3. 4 个预置 task + CLI/API：`GET /api/evolution/status`，`POST /api/evolution/run_task`  
4. **`GET /api/evolution/assets` + `/stats` + use_count 挂钩**（清单可查）  
5. `configure_takton` 增加 evolution 开关说明  
6. 文档 `docs/EVOLUTION.md` 用户向短文  

**验收：**

- enabled=false 时单测/压测无额外 LLM 调用  
- 手动跑 `smoke-health` score=1  
- 故意坏 criteria 时 score&lt;1 且有 failure_code  
- 轨迹可查询，不含 token 明文  
- **assets 可见 auto 项；调用后 use_count 增加**  

### Phase 2 — 改进环 + 安全门 + 进化中心 UI（约 5–7 天）

**交付：**

1. FailureAnalyzer（规则 6–10 类，中文 code）  
2. ImprovementProposer → `evo_skill_versions` draft  
3. RegressionGate 五门  
4. 对话：`采纳/拒绝 进化草稿`；**删未使用项**  
5. **前端 `/evolution` 进化中心**：筛选、次数、删除、采纳/拒绝、汇总条  
6. Skills 页角标「待审 N」跳转进化中心  

**验收：**

- 构造「错误 weather skill」→ 评估失败 → 生成 draft → 过门前不可 active  
- seesaw：改进 A 任务不拖垮 smoke-health  
- **删除后 use_count 项消失且不再被调度**  

### Phase 3 — 静默进化体验（约 3–4 天）

1. AutoTrigger（工具失败、空结果、用户抱怨关键词、重复重试）  
2. Observer 聚类「常用任务」→ 建议新 TaskDefinition  
3. Skill gen 版本与回滚  
4. **批量清理未使用 auto 资产** + 侧栏角标  
5. 可选训练导出（对齐语料 jsonl，过滤噪音）

### Phase 4 — 可选增强

- llm_judge 成本预算与采样  
- 远程设备 criteria（`@device` 上的 command_output）  
- PR proposer（仅开发者模式）  
- 统计面板（Wilcoxon 等可后置，避免为指标而指标）

---

## 8. 目录与 API 草图

```
backend/evolution/
  __init__.py
  config.py
  manager.py
  store.py
  tasks/
    smoke_health.yaml
    ...
  evaluator.py
  trajectory.py
  failure_analyzer.py      # P2
  improver.py              # P2
  gates.py                 # P2
  hooks.py
  export_training.py       # P3
backend/api/routes/evolution.py
backend/content/product_handbook.py  # +进化手册 topic
```

API（REST，供 UI / configure）：

| Method | Path | 说明 |
|--------|------|------|
| GET | `/api/evolution/status` | enabled, 最近 runs, draft 数 |
| GET | `/api/evolution/stats` | 汇总：自主条数、待审、零使用、Top |
| GET | `/api/evolution/assets` | **可管理清单**（type/status/source/unused_only/sort） |
| GET | `/api/evolution/assets/{id}` | 详情 |
| DELETE | `/api/evolution/assets/{id}` | **用户删除** |
| POST | `/api/evolution/assets/bulk_delete` | 批量删未使用等 |
| POST | `/api/evolution/assets/{id}/enable` | 启用 |
| POST | `/api/evolution/assets/{id}/disable` | 停用 |
| GET | `/api/evolution/tasks` | 任务库 |
| POST | `/api/evolution/tasks` | 自定义任务 |
| POST | `/api/evolution/evaluate` | 对某 session/run 评估 |
| GET | `/api/evolution/drafts` | skill 草稿 |
| POST | `/api/evolution/drafts/{id}/apply` | 采纳（再跑 gate） |
| POST | `/api/evolution/drafts/{id}/reject` | 拒绝 |

---

## 9. 与现有子系统关系

| 子系统 | 关系 |
|--------|------|
| Goal / manage_goal | Goal 完成时可挂默认 criteria；进化不替代 Goal UI |
| 多信源聚合 | 先答复用户，再评估；评估不改用户可见四答案问题的已修逻辑 |
| 远程 L1 | criteria 可声明 `runtime: local|device`；device 需已配对 |
| 知识库 seed | 可增加「进化」新手文；**禁止**写私人主机名 |
| 通道 gateway | 进化事件默认不推 QQ；可配置 home 摘要「今日进化 1 条待审」 |
| 语料收集目标 | trajectory + failure 标签 → 高质量 jsonl（P3） |

---

## 10. 风险与缓解

| 风险 | 缓解 |
|------|------|
| LLM judge 费钱、不稳 | 默认硬准则优先；judge 采样/降级 |
| 自动 skill 写坏系统 | 默认 draft；五门；禁写代码树 |
| 评估拖慢对话 | defer + 超时 + 通道不展示 |
| 与 Hermes 上游漂移 | 只借鉴思想与准则类型，不依赖其 PR 合入 |
| 隐私进 skill | ban_patterns + 人工审 draft |
| 范围膨胀 | 严格 Phase 门禁；P4 需再立项 |

---

## 11. 方案选项（请用户选）

| 选项 | 内容 | 工期感 | 适合 |
|------|------|--------|------|
| **A. 最小验收环** | 仅 Phase 1 | 短 | 先有「可测的进化基础设施」 |
| **B. 验收 + 草稿改进** | Phase 1+2 | 中 | **铃推荐**：完整闭环但不静默改系统 |
| **C. 全量静默进化** | Phase 1–3 | 长 | 接近 HAEE 体验；要更强产品纪律 |
| **D. 先观察不对齐代码** | 只做轨迹+仪表，不 improver | 很短 | 纯研究/语料 |

**铃的推荐：B**  
理由：HAEE 的真正增量是 **验收 + 门禁**；没有门禁的 auto skill = 重复 Hermes 被诟病的点。  
自动 apply 默认 **关**，符合 Takton「高风险要 confirm」。  
**无论 A/B/C，都保留「进化中心」最小集：清单 + 使用次数 + 手动删除**——这是产品底线，不作可选项。

---

## 12. 明确不做（本方案边界）

- 不默认合并社区 HAEE 整 PR 代码（许可/结构/未审核基准）。  
- 不自动 `git push` / 开 PR 到用户业务仓（除非 Phase 4 显式开发者模式）。  
- 不在进化日志里存 API Key、配对 token、内网拓扑真值。  
- 不把 mac 未测能力写进进化验收默认集。

---

## 13. 下一步（拍板后）

1. 用户选 **A / B / C / D**（或改默认：`auto_apply`、是否 defer）。  
2. 铃按 Phase 开工：先 `backend/evolution/` + loop 钩子 + 2～4 个 task + status API。  
3. 每阶段给：可运行演示命令 + 开关证明零开销。

---

## 参考

- Hermes HAEE Issue: https://github.com/NousResearch/hermes-agent/issues/61644  
- Hermes HAEE PR: https://github.com/NousResearch/hermes-agent/pull/61645  
- Takton: `backend/agent/loop.py`, `backend/skills/*`, `configure_takton`, Goal/checkpoint  
- 内部相关：多信源聚合、远程 L1、语料 jsonl 目标  
