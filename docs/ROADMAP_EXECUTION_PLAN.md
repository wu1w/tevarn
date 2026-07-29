# Takton AIOS 路线图执行计划（0→12 周全目标）

> 制定：2026-07-30 · 基线分支：`feature/agent-kernel` @ `9daac99`
> 目标：以一人之力把 Takton 推进到「可对标 OpenClaw / Hermes / Claude Code 的下一代 AIOS」。
> 差异化主轴：**受治理的自进化**（有 kernel 审计链与预算治理的 learning loop）——
> 这是 Hermes 没有、OpenClaw 也没有的定位。

图例：✅ 已完成 · 🔶 部分完成 · ⬜ 未开始

---

## 第 0 周：工程止血（前提，不做新功能）

| # | 任务 | 状态 | 验收标准 |
|---|------|------|----------|
| 0.1 | git 接入远端 + 异地备份 | ✅ `91ee2dc` | push 成功，密钥零入库 |
| 0.2 | pytest 收集错误修复（tests 包 `__init__.py`） | ✅ `ba81cc7` | `pytest --co` 零 error |
| 0.3 | alembic 骨架 + `0001_baseline` 锚点 | ✅ `ba81cc7` | `alembic heads` 可解析；开发库已 stamp |
| 0.4 | 根目录清扫（tmp_*/_patch_* 归档） | ✅ `ba81cc7` | 根目录无一次性调试脚本 |
| 0.5 | GitHub Actions CI | ✅ `4a56631` | push/PR 触发：收集→测试→alembic 检查 |
| 0.6 | **22 个存量失败测试逐个处置** | ⬜ | 每个失败：修复 / 标记 skip+原因 / 删除，CI 全绿 |
| 0.7 | CI 加 badge 到 README + 失败通知 | ⬜ | README 显示 build 状态 |

**0.6 处置指引**（按上次全量跑的失败清单）：
- `test_version_sync.py` ×2：版本号对齐即可（VERSION / CHANGELOG / package.json）
- `test_command_cwd_security.py` ×3、`test_command_policy.py` ×2：疑似 Windows 路径分隔符断言，优先在 CI (Linux) 确认是否只在 Win 失败 → 平台性 skip
- `test_sandbox_backends.py` ×3、`test_agent_computer.py` ×2：seatbelt/job 平台分支断言，同上
- `test_security_hardening.py` ×2：secret 引导逻辑，需真修
- 其余（bridge/subagent/skill_contract/orphan_tool）：逐个 debug，不许一刀切 skip

---

## 第 1-4 周：进化环从骨架到血肉（差异化赌注）

### 已落地的地基（9daac99）
- ✅ `evolution/distiller.py`：轨迹→SKILL.md 蒸馏（LLM + 模板兜底），draft 入审批链
- ✅ `evolution/scoreboard.py` + `evo_skill_outcomes` 表：计分 + 退化自动回滚（降级不删除，kernel 审计留痕）
- ✅ epilogue 7.65 钩子接线；阈值 settings 化（`agent_evolution_score_*`）

### 剩余任务

| # | 任务 | 状态 | 验收标准 |
|---|------|------|----------|
| 1.1 | **蒸馏质量实测与调优**：配真实 LLM 跑 ≥20 条真实任务轨迹，人工评审蒸馏出的 SKILL.md 合格率 | ⬜ | 合格率 ≥60%；不合格样本归因（prompt / 轨迹裁剪 / 门槛） |
| 1.2 | 蒸馏 prompt 迭代：把 1.1 的失败模式写回 `_DISTILL_SYSTEM`（如：过度具体化、步骤幻觉） | ⬜ | 第二轮合格率 ≥80% |
| 1.3 | **技能改进闭环**：已 applied 的技能失败率高时，自动生成 gen+1 草案（复用 distiller，输入=失败轨迹+旧技能全文），进 draft 审批 | ⬜ | 新表字段无需增加；`store.create_asset(gen=N+1)`；测试覆盖 |
| 1.4 | **自动审批分级**：低风险进化（纯 playbook、无新工具/权限）由 gates 自动通过；红线类（新能力、外部安装）保持人审 | ⬜ | `evolution/gates.py` 加 auto_approve 规则 + settings 开关（默认关）；审批面板显示「自动通过」标签 |
| 1.5 | 前端进化面板补强：技能计分卡（成功率曲线 / gen 对比 / 回滚历史） | ⬜ | `/approvals` 进化 tab 显示 scoreboard 数据；回滚事件可回溯到 kernel 审计链 |
| 1.6 | scoreboard 阈值实测校准（当前 8 样本 / 15pp 为拍脑袋初值） | ⬜ | 以 1.1 的数据回放验证误回滚率 <5% |
| 1.7 | 每周进化周报：cron 汇总 本周新技能/计分/回滚 推送到渠道 | ⬜ | 复用 `cron_scheduler` + `build_daily_report` 模式 |

---

## 第 5-8 周：MCP 深化 + 远程执行

### 已落地的地基
- ✅ MCP 调用经 `kernel.mediate(action=mcp_call)` 收口
- ✅ `computer/docker_backend.py`（长驻容器 per agent_key）、`computer/ssh_backend.py`（BatchMode，免密 key）

### 剩余任务

| # | 任务 | 状态 | 验收标准 |
|---|------|------|----------|
| 5.1 | **MCP server 生命周期管理**：断线重连、健康检查、单 server 故障不拖垮启动 | ⬜ | `mcp_hub/client.py` 加 reconnect + backoff；`get_mcp_status` 显示 last_error/uptime |
| 5.2 | **MCP 工具过滤**：per-server allowlist/denylist（学 Hermes 的 tool filter） | ⬜ | MCP server 配置加 `tool_filter` 字段 + alembic 迁移 + 前端扩展页 UI |
| 5.3 | MCP 市场：从 URL/npm 一键装 server（复用 skill_store 的 url_review 安全审查） | ⬜ | 扩展页可搜索/安装/卸载；安装前展示审查报告 |
| 5.4 | **Docker 后端实机验证**：Linux + Win(Docker Desktop) 各跑通一次工单 | ⬜ | e2e 手册化脚本 `tests_manual/`；容器泄漏检查（evict 后 `docker ps` 干净） |
| 5.5 | **SSH 后端实机验证**：一台真实 VPS 跑通「IM 下发工单 → 远端执行 → 报告回传」 | ⬜ | 全链路录屏；断网重试行为文档化 |
| 5.6 | dispatcher `evict_worker` 联动 `DockerBackend.dispose()`（容器回收） | ⬜ | 单测：evict 后容器名不存在 |
| 5.7 | 文件工具（file_read/write/edit/glob/grep）在 docker/ssh 后端下的路径语义统一 | ⬜ | 明确：远端模式下文件工具走哪个文件系统；写决策进 ALPHA_AUTHORITY.md |

---

## 第 9-12 周：可信度与分发

| # | 任务 | 状态 | 验收标准 |
|---|------|------|----------|
| 9.1 | **拆 loop.py 巨石**（2842 行 → <800 行）：延续 phases/ 化，剩余大块按「工具批执行 / 权限申诉 / 流式推送 / 状态机」切模块 | ⬜ | `tests/test_loop_freeze.py` 拆分前后同绿（行为冻结既有先例）；分 ≥4 个 PR 渐进，绝不一把梭 |
| 9.2 | 一键安装验证：`install.ps1` / `install.sh` 在干净虚拟机跑通 + 装后 smoke test（起服务→发一条消息→收到回复） | ⬜ | 三平台（Win/macOS/Linux）录屏或 CI 矩阵 |
| 9.3 | **3 个一镜到底演示**：①IM 遥控 VPS 上的 agent 完成运维任务 ②workforce 多身份协作产出报告 ③技能自动沉淀→审批→复用全流程 | ⬜ | 每个 ≤3 分钟视频 + 配套可复现脚本 |
| 9.4 | llms.txt | ✅ | 已入库（根目录）；后续每次大改同步更新 |
| 9.5 | llms-full.txt 自动生成（docs 拼接，学 Hermes） | ⬜ | `scripts/gen_llms_full.py` + CI 产物 |
| 9.6 | 对外 README 重写：定位一句话 + 架构图 + 快速开始 + 与 OpenClaw/Hermes 对比表 | ⬜ | 英文为主中文次之；badge 齐全 |
| 9.7 | CONTRIBUTING.md + issue 模板 + 公开 roadmap（破除巴士系数=1） | ⬜ | 首个外部 issue/PR 有明确路径 |
| 9.8 | alembic 首个真实迁移演练：任选一个 model 小改动走全流程 | ⬜ | `revision --autogenerate` → CI 验证 → 升级老库无损 |

---

## 战略上明确不做（本周期）

- ❌ 追 20+ IM 渠道（现有 9 个够覆盖）
- ❌ 语音模式（等生态稳定）
- ❌ 自研 RL 训练管道（模型实验室的游戏）
- ❌ Daytona/Modal serverless 后端（先服务自托管人群，docker/ssh 够用）

## 执行纪律

1. **每周五**：全量 pytest + 手工冒烟，红了先修再前进
2. **任何 schema 变更走 alembic**，create_all 只服务全新库
3. **进化红线不放松**：新能力/新工具/外部安装永远人审；自动通过的只能是纯 playbook
4. 每完成一个编号任务，本文件状态位更新并随代码同 commit

## 风险与依赖

| 风险 | 缓解 |
|---|---|
| 蒸馏质量不达标（1.1-1.2） | 兜底：保留模板生成路径；质量差就提高入库门槛而不是关闭功能 |
| loop.py 拆分引入回归（9.1） | 行为冻结测试先行；每步小 PR；任何一步红灯即回滚 |
| Windows 原生库退出崩溃（pytest 0xC0000005） | CI 以 Linux 为准；本地用 `-p no:cacheprovider` 已可拿到完整结果 |
| 单人节奏断档 | 本计划文件 = 唯一事实源；每次会话从「状态位」接续 |
