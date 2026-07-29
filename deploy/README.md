# Takton 系统服务化部署（阶段 3）

Takton 后端可脱离桌面端，作为系统级服务常驻运行——这是 Agent OS
「从应用变成系统进程」的落地形态。

## Linux（systemd）

```bash
sudo cp deploy/takton-backend.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now takton-backend
systemctl status takton-backend
journalctl -u takton-backend -f   # 跟踪日志
```

unit 文件默认加固项：`NoNewPrivileges` / `ProtectSystem=full` /
`ProtectHome=read-only`（仅放行数据目录）/ `MemoryMax=4G` / 崩溃自动重启。
按部署实际路径调整 `User` / `WorkingDirectory` / `ExecStart`。

## Windows（服务 / 计划任务）

方式一：NSSM（推荐，成熟稳定）

```powershell
nssm install TaktonBackend "C:\path\to\.venv\Scripts\python.exe" "-m uvicorn backend.main:app --host 127.0.0.1 --port 8090"
# 或: python -m backend.runtime --port 8090
nssm set TaktonBackend AppDirectory "C:\path\to\takton"
nssm start TaktonBackend
```

方式二：任务计划程序（免第三方工具）——登录时触发，运行
`python -m backend.runtime --port 8090`（或 uvicorn `backend.main:app` 同端口）。

## 服务化后的访问形态

- 前端：`backend/static` 已内嵌，浏览器直开 `http://127.0.0.1:8090`
- 桌面端（Electron）：连接本机 Kernel Host（默认 8090；设置页可改）
- 多设备：同局域网设备访问主机 **8090** 端口（建议仅绑内网网卡 + 关闭 single_user_mode 并设密码）

## 与 Agent Kernel 的关系

服务常驻后，`get_kernel()` 单例即全系统唯一的 Kernel 实例——
所有会话、Cron、工作流、桌面端连接产生的 agent 进程在同一棵进程树下，
Security Console（`/security` 页）可统一观测与审计。
