# 全自动冲刺：0.4.6 收口 → 0.5.0 Durable → 0.6 对齐

> 主人睡眠期间代理执行。顺序固定，不可倒序堆功能。

## 本夜目标（可完成切片）

### P0 体验
- [x] 运行记录默认收起 + 展开/收起
- [x] 死信工单 API + 重放/丢弃 + 员工页面板
- [x] 全局「运行中」`/kernel/jobs/running` + 内核页摘要
- [x] process_id 出现在 inbox 列表字段

### P0 耐久（0.5.0 核心）
- [x] dead 状态 + reclaim 既有路径 + CRASH_RECOVERY 文档
- [x] pytest：requeue dead / discard

### P1 记忆与权限
- [x] `docs/internal/MEMORY_AUTHORITY.md` 写入权威
- [x] 审批中心文案统一（员工扩权 / 进化提案）
- [x] 记忆读取优先级测试 `test_memory_authority.py`
- [x] policy.decision 事件 + `/kernel/policy/decisions` + 内核「权限网」Tab

### P1 0.6 体验
- [x] 工单完成/失败系统通知
- [x] 待批扩权系统通知
- [x] `AIOS_OPERATOR.md` 一周自检清单
- [x] 一键备份导出 + 内核页按钮
- [x] 进化叙事合并 `EVOLUTION_NARRATIVE.md`
- [x] 模板员工 seed（管家/研究/编码）
- [x] 委托预算（parent→child 预留）已有 + 测试

### 质量门
- [x] 全量 `backend/tests/kernel` 绿（除 bwrap 环境 skip）
- [x] 回归：dead、org、stub、agent_call、policy 事件计数
- [ ] 手工：杀后端后工单恢复（主人醒来自测 Day3）
- [ ] 连续 7 天真实使用（0.6.0 DoD，需主人在场）

### 本夜已交付摘要（2026-07-28 续）
1. 运行记录默认收起  
2. 死信 dead + requeue/discard + UI  
3. jobs/running + 内核页  
4. MEMORY_AUTHORITY / CRASH_RECOVERY / AIOS_OPERATOR / EVOLUTION_NARRATIVE  
5. 工单/扩权通知（Notification.data）  
6. policy.decision 权限网 + 活动页 kind 映射  
7. 备份导出 API + 内核一键备份  
8. 全局 Runs：`GET /runs/recent` + 活动页「全局运行」  
9. 测试修复循环：kernel 全量绿  


### 醒来后建议（收口 0.6.0）
- Day1–7 按 `AIOS_OPERATOR.md` 自检打勾  
- 跑 `python -m backend.scripts.seed_template_crew` 若编制为空  
- 可选：审计只读页独立路由、日报一键已读、性能基线记录  
- 真机 kill -9 后端验证 CRASH_RECOVERY  

## 不做（本夜）
- 公有发布、多租户 SaaS
- 新记忆后端
- 重写全部 FE 为完整飞书
- 进化 auto_apply（硬禁止）
