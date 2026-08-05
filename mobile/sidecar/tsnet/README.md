# Takton 内嵌 Tailscale（无感 mesh）

用户体验目标：**扫一次二维码即连接**，不装系统 Tailscale、不手填 IP。

## 架构

| 端 | 角色 | 行为 |
|----|------|------|
| PC | `takton-tsnet -role pc` | 进 tailnet，把本机后端反代到 100.x |
| 手机 | `takton-tsnet -role phone -client-only` | 用 QR 里的一次性入网 key 进同一 tailnet，再 claim PC |

由 **Takton Host（Rust）自动 spawn**，UI 不出现 Tailscale 字样（高级设置可看状态）。

## 一次配置（仅 PC）

管理员/用户在 PC **只做一次**：

```bash
export TAKTON_TS_AUTHKEY=tskey-auth-…   # Tailscale 控制台可复用 auth key
# 或在 App「连接 → 首次启用远程」粘贴
```

之后每次点「匹配手机」：

1. Host 自动拉起 PC 侧 tsnet  
2. QR（v3）写入 `lan` + `ts` + `tsk`（手机入网材料，TTL=配对窗口）  
3. 手机扫码 → 自动入网 → LAN 优先 claim，失败走 TS  

## 构建

```bash
cd sidecar/tsnet
go mod tidy
go build -o takton-tsnet .
# 放到 PATH 或设置 TAKTON_TSNET_BIN
```

## 安全

- `tsk` 仅在配对窗口（默认 300s）有效场景下使用；建议控制台 auth key 打 tag、可吊销  
- 手机 claim 成功后可丢弃内存中的 key（状态目录仍保留节点，便于重连）  
- Status HTTP 仅绑 loopback  

## 无公网 IP

PC 无公网、手机 5G 出网：两侧同 tailnet 即可。LAN 在家时自动优先内网。
