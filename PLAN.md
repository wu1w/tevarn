# Takton AIOS 前端施工 PLAN

> 依据：`docs/design/aios-workbench-demo-v2.html`（已拍板定稿）
> 分支：`feature/agent-kernel`
> 原则：不推倒重来——在现有 Next.js 16 + Tailwind 4 骨架上「换肤 → 重构布局 → 逐页重建」

## 技术现状

- Next.js 16 App Router + React 19 + Tailwind 4（`@theme inline` 映射 CSS 变量）
- 主题系统已有：`data-theme` + ThemeProvider + themeStore（zustand）
- 布局：`AppShell`（IconRail + Sidebar + TitleBar）
- 数据层：`lib/api.ts` + `api-hooks.ts`（react-query）+ WebSocket
- 组件库：`components/ui/` + 业务组件 20 个目录
- 现有路由 20+ 个（pages 粒度过细，导航超载）

## 分期

### P0 换肤（变量级，组件零改动）✅ 本期目标
- [x] 盘点 globals.css 变量结构
- [ ] `[data-theme="light"]` → 暖纸色板（#f6f2e8 底 + sage #5f7550 主色）
- [ ] `[data-theme="dark"]` → 暖炭色板（#171613 底 + sage #a3bd8b 主色）
- [ ] 浅色模式纸张颗粒纹理（SVG feTurbulence，multiply 0.16）
- [ ] glow/gradient 硬编码紫色 → sage 系
- [ ] dev server 双主题截图验证

### P1 布局重构（AppShell → 三栏）
- [ ] IconRail 收敛为 9 项：驾驶舱 / Agent / 目标 / 审批(badge) / 知识 / 活动 / 内核 / 扩展 / 设置(底)
- [ ] rail 顶部 takton logo（圆形，点击回驾驶舱）
- [ ] rail 底部主题切换 ☀/☾
- [ ] Sidebar 重构：品牌区 + 全局搜索 + 「+ 新建 Agent」+ Agent 分组列表（协调/领域/支持）+ 协作关系
- [ ] 旧路由保留可访问（URL 直达），导航不再露出；内容迁移后逐页删除

### P2 驾驶舱（新首页 `/`）
- [ ] 状态卡 ×4（今日任务/Token/知识新增/待审批，可点击跳转）
- [ ] 工作动态 feed（汇报式，非聊天）
- [ ] 目标卡 + Agent 实时状态
- [ ] 协作组卡（项目组讨论，可旁观）
- [ ] 数据接 lib/api.ts 真实接口

### P3 Agents + Profile 抽屉
- [ ] Agent 卡片网格（状态/预算条/能力标签/成功率）
- [ ] Profile 抽屉 5 tab：今日工作 / 记忆(版本链) / 成长轨迹 / 成本 / 联系
- [ ] 挂起/复职/编辑配置 操作接 kernel API

### P4 审批中心
- [ ] 三类分色卡片（决策/权限/高危）+ 关联跳转 + 批量通过(高危二次确认)
- [ ] 审批规则模态
- [ ] badge 实时联动（WS）

### P5 目标 / 知识 / 活动
- [ ] 目标：O-KR 树 + 进度条 + 责任 Agent + 制定向导
- [ ] 知识：卡片流 + 全文抽屉 + 手动上传 + RAG 状态
- [ ] 活动：时间线 + 类型筛选 + 审计链导出

### P6 内核 / 扩展 / 设置
- [ ] 内核：/proc 进程表（挂起/恢复）+ mediate 拦截 + 预算事件 + 节点
- [ ] 扩展：技能市场 + MCP 服务 + 从链接添加（安全审查流）
- [ ] 设置：通用(主题/语言/通知) / LLM(provider+按角色分模型) / 渠道 / 后端 / 关于

### P7 双语正式化
- [ ] localeStore 接 689 条翻译表（demo 资产直接复用）
- [ ] 英文模式全路径扫描脚本移植（playwright e2e）
- [ ] 旧页面清理 + 导航收口

## 验证标准

- 每期：`npm run build` 零错误 + dev server 截图核对 demo
- P0 验收：现有 20+ 页面在无色板破坏的前提下整体呈现新气质（允许局部违和，P1+ 逐页修）
- 最终验收：与 demo v2 逐页对比截图 + 英文模式零中文残留
