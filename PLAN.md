# Takton AIOS 前端施工 PLAN

> 依据：`docs/design/aios-workbench-demo-v2.html`（已拍板定稿）
> 分支：`feature/agent-kernel`
> 原则：不推倒重来——在现有 Next.js 16 + Tailwind 4 骨架上「换肤 → 重构布局 → 逐页重建」

## 技术现状

- Next.js 16 App Router + React 19 + Tailwind 4（`@theme inline` 映射 CSS 变量）
- 主题系统已有：`data-theme` + ThemeProvider + themeStore（zustand）
- 布局：`AppShell`（IconRail + AgentSidebar + TitleBar）
- 数据层：`lib/api.ts` + `api-hooks.ts`（react-query）+ WebSocket
- 组件库：`components/ui/` + 业务组件 20 个目录
- 一级导航 9 项（demo v2）：驾驶舱 / Agent / 目标 / 审批 / 知识 / 活动 / 内核 / 扩展 / 设置

## 分期

### P0 换肤（变量级，组件零改动）✅
- [x] 盘点 globals.css 变量结构
- [x] `[data-theme="light"]` → 暖纸色板（#f6f2e8 底 + sage #5f7550 主色）
- [x] `[data-theme="dark"]` → 暖炭色板（#171613 底 + sage #a3bd8b 主色）
- [x] 浅色模式纸张颗粒纹理（SVG feTurbulence，multiply 0.16）
- [x] glow/gradient 硬编码紫色 → sage 系
- [x] 样式与 demo v2 变量对齐

### P1 布局重构（AppShell → 三栏）✅
- [x] IconRail 收敛为 9 项：驾驶舱 / Agent / 目标 / 审批(badge) / 知识 / 活动 / 内核 / 扩展 / 设置(底)
- [x] rail 顶部 takton logo（圆形，点击回驾驶舱）
- [x] rail 底部主题切换 ☀/☾
- [x] Sidebar 重构：品牌区 + 全局搜索 + 「+ 新建 Agent」+ Agent 列表 + 协作关系
- [x] 全局搜索：Agent / 目标 / 审批 / 进化 / 页面
- [x] 协作关系接 `workforce/org` 真数据
- [x] 旧路由保留可访问（URL 直达），导航不再露出

### P2 驾驶舱（新首页 `/`）✅
- [x] 状态卡 ×4（今日任务/Token/知识/待审批，可点击跳转）
- [x] 工作动态 feed（workforce 汇报 + kernel events）
- [x] 目标卡 + Agent 实时状态（goal-tree 真数据）
- [x] 协作组卡（workforce/org 汇报线涌现）
- [x] 数据接 lib/api.ts 真实接口

### P3 Agents + Profile 抽屉 ✅
- [x] Agent 卡片网格（状态/预算条/能力标签/成功率）
- [x] Profile 抽屉 5 tab：今日工作 / 记忆(版本链) / 成长轨迹 / 成本 / 联系
- [x] 成长轨迹接 evolution proposals（生成述职 / 批准 / 拒绝 / 回滚）
- [x] 挂起/复职/编辑配置 操作接 kernel API
- [x] 协作关系区块（org 卡）

### P4 审批中心 ✅
- [x] 三类分色卡片（决策/权限/高危）+ 关联跳转 + 批量通过(高危二次确认)
- [x] **第二 tab：AI 团队自我进化**（proposals approve/reject/rollback）
- [x] 审批规则模态（含进化必审红线）
- [x] badge 实时联动（escalation + evolution pending，15s 轮询）

### P5 目标 / 知识 / 活动 ✅
- [x] 目标：O-KR 树 + 进度条 + 责任 Agent + 制定向导
- [x] 知识：KnowledgeCenter（卡片流 + 上传 + RAG）
- [x] 活动：时间线 + 类型筛选 + **审计链 JSON 导出**
- [x] 进化相关 kind 文案（propose/apply/reject/rollback）

### P6 内核 / 扩展 / 设置 ✅
- [x] 内核：进程表 + mediate 裁决 + 哈希链 + 治理 tab + shared_state 观测
- [x] 扩展：技能市场 + MCP + **从链接安全审查/安装**
- [x] 设置：demo 侧栏拆分 通用 / LLM / 渠道 / 后端 / 关于

### P7 双语正式化 ✅
- [x] localeStore 双语表
- [x] 英文全路径扫描 e2e：`frontend/e2e/i18n_en_scan.spec.ts`
- [x] B 类静默条：skills/tools/cron/mcp/config/context/profiles

## 验证标准

- 每期：`npm run build` 零错误 + dev server 截图核对 demo
- P0 验收：现有页面在无色板破坏的前提下整体呈现新气质
- 最终验收：与 demo v2 逐页对比截图 + 英文模式零中文残留

## 本轮补完（相对 demo v2 + 后续 6 项）

| 缺口 | 落地 |
|---|---|
| 审批第二 tab 进化闭环 | `/approvals` + kernel evolution API |
| 扩展页 WIP | `/market` 技能 + MCP + URL 审查 |
| 驾驶舱目标/协作组 | goal-tree + workforce/org + report |
| 侧栏搜索/org | ⌘K + reports_to |
| Profile 成长 | proposals + analyze |
| 活动导出 | 审计链 JSON |
| 内核治理 | 红线 + 快捷入口 |
| P7 英文扫描 | `e2e/i18n_en_scan.spec.ts` |
| 设置分栏 | demo set-nav 五栏 |
| B 类静默 | `LegacyQuiet` 引导回 AIOS |
| notifications 性能 | `list_page` 单 session + 复合索引 |
| kernel 外部化 | escalations 落盘 + shared_state 合并 DB |
