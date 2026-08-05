# 无感互联（扫一码即连）

## 用户路径

1. **PC 首次（仅一次）**  
   连接页 →「首次启用外出连接」→ 粘贴 `tskey-auth-…` → 启用。  
   或部署时设置环境变量 `TAKTON_TS_AUTHKEY`。

2. **日常配对**  
   PC 点「匹配手机 · 生成二维码」。  
   手机连接页扫码（或粘贴）→ 自动完成。

3. **使用**  
   - 在家同一 Wi‑Fi：自动走局域网  
   - 出门 5G / 公司网：自动走内嵌安全通道  
   - 无需公网 IP、无需装系统 Tailscale、无需手填 IP

## 技术要点

| 组件 | 作用 |
|------|------|
| `takton-tsnet` | 官方 tsnet userspace，PC 反代 / 手机 client-only |
| `TsnetEmbed` (Rust) | 进程生命周期、密钥 0600 落盘、状态探测 |
| QR v3 `tsk` | 配对窗口内把入网材料带给手机，扫码即 join |
| `PathService` | LAN 优先，失败切 TS，网络切换自动重解析 |

## 安全

- `tsk` 只在配对 TTL（300s）场景使用；成功后 Flutter 落盘会 redact  
- Auth key 文件权限 0600，API 只回 masked  
- Status HTTP 仅 loopback  

## 构建 sidecar

```bash
cd sidecar/tsnet && go mod tidy && go build -o takton-tsnet .
export TAKTON_TSNET_BIN=$PWD/takton-tsnet
```
