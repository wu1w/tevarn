# Phase 5 执行详规（轻量化发行与公开）

> **版本决议（2026-07-30）**：feature 分支上传 GitHub 统一为 **`0.4.10-alpha`**  
> （权威 `backend/VERSION` + `scripts/sync_version.py`）。公开正式 tag 叙事后续可再升，**本阶段不锁死 0.7/1.0**。  
> 原则：**不重写产品，只验证默认路径 + 安全终审 + 定位文案 + 可演示**；吸取「太重装不动」教训。  
> 上游：`docs/DEV_PLAN_PHASE1-5.md` §Phase 5 · 依赖 Phase 1–4 工程关账（已完成）。

---

## 0. 目标与关账

| 目标 | 消灭方式 |
|------|----------|
| 装不动 / 依赖重 | 默认 SQLite + 无 Redis + 无 Qdrant 全绿；安装 10 分钟可对话 |
| 不敢公开 | 暴露面安全终审 + JWT/CORS/webhook 清单 + 回归 suite |
| 定位不清 | README / CHANGELOG / TECHNICAL_MANUAL 统一「治理内核 + 可自进化数字员工」 |
| 没人信 | 首发 demo 三连（kill 恢复 / 进化回放 / 权限审计） |

**工程关账**：5.1–5.2 checkbox 绿 + 安全回归绿 + 降级矩阵测绿 + Win NSIS 冒烟。  
**公开关账**：README 定位改完 + demo 三连可点播 + 版本/CHANGELOG 对齐。  
**5.3 公开后节奏**：不阻塞 0.7 发版；发版后滚动。

### 现状快照（开工前，2026-07-30）

| 零件 | 现状 | 缺口 |
|------|------|------|
| `start.py` | 开发/prod/electron 启动脚本存在 | 缺「全新机 10 分钟」验收脚本与记录 |
| `scripts/install.ps1` / `install.sh` | **Release 下载** Setup/AppImage | **不是**源码 venv 从零路径；且依赖已有 GitHub Release |
| Electron | 根 `package.json` + `electron-builder` NSIS / AppImage / mac | 本机未强制冒烟；`frontend/release` 可能未产出 |
| 默认依赖 | `db_url` SQLite；`agent_kernel_redis_shared=false`；`qdrant_url=""` | **缺降级矩阵自动化**（无 Redis/无 Qdrant 冒烟） |
| JWT | `_load_or_generate_secret` + `_reject_default_secrets` + `security_check` | 公开终审清单未跑；channel 入站长度/注入仍空 |
| README | badge 仍写 0.3.2；叙事偏「Agent 终端」 | 未对齐 AIOS / 治理内核 / Run·Kernel·Evolution |
| VERSION / CHANGELOG | `1.0.0-alpha` 已有；DEV_PLAN 写 0.7 | **版本叙事需统一**（见 §2） |
| demo 三连 | 机制齐（P2 recovery / P4 replay / P3 court） | **无录屏/脚本化演示路径** |

---

## 1. 切片总览（依赖序）

```
5.0 版本与范围冻结
   │
   ▼
5.1a 零依赖降级矩阵 ──► 5.1b 安装双路径 ──► 5.1c 资源基线 ──► 5.1d Electron Win 冒烟
   │                         │
   └────────────┬────────────┘
                ▼
         5.2a 安全终审 ──► 5.2b 文档定位 ──► 5.2c demo 三连 ──► 5.2d 版本/CHANGELOG/手册
                │
                ▼
         5.2e 公开 checklist 关账 → tag / Release
                │
                ▼
         5.3 滚动（生态文档 · 渠道按需 · backlog）
```

| 切片 | 工期感 | 交付物 | 风险 |
|------|--------|--------|------|
| **5.0** | 0.5d | 版本号决议 + 本详规冻结 | 0.7 vs 1.0 叙事打架 |
| **5.1a** | 1d | 降级矩阵测试 + 默认配置文档 | RAG local 路径行为漂移 |
| **5.1b** | 1–2d | 源码 bootstrap + Release 安装双文档；10 分钟计时 | Win 权限/杀软 |
| **5.1c** | 0.5d | 资源基线实测表（RSS） | 本机干扰 |
| **5.1d** | 1–2d | `dist:win` NSIS 冒烟；Linux/mac 尽力 | 打包体积/python 嵌入 |
| **5.2a** | 1–2d | 公开暴露面审计报告 + 必修洞 | webhook/CORS 边界 |
| **5.2b** | 1d | README 重写 + 定位一页纸 | 文案范围蔓延 |
| **5.2c** | 1–2d | demo 脚本 + 录屏三连 | 真 LLM 成本 |
| **5.2d** | 0.5–1d | CHANGELOG / TECHNICAL_MANUAL / VERSION 对齐 | 漏模块 |
| **5.2e** | 0.5d | 公开 checklist + Release 清单 | — |
| **5.3*** | 滚动 | packages 文档；渠道冻结维持；issue backlog | 不进 0.7 硬门禁 |

---

## 2. 5.0 版本与范围冻结

### 2.1 版本决议（已拍板）

| 项 | 值 |
|----|-----|
| **feature 分支产品版本** | **`0.4.10-alpha`** |
| 权威文件 | `backend/VERSION` |
| 同步命令 | `python scripts/sync_version.py` / `--check` |
| 覆盖 | package.json · frontend · pyproject · appVersion.ts · FastAPI · CLI |

公开正式版号（0.7.0 / 1.0.0 等）**留到 5.2e Release 再议**；本分支一律 `0.4.10-alpha`。

### 2.2 明确不做（防 Phase 5 膨胀）

- 不自建技能市场 / 多租户 / 云托管  
- 不补 GAP 其余 57 个前端缺口  
- 不新增第 8+ 渠道适配器（**冻结维持**）  
- 不做移动端  
- 不重写 Kernel / 记忆 / 权限架构  
- 不为 xdist 以外的测试基建大重构（已修 worker DB 隔离即可）  
- **不把「全平台 Electron 完美签名公证」当硬门禁**（Win NSIS 硬；mac 公证软）

### 2.3 公开前必须仍满足的上游债（从 P1–4 体验关账带入）

> 不阻塞 5.1 开工，但 **5.2e 公开 checklist 前建议至少完成 ★ 项**。

| ID | 项 | 优先级 |
|----|-----|--------|
| D1 | channel 入站长度上限 + 基础注入 harden | ★ 安全终审 |
| D2 | 一次隔夜 inbox 恢复 dogfood 记入 DOGFOOD_LOG | ★ demo1 素材 |
| D3 | 一次 evolution 回放→上岗 dogfood | ★ demo2 素材 |
| D4 | PPT 跨会话 preference dogfood | 中（可演示脚本代替） |
| D5 | Intent 接 subagent / Wiki bus 真写 | 低（0.7 后） |

---

## 3. 5.1 轻量化发行 — 详细设计

### 3.1a 零外部依赖降级矩阵

**默认画像（公开默认）**

```text
SQLite (takton.db)
Redis: OFF（agent_kernel_redis_shared=false, redis_url=""）
Qdrant: 未配置（qdrant_url=""）→ RAG local / 禁用向量，不炸启动
单进程 Kernel 内存态
single_user_mode=true + loopback bind
```

**验收矩阵（自动化优先）**

| # | 场景 | 期望 | 实现建议 |
|---|------|------|----------|
| M1 | 无 `redis_url` 启动 | lifespan OK；busy/charge 回落内存 | pytest + 可选 `scripts/smoke_zero_deps.py` |
| M2 | 无 Qdrant / 无 embedding key | 启动 OK；recall/RAG 不 500 | 同上 |
| M3 | 仅 SQLite 跑 security + kernel 冒烟子集 | 绿 | CI job 或本地脚本 |
| M4 | 文档：`docs/internal/STORAGE.md` 或新 `docs/ZERO_DEPS.md` 一页说明 | 可读 | 文档 |

交付：

- [ ] `scripts/smoke_zero_deps.py`（或 `backend/scripts/…`）：启 app / 打 `/api/health` / 一次 login 或 single-user 探针  
- [ ] `backend/tests/test_phase5_zero_deps.py`：配置断言 + 关键路径不依赖 redis/qdrant  
- [ ] README / ZERO_DEPS 写明「可选加速：Redis / Qdrant」

### 3.1b 安装双路径（10 分钟可对话）

公开用户实际有两条路，**都要写清，避免 install.ps1 被误认为源码安装**：

| 路径 | 受众 | 命令/动作 | 成功标准 |
|------|------|-----------|----------|
| **R. Release 客户端** | 终端用户 | `install.ps1` / `install.sh` 拉 Setup/AppImage | 安装后打开能登录/单用户对话 |
| **S. 源码开发者** | 贡献者 / dogfood | `git clone` → venv → `pip` → `start.py` | 浏览器打开 chat 发出第一条消息 |

交付：

- [ ] `docs/INSTALL.md`（中英可先中文）：R + S 两节，计时目标 ≤10 分钟（S 含模型 key 配置）  
- [ ] 复核 `start.py`：venv 优先、密钥生成、端口占用提示、失败可读  
- [ ] 可选：`scripts/bootstrap_dev.ps1` / `.sh`（创建 venv、装 deps、写 `.env.example`→`.env`）  
- [ ] **实测记录**：Win 主机一张表（机器配置 / 耗时 / 卡点）写入 `reports/PHASE5_INSTALL_TIMING.md`

> 若 Release 资产尚未发布：5.1b 先关账 **S 路径**；R 路径与 5.1d / 5.2e Release 绑定。

### 3.1c 资源基线

| 指标 | 目标 | 量法 |
|------|------|------|
| 空载（backend+idle FE 或 Electron 空闲） | RSS **&lt; 500MB** | 任务管理器 / `psutil` 脚本 |
| 单会话轻聊峰值 | **&lt; 1.5GB** | 10 轮短对话后采样 |
| 机器 | 8GB RAM、无 GPU | 笔记本即可 |

交付：

- [ ] `scripts/measure_rss.py`（或手工表）  
- [ ] `reports/PHASE5_RESOURCE_BASELINE.md`（日期、版本、数字、是否达标）  
- [ ] 不达标时的止损：关 RAG、减 FE devtools、prod 模式测（`start.py --prod`）

### 3.1d Electron 打包冒烟

优先级：**Win NSIS > Linux AppImage > mac（软）**

```text
npm run dist:win   # 或 package.json 等价
→ 产出 Setup.exe
→ 干净用户目录安装
→ 启动 → 单用户/登录 → 发一条 chat
→ 关窗行为符合 CANONICAL（不误杀 runtime，若适用）
```

交付：

- [ ] Win 冒烟 checklist（`docs/internal` 或 reports）  
- [ ] 已知限制列表（杀软误报、需本机 Python 嵌入与否）  
- [ ] Linux/mac：有机器则跑；无则文档标注「CI/维护者机」

---

## 4. 5.2 公开准备 — 详细设计

### 4.1 5.2a 安全终审

**范围（公开暴露面，不做全库哲学审计）**

| 面 | 检查点 | 已有零件 | 动作 |
|----|--------|----------|------|
| 认证 | JWT 强度；无默认弱密钥；single_user × 非 loopback = fail | `security_check`、`_reject_default_secrets` | 补测 + 报告勾选 |
| CORS | 默认不 `*`；与 single_user 组合 | `simple_cors` | 审计 + 测 |
| Channel webhook | 签名校验 / 密钥；**入站长度 + 注入** | gateway 去重等；长度/注入仍空 | **实现 D1 + 测** |
| 工具执行 | loopback 信任边界；command 策略 | Phase1 security suite | 回归必绿 |
| 默认凭据 | 首次启动生成；文档禁止提交 `.env` | `start.py` / secrets.json | 勾选 |
| 管理 API | 未授权 401/403 | 既有 | 抽样子集 |

交付：

- [ ] `reports/PHASE5_SECURITY_REVIEW.md`（日期、范围、发现、处置）  
- [ ] `backend/tests/security/` 保持全绿；**新增** channel 长度/注入用例  
- [ ] 若有 fail 级：修完再 5.2e  

**D1 建议实现（最小）**

```text
channel_gateway 入站：
  - max body / text length（配置项，默认如 32k）
  - 拒绝 NULs；可选剥离明显 tool-injection 定界符（保守，避免误伤）
  - 超限 → 4xx + 日志，不进 loop
```

### 4.2 5.2b 版本对齐与 README

**定位一句话（中英固定）**

> Takton：**带治理内核的可自进化数字员工运行时**（本地优先）  
> — 不是又一个 coding CLI / chat wrapper。

README 结构建议：

1. 一句话定位 + 与 OpenClaw/CLI 差异  
2. 三张能力图：统一 Run · 权限 court · 进化回放  
3. 10 分钟安装（链到 INSTALL.md）  
4. 架构简图（链 EXECUTION_MODEL / MEMORY_BUS）  
5. 安全与本地优先  
6. Demo 三连链接  
7. 版本 / License  

交付：

- [ ] README 重写（badge 版本号正确）  
- [ ] `docs/design/PUBLIC_POSITIONING.md` 半页（可选，防文案漂移）

### 4.3 5.2c 首发 demo 三连

| # | 故事 | 依赖机制 | 脚本化建议 | 录屏要点 |
|---|------|----------|------------|----------|
| 1 | kill 进程，任务爬起来继续 | `run_recovery` / durable inbox | 派长 inbox → kill backend → 重启 → 见续跑 | 前后 Run status / checkpoint |
| 2 | 技能学会并回放验证上岗 | `replay_validator` + Evolution UI | 造 draft → replay pass → apply | 面板 pass/fail |
| 3 | 审计链解释权限 | `permission_court` + Kernel policy | 触发 allow/deny → Kernel 页 layer/rule | 哈希/事件列表 |

交付：

- [ ] `docs/demo/` 或 `docs/internal/DEMO_TRILOGY.md`：逐步操作（可无真视频先文字）  
- [ ] 可选：`scripts/demo_*.ps1` 准备数据  
- [ ] 视频托管：Release 附件 / 文档链（发版前至少 1 路完整）

### 4.4 5.2d CHANGELOG + TECHNICAL_MANUAL

- [ ] CHANGELOG：从 0.4.6-alpha / 1.0.0-alpha 增量 **收束到 0.7.0** 条目（P1–4 能力用用户语言写）  
- [ ] TECHNICAL_MANUAL：执行模型改为 Run 统一；链 `EXECUTION_MODEL` / `MEMORY_BUS` / court / recovery；删过时「双轨」描述  
- [ ] `scripts/sync_version.py --check` CI 绿  

### 4.5 5.2e 公开 checklist（硬门禁）

```text
[ ] 安全回归 security/ 全绿
[ ] 降级矩阵 M1–M3 绿
[ ] PHASE5_SECURITY_REVIEW 无未关闭 fail
[ ] INSTALL 双路径至少 S 实测 ≤15min（目标 10）
[ ] Win NSIS 冒烟通过（或书面豁免原因）
[ ] README 定位 + 版本号
[ ] CHANGELOG 0.7.0 节
[ ] demo 三连至少文字可跟做；尽量 1 路视频
[ ] DOGFOOD_LOG 无「严重度=高」未关闭
[ ] tag + GitHub Release 资产（Setup / AppImage 尽力）
```

---

## 5. 5.3 公开后节奏（不阻塞发版）

| 项 | 动作 | 触发 |
|----|------|------|
| 生态最小面 | `docs/PACKAGES.md`：publisher 安装 URL、agentskills 兼容 | 发版后 1 周内 |
| 渠道解冻 | 仍冻结；用户 issue 投票再开 | 外部需求 |
| Backlog | GitHub issues 标签 `post-0.7` | 持续 |

---

## 6. 建议日历（单人 + AI，约 2–3 周）

| 周 | 焦点 |
|----|------|
| **W1** | 5.0 冻结 · 5.1a 降级矩阵 · 5.1b 源码安装路径 · D1 入站 harden 开工 |
| **W2** | 5.1c 资源 · 5.1d Win 包 · 5.2a 安全终审收口 · README 草稿 |
| **W3** | demo 三连 · CHANGELOG/手册 · 5.2e checklist · tag Release |

每日：CI 保绿；周五 dogfood 一条写入日志。

---

## 7. 风险与止损

| 风险 | 信号 | 止损 |
|------|------|------|
| Electron 打包泥潭 | Win dist 连续 2 天不过 | 公开先 **源码 + start.py**；Release 标 beta |
| 安全审计开大 | 又搞全库编制审计 | 只审暴露面清单 §4.1 |
| 版本号争论 | README/CHANGELOG/tag 不一致 | 冻结方案 A 或 B，一天内改完 |
| demo 依赖真 LLM | 预算/不稳定 | mock 数据 + UI 走查；视频后期补 |
| 资源超标 | 空载 >500MB | prod 模式测；关可选服务 |

---

## 8. 提交节奏（建议）

1. `docs: Phase5 详规 + INSTALL 骨架`  
2. `test: zero-deps matrix + channel ingress limits`  
3. `docs: README positioning + ZERO_DEPS`  
4. `chore: security review report + harden fixes`  
5. `chore: version 0.7.0 + CHANGELOG + manual`  
6. `release: win nsis smoke notes + demo trilogy`  

---

## 9. 验收清单（工程 + 公开）

### 5.1

- [ ] 零依赖启动与测试矩阵绿  
- [ ] INSTALL 双路径文档 + 至少源码路径计时记录  
- [ ] 资源基线报告  
- [ ] Win Electron/NSIS 冒烟或书面降级  

### 5.2

- [ ] 安全终审报告无未关闭 fail；security suite 绿  
- [ ] channel 入站 harden + 测  
- [ ] README 公开定位  
- [ ] demo 三连可跟做  
- [ ] CHANGELOG + TECHNICAL_MANUAL + 版本对齐  
- [ ] 公开 checklist 全勾  

### 5.3（发版后）

- [ ] packages 文档  
- [ ] 渠道策略声明（仍冻结）  
- [ ] backlog 标签就绪  

---

## 10. 一句话

> Phase 5 **不发明新 OS 能力**，只把已有 P1–4 收成：**默认装得动、暴露面敢公开、一句话说清、三件事能演示**。
