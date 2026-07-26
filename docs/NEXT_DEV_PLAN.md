# Takton 下一步开发文档 · 内核收敛计划

> 版本：v2 · 日期：2026-07-26 · 范围：`backend/agent/` + `backend/services/tools/` + `backend/services/llm/`
>
> 本文来自一次全量源码扫描（~149k LOC）后，对照 Claude Code / Codex / Hermes 得出的差距清单。
> 每项含：问题证据（file:line）、当前代码、目标方案、验收标准、工作量。
>
> **v2 变更**：T1–T4 已复审确认并实施完成；复审过程中发现 3 处初版遗漏的问题（见 §7 索引
> 第 19–21 条），其中 2 处为并行前必须修的阻断项。测试基线 540 → 582，新增失败 0。

## 实施进度

| 任务 | 状态 | 关键提交内容 |
|---|---|---|
| T1 工具并行 | ✅ 已完成 | `_prefetch_readonly_calls` 并发只读工具；混入写类整批退回串行 |
| T1b 阻塞 I/O | ✅ 已完成 | `file_read` / `grep` / `glob` 改 `asyncio.to_thread` |
| T2 edit 唯一性 | ✅ 已完成 | 多处匹配报错并给出行号；新增 `replace_all` |
| T3 file_read 分页 | ✅ 已完成 | 行号输出 + `offset`/`limit`；**并修复 2000 字符预算首尾拼接** |
| T4 prompt caching | ✅ 已完成 | Volatile 剥离出 `messages[0]`；Anthropic `cache_control` + 缓存指标 |
| T5 权限收敛 | ✅ 已完成 | `working_mode.py` 单一事实源；沙箱默认开；前端可选工作方式 |
| T6 eval harness | ✅ 已完成 | `scripts/bench_agent/` 20 任务 + 断言 + 跨 sha 对比 |

回归状态：**`628 tests / 0 failures`**（基线 540 / 6）。基线那 6 项预存失败已全部处理，
新增失败 0。

## T5 实施纪要：权限体系收敛

原诊断准确但不完整。真正的问题不是「规则写得不对」，而是**三套口径互不知情、
且默认全部形同虚设**：

| 症状 | 证据 |
|---|---|
| 所有 ask 被静默降级为放行 | `agent_permission_ask_mode` 默认 `local_allow` |
| 工具自声明的确认要求从未生效 | `requires_confirmation` 全项目无调用点 |
| 实际边界只剩可轻易绕过的正则 | 沙箱默认关；`$(printf '\x72\x6d')` 即可绕过黑名单 |

收敛方案 —— 把用户真正关心的两个**正交**决定显式化，各自只有一个开关
（`backend/agent/working_mode.py`）：

- **工作方式**：只读探索 / 谨慎 / 自动编辑 / 全自动 → 派生 `profile` + `ask_mode`
- **执行环境**：强制沙箱 / 自动 / 本机直跑 → 派生沙箱后端

关键取舍：

1. **`ask_mode` 新增 `auto`（新默认）**：有确认通道就真弹窗，没有（cron / 渠道
   机器人 / headless）走 `agent_permission_headless` 兜底（默认 allow）。
   直接把默认改成 `interactive` 会让所有定时任务集体卡死——`request_confirmation`
   在无 ws_manager 时返回 False，等于全部拒绝。
2. **执行环境三档语义刻意不同**：`sandbox` 不可用时报错而**不降级**（静默退回本机
   会让用户以为隔离着、其实没有）；`auto` 退回本机但打 `degraded` 标记，UI 明示。
3. **`requires_confirmation` 从死标志变成真语义**：只对 `PermissionGate` 规则未覆盖
   的工具（自定义 / MCP）生效，且只能**收紧**。不这样限定的话，`acceptEdits`
   明确表达的「编辑别问我」会被 `file_write` 的自声明推翻。
4. **正则黑名单降级为 UX 提示**：代码注释里写明它不是安全边界，并用测试
   （`test_dangerous_regex_is_trivially_bypassable`）固化这一事实，防止后人把它当防护依赖。

前端 `/security` 以「工作方式 / 执行环境」两组卡片为主线，并单列
**「当前实际生效」**——用户所选与真正生效可能不一致（本机无沙箱、高级用户覆盖了
底层键），不显示出来就会出现「以为选了就生效了」。

副作用：沙箱默认开启后，5 项旧测试因用 `tmp_path` 作 cwd 被沙箱正确拒绝而失败。
这些测试考察的是**本机执行语义**，已显式声明 `execution_mode="local"`——
execution mode 现在有三档，测试必须声明自己走哪条路径。

## T6 实施纪要：eval harness

`scripts/bench_agent/`，20 个任务覆盖 6 类（fix_bug 6 / feature 4 / answer 3 /
long_task 3 / honesty 2 / safety 2）。

发现的关键事实：**`.gitignore:92` 此前忽略了整个 `scripts/bench_agent/`** ——
这正是 `docs/CORE_RUNTIME.md` 引用 `run_bench.py` 却找不到文件的原因。
已改为只忽略 `bench/results/`（结果产物），harness 本身入库。

设计约束：

- **只认机器可验证的事实**。不用 LLM 当裁判——那会让分数随裁判模型漂移，
  失去「同一 sha 重复跑得同一结论」这个唯一有价值的性质
- **没有 LLM 配置就拒绝运行**，不产出看似成功的假数据
- **每个任务都带防作弊断言**（如 `file_not_contains: skip|xfail`），
  堵住「改测试而非改代码」
- `{python}` 占位符解析为当前解释器绝对路径：裸写 `python` 在 macOS 会 127，
  那样失败的是 harness 而不是 agent，分数就没意义了

`backend/tests/test_bench_harness.py`（14 项，跑在常规套件里）证明 harness 可信：
任务做对时断言**变绿**、没做时**变红**、靠 skip/删测试**骗不过**。
第一条尤其重要——若做对也不给分，bench 永远 0 分、毫无价值。

## 附带修复的运行时 bug

计划外发现，均为 pyflakes 可静态发现的 NameError，全部位于错误/后台路径 ——
正常流程覆盖不到，所以长期未被发现。回归测试见 `test_bugfix_regressions.py`。

| 位置 | 缺陷 | 影响 |
|---|---|---|
| `api/websocket.py` ×6 | 模块级 `websocket_endpoint` 里调用 `self._safe_close(...)`，但函数没有 `self` | **每一条 WebSocket 认证失败路径都抛 NameError**：空/非法 auth 消息、token 过期、token 非法、会话过期、无权访问。连接不被干净关闭，异常反冒到 ASGI 层 |
| `api/routes/cron_hook.py:178` | `hook.workflow_id` —— 本函数中无 `hook` 这个名字（是 `obj`） | `target_type="workflow"` 的定时钩子每次触发都 NameError，**工作流从未真正执行** |
| `api/routes/knowledge.py` ×6 | 引用 `logger` 但模块从未定义 | 知识库重建索引路径必然 NameError；因跑在 `BackgroundTasks` 里被静默吞掉，表现为「点了重建没反应」 |
| `project/worktree.py:76` | `find_git_root(file)` 把**文件路径**当 `subprocess` 的 `cwd` | 传文件路径即抛 `NotADirectoryError`；同时 `_run_git` 未捕获 `OSError`，裸异常冒给调用方 |
| `frontend/hooks/useActionLock.ts` | 返回的 `locked` / `isCooling` 读的是 **ref**，而 ref 变化不触发重渲染 | 暴露给 UI 的锁定标志**恒为 false**，任何依赖它做按钮禁用/加载态的代码都静默失效（当前两个消费者恰好只用了第一个返回值，属潜伏陷阱） |
| `frontend/components/workflow/NodePalette.tsx` | `useMemo` 把 12 个已翻译标签烤进 `[nodeTypes]` 缓存 | 切换语言后节点标签保持旧语言。注：eslint 提示的「加 `t`」修不好——`useT()` 返回的 `t` 标识稳定，真正该依赖的是 `locale` |

同时把 6 项预存失败中的 5 项沙箱测试改为**自洽**：原实现 mock 缺失，直接断言
「开发机是装了 bwrap 的 Linux」，在 macOS/Windows 上必然失败——那是在测机器不是测代码。

### 未修复但已定位（建议后续处理）

- `react-hooks/set-state-in-effect` ×48、`exhaustive-deps` ×28：系统性模式，多数为
  `useCallback` + `t` 的无害组合（`t` 标识稳定、调用时取 locale）。**只有 `useMemo`
  烤入翻译的才是真 staleness**，已修 NodePalette 一处，其余需逐个判定，不宜批量改。
- `@typescript-eslint/no-explicit-any` ×73：风格策略问题，批量改动风险高于收益。

---

## 0. 结论先行

代码量分布暴露了重心错配：

```
backend/api/routes/settings.py        2494 行   平台
backend/tools/builtins/manage_tools.py 1866 行  平台
backend/services/tools/executors.py   1247 行   内核 ← 有 3 处硬伤
backend/services/workflow_engine.py   1041 行   平台
backend/services/channel_gateway.py   1025 行   平台
backend/agent/loop.py                 2244 行   内核 ← 唯一决定"强不强"
```

平台侧（workflow / cron / 渠道网关 / 知识库 / 技能市场 / 自主进化）广度已超主流产品。
但用户感知的 agent 能力全部落在 `backend/agent/` 那几千行上，而那里有 4 处能立刻拉开差距的缺陷。

**本轮目标：冻结平台功能，只做内核 + 建立度量。**

对标定位：

| | 形态 | 工具面 | 核心投入 |
|---|---|---|---|
| Claude Code | CLI / IDE harness | ~15，几乎不变 | 编码回路深度打磨 + eval |
| Codex | CLI + 云端沙箱 | ~10 | 默认强隔离执行 |
| Hermes | 长时运行 agent | 中等 | 三层 prompt + 记忆 |
| **Takton** | Web + Electron 工作站 | **63**，pack/scene 动态裁剪 | 广度 |

---

## 1. 已对齐主流的部分（不要动）

作为回归基线记录，重构时以下行为必须保持：

| 能力 | 位置 | 说明 |
|---|---|---|
| 三层 system prompt | `backend/agent/system_prompt.py` | Stable / Context / Volatile 分层正确，`TOOL_USE_ENFORCEMENT`、`TASK_COMPLETION`（禁编造输出）、`PROFESSIONAL_OBJECTIVITY` 措辞达标 |
| 迭代预算 + grace 终答 | `backend/agent/iteration_budget.py`、`loop.py` `_budget_grace_call` | 预算耗尽强制一次无工具终答，优于硬断 |
| doom-loop / thrash 熔断 | `backend/agent/robust.py` `ToolRepeatGuard`、`turn_retry.py` `TurnRetryState` | 空正文 / 空工具名 / 429 分类重试，多数自研 agent 没有 |
| 工具结果统一契约 | `backend/agent/tool_result_contract.py` | 统一截断 |
| 段边界 checkpoint + 续跑 | `backend/agent/checkpoint.py`、`phases/prologue.py` | 「请继续」自动接断点 |
| use_tool_pack 元工具 | `backend/agent/tool_policy.py` | 模型自主申请扩容工具面，方向正确 |
| 多后端沙箱抽象 | `backend/computer/` | bwrap / seatbelt / WSL job / local 四套 |
| 行为冻结测试 | `tests/test_loop_freeze.py` | 重构巨型 loop 的保底 |
| SKILL.md 格式 | `backend/services/skill_store/skill_md_storage.py` | 与 Anthropic Agent Skills 对齐 |

---

## 2. P0 任务（本轮必做，合计约 2.5 人日）

### T1 · 工具并行执行 —— 兑现提示词承诺

**优先级：最高（ROI 全项目第一）**

#### 问题

`backend/agent/system_prompt.py:70` 向模型承诺：

> "the runtime executes independent calls concurrently, and batching avoids resending the whole conversation on every extra round-trip"

同段还有 HARD RULE 要求批量发 `file_read`。但 `backend/agent/phases/tool_round.py:77` 的实现是：

```python
for tc in tool_calls:          # 纯串行 await，全文件无 asyncio.gather
    ...
    tool_result = await asyncio.wait_for(
        loop._execute_registered_tool(tc.name, validated_args),
        timeout=_tool_timeout,
    )
```

#### 后果

模型听话地批 5 个 read → 串行等 5 次 → **比不批更慢**（批量后单次失败要整轮重来）。
提示词与运行时的契约破裂，`PARALLEL_TOOL_CALLS` 那 17 行 prompt token 每轮都在白烧。

#### 审计补充（2026-07-26 复审发现，计划初版遗漏）

**遗漏 1 · `_contract_tool_block_reason` 存在竞态，并行前必须修**

`backend/agent/loop.py:218-241` 的懒加载：

```python
if not hasattr(self, "_contract_wl_loaded"):
    self._contract_wl_loaded = True          # 先置位
    self._contract_whitelist = None
    try:
        ...
        self._contract_whitelist = await resolve_attached_tool_whitelist(attached)   # 后 await
```

并发下第二个调用看到 `hasattr(...)` 已为 True，直接读到 `_contract_whitelist is None`
→ **Skill 契约白名单被静默绕过**。串行时不会触发，一旦并行就是安全漏洞。

修法：改用 `asyncio.Lock` 保护，或在并行批次启动前预热一次。本轮采用预热 + Lock 双保险。

**遗漏 2 · 文件类 executor 是同步阻塞 I/O，`gather` 对其零加速**

`execute_file_read` / `execute_grep` / `execute_glob`（`executors.py:703 / 1109 / 1074`）
内部是 `open().read()`、`os.walk()` 等**同步调用**，`async def` 里没有任何 await 点。
`asyncio.gather` 这些协程时，事件循环无处切换，**实际仍是串行**。

因此 T1 的收益分布要说清楚：

| 工具类别 | gather 后是否真并行 | 单次耗时量级 |
|---|---|---|
| `http` / `search` / `web_search` / `browser` / `fetch_webpage` | ✅ 真并行（aiohttp 有 await） | 秒级 —— **收益主体** |
| `file_read` / `grep` / `glob` | ❌ 需额外包 `asyncio.to_thread` | 毫秒~百毫秒 |

结论：T1 拆成两步做——
- **T1a**：`gather` 调度（网络类立即见效）
- **T1b**：`file_read` / `grep` / `glob` 的阻塞体包进 `asyncio.to_thread`（让文件读也真并行）

T1b 与 T3 改同一个函数，合并实施。

#### 方案

在 `run_tool_round` 中按 `risk_level` 分组调度：

- **并发组**：`ToolRiskLevel.SAFE` / `LOW`（`file_read` `grep` `glob` `search` `web_search` `http` `current_time` `doc_read` `session_search`）→ `asyncio.gather`
- **串行组**：`MEDIUM` / `HIGH` / `DANGEROUS`（`file_write` `edit` `apply_patch` `command` `python` `process` 等）→ 保持顺序 await

关键约束：

1. **messages 顺序必须与 `tool_calls` 原顺序一致**（OpenAI/Anthropic 都要求 `tool_call_id` 一一对应且不乱序）。做法：先并发拿结果存 `dict[tool_call_id -> result]`，再按原顺序 append。
2. **WS 事件推送**（`_push_tool_event` start/end）在并发下会交错，前端需能按 `tool_call_id` 归位——检查 `frontend/components/chat/` 的工具卡片渲染是否依赖到达顺序。
3. **一批里混有写类**时，保守做法：整批降级串行（避免"并发读 + 写同一文件"的竞态）。
4. 并发度上限设 `agent_tool_parallel_max`（默认 5），防止 20 个 `http` 打爆下游。

新增 settings：

```python
# backend/core/config.py
agent_tool_parallel: bool = True
agent_tool_parallel_max: int = 5
```

#### 验收

- 新增 `tests/test_tool_parallel.py`：
  - 3 个 `file_read` 并发，总耗时 < 单个耗时 × 1.5
  - 结果 messages 顺序 == tool_calls 顺序
  - 混入 1 个 `file_write` 时整批串行
  - `agent_tool_parallel=False` 时行为与当前完全一致
- `tests/test_loop_freeze.py` 保持绿

**工作量：0.5 人日**

---

### T2 · `edit` 唯一性校验 —— 消灭静默错改

#### 问题

工具描述 `backend/tools/builtins/core_tools.py:126` 声称：

> 「old_text 必须在文件中唯一且与原文完全一致（含缩进）；**不唯一或找不到会失败**」

实现 `backend/services/tools/executors.py:1061`：

```python
new_content = content.replace(old_text, new_text, 1)   # 静默替换第一处
```

#### 后果

多处匹配时**静默改第一处**，不报错、不提示。这是最难排查的一类 agent 故障——模型以为改对了，继续往下跑，错误在几十轮后才暴露。Claude Code 的 Edit 在此情形直接报错并要求扩大上下文，正是这个原因。

#### 方案

`execute_edit` 中替换前计数：

```python
n = content.count(old_text)
if n == 0:
    return (
        f"[Error] old_text not found in {filepath}. "
        f"Read the file first and copy the exact text including indentation."
    )
if n > 1:
    return (
        f"[Error] old_text appears {n} times in {filepath}. "
        f"Include more surrounding lines to make it unique, "
        f"or pass replace_all=true to replace every occurrence."
    )
new_content = content.replace(old_text, new_text, 1)
```

配套加可选参数 `replace_all: bool = False`（对应 Claude Code 的同名参数），schema 同步更新。

顺带：返回值应带上修改位置的行号区间，便于模型自检：

```python
line_no = content[: content.index(old_text)].count("\n") + 1
return f"[Success] {filepath}:{line_no} replaced {len(old_text)} -> {len(new_text)} chars"
```

#### 验收

- `tests/test_edit_uniqueness.py`：0 处 → 报错；2 处 → 报错且文件未改动；2 处 + `replace_all=true` → 全改；1 处 → 成功且返回行号

**工作量：0.25 人日**

---

### T3 · `file_read` 行号 + 分页

#### 问题

`backend/services/tools/executors.py:703-733`：

```python
if len(content) > 20000:
    content = content[:20000] + "\n...[truncated]"
return content
```

参数 schema（`core_tools.py:82`）只有 `filepath` 一个字段。

#### 后果

1. **大文件后半部分永远读不到**——没有 offset 参数，截断即终点。项目自己的 `backend/agent/loop.py`（2244 行）就超了。
2. **没有行号** → 模型无法用行号定位，只能靠 `edit` 的字符串匹配，而字符串匹配又有 T2 那个 bug，两个缺陷叠加放大。
3. 截断提示不含总量信息，模型不知道自己漏了多少。

#### 方案

对齐 Claude Code 的 Read 工具：

```python
# schema
{
    "filepath": {"type": "string"},
    "offset":   {"type": "integer", "description": "起始行号（1-based），大文件分段读时使用"},
    "limit":    {"type": "integer", "description": "读取行数，默认 2000"},
}
```

实现要点：

- 按行读，输出 `cat -n` 格式：`{lineno:6d}\t{line}`
- 默认 `limit=2000` 行；单行超 2000 字符则该行截断并标注
- 截断时明确给出续读指引：
  `[File has 2244 lines. Showing 1-2000. Use offset=2001 to continue.]`
- 二进制文件检测（含 NUL 字节）→ 返回 `[Error] Binary file, use command tool instead`
- 保留现有路径安全检查（`Path.resolve().relative_to()`）

**注意**：改了输出格式后，`backend/agent/tool_result_contract.py` 的截断逻辑和 `system_prompt.py` 里对 `edit` 的指导（"old_text 必须与原文完全一致"）需同步说明——行号是**展示前缀，不属于文件内容**，模型不能把行号抄进 `old_text`。这一句必须写进 `file_read` 的 description，否则会引入新故障。

#### 验收

- `tests/test_file_read_paging.py`：3000 行文件默认读到 2000 行且提示续读；`offset=2001` 读到尾部；行号格式正确；二进制文件被拒
- 人工验证：让 agent 读 `loop.py` 全文并定位 `_run_locked`，应能通过两次 `file_read` 完成

**工作量：0.5 人日**

---

### T4 · Prompt Caching —— 成本降一个数量级

**优先级：与 T1 并列（省钱最多）**

#### 问题

全仓库 `grep -rn "cache_control\|prompt_cache" backend/` **零命中**。
`backend/services/llm/anthropic.py:38` 的 `_get_headers()` 只有 `anthropic-version`，payload（`anthropic.py:151`）无任何缓存标记。

#### 后果

Agent loop 的本质是**每轮把整个 messages 重发一遍**。一个 20 轮工具循环的任务：

```
system prompt (~3k) + 工具 schema (~8k) + 累积历史
        × 20 轮 全部按新 token 计费
```

Anthropic / OpenAI / DeepSeek 均支持前缀缓存，命中后输入 token 成本降至 10%。
**这是全项目单点收益最大的改动，长任务成本可降 5-10 倍。**

#### 方案

**Anthropic 侧**（`backend/services/llm/anthropic.py`）：

1. `_convert_messages` 返回的 `system_text` 改为结构化块，尾部打缓存断点：

```python
payload["system"] = [
    {"type": "text", "text": system_text,
     "cache_control": {"type": "ephemeral"}}
]
```

2. `tools` 数组最后一个工具打 `cache_control`（缓存整个工具定义前缀）
3. 历史消息：在**倒数第二个 user 块**打断点（滚动缓存，保留最新一轮不缓存）

**前置阻断项 —— 必须先修，否则 T4 全部白做**：

`backend/agent/system_prompt.py:342-352` 把**秒级时间戳**塞进 Volatile 层：

```python
ts_line = (
    f"Current time: {now_local.strftime('%A, %B %d, %Y %H:%M:%S')} "   # ← 精确到秒
    f"({now_local.strftime('%Z')}) / {now_utc.strftime('%H:%M:%S')} UTC"
)
volatile_parts.append(ts_line)
```

而 `merge_prompt_parts`（`system_prompt.py:363`）把三层拼成**单个 system 字符串**，
即 `messages[0]`：

```python
ordered = [parts.get("stable"), parts.get("context"), parts.get("volatile")]
return "\n\n".join(...)
```

**结果：system 块每秒都在变 → 前缀在第一个 block 就断，
Anthropic 的 system cache 与 OpenAI 的自动前缀缓存永远不可能命中。**

影响范围要说清楚：单次 run 内 `build_messages` 只调一次，system 在该 run 的 ~20 轮里不变，
所以**打了断点后 run 内缓存仍有效**；但同一会话的每个新用户轮次 system 都变，
**跨轮次的历史缓存全灭**——而历史恰恰是长会话中最大的一块。

修法（T4 的第一步）：

1. Volatile 层从 system 剥离，改为追加到**最后一条 user 消息**尾部
2. 若坚持留在 system，时间戳降到**小时级**（`%Y-%m-%d %H:00`），文案注明"精确到小时"
3. `Session: {id[:8]}` / `Model: {model}` 在会话内本就是常量，可安全留在 system

**OpenAI 兼容侧**（`backend/services/llm/openai_compatible.py`）：
自动前缀缓存，无需显式标记，但要求**前缀字节级稳定**。审计结果：

- ❌ 上面的秒级时间戳 —— **唯一的破坏源**
- ✅ `loop.py:684` 的 `compact_capability_brief` 追加在 messages **尾部**，位置稳定；
  内容随 `scene_plan` 变化会断尾部前缀，但工具面变了本来就该重算，可接受
- ✅ RAG / Wiki / 实体注入（`loop.py:790-826`）走 `_append_to_system`，
  而该函数（`loop.py:271-281`）是**从后往前找最后一个 system 消息**，
  命中的是上述尾部 brief 块，**不会污染 `messages[0]`**——这个设计是对的，保持不动

**监控**：`backend/agent/token_meter.py` 的 `update_from_response` 增加 `cache_read_input_tokens` / `cache_creation_input_tokens` 统计，WS 推给前端展示命中率。

#### 验收

- `tests/test_prompt_cache.py`：
  - **system 串在同一会话的连续两轮中字节相同**（时间戳剥离后，这条是前提）
  - payload 结构断言：system 块带 `cache_control`、tools 尾部带断点
- 手工基准：同一会话连发 3 轮，第 2、3 轮 `cache_read_input_tokens` > 0，累计输入 token 下降 > 60%
- 前端能看到缓存命中率

**工作量：1 人日**

---

### T5 · 权限模型收敛 + 沙箱默认开启

#### 问题（三个叠加）

**5a · 权限门默认是装饰性的**

`backend/core/config.py:234` `agent_permission_ask_mode = "local_allow"`，
`backend/agent/tool_hooks.py:178`：

```python
if ask_mode in ("local_allow", "allow", "auto_allow"):
    logger.info("permission ask→local_allow tool=%s ...")
    return BeforeHookResult(arguments=arguments)   # 所有 ask 静默放行
```

`PermissionGate` 的全部 `ask` 规则（含 `*.env` 读取、外部目录写入）默认只打一行 log。

**5b · `requires_confirmation` 是死标志**

`core_tools.py` 中 `file_write:113`、`edit:139`、`python:394`、`sqlite_query:441` 都标了 `requires_confirmation=True`。
但统一执行路径 `backend/tools/registry.py:140-145` 只调用 `check_tool_permission`，
`ToolPermissionManager.needs_confirmation`（`permissions.py:305`）**全项目无调用点**。声明与行为完全脱节。

**5c · 实际生效的只剩正则黑名单，且沙箱默认关**

`backend/core/config.py:244` `agent_computer_enabled = False` → 默认走
`executors.py:669` `asyncio.create_subprocess_shell` **直接跑在宿主机**。
唯一防线是 `executors.py:528` `_match_dangerous` 正则黑名单，绕过成本极低：

```bash
$(printf '\x72\x6d') -rf /        # 变量拼接
echo cm0gLXJmIC8K | base64 -d | sh # base64 管道
```

对比：Codex 默认开 seatbelt/landlock；Claude Code 用 allowlist 而非 blocklist。
`backend/computer/` 四套沙箱后端已经写好了，默认不开是纯浪费。

#### 方案

1. **收敛成一套**：删除 `ToolPermissionManager.needs_confirmation` 和各工具的 `requires_confirmation` 声明（或让它真正接入 `PermissionGate`）。保留：
   - `PermissionGate`（`permissions_rules.py`）—— 唯一的 allow/deny/ask 决策器
   - `ToolPermissionManager.check_tool_permission` —— 降级为纯路径边界检查
   - `command_policy` 三态类别 —— 归入 `PermissionGate` 的 `PERM_BASH` 规则
2. **默认开沙箱**：`agent_computer_enabled` 默认改 `True`，`agent_computer_backend="auto"`（已有 `backend/computer/detect.py` 自动探测）。探测失败时**明确报错而非静默降级**——现有 `executors.py:660` 的处理是对的，保持。
3. **黑名单降级定位**：`_match_dangerous` 保留但重新定位为「UX 提示」而非安全边界，注释和文档写清楚。真正的边界是沙箱。
4. **ask 模式默认值**：桌面版（有前端 WS）默认 `interactive`；headless/CLI 默认 `local_allow`。按 `ws_manager` 是否存在自动选择。

#### 验收

- `tests/test_sandbox_backends.py` 扩展：默认配置下 `command` 走沙箱后端
- 新增绕过用例：`$(printf ...)` 拼接的 `rm -rf` 在沙箱内无法触达宿主机路径
- `tests/test_security_hardening.py` 保持绿
- 文档 `docs/CORE_RUNTIME.md` 更新默认值表

**工作量：2-3 人日**（放到 P0 末尾，可跨里程碑）

---

## 3. P1 任务（架构级，2-3 周）

### T6 · Eval Harness —— 与主流最本质的差距

**建议这是整个计划的第一件事**（虽然归在 P1，因为它不修 bug，但 T1-T5 的效果都需要它验证）。

#### 问题

`docs/CORE_RUNTIME.md` 的 Bench 章节写着：

```bash
.venv311/bin/python scripts/bench_agent/run_bench.py --models local,kimi
```

**`scripts/bench_agent/` 目录不存在。**

现有 73 个测试（`backend/tests/` 53 个 + `tests/` 20 个）全是结构性的——freeze / 单元 / 契约。没有一个能回答：

> 我改了 `system_prompt.py` 这段措辞，agent 变强了还是变弱了？

Claude Code / Codex 的护城河 90% 在这里。每一条 prompt、每个工具描述、每个熔断阈值都是 eval 打出来的。**没有 eval，对 `system_prompt.py` 那千余行的每次修改都是凭感觉。**

#### 方案

最小可行版本，`scripts/bench_agent/`：

```
scripts/bench_agent/
├── run_bench.py          # 入口：--models --tasks --repeat --out
├── tasks/
│   ├── fix_bug_01.yaml
│   ├── add_feature_02.yaml
│   └── ...               # 20 个
├── fixtures/             # 每个任务的初始仓库快照
└── report.py             # 汇总 markdown/json
```

任务格式：

```yaml
name: fix_bug_01
fixture: fixtures/flask_app_broken
prompt: "登录接口返回 500，修好它并跑通测试"
assertions:
  - type: command_exit_code
    command: pytest tests/test_auth.py
    expect: 0
  - type: file_contains
    path: app/auth.py
    pattern: "check_password_hash"
  - type: not_file_contains      # 防作弊：不许改测试
    path: tests/test_auth.py
    pattern: "skip"
budget:
  max_iterations: 30
  max_seconds: 300
```

任务分布建议（20 个）：

| 类别 | 数量 | 考察点 |
|---|---|---|
| 定位并修 bug | 6 | grep/read 效率、edit 正确性 |
| 加功能（多文件） | 4 | 并行读、批量写 |
| 查配置 / 回答问题 | 3 | 早停能力，不该滥用工具 |
| 长任务（>20 轮） | 3 | 压缩、续跑、checkpoint |
| 应报错不能编造 | 2 | `TASK_COMPLETION` 那段的诚实性 |
| 危险操作应被拦 | 2 | 权限 / 沙箱 |

每任务跑 3 次取通过率，记录指标：

- **通过率**（主指标）
- 平均轮数 / 平均 wall-clock
- 总输入 token / 总输出 token / **缓存命中率**
- 工具调用次数、并行批次占比
- 熔断触发次数（thrash / doom-loop / budget）

输出 `bench/results/<git-sha>-<model>.json`，`report.py` 对比两个 sha 出 diff 表。

#### 验收

- `python scripts/bench_agent/run_bench.py --models local --tasks all --repeat 3` 可跑通并产出报告
- 至少 20 个任务，覆盖上表分布
- 基线数据入库：当前 HEAD 的通过率作为 T1-T5 的对照组

**工作量：1 人日（首版 20 任务）+ 持续维护**

---

### T7 · 真子代理（上下文隔离）

#### 问题

README 描述的「并行草稿扇出（子任务无工具权限）」是 **best-of-n 采样**，不是 subagent。
`backend/agent/cluster_executor.py` + `cluster_aggregator.py` 拿不到 subagent 的核心收益。

Claude Code 的 Task 工具价值在于**上下文隔离**：子代理带完整工具跑 100k token 的探索，只回传 2k 结构化摘要给主循环。主循环的上下文窗口因此不被探索过程污染。

#### 现状可复用

`backend/agent/subagent_runner.py:51` 的 `run_subagent` 已经有：

- 独立 loop 实例
- LLM 快照覆盖（`snapshot_for_model_ref`）
- 独立 Agent Computer 沙箱身份
- 深度限制（`agent_subagent_max_depth=1`）+ 超时（300s）

**基础设施已经在了，缺的是让它带工具跑。**

#### 方案

1. 给 `run_subagent` 传 `tool_profile` / `enabled_tools_filter`，默认给只读组合（`file_read` `grep` `glob` `search`），可按需放开
2. 子代理独立 `IterationBudget`（默认 15 轮）和独立 `context_window` 预算
3. **强制结构化返回**：子代理的最后一轮注入「你必须以不超过 N 字的摘要回答，包含：结论 / 关键文件路径 / 未解决问题」，主循环只收摘要
4. 主循环侧的 `delegate_task` 工具描述要写清楚「适合广度搜索、不适合需要精确改动的任务」
5. 并发跑多个子代理时复用 T1 的并发调度

#### 验收

- 新增 `tests/test_subagent_isolation.py`：子代理消耗 > 20k token，主循环 messages 增量 < 3k
- bench 任务集加 2 个"大仓库定位"任务，对比开/关子代理的轮数与 token

**工作量：3-5 人日**

---

### T8 · `_run_locked` 状态收敛

#### 问题

`backend/agent/loop.py:458-1197`，单函数 740 行。已拆出 `phases/`，但主干仍靠手工"读回协议"传状态——`loop.py:1092-1101` 连续 10 行：

```python
messages = _tr_state.messages
tools = _tr_state.tools
enabled_tools_filter = _tr_state.enabled_tools_filter
_force_final_no_tools = _tr_state.force_final_no_tools
_suppress_content_stream = _tr_state.suppress_content_stream
_multi_source_pending = _tr_state.multi_source_pending
_timid_read_streak = _tr_state.timid_read_streak
_timid_write_streak = _tr_state.timid_write_streak
_tool_rounds = _tr_state.tool_rounds
_last_tool_round_count = _tr_state.last_tool_round_count
```

`phases/tool_round.py` 的文件注释自己写着「调用方必须读回」——这是重构没做完的信号。漏读一个标量就是一个静默 bug。

#### 方案

引入 `RunContext` dataclass 承载全部循环状态（messages / tools / filter / 各 streak / budget / retry / trace 缓冲），phases 直接原地改它，**消灭读回协议**：

```python
# backend/agent/run_context.py
@dataclass
class RunContext:
    session_id: uuid.UUID
    mode: str
    messages: list[dict]
    tools: list[dict]
    enabled_tools_filter: set[str] | None
    scene_plan: ScenePlan
    budget: IterationBudget
    turn_retry: TurnRetryState
    repeat_guard: ToolRepeatGuard
    # flags
    force_final_no_tools: bool = False
    suppress_content_stream: bool = False
    multi_source_pending: bool = False
    # counters
    timid_read_streak: int = 0
    timid_write_streak: int = 0
    tool_rounds: int = 0
    # trace
    trace_tool_calls: list[dict] = field(default_factory=list)
    ...
```

phases 签名统一为 `async def run_xxx(loop, ctx: RunContext) -> PhaseAction`。

**必须在 T1-T5 之后做**——先修行为，再动结构，`test_loop_freeze.py` 才有意义。

#### 验收

- `tests/test_loop_freeze.py` 全绿（这是唯一的安全网）
- bench 通过率与重构前持平（±2%）
- `_run_locked` 降到 200 行以内

**工作量：3-5 人日**

---

### T9 · 工具面裁剪改为单通道

#### 问题

`backend/agent/tool_policy.py:176` `_PACK_KEYWORDS` 用中英文关键词猜场景。关键词匹配天生脆：
用户说「帮我看看这个定时的东西」命不中 `manage` pack，`manage_cron` 就不在工具面里。

同时存在两条扩容路径（关键词自动加包 + `use_tool_pack` 元工具），两者会漂移——
`tool_round.py:487` 里 `use_tool_pack` 还要手工把 pack 塞回 `scene_plan.packs` 保持一致，这是双通道的税。

#### 方案

**让 `use_tool_pack` 成为唯一扩容通道**：

- 底座固定 15 个核心工具（`DEFAULT_CHAT_TOOL_WHITELIST` 已经是这个规模）
- system prompt 加一行 pack 目录说明：「你还可以用 `use_tool_pack` 申请：devices / desktop / office / manage / evolution / cluster / data / github」
- 删除 `_PACK_KEYWORDS` 的自动加包（保留 `infer_scene` 用于 `injection_tier` 的 RAG/Wiki 控制）
- `profile=dynamic` 的语义变成「允许模型自主扩包」，`core` 变成「禁止扩包」

模型申请比关键词猜准得多，也省掉双通道同步。

#### 验收

- `tests/test_tool_policy.py` 更新
- bench：对比改前/改后，工具面大小中位数应下降，通过率不降

**工作量：1-2 人日**

---

### T10 · Anthropic extended thinking

#### 问题

`backend/services/llm/anthropic.py:151` 的 payload 无 `thinking` 参数。
现状靠 `system_prompt.py` 的 `THINKING_GUIDANCE` 让模型自己写 `<thinking>` 标签模拟。

在支持原生 thinking 的模型上这是倒退——原生 thinking 有独立 token 预算、不占输出窗口、且工具调用间可保留（interleaved thinking）。

#### 方案

- payload 支持 `thinking: {"type": "enabled", "budget_tokens": N}`
- 流式解析支持 `thinking_delta` 事件块 → 映射到现有的 `accumulated_reasoning`（`phases/llm_round.py` 已有这个字段，说明 reasoning 通道已经打通）
- 开启 thinking 时**自动抑制** `THINKING_GUIDANCE` 那段 prompt（避免双重思考）
- 加 `anthropic-beta: interleaved-thinking-...` 头以在工具轮之间保留思考

#### 验收

- `tests/test_anthropic_thinking.py`：payload 断言 + 流式块解析
- 前端 reasoning 折叠区能正常渲染原生 thinking

**工作量：1 人日**

---

## 4. P2 任务（体验层，按需）

| ID | 任务 | 问题 | 工作量 |
|---|---|---|---|
| T11 | 手动 `/compact` | 压缩只在过阈值自动触发（`context_compress.py:28`），用户无法主动压缩 | 0.5d |
| T12 | rewind / undo 出口 | `.takton/checkpoints/` 有快照（`file_checkpoint.py`），无 UI 入口。Claude Code 的 `/rewind` 是高频救命功能 | 1d |
| T13 | hooks 声明式配置 | `tool_hooks.py` 只能 Python 注册，无 `settings.json` 式用户配置。个人 agent 可能够用，但这是插件生态门槛 | 2d |
| T14 | `grep`/`glob` 输出预算 | 需审计 `executors.py:1074/1109` 的结果上限，大仓库下可能撑爆上下文 | 0.5d |
| T15 | 工具描述 token 审计 | 63 个工具的 description 都是中文长句，全量注入时 schema 开销需实测 | 0.5d |

---

## 5. 里程碑

### M1 · 度量与止血（1 周）

```
D1      T6  eval harness 首版 20 任务 → 跑出当前 HEAD 基线
D2      T2  edit 唯一性
        T3  file_read 行号分页
D3      T1  工具并行
D4-D5   T4  prompt caching
D5      跑 bench，与 D1 基线对比，出报告
```

**M1 出口标准**：bench 通过率提升，累计输入 token 下降 > 50%，平均轮数下降。
若某项改动让 bench 变差，回滚并记录——**这就是建 eval 的意义。**

### M2 · 安全边界（1 周）

```
T5  权限模型收敛 + 沙箱默认开启
    bench 加 2 个"危险操作应被拦"任务验证
```

### M3 · 架构（2-3 周）

```
T8  RunContext 状态收敛（前置：M1 全绿）
T7  真子代理
T9  工具面单通道
T10 extended thinking
```

---

## 6. 明确不做的事（本轮冻结）

以下模块本轮**不接受新功能**，只接受 bug 修复：

- `backend/services/workflow_engine.py` — 工作流引擎
- `backend/services/channel_gateway.py` + `channel_adapters/` — IM 渠道
- `backend/api/routes/settings.py` — 设置面板
- `backend/tools/builtins/manage_tools.py` — 平台管理工具
- `backend/evolution/` — 自主进化
- `backend/services/knowledge/` + RAG — 知识库
- `frontend/` 除非 T1（工具事件乱序）/ T4（缓存命中率展示）/ T12（rewind 入口）需要

理由：这些是平台广度，已经超过对标产品；而用户感知的 agent 能力全在内核。
广度铺得越开，内核每个缺陷的影响面越大。

---

## 7. 附录：本次扫描的完整问题索引

| # | 严重度 | 位置 | 问题 | 任务 |
|---|---|---|---|---|
| 1 | P0 | `agent/phases/tool_round.py:77` vs `agent/system_prompt.py:70` | 提示词承诺并行，运行时串行 | T1 |
| 2 | P0 | `services/tools/executors.py:1061` | `edit` 声称唯一性校验，实际静默替换第一处 | T2 |
| 3 | P0 | `services/tools/executors.py:729` | `file_read` 无行号 / 无 offset / 20000 字符硬截断 | T3 |
| 4 | P0 | 全仓库 | 无 prompt caching，每轮全量重发计费 | T4 |
| 5 | P0 | `core/config.py:234` + `agent/tool_hooks.py:178` | `ask_mode=local_allow` 使权限门默认失效 | T5 |
| 6 | P0 | `tools/registry.py:140` | `requires_confirmation` 声明了但从不被读取 | T5 |
| 7 | P0 | `core/config.py:244` + `executors.py:669` | 沙箱默认关，默认宿主机直跑 | T5 |
| 8 | P0 | `services/tools/executors.py:528` | 正则黑名单可被 `$(printf)` / base64 绕过 | T5 |
| 9 | P1 | `docs/CORE_RUNTIME.md` | 引用的 `scripts/bench_agent/run_bench.py` 不存在，无 eval | T6 |
| 10 | P1 | `agent/cluster_executor.py` | 子代理是 best-of-n 采样，非上下文隔离 | T7 |
| 11 | P1 | `agent/loop.py:458-1197` | `_run_locked` 740 行 + 10 行手工读回协议 | T8 |
| 12 | P1 | `agent/tool_policy.py:176` | 关键词猜场景脆弱，与 `use_tool_pack` 双通道漂移 | T9 |
| 13 | P1 | `services/llm/anthropic.py:151` | 无 extended thinking，靠 `<thinking>` 标签模拟 | T10 |
| 14 | **P0** | `agent/system_prompt.py:342-352` + `:363` | 秒级时间戳并入 `messages[0]` 的 system 串，跨轮次前缀缓存永不命中 | T4 |
| 15 | P2 | `agent/context_compress.py:28` | 无手动 `/compact` | T11 |
| 16 | P2 | `agent/file_checkpoint.py` | checkpoint 无用户可见 rewind 入口 | T12 |
| 17 | P2 | `agent/tool_hooks.py` | hooks 无声明式配置层 | T13 |
| 18 | P2 | `services/tools/executors.py:1074/1109` | `grep`/`glob` 输出预算待审计 | T14 |
| 19 | **P0** | `agent/loop.py:218-241` | `_contract_tool_block_reason` 懒加载竞态，并行下技能契约白名单可被绕过（复审发现） | T1 前置 ✅ |
| 20 | P1 | `services/tools/executors.py:703/1074/1109` | 文件类 executor 同步阻塞 I/O，`gather` 对其零加速（复审发现） | T1b ✅ |
| 21 | **P0** | `agent/tool_result_contract.py:12` | `file_read` 结果预算仅 **2000 字符**，超限做 head 70%+tail 20% **首尾拼接**——模型拿到「前 30 行 …省略… 末尾几行」却以为读了整个文件（复审发现，比初版记录的 executor 层 20000 截断严重得多） | T3 ✅ |

> 复审记录：基线测试 540 tests / 6 failures（全部环境性：macOS 上模拟 Linux 沙箱 5 项 +
> 非 git 仓库的 worktree 1 项），非本计划引入，作为回归对照。

### 实施中确立的新契约（后续重构不得破坏）

| 契约 | 锁定测试 |
|---|---|
| 跨轮次 `messages[0]` 逐字节稳定（否则前缀缓存全灭） | `test_prompt_cache_prefix.py` |
| Volatile 层（时间戳/记忆）恰好出现一次且不在 `messages[0]` | 同上 |
| `edit` 多处匹配报错且**文件零改动** | `test_file_tools_contract.py` |
| `file_read` 截断只发生在行边界，并给出精确续读 offset | 同上 |
| 分页上限 < `tool_round` 有效上限（不被二次拼接） | 同上 |
| 只读批并发、混入写类整批串行、失败语义与串行一致 | `test_tool_parallel.py` |
| Anthropic `input_tokens` 与 `cache_read` 相加才等于 prompt_tokens | `test_prompt_cache_anthropic.py` |
| 非法工作方式/执行环境值一律回落到安全默认，绝不放宽 | `test_working_mode.py` |
| `sandbox` 档不可用时报错而非静默降级为本机 | 同上 |
| bench 断言在任务做对时必须变绿、没做时必须变红 | `test_bench_harness.py` |
