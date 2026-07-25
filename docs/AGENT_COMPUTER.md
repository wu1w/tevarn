# Agent Computer（Phase 0.5.3）

命令/python 工具的隔离执行后端。默认**关闭**（`agent_computer_enabled=false`，现状不变）。

## 设置项

| 设置 | 默认 | 说明 |
|---|---|---|
| `agent_computer_enabled` | `false` | 总开关。开启后前台 `command`/`python` 走 ExecutionBackend |
| `agent_computer_backend` | `bwrap` | `bwrap`=Linux 沙箱（需 bubblewrap）；`local`=现状直跑 |
| `agent_computer_network` | `false` | 沙箱内是否放开网络（默认 `--unshare-net` 断网） |

后台命令（`background=true` / 自动后台）暂走既有 process_registry，不进沙箱（后续阶段）。

## 隔离模型（bwrap）

- **workspace 根 rw**：项目目录内随便读写（协作语义）
- **per-agent HOME**：`workspace/.computers/<agent_key>/home`，主 Agent（`main`）与各子代理**互不干扰**（A 写的 `$HOME` 文件 B 不可见；workspace 本身共享）
- **只读系统**：`/usr /bin /sbin /lib /lib64 /etc /opt` ro-bind → 写 `/etc`、改系统配置必然失败
- **宿主 HOME 不绑定**：`~/.ssh`、`~/.aws` 等凭证天然不可见
- **最小环境**：`--clearenv`，仅 PATH/HOME/LANG/TERM
- **生命周期**：`--die-with-parent --new-session`
- **启动失败不降级**：bwrap 缺失/userns 不可用时返回清晰错误，**不静默回退本地直跑**

## 敢装清单（沙箱内允许）

- 语言工具链：python/pip（user site 落在沙箱 HOME）、node/npm、git
- 项目内构建与测试：pytest、npm build、make（产物在 workspace）
- 常规 CLI：curl（需开 network）、jq、ripgrep

## 禁止/必然失败（攻击回归 `test_agent_computer.py` 锁定）

- 写 `/etc`（Permission denied）
- 读宿主 `~/.ssh` 等凭证（路径不可见）
- 默认外连（Network is unreachable；`agent_computer_network=true` 才放开）
- `cwd` 越出 workspace（清晰报错）

## 前端

专业模式右侧 Dock：每个 agent 一个终端 tab（主 Agent 沿用 `Agent` tab，子代理懒创建独立 tab），
`computer.exec` 事件实时写入，tab 切换/新行有过渡动画（framer-motion）。
