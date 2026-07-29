# 进化叙事合并（0.6 · TEE 草稿 × 编制进化）

> 避免产品里出现两个互不相干的「进化」。

## 唯一叙事

**员工写述职 → 主人批 → 写入编制档案（可回滚）**。

这就是 AIOS 的「进化」。没有第二条静默成长通道。

| 名称（代码） | 用户语言 | 是否自动生效 |
|--------------|----------|--------------|
| EvolutionEngine / proposals | 进化提案 / 述职 | **否**，审批中心 |
| TEE / skill distill / planner tune | 同上（提案 kind） | **否** |
| Identity Memory 蒸馏 | 记忆沉淀（须 `approved_by`） | **否** |
| caps_adjust | 能力入编 | **否**，审批中心 |
| SubAgent 技能包归纳（市场页） | 扩展资产，**不是**员工升职 | 独立产品面 |

## 硬约束

1. `auto_apply=False`：进化建议永不静默改 caps / memory。
2. 审批中心两 Tab：**员工扩权** + **进化提案**（不是工具调用洪水）。
3. TEE 草稿若再出现，必须落到 `evolution_proposals` 表，同一套 approve/reject/rollback。

## UI 入口

- 审批 → 进化提案
- 员工资料卡 → 生成述职 / 查看提案
- 技能/市场 → 装技能（操作员视角），不替代编制进化

## 与 MEMORY_AUTHORITY

蒸馏写入 Identity Memory 必须 `source=distilled` + `approved_by`；禁止 graph 当编制真源。
