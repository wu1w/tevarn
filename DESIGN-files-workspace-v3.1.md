# Takton Files / Workspace 系统设计方案 v3.1

> 目标：参考近 2 个月主流 coding agent（Hermes、Qoder、Claude Code、Codex、Cursor、Windsurf）  
> 让 **Agent 运行时改文件、用户指定项目目录、对话与文件树联动** 更符合直觉、更丝滑。  
> **本文仅方案，不含实现。**

---

## 0. 调研摘要（2026-05 ~ 2026-07）

### 0.1 各家核心模型

| 产品 | 工作区心智 | 文件变更呈现 | 上下文/规则 | 关键直觉点 |
|------|------------|--------------|-------------|-----------|
| **Claude Code** | 「打开一个文件夹 = Agent 的家」；可 `/add-dir` 追加可读目录；默认沙箱在 root | CLI 终端 diff + 工具调用轨迹 | `CLAUDE.md` + `.claude/` 层级加载 | **文件夹即 Agent**；多根目录可追加 |
| **Qoder** | Desktop/Quest：项目级 Agent；Repo Wiki 同步结构 | 多文件：Generating → Applying → **Review 接受/拒绝** | `.qoder/rules` + 兼容 `AGENTS.md` | Spec/Wiki 与改文件闭环；变更需可审 |
| **Cursor** | 编辑器 workspace + Agents Window | Composer 多文件 edit；Mission Control 看改了啥 | `.cursorrules` / project rules | **编辑器中心** + 并行 agent 看板 |
| **Windsurf** | Cascade 与编辑器并列 | **Diff-staging**，逐步 accept 后落盘 | Flows 跨会话上下文 | 改文件「暂存再应用」最丝滑 |
| **Codex CLI** | 仓库根 cwd | 终端 patch / 文件写回 | `AGENTS.md` 等 | 轻量、cwd 清晰 |
| **Hermes** | Profile + 可选 workdir；技能/记忆分离 | 工具结果流式 + 路径可点 | SOUL/AGENTS/skills | **证据驱动**；会话与目录可绑定 |

### 0.2 行业共识（可抄的直觉）

1. **工作区（Workspace）是一等公民**：不是「侧边栏随便填个路径」，而是会话/Agent 的 **root cwd**。  
2. **改文件 = 可见生命周期**：proposed → staging/diff → accept/reject → applied（可撤销）。  
3. **对话内文件引用可点击**：`@path` / 工具结果路径 → 打开预览 / 定位树节点。  
4. **规则文件跟目录走**：`AGENTS.md` / `CLAUDE.md` / `.takton/` 在 root 自动注入。  
5. **沙箱与本机目录分离但可切换**：默认安全边界清晰，用户「打开项目」一键提升权限。  
6. **运行时高亮**：Agent 正在读/写的文件，树与对话同步状态（reading / writing / dirty）。

---

## 1. Takton 现状与问题

### 1.1 现状（代码侧）

- 侧栏 **Files** 折叠区：`sandbox | local` 模式 + 路径输入 + 简易 `FileTree` / `FilePreview`  
- 树组件很薄（展开/选中/预览），与 **会话、Agent 工具 cwd、diff 审阅** 基本脱节  
- 工具改文件（`file_write` / `file_edit` 等）主要出现在 **tool 气泡**，树不实时动、无 accept 流  
- 「用户指定项目文件夹」体验接近「手动填服务器路径」，桌面端未形成 **Open Folder** 心智

### 1.2 主要痛点

| 痛点 | 表现 | 对标缺口 |
|------|------|----------|
| 工作区模糊 | sandbox vs local 概念对用户不友好 | Claude/Cursor：打开文件夹即 workspace |
| 改文件不可见 | 只在 tool 结果里，像黑箱 | Windsurf/Qoder：diff + review |
| 对话与树割裂 | 路径不能点、树不跟随 tool | 全员标配 path → open |
| 无变更清单 | 一轮对话改了 10 个文件只能翻聊天 | Cursor Mission Control / Cascade staging |
| 规则无根 | 未绑定项目级 `AGENTS.md` 注入 | Qoder/Claude 标配 |
| 桌面能力浪费 | Electron 有本地 FS，却像「远程浏览器填路径」 | 系统文件夹选择器 |

---

## 2. 设计原则

1. **Workspace First**：每个会话（或每个 Agent Profile）绑定一个 **Workspace Root**。  
2. **变更可审、默认可控**：Agent 写盘走 **ChangeSet**；危险路径二次确认。  
3. **对话即导航**：路径、diff、文件 chip 都是可点击入口。  
4. **一种主路径，两种能力**：默认「打开本机项目」；高级用户仍可挂 sandbox / 额外 allow-dir。  
5. **最小认知负担**：砍掉「sandbox/local 双模式文案」，改成 **当前项目 + 权限徽章**。  
6. **与 v3 工具层兼容**：统一 `ToolRegistry` 的 file_* / command 的 cwd 与 allowlist 来自 Workspace 服务。

---

## 3. 核心概念模型

```
Workspace
  id, name, root_path (absolute, host-local)
  kind: local | sandbox
  allow_extra_dirs: string[]
  rules: [AGENTS.md, .takton/rules/*, ...]
  git?: { branch, dirty }

Session
  workspace_id (可空 → 用默认/全局)
  change_set_id? (进行中的变更集)

ChangeSet (一轮 agent 任务产生的文件变更集合)
  id, session_id, status: open | reviewing | applied | discarded
  files: ChangeEntry[]

ChangeEntry
  path (relative to root)
  op: create | modify | delete | rename
  status: proposed | accepted | rejected | applied
  before_hash?, after_preview?, diff_unified?
  tool_call_id?, message_id?
```

**Agent 运行时文件访问：**

- `cwd = workspace.root_path`  
- 读：root + allow_extra_dirs  
- 写：默认仅 root（extra 只读 unless 用户勾选「允许写入附加目录」）  
- 所有写操作先进入 ChangeSet（可配置「信任模式」直接写盘，见 §7）

---

## 4. 信息架构 & UI 布局

### 4.1 布局（桌面优先）

```
┌──────────┬─────────────────────────────┬──────────────────┐
│ Sidebar  │  Chat                        │  Workspace       │
│ 会话列表  │  消息 / tool / thinking       │  面板（可折叠）    │
│ ...      │  [变更条 ChangeStrip]         │  树 + 预览/Diff   │
│          │  输入框 + @文件               │  变更列表 Tab     │
└──────────┴─────────────────────────────┴──────────────────┘
```

- **Workspace 面板**从「Sidebar 里的 Files 折叠」升级为 **主内容区右侧可固定面板**（宽屏默认开，窄屏抽屉）。  
- 旧 Sidebar Files 降级为入口：点击 → 打开右侧 Workspace 面板。

### 4.2 Workspace 面板 Tabs

| Tab | 内容 |
|-----|------|
| **树** | 文件树；状态点：`·` 未改 / `M` 已改 / `A` 新增 / `D` 删除 / spinner 读写中 |
| **变更** | 当前 ChangeSet 列表；批量 Accept / Reject；单文件 diff |
| **规则** | 检测到的 `AGENTS.md` / `.takton/rules`；开关是否注入 |
| **搜索** | 按文件名 / 符号（后续可接轻量索引） |

### 4.3 顶栏 / 会话标题区

- **Workspace Chip**：`📁 taktonl-0.1.0` · 分支 · 权限（Local / Sandbox）  
- 点击 → 切换项目、打开文件夹、最近项目  
- 无 workspace 时：空态 CTA **「打开项目文件夹」**（Electron `dialog.showOpenDialog`）

### 4.4 对话内 Files 相关 UI

1. **Tool 卡片增强**（file_read/write/edit）  
   - 标题：`编辑 frontend/app/page.tsx`  
   - 摘要：`+12 -3`  
   - 按钮：`查看 Diff` / `在树中显示` / `Accept` / `Reject`（写操作）

2. **ChangeStrip（粘性条，仅 agent 运行或有 pending 变更时）**  
   - `Agent 修改了 6 个文件 · 查看全部 · 全部接受 · 全部拒绝`  
   - 点击文件名跳转 Diff

3. **@ 引用**  
   - 输入 `@` 弹出 workspace 内文件模糊搜索  
   - 发出消息时附带 `attachments: [{type:'path', path}]`，后端注入文件片段

4. **路径自动链接**  
   - Markdown / tool 输出中的相对路径 → 可点击（正则 + workspace 存在性校验）

---

## 5. 关键用户流程

### 5.1 打开项目（最重要入口）

```
用户点击「打开项目」
  → 系统文件夹选择器（仅 Electron）
  → 校验可读；扫描 AGENTS.md / package.json / .git
  → 创建/更新 Workspace
  → 绑定当前 Session（或询问：仅本次 / 设为默认）
  → 工具层 cwd/allowlist 热更新
  → 树加载 root；规则 Tab 预览注入内容
```

Web 无 Electron 时：保留路径输入 + 仅 sandbox；文案标明「桌面版可打开本机文件夹」。

### 5.2 Agent 改文件（默认：审阅后落盘）

```
tool file_edit 被调用
  → WorkspaceService.stageChange(entry)   # 写到 shadow 或计算 diff，不直接覆盖（见存储策略）
  → WS 推送 file_change 事件
  → 前端：树标 M、ChangeStrip +1、tool 卡片出 Diff
  → 用户 Accept → 原子写盘 → status=applied
  → 用户 Reject → 丢弃 staging
  → idle 时若仍有 pending：会话结束前提示「还有 N 个未处理变更」
```

### 5.3 信任模式（高级）

设置项：`文件写入策略`

- **Ask（默认）**：写操作进 ChangeSet，需 Accept  
- **Auto-apply in workspace**：root 内自动写盘，仍记 ChangeSet 便于回顾/回滚  
- **Full trust**：同 auto，且减少提示（仅 dangerous 路径确认）

### 5.4 多目录

- 主 root 一个  
- **附加目录**（只读默认）：类似 Claude `/add-dir`  
- UI：Workspace 设置 →「添加可读目录」  
- Agent 系统提示中声明可访问边界

---

## 6. 运行时与后端设计

### 6.1 新模块（建议）

```
backend/workspace/
  models.py          # Workspace, ChangeSet, ChangeEntry
  service.py         # resolve_cwd, stage, apply, reject, list_tree
  rules_loader.py    # AGENTS.md / .takton/rules 合并
  path_guard.py      # 规范化、越界检查、符号链接策略

backend/api/routes/workspace.py
  GET    /workspace
  POST   /workspace/open          # { path } 桌面端
  GET    /workspace/tree?path=
  GET    /workspace/file?path=
  GET    /workspace/changes
  POST   /workspace/changes/{id}/accept
  POST   /workspace/changes/{id}/reject
  POST   /workspace/changes/accept_all
  WS     file_change / file_watch 事件
```

### 6.2 与 ToolRegistry 集成

| 工具 | 改造 |
|------|------|
| `file_read` / `file_list` | path 相对 workspace root；越界拒绝 |
| `file_write` / `file_edit` / `file_delete` | 经 `WorkspaceService.stage*`；返回 `change_id` + diff 摘要 |
| `command` / `bash` | `cwd=workspace.root`；env 注入 `TAKTON_WORKSPACE` |
| `execute_python` | 默认 cwd 同上 |

权限：`ToolPermissionManager.allowed_paths` **自动 = root + extras**，用户自定义白名单可叠加。

### 6.3 Staging 存储策略（二选一，实现时定）

| 方案 | 做法 | 优缺点 |
|------|------|--------|
| **A. Shadow 目录** | `~/.takton/staging/{changeset}/` 镜像文件 | 大文件成本高；回滚简单 |
| **B. Diff-only（推荐）** | 只存 unified diff + 原文件 hash；Accept 时 apply patch | 轻量；二进制需特殊处理 |
| **C. 直接写盘 + 本地历史** | 先写盘，Copy 到 history 以便 Undo | 最简单；「审阅」变弱 |

**推荐默认 B + 文本文件；二进制走 C（直接写 + 可 Undo 一次）。**

### 6.4 WebSocket 事件

```json
{ "type": "file_change", "phase": "staged|applied|rejected", "change_set_id": "...",
  "entry": { "path": "a/b.ts", "op": "modify", "additions": 12, "deletions": 3 } }

{ "type": "file_activity", "path": "a/b.ts", "activity": "reading|writing" }
```

前端树与 ChangeStrip 订阅上述事件（与现有 `tool_event` 并列）。

### 6.5 规则注入

会话启动 / 切换 workspace 时：

```
load:
  workspace/AGENTS.md
  workspace/CLAUDE.md          # 可选兼容
  workspace/.takton/rules/**/*.md
→ 拼进 system 或 context 包（截断策略与现有 memory 一致）
```

---

## 7. 安全模型

1. **默认不能写 workspace 外**  
2. 打开文件夹 = 用户显式授权该树  
3. 敏感路径黑名单：`~/.ssh`, 浏览器配置、系统目录等（可配置）  
4. `command` 高风险仍走现有 confirmation  
5. ChangeSet Accept 是第二道闸（Ask 模式）  
6. 审计日志：谁在何时 accept 了哪些 path  

---

## 8. 与对话 UI 的丝滑细节

| 细节 | 说明 |
|------|------|
| 运行中锁定切换 workspace | 或提示「将中断当前任务」 |
| 文件树虚拟滚动 | 大仓库 |
| 最近项目 MRU | 最多 10 个，启动秒开 |
| 拖拽文件夹到窗口 | Electron drop → open workspace |
| 从资源管理器「用 Takton 打开」 | 后续：协议/右键（可选） |
| Diff 视图 | 内联 monospaced；大 diff 外置全屏 |
| 键盘 | `Ctrl+P` 文件跳转；`Ctrl+Shift+E` 聚焦树 |

---

## 9. 分阶段落地（建议）

### Phase F1 — 工作区一等公民（3–5 天）

- Workspace 模型 + 打开文件夹（Electron）  
- Session 绑定 root；工具 cwd/allowlist  
- 右侧 Workspace 面板：树 + 预览（从 Sidebar 迁出）  
- 顶栏 Workspace Chip  

### Phase F2 — 变更可审（4–6 天）

- ChangeSet + file_* 工具改造  
- WS `file_change`  
- ChangeStrip + Diff + Accept/Reject  
- Tool 卡片联动  

### Phase F3 — 对话深联动（2–3 天）

- `@` 文件搜索  
- 路径自动链接  
- file_activity 树高亮  

### Phase F4 — 规则与索引（2–4 天）

- AGENTS.md 自动注入  
- 轻量文件名索引 / Ctrl+P  
- 附加目录 allow-list  

### Phase F5 — 体验抛光

- 拖拽打开、MRU、信任模式、撤销 applied 变更  

**不建议** 第一期就做完整 Repo Wiki / 多 Agent 并行 worktree（可跟 Qoder Quest 对齐到 v4）。

---

## 10. 成功指标（体感）

### 10.1 有代码经验用户
1. **3 秒内**完成「打开项目 → 看到树 → 发消息 Agent 在该目录工作」。  
2. Agent 改文件时，**无需翻 tool 原始 JSON** 即可看 diff 并接受。  
3. 误改可一键拒绝；无「写到了错误盘符」工单。  
4. 对话里出现的路径 **>90% 可点击定位**。  

### 10.2 零代码基础小白（硬指标）
1. **首次**不看文档、不问人，完成「选一个文件夹 → 让 AI 改点东西 → 看懂并确认/撤销」全流程。  
2. 全程 **不出现** 必须理解的词：sandbox、cwd、path、staging、diff、repo、commit（可用「人话」替代，专家模式再显示原文）。  
3. 任何失败用 **下一句能做什么** 收尾，而不是堆栈或错误码。  
4. 误点「全部接受」后，**30 秒内**能撤销回改之前。  

---

## 11. 开放决策（待产品决策）

| # | 问题 | 选项 | 铃的推荐 |
|---|------|------|----------|
| D1 | 默认写入策略 | Ask / Auto-apply | **Ask**（桌面可在设置改 Auto） |
| D2 | Staging 实现 | Diff-only / Shadow / 直接写 | **Diff-only（文本）** |
| D3 | Workspace 面板位置 | 右侧固定 / 仅 Sidebar | **右侧固定（可关）** |
| D4 | 是否兼容 CLAUDE.md | 是 / 仅 AGENTS.md | **是（只读注入）** |
| D5 | 无 workspace 时 Agent | 禁止写文件 / 仅 sandbox | **仅「练习本」+ 强提示打开文件夹** |
| D6 | 是否本期做 Git 集成 | 显示 branch+dirty / 完整 commit | **仅显示 branch+dirty**（小白文案隐藏） |
| D7 | 默认界面模式 | 简洁 / 专业 | **简洁（小白）**，设置可切专业 | ✅ 已锁定 |
| D8 | 变更默认呈现 | 人话卡片 / 代码 Diff | **人话卡片为主，可展开 Diff** | ✅ 已锁定 |
| D9 | 首次引导 | 强制 3 步 / 可跳过 | **可跳过的 3 步引导** | ✅ 已锁定 |
| D10 | 专业模式布局 | 见 §14 | **强制项目文件夹 + 右侧可折叠栏（上目录 / 下多标签终端）** | ✅ 已锁定 |

> **D1–D10 已于 2026-07-13 由产品确认按铃推荐落地。**

---

## 12. 结论

把 Files 从「侧边栏路径浏览器」升级为：

> **Workspace（项目根）+ ChangeSet（可审变更）+ 对话内文件导航**

并对 **零基础用户** 再盖一层：

> **「工作文件夹」心智 + 人话变更卡片 + 练习本兜底 + 可撤销安全网**

专业能力（diff、路径、规则、附加目录、**多标签终端**）全部保留在「专业模式」里。

---

## 14. D10 专业模式：强制项目目录 + 右侧文件树 / 多标签终端

### 14.1 产品定义

| 项 | 说明 |
|----|------|
| 入口 | 对话顶栏切换 `简洁 | 专业`；默认简洁 |
| 强制项目 | 专业模式下 **未绑定项目文件夹则阻断发送**，弹层仅提供「选择项目文件夹」 |
| 右侧栏 | 可折叠；宽屏默认展开；`Ctrl+B` 切换 |
| 上半 | 当前项目文件目录树（懒加载） |
| 下半 | **可执行终端**，支持多分页；cwd = 项目根 |
| Agent 联动 | `command` / `bash` 等工具输出镜像到终端「Agent」页；可「在终端打开」 |

### 14.2 布局

```
┌────────┬──────────────────────────┬─────────────────────────┐
│ 左栏   │ 对话（Chat）              │ 专业右侧栏（可折叠）      │
│        │                          │ ┌─────────────────────┐ │
│        │                          │ │ 📁 项目文件目录      │ │
│        │                          │ │ （可滚动树）         │ │
│        │                          │ ├────── split ────────┤ │
│        │                          │ │ ⬛ Terminal tabs    │ │
│        │                          │ │ Agent | bash | +    │ │
│        │                          │ │ $ _                 │ │
│        │                          │ └─────────────────────┘ │
└────────┴──────────────────────────┴─────────────────────────┘
```

### 14.3 终端模型

```
TerminalTab
  id, title, kind: agent | shell
  cwd: workspace.root
  lines: { type: in|out|err|sys, text, ts }[]
  status: idle | running
```

- **Agent 页**：只读聚合 Agent 终端类工具；自动滚动  
- **shell 页**：用户输入命令 → `POST /workspace/exec`（cwd=项目根）→ 追加输出  
- **+**：新建 shell 页  

### 14.4 与 Agent 工具

- `command` / `bash` / `CommandTool` 执行时：  
  1. 推送 `terminal_event` WS（或复用 tool_event + 前端识别）  
  2. 右侧若折叠，角标提示「有终端输出」  
  3. 写入 Agent tab  

### 14.5 安全

- 专业模式 exec 仅在已绑定项目根下  
- 危险命令沿用确认策略  
- 简洁模式不暴露原生终端输入（避免小白误操作）

---

## 13. 零代码基础小白：无障碍 Files 设计（深度补充）

> 程序员产品的默认语言是「路径 / 仓库 / diff」。  
> 小白的默认语言是「我的东西在哪个文件夹、AI 动了我的什么、我能不能反悔」。  
> 本节把 v3.1 的专业骨架，翻译成 **零门槛表层**。

### 13.1 小白会卡在哪？（真实失败模式）

| 卡点 | 他们心里想的 | 传统 UI 给的 | 结果 |
|------|--------------|--------------|------|
| 不知道选什么 | 「我要给 AI 看啥？」 | sandbox / local / 绝对路径 | 放弃或乱填 |
| 怕搞坏电脑 | 「会不会删我照片？」 | 无边界说明 | 不敢用写文件 |
| 看不懂变更 | 「+12 -3 是啥？」 | unified diff | 盲点接受或全拒 |
| 路径像密码 | `E:\项目\foo\src\...` | 原始 path | 焦虑、无法定位 |
| 双模式概念 | sandbox vs 本机 | 技术切换 | 完全迷路 |
| 出了错 | 红字 traceback | 模块名+堆栈 | 以为软件坏了 |
| 多文件轰炸 | 一次改 15 个文件 | 长 tool 列表 | 认知过载 |

设计目标：**把每一步都变成「像用微信传文件夹 / 像用 WPS 修订」那么熟。**

### 13.2 心智模型替换表（对外文案）

| 内部概念（专业） | 小白界面用语 | 一句话解释（首次气泡） |
|------------------|--------------|------------------------|
| Workspace | **工作文件夹** | AI 这次只动你选中的这个文件夹里的内容 |
| sandbox | **练习本** | 练手用的安全本子，不碰你电脑里的真文件 |
| local path | **我的文件夹** | 你电脑上真实的一个文件夹 |
| cwd | （不出现） | — |
| ChangeSet | **AI 的修改草稿** | 还没正式写入，你可以点「用这些修改」或「不用」 |
| Accept | **用这些修改** | 确认写进你的文件夹 |
| Reject | **不用这些** | 丢掉草稿，保持原样 |
| Diff | **改动对比**（次级） | 左边原来 / 右边改后；默认先给人话摘要 |
| staging | （不出现） | — |
| allowlist | **AI 能碰的范围** | 徽章：仅此文件夹 |
| AGENTS.md | **给 AI 的说明书**（高级） | 可折叠，小白默认不打开 |

**产品原则：界面永远可以只用右列词活下去；左列只给「专业模式」或「复制路径」。**

### 13.3 双层界面：简洁模式 vs 专业模式

```
默认 = 简洁模式（小白）
  - 大按钮、少字、图标优先
  - 变更用人话卡片
  - 隐藏分支/SHA/原始 path（可「显示详细信息」）
  - 危险操作二次确认用白话

设置里可开 = 专业模式
  - 显示相对路径、diff、git branch
  - 键盘快捷键、附加目录、规则文件
```

切换位置：设置 →「界面专业程度」，或 Workspace 面板右上角 `简洁 | 专业`。  
**不要**让小白在首次启动就做这个选择——默认简洁，用熟了再发现专业。

### 13.4 首次 3 步引导（可跳过，不可吓人）

全屏或居中卡片，插画风格，每步一个动作：

1. **选一个文件夹当「工作文件夹」**  
   - 主按钮：`选择文件夹`（系统对话框）  
   - 次按钮：`先用练习本试试`（不碰真文件）  
   - 脚注：`AI 默认只能改这里面的内容，不会乱翻你的整个电脑`

2. **用一句话试试**  
   - 预填示例（按文件夹类型智能变）：  
     - 若检测到文档多：`帮我把这个文件夹里的说明整理成一个目录清单`  
     - 若检测到代码：`用简单的话说明这个项目是干什么的`  
     - 通用：`看看这里面都有什么，用条目列给我`

3. **当 AI 提出修改时**  
   - 演示一张「修改草稿」卡片：`用这些修改` / `不用这些`  
   - 文案：`你可以先预览，不满意就点「不用」`

跳过 → 进入练习本 + 顶部常驻浅提示条：`还没选择工作文件夹 · 点这里选择`。

### 13.5 空态与入口：消灭「路径输入框」

**简洁模式默认不展示**「请输入绝对路径」。

主空态（无工作文件夹时）：

```
📁 还没有工作文件夹

   [ 选择文件夹 ]     [ 先用练习本 ]

AI 会在你选择的范围内阅读和修改。
练习本适合第一次试用，不会改动你电脑里的真实文件。
```

次要入口（小字）：`我有特殊路径需求（专业）` → 才展开路径输入。

桌面端增强：

- 支持 **拖拽文件夹到窗口** → 直接设为工作文件夹  
- 最近使用：大卡片图标（文件夹名 + 上次时间），不要路径墙  
- 文件夹名用系统显示名，路径只在 tooltip

### 13.6 「练习本」：小白的安全气囊

| 属性 | 设计 |
|------|------|
| 是什么 | 应用数据目录下的独立沙箱文件夹（用户无感路径） |
| 叫什么 | 只叫 **练习本**，不叫 sandbox |
| 何时用 | 没选工作文件夹时；引导第二选项；设置「重置练习本」 |
| 预置内容 | 3～5 个无害示例（`欢迎.txt`、`待办示例.md`、`示例表格.csv`），让树「不是空的」 |
| 切换 | 顶栏 chip：`练习本` / `我的文件夹 · 名字` 一键切换 |
| 心理安全 | 练习本内「全部接受」也只影响练习本；文案写死 |

小白第一小时应 **几乎都在练习本或自己明确选的一个资料夹**，而不是系统盘根目录。

### 13.7 变更呈现：人话优先，Diff 退居二线

#### 人话变更卡片（默认）

```
┌─────────────────────────────────────────┐
│ ✏️ AI 想改 3 个文件                      │
│                                         │
│ 1. 说明文档.md     补充了「安装步骤」一节  │
│ 2. 名单.csv        新增 2 行联系人        │
│ 3. 配图.png        （新建）               │
│                                         │
│  [ 看看改了啥 ]  [ 用这些修改 ]  [ 不用 ]  │
└─────────────────────────────────────────┘
```

摘要生成策略（优先级）：

1. 工具/模型返回的短说明（若有）  
2. 启发式：新建 / 删除 / 大约增删行数 → 模板句  
3. 实在不行：`修改了此文件` + 可点开对比  

**禁止**默认第一屏甩出 unified diff 色块墙。

#### 「看看改了啥」渐进披露

```
点开单文件
  → 默认「对照阅读」：上=改前摘要，下=改后摘要（非程序员可读）
  → Tab：并排原文 | 高亮差异（专业）
  → 图片/二进制：缩略图 + 「替换为新版本」说明，不进 diff
```

#### 批量修改降噪

- 超过 5 个文件：折叠为 `还有 12 个文件` 分组（按文件夹聚合）  
- 自动分类标签：`文档` `表格` `代码` `图片` `其他`（扩展名映射，小白能懂）  
- **危险操作单独标红**：删除、清空、改后缀 —— 默认 **不** 进「全部接受」，需逐个点

### 13.8 对话里怎么「碰文件」而不学路径

1. **附件优先**  
   - 输入区：大号 `添加文件或文件夹`（比 @ 更显眼）  
   - 拖拽到输入框 = 附加到本条消息  

2. **点选代替打路径**  
   - 工作区树：勾选文件 → `问 AI 这些` / `让 AI 改这些`  
   - 对话生成 chip：`📄 说明文档.md` 可再点开  

3. **@ 降级为可选**  
   - 简洁模式不强调 @；专业模式保留  

4. **AI 引用可点击**  
   - 气泡里的文件名 chip → 预览  
   - 不用显示盘符路径  

### 13.9 权限与恐惧管理（安全感设计）

顶栏始终有一颗 **范围徽章**：

- `🛡️ 仅限：工作文件夹「毕业设计」`  
- 点开抽屉：用清单说明  

```
AI 现在可以：
  ✅ 阅读该文件夹内的文件
  ✅ 提出修改（需你点「用这些修改」）
  ❌ 不能动文件夹以外的内容
  ❌ 不能静默删除（删除需单独确认）
```

首次写入确认（比专业用户多一步白话）：

```
AI 准备把修改写入你的文件夹「毕业设计」。

这不会影响该文件夹以外的文件。
你以后仍可以在「修改记录」里撤销最近一次确认。

        [ 取消 ]    [ 确认写入 ]
```

### 13.10 错误与失败：只给「下一步」

| 场景 | 禁止展示 | 改为 |
|------|----------|------|
| 无权限 | Access denied / errno | `没权限改这个文件。可以换一个文件夹，或在系统里给 Takton 权限。` + 按钮 |
| 文件被占用 | sharing violation | `这个文件正被其他软件打开（例如 Word）。关掉后再点「重试」。` |
| 路径不存在 | ENOENT | `找不到这个文件，可能被挪走或改名了。` + `在树里重新选` |
| 磁盘满 | OS error | `电脑存储空间不足，清理一些空间后再试。` |
| Agent 幻觉路径 | 硬写失败 | 拦截为：`AI 想找的文件不在工作文件夹里，已阻止。` |

所有错误组件结构：

```
发生了什么（1 句）→ 为什么（可选 1 句）→ 你可以怎么做（1～2 个按钮）
```

### 13.11 撤销：小白的后悔药

- 每次「用这些修改」生成一条 **修改记录**（时间 + 人话摘要 + 涉及文件数）  
- 入口：Workspace → `修改记录` Tab；或成功 Toast 上的 `撤销`  
- **撤销最近一次** 一键恢复（依赖 ChangeSet 快照/反向 diff）  
- 文案避免 “revert commit”；用 `恢复到修改前`  

没有后悔药，就不要鼓励小白点「全部接受」。

### 13.12 信息架构：小白只看到 4 件事

右侧面板（简洁模式）只保留：

1. **我的文件**（树，大图标，中文友好名）  
2. **待确认的修改**（有红点才出现）  
3. **修改记录**（可撤销）  
4. **（次要）设置** 里的说明与专业开关  

「规则 / Git / 附加目录」全部收进 `更多` 或专业模式。

### 13.13 文案与视觉规范（执行清单）

- 主按钮动词：选择、试试、用这些修改、不用、撤销、重试  
- 避免：执行、提交、应用补丁、工作区、仓库、挂载  
- 图标 > 路径；颜色：绿=安全建议，琥珀=需确认，红=删除/不可逆  
- 一次只突出 **一个** 主行动按钮  
- 数字用「3 个文件」而不是「3 paths」  
- 时长：`大约几秒前` 而不是 ISO 时间戳  

### 13.14 无障碍与包容

- 字体可放大（跟随系统 / 应用内 2 级）  
- 色不单独表意（图标+文字）  
- 屏幕阅读：卡片有 `aria-label`：「AI 建议修改说明文档，需确认」  
- 不依赖悬停才显示关键操作（触控友好）  
- 动画可减弱（系统「减少动态效果」）

### 13.15 与开发者模式的共存（不要做成两个产品）

```
同一套 Workspace / ChangeSet 数据模型
        │
        ├─ 简洁渲染器（人话、大按钮、练习本）
        └─ 专业渲染器（diff、path、规则、附加 dir）
```

Agent 工具层、安全边界、事件协议 **完全一致**；只换 **表达层**。  
这样小白长大变成熟手，不用「毕业迁移数据」。

### 13.16 小白旅程脚本（验收用）

**剧本 A：完全新手第一次打开**

1. 看到 3 步引导或空态双按钮  
2. 选「练习本」→ 树里已有示例文件  
3. 点预置问题发送 → AI 提出修改  
4. 看懂人话卡片 → 点「用这些修改」  
5. Toast 提示成功且有「撤销」  
6. 全程不出现 sandbox/cwd/diff 字样  

**剧本 B：学生要改自己的论文文件夹**

1. 「选择文件夹」→ 选「毕业论文」  
2. 徽章显示仅限该文件夹  
3. 说「帮我把摘要改短一点」  
4. 只对 `摘要.md` 出草稿卡片  
5. 点「看看改了啥」能读懂前后文差异  
6. 确认写入；误确认后 30 秒内撤销成功  

**剧本 C：吓到了**

1. AI 建议删除多个文件 → 删除项 **不能** 被「全部接受」带走  
2. 必须逐项确认，文案含「不可轻易恢复」  

### 13.17 实现分期时如何照顾小白（插入原 Phase）

| 原 Phase | 小白必做增量 |
|----------|----------------|
| F1 工作区 | 练习本 + 空态双按钮 + 顶栏人话 chip + 拖拽文件夹；**隐藏**路径框 |
| F2 变更可审 | 人话变更卡片 + 危险操作拆分 + Toast 撤销入口 |
| F3 对话联动 | 添加文件大按钮 + 文件 chip；@ 不作为主路径 |
| F4 规则/索引 | 规则仅专业模式；Ctrl+P 可有但简洁模式用「搜索我的文件」 |
| F5 抛光 | 3 步引导、修改记录 Tab、文案审查、无障碍 |

### 13.18 成功时的产品感觉（一句话）

> 小白觉得：Takton 是「我指定一个文件夹，AI 在里面帮我收拾东西，改之前都会问我，做错了能撤销」。  
> 而不是：「又一个要我懂路径和 diff 的程序员工具」。

---

**下一步（待你确认决策表 D1–D9 后）：**  
按 Phase F1 + 小白增量拆任务进 `WORKPLAN`，优先：**练习本、打开文件夹、人话空态、范围徽章**，再做变更草稿卡片。
