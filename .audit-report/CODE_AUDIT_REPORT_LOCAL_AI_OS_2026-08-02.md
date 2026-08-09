# Tevarn Alpha 代码审计报告（本地个人 AIOS 威胁模型）

> **历史审计报告（Historical）**
> 本文件记录 **2026-08-02** 对提交 **`a9f8912`** 的审计与当轮修复结论。
> 当前开发 HEAD 已前进（例如 **`92ff48a`** 及之后），**不能**将本报告等同于「最新提交已全部复审」。
> 发布前请按报告中的回归清单（权限确认、远程包、预览、Kernel ABI、工单、配置、迁移）在目标提交上重跑验证。

- 审计日期：2026-08-02
- 分支：`feature/agent-kernel`
- **本报告覆盖的本地提交：`a9f8912`（非当前 HEAD）**
- 产品边界：本地优先、单用户、个人 AIOS 工作站
- 主要对手：恶意提示或工具输出、恶意/被篡改的 skill 包、不可信文档与网页、配置误操作、运行时故障
- 明确非范围：多租户 SaaS、互不信任的本机账号、已取得本机用户权限的攻击者、物理接触攻击

## 0. 修复执行结果（2026-08-02）

本报告发现的 12 组问题已经完成代码处置；其中 Electron 桌面形态按“本地个人 AIOS”边界实现了原生确认，普通浏览器开发模式仍保留页面确认作为兼容路径。

| 原编号 | 处置结果 | 关键改动 |
|---|---|---|
| P1-01 | 已修复 | 非法工作模式固定回退 `cautious`，不再回退到产品推荐默认值 `auto_edit`。 |
| P1-02 | 已修复 | 远程 package 默认强制要求命中的 SHA-256；URL 安装接口可显式提交预期 hash。 |
| P1-03 | 已修复 | Electron 锁定应用 origin、校验高权限 IPC sender、限制外链 scheme；DOCX 改用 DOMPurify 并拦截危险链接。 |
| P1-04 | Electron 已修复 | renderer 请求体不能自证授权；Electron 主进程显示原生确认框并用持久化的进程间 secret 直接向后端授权。启用该 secret 时，renderer 直接调用授权 API 会收到 403。 |
| P1-05 | 已修复 | Rust 保留嵌套 `stats`，同时恢复 ABI v1 顶层计数字段，兼容现有 Python/UI 消费者。 |
| P1-06 | 已修复（fail-closed） | 编辑器不再公布 Loop；旧工作流遇到 Loop 明确失败并给出迁移提示，不再只处理第一项后伪装成功。 |
| P2-01 | 已修复 | URL 安装统一复用逐跳复核地址的安全下载器，并在工作线程中执行有界流式下载。 |
| P2-02 | 已修复 | ZIP 增加条目数、单文件、总展开大小和压缩比预算。 |
| P2-03 | 已修复 | 知识导入增加 16 MiB 业务上限、表单限制和 `max+1` 有界读取。 |
| P2-04 | 已修复 | 超过 bcrypt 72 UTF-8 字节的密码显式拒绝，注册、登录、改密语义一致。 |
| P3-01 | 已修复门禁错误 | 清除 Ruff/ESLint error，恢复生产 TypeScript 门禁，修好 Electron 编译脚本并统一版本为 `0.5.3-alpha`。 |
| P3-02 | 已修复 CI 隔离 | backend CI 固定 Python court；Rust ABI/host 测试继续由独立 kernel job 用独立生命周期执行。 |

另外，回归验证又发现“缺失 `_run_origin` 被当成主聊天静默放行”。现已要求主聊天来源必须显式标记；未知来源按无人值守策略处理，自声明需要确认的第三方工具在无真实确认通道时默认拒绝。

### 前端实机体验审计与修复

本机 `3000` 端口属于另一份同名仓库，未将其页面结果计入本项目。本项目临时运行在 `3001`，以 1280×720 和 Electron 最小窗口 960×640 检查了工作台、聊天、员工、审批、内核和设置，并覆盖亮/暗主题。

- 修复中文界面误显示英文“联系员工”提示的反向语言判断。
- 为聊天输入框补充可访问名称。
- 提高亮/暗主题次要文字与 placeholder 对比度；将桌面宽度下 9–10.5px 的关键辅助文字提升到 11–12px。
- 修复开发构建根目录和旧 manifest 缓存问题，迁移 Next 16 的 `middleware` 到 `proxy`。
- 主要页面在 960×640 下均无横向溢出，亮/暗主题变量均按新值生效。

### 本轮验证

- Rust：`cargo test -q`，90 个单元测试 + 4 个 ABI 测试全部通过。
- Python：package/security 相关 55 个测试通过；working-mode 与桌面权限边界 26 个测试通过；新增 Loop/版本测试通过（CHANGELOG 可选项 1 个跳过）。
- Python 静态检查：本轮修改文件 Ruff 通过。
- 前端：ESLint error 为 0，`tsc --noEmit` 通过，Electron TypeScript 构建通过，Next 生产构建（32 个页面）通过。
- 尚存非阻断债务：全量 ESLint 仍有 126 条 warning，主要是历史 `any` 与 React effect 建议；pytest 仍报告 Starlette/httpx 和 Pydantic v2 弃用警告。

## 1. 校准说明

项目 `README.md` 明确写明 Tevarn 完全运行在用户本机，是从 Agent 工作站演进中的 Personal Agent OS；`docs/THREAT_MODEL.md` 又进一步把范围限定为“本地优先、单用户工作站上的 Agent 控制平面”，并把多租户 SaaS 列为非范围。默认配置也与此一致：

- `backend/core/config.py:590`：后端默认绑定 `127.0.0.1`。
- `backend/core/config.py:605`：`single_user_mode=True`。
- `docs/THREAT_MODEL.md`：本机 UI/进程属于信任边界内；主要防范的是恶意提示、工具输出、skill 包与错误配置越过治理内核。

因此，以下行为在默认产品语义下属于正常能力，不应被单独定性为漏洞：

- 当前本机用户浏览自己的项目文件，包括 `.env`。
- 当前本机用户控制自己的桌面。
- 当前本机用户安装、卸载和激活全局 skill/package。
- 单用户模式下不区分 session、Cluster、workflow 的不同账号 owner。

上一版报告以多用户服务为假设，将这些能力错误地评为越权问题。本修订版撤销这一结论，只在“以后显式支持多用户/远程部署”章节保留条件性提醒。

## 2. 修订后的结论

在正确威胁模型下，没有确认到需要按“远程账号接管”描述的 P0 问题。当前最重要的问题变为：治理路径是否 fail-closed、不可信内容能否绕过用户确认、远程 package 是否真的有可信来源、Rust/Python ABI 是否一致，以及工作流是否按界面承诺执行。

本次保留 12 组与本地个人 AIOS 定位直接相关的问题：

| 等级 | 数量 | 说明 |
|---|---:|---|
| P1 / 高 | 6 | 可能破坏权限法院、供应链信任或核心运行正确性 |
| P2 / 中 | 4 | 可导致资源耗尽、认证语义异常或不稳定 |
| P3 / 工程质量 | 2 | 会削弱发布门禁和长期可维护性 |

## 3. P1 高优先级问题

### P1-01 非法工作模式会 fail-open 到 `auto_edit`

**证据**

- `backend/agent/working_mode.py:136` 的契约是“非法值回落到默认（谨慎），绝不静默放宽”。
- `backend/agent/working_mode.py:140` 实际使用 `DEFAULT_WORKING_MODE`，而该常量在 `:29` 是 `auto_edit`。
- `backend/tests/test_working_mode.py:113-118` 已经明确期望非法值回到 `cautious`，当前测试失败。

**为什么符合本项目威胁模型**

配置错误是威胁模型中的明确攻击者画像。环境变量拼写、旧数据库值或迁移错误会让本应谨慎的模式静默获得工作区写权限，直接违反治理内核的 fail-closed 原则。

**建议**

非法值固定回退 `cautious`，记录安全告警；产品推荐默认仍可保持 `auto_edit`，但“正常默认”和“解析失败兜底”必须分开。

### P1-02 远程 package 默认不要求可信 hash，下载后由本机重新签名

**证据**

- `backend/core/config.py:218-221`：可信 hash 列表默认空，`agent_package_require_content_hash=False`。
- `backend/packages/market.py:365-404`：当可信列表为空且未强制 hash 时，任何下载内容都会 `ok=True`，只返回 warning。
- `backend/packages/market.py:102-126`：下载后的内容由本机调用 `pkg_sign` 签名，再送入 kernel 安装。

**问题本质**

本机 HMAC 签名只能证明“这份内容经过当前 Tevarn 实例”，不能证明发布者身份或下载过程中的来源完整性。如果没有预先固定的 hash/发布者签名，恶意或被劫持的包在下载后会获得本机签名。扫描和 quarantine 可以降低风险，但不能替代来源认证。

**建议**

远程安装默认要求 catalog 提供并命中 SHA-256，或引入发布者公钥签名；本机签名只作为安装后完整性标记，不应被表述为供应链信任根。UI 应把“未固定来源”作为明确确认项，而不只是状态 warning。

### P1-03 Electron 对不可信内容的导航与 preload IPC 隔离不足

**证据**

- `electron/main.ts:1062-1087`：主窗口加载 preload，但没有发现 `will-navigate`/`will-redirect` 的主框架来源限制。
- `electron/main.ts:1179-1182`：新窗口 URL 直接交给 `shell.openExternal`，没有 scheme allowlist。
- `electron/preload.ts`：页面可调用 `openPath`、`openTevarnCode`、`openExternal` 等本机能力。
- `electron/main.ts:1200-1210,1263-1388`：IPC handler 没有统一校验 `event.senderFrame.url`。
- `frontend/components/chat/FilePreviewHost.tsx:417-421` 使用 `dangerouslySetInnerHTML` 展示 DOCX HTML；`frontend/lib/filePreviewLoaders.ts:189-197` 使用正则净化，不是解析器白名单。

**为什么仍然相关**

这里的攻击者不是“另一个登录用户”，而是威胁模型中的不可信文档、网页或工具输出。如果恶意内容让主窗口进入非应用 origin，preload 能力可能把内容层问题升级为本机操作问题。

**建议**

主窗口只允许精确应用 origin；外链必须在独立、无 preload 的窗口或系统浏览器打开；IPC handler 验证 frame origin；scheme 只允许必要的 `https/http`；DOCX 使用成熟 HTML sanitizer 和链接拦截。

### P1-04 桌面权限由渲染器参数声明，缺少不可伪造的确认凭据

**证据**

- `backend/api/routes/desktop.py:110-147` 接受客户端提供的 `permission`。
- `backend/services/desktop/__init__.py:195-214`：只要请求的 permission 不是 `ASK`，就记录权限并执行。
- `backend/api/routes/desktop.py:192-224`：渲染器还能直接持久化 `always_allow`。

**问题本质**

在单用户产品中，用户当然有权控制自己的桌面；缺陷在于后端把“渲染器说用户已同意”当成了“用户确实同意”。一旦渲染器受到恶意文档/XSS/工具输出影响，权限确认可被同一渲染器伪造，绕过 Permission Court 的交互意图。

**建议**

确认动作由受信主进程或独立本机确认窗口产生一次性票据，票据绑定 operation、参数摘要、过期时间和 nonce；执行 API 只接受有效票据，不能接受语义型 `allow_once` 字符串作为证明。

### P1-05 Rust 调度统计 ABI 与 Python/Golden 契约不一致

**证据**

- `crates/tevarn-kernel/src/kernel.rs:1603-1605` 返回 `scheduler.status()`，计数位于嵌套 `stats`。
- `crates/tevarn-kernel/tests/abi_v1.rs:106-107` 读取顶层 `done`。
- `backend/tests/kernel/test_abi_rust.py:150-151` 和 `test_p0c_scheduler_resources.py:59-60` 也读取顶层统计字段。
- Rust workspace 的 `golden_escalation_and_scheduler` 可稳定复现失败；其余 90 个单元测试通过。

**影响**

完成任务可能被 UI、Python 控制层或容量逻辑读取为 0，影响状态展示、调度判断与可观测性。这是实际 ABI bug，不是威胁模型争议。

**建议**

确定 ABI v1 的唯一 JSON schema，并让 Rust/Python 从同一 schema 或 golden fixture 验证；兼容现有消费者时恢复顶层字段，必要时同时保留嵌套结构并标记弃用周期。

### P1-06 Workflow Loop 节点承诺逐项循环，实际上只执行第一项

**证据**

- `backend/schemas/workflow_node.py:397-408` 的公开语义是对列表每个元素执行循环体。
- `backend/services/workflow_engine.py:1134-1157` 明确不会重复调度子图，只把第一项作为 `item` 返回，并让流程继续。

**影响**

用户建立批处理工作流时会得到表面成功、实际只处理第一项的结果，造成静默遗漏。这属于核心产品逻辑错误。

**建议**

真正实现子图迭代前，UI/API 禁用 Loop 或在验证阶段直接报错；不要仅写 warning 后继续成功。完整实现需覆盖迭代上限、预算、取消、失败策略和聚合输出。

## 4. P2 中优先级问题

### P2-01 包上传 URL 入口存在重定向 SSRF 与先读后限额

`backend/api/routes/packages.py:203-234` 只验证初始 URL；请求默认跟随重定向，最终地址未复核，并在 64 MB 判断前 `await resp.read()`。即使只有本机用户，这仍可能被恶意 catalog/链接用来访问 `127.0.0.1` 上的其他服务、路由器管理页或云环境元数据，并造成内存峰值。

项目在 `backend/packages/market.py:302-320` 已有逐跳复核的安全下载器，应统一复用并采用流式限额。

### P2-02 ZIP 安装没有展开资源上限

`backend/packages/publisher.py:113-147,164-175` 检查了路径和符号链接，但没有限制条目数、单项/总展开大小、压缩比或 inode 数量。恶意 skill 包可以耗尽个人工作站磁盘和 CPU。建议边解压边计数，并在独立临时目录和配额内完成验证后原子安装。

### P2-03 知识库导入会完整读入请求并放大后台工作量

`backend/api/routes/knowledge.py:60-133` 对 multipart 使用 `request.form()`/`uploaded.read()`，对 JSON 使用 `request.body()`，缺少业务层大小、文件数和队列上限。不可信大文档可能造成 UI/后端卡死、内存放大和向量化任务堆积。应使用流式读取、总字节上限和有界后台队列。

### P2-04 bcrypt 密码被静默截断到 72 字节

`backend/core/security.py:20-25,85-92` 在哈希与验证前截断 UTF-8 字节；schema 却允许 128 字符。两个前 72 字节相同的密码会被视为等价，多字节密码更容易触发。即使默认单用户，这也是认证语义 bug。建议迁移到 Argon2id/bcrypt-sha256，或至少按 UTF-8 字节数显式拒绝超限输入。

## 5. P3 工程与发布质量

### P3-01 静态检查和版本门禁未收敛

- Ruff：28 个错误。`backend/agent/grant_store.py:58,70` 的 `current_time` 重复键导致前一个映射永远失效，说明权限映射存在残留冲突。
- ESLint：5 个错误、126 个警告；包含 React memo 依赖不一致及 3 处禁用的 `require()`。
- `frontend/next.config.ts` 设置 `typescript.ignoreBuildErrors: true`，生产构建跳过类型门禁；独立 `tsc --noEmit` 当前通过，但配置会掩盖未来回归。
- 版本文件同时存在 `0.5.1-alpha` 和 `0.5.0-alpha`。

建议清零 error、移除忽略类型错误、建立单一版本源，并把 Ruff/ESLint/tsc/版本同步作为必须通过的 CI。

### P3-02 后端完整测试容易被外部 Rust Host 状态污染

默认 `agent_kernel_backend="rust"`，而后端 CI 完整测试 job 没有始终构建并启动隔离 host，也没有明确切到 Python backend。本次本地完整测试出现大量 `RPC auth required`，来自已经运行且密钥不匹配的 host；这使真实回归被环境噪声淹没。

建议把测试拆为：强制 Python backend 的后端单元/服务测试，以及使用独立端口、临时密钥、明确生命周期的 Rust 集成测试。

## 6. 不再作为当前漏洞的问题

下列项目从主报告撤销，不计入当前 12 组缺陷：

| 原问题 | 修订结论 |
|---|---|
| 登录用户读取 `.env`/项目文件 | 默认只有受信本机用户，属于个人工作站文件浏览能力；只有结合渲染器失陷或未来远程部署时才成为边界问题 |
| 登录用户控制桌面 | 产品核心能力；真正问题是确认凭据可由渲染器自证，已在 P1-04 重述 |
| session package / Cluster 跨用户 IDOR | 单用户威胁模型不成立；若以后做多用户才需要 owner 隔离 |
| 普通用户管理全局 package | 单用户工作站中“普通用户”就是机器所有者；供应链可信来源才是当前问题 |
| 首用户超级管理员竞争 | 默认本机初始化路径下不是主要风险；仅在显式开放远程注册时成立 |
| `/uploads` 无鉴权静态读取 | 默认 loopback 单用户下优先级低；如支持远程访问或分享链接再建立访问控制 |

## 7. 推荐修复顺序

### 第一批：治理正确性

1. 非法 working mode 回退 `cautious`。
2. 修复 Rust scheduler ABI。
3. Loop 节点在未实现前直接 fail closed。
4. 桌面确认改为主进程签发的一次性票据。

### 第二批：不可信内容与供应链

1. Electron 锁定应用 origin、IPC sender 和外链 scheme。
2. 远程 package 默认要求可信 hash/发布者签名。
3. URL 下载统一逐跳 SSRF 校验并流式限额。
4. ZIP 和知识导入加入资源预算。

### 第三批：工程门禁

1. 清零 Ruff/ESLint error，恢复构建类型检查。
2. 统一版本源。
3. 隔离 Rust Host 测试生命周期。
4. 修复密码超长语义并制定迁移方案。

## 8. 最终结论

Tevarn 的正确审计重点不是阻止机器所有者使用自己的文件、桌面和 package，而是确保 Agent、恶意内容和第三方 skill 不能借这些能力越过用户设置的 Permission Court。按这一标准，项目目前真正需要优先修复的是：配置解析 fail-open、渲染器确认不具备不可伪造性、远程包来源信任不足、Electron 内容边界、Rust ABI 和 Workflow Loop 的静默错误。

在默认 `127.0.0.1 + single_user_mode=True` 的个人工作站形态下，上述问题以治理完整性、供应链与可靠性为主，而不是传统多租户越权。若未来新增局域网、远程访问或家庭多用户模式，应再单独启用多用户威胁模型，不应把那套结论倒灌到当前产品定位。
