# Changelog

本项目版本记录遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/) 与语义化版本。

## [0.3.2] - 2026-07-26

安全加固与跨平台沙箱大版本。含一次完整第三方安全审计（Claude）的全部处置结果：
**P0×6 / P1×4 / P2×4 全部清零**。

### Added

- **独立权限控制台 `/security`**：侧边栏盾牌入口
  - 命令执行模式二选一：沙箱模式 / 本地模式（实时生效）
  - 高危命令策略表：8 个分类（删除文件/提权执行/关机重启/磁盘操作/系统服务/
    远程脚本管道/数据外泄/写入系统目录）逐类三态控制——
    **放行**（直接执行）/ **每次确认**（弹窗，默认）/ **禁止**（硬拦截 `[Policy Blocked]`）
  - 访问与凭证：单用户模式开关（关闭需确认）+ 桥接令牌一键生成
  - 安全自检面板：6 项检查绿/黄/红分级 + 修复建议
- **跨平台真沙箱**（`agent_computer_backend` 默认 `auto`，按平台自动选最强隔离）：
  - macOS：`sandbox-exec`（seatbelt）——deny default / 读全系统保工具链 /
    写仅 workspace / 最小环境 / 默认断网；系统自带零依赖
  - Windows + WSL2：经 `wsl.exe` 转发 bwrap，完整 Linux 隔离语义
  - Windows 无 WSL2：Job Object 受限模式（进程树清理 / 防 fork 炸弹 / 内存限额）
  - 统一能力探测（完整/受限/无三级），启动自检与权限控制台按真实能力展示
- **实时终端面板**：chat 页 agent 工具调用以终端命令流形式实时展示
  （$ 命令 + ✓/✗ 结果行），取代原截图面板；header「终端」开关 + 未读提示
- **统一启动安全自检**：host 绑定 × 单用户模式组合 fail-hard 拒绝启动，
  其余项（bridge_token/沙箱/加密盐/弱密码）醒目告警
- **管理员初始密码随机化**：非 Electron 部署首启自动生成并写入
  `~/.takton/initial_admin_password`（0600），不再硬编码 `admin`

### Security

- **P0-1**：`single_user_mode` 增加 loopback 硬闸门——非本机直连来源一律 403
  （只信 socket 对端，不信任可伪造的 X-Forwarded-For）
- **P0-2**：`jwt_secret`/`api_key` 默认值改为首次启动随机生成并持久化
  `~/.takton/secrets.json`（0600）；validator 修正为比对真实已知弱值集合
  （原比对 `change-me` 与实际默认值不符，防线从未触发）
- **P0-3**：docker-compose 环境变量名全面修正（`TAKTON_JWT_SECRET` 等；
  另修复 `DATABASE_URL`/`QDRANT_URL`/`TAKTON_ENCRYPTION_SALT` 失效变量，
  移除后端未使用的 redis 服务）；`TAKTON_SECRET_KEY` 旧名兼容 + deprecation 告警
- **P0-4**：补齐 `backend/Dockerfile`（非 root）与 `frontend/Dockerfile`（多阶段）
- **P0-5**：危险命令黑名单补数据外泄类规则（文件上传/反弹连接/凭证读取/
  编码外发/远程传输）；evolution G2 内容检查与执行层共用高严重度子集
- **P0-6**：bridge 端点非 loopback 且未设 token 时启动告警
- **P2**：审计日志 details 落盘前递归打码（api_key/password/secret/token 等
  键名 → `[REDACTED]`）
- **P2**：MCP 商店安装详情展示来源链接与 `registry:package_id` 包标识
- **P2**：Electron 渲染进程开启 `sandbox: true`（OS 级沙箱）
- **依赖漏洞清零**：`python-jose`（弃维护）迁移至 PyJWT 2.13.0；
  python-multipart 0.0.31 / python-dotenv 1.2.2 / jinja2 3.1.6 升级；
  xlsx 换 SheetJS 官方 0.20.3（修 Prototype Pollution + ReDoS）。
  `pip-audit` 14 → 0，`npm audit` 1 高危 → 0

### Fixed

- 运行中切换页面导致会话被误删（`Session not found`）——三层防线：
  活跃会话查询 API + 侧边栏清理前置跳过 + 删除接口 409 保护
- 输入框偶发无法输入（消息菜单透明遮罩吞点击 + composer 焦点兜底）
- Electron `file://` 协议下相对 `/api` 图片地址 404 导致的「实时画面加载失败」
- Docker 部署路径不可用（变量名错误 + Dockerfile 缺失）现已端到端可用

### Changed

- 截图实时面板退役（agent 的 `desktop_screenshot` 工具保留用于视觉感知，
  不再向前端推送截图流）
- 设置页安全区块迁移至独立 `/security` 权限控制台

## [0.3.1] - 2026-07-25

### Added

- Windows 一键安装包发布（Setup.exe + portable zip + install.ps1 自动解析最新 Release）
- 对话底栏模型选择框视觉瘦身

### Fixed

- 根 package.json 版本号对齐

## [0.3.0] - 2026-07-24

### Added

- Agent Computer（Linux bwrap 沙箱执行后端）
- 本地 RAG 全家桶：Qdrant 向量库 + 本地 Embedding/Reranker（OpenAI 兼容服务）
- 会话误删防护初版与桌面端体验打磨
