# 加深轮：把「未做满」补齐

**日期**：2026-07-31  
**状态**：✅ 已落地  

对应上一轮诚实边界：

| 缺口 | 本轮交付 |
|------|----------|
| Linux cgroup / RSS | `backend/kernel/resource_os.py` + Rust `resource_report_rss` + 命令后采样 |
| 前端观测 / collab | Kernel 页 **仪表盘 / 协作** tab；API 已接 |
| Electron host 捆绑 | `package.json` extraResources → `vendor/takton-kernel-host`；便携脚本同步 vendor |
| 远程包一键装 | `POST /packages/market/install-remote` + 市场页 **本地/远程包** tab |
| Court 加深生效 | 重建 host（`resource_report_rss` + secret globs） |

---

## 配置

```text
agent_resource_cgroup_enabled = false   # Linux 上可 true
agent_resource_rss_sample = true
agent_package_market_url = ""           # https catalog
```

## 构建 host（不杀进程）

```powershell
cargo build -p takton-kernel-host --release
# 仅当没有在用 debug 可执行文件时：
.\scripts\package_portable_kernel.ps1 -HostBin target\release\takton-kernel-host.exe
```

若 `target\debug\takton-kernel-host.exe` 正被占用，脚本会复制失败并警告——**不要强杀**，改用 release 输出路径或换文件名。

## 测试

```text
pytest backend/tests/kernel/test_resource_os_deepen.py backend/tests/kernel/test_next_round_10.py -q
cargo test -p takton-kernel resource -- --test-threads=1
```
