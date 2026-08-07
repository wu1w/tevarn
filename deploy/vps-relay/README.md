# Takton VPS 中继（一键包）

把家里的 PC 后端通过**出站隧道**挂到你的公网 VPS，手机扫码即可在 5G 连回家。  
**不需要**给家里做端口映射；PC 只出站。

## 3 步上手

### ① 在 VPS 上一键安装（Ubuntu 22.04+）

```bash
# 把本目录拷到 VPS 后：
sudo bash install.sh
```

安装结束会打印：

```text
VPS 地址 (Host):  x.x.x.x
端口 (Port):      80
访问令牌 (Token): tr_live_…
```

**云厂商安全组**请放行 TCP `80`（或你设的 `RELAY_PUBLIC_PORT`）。

### ② 在 PC Takton 填写

打开 **设置 → 远程连接 → 自有 VPS 中继**：

1. 粘贴 Host + Token  
2. **检测连通**  
3. **启用中继**（状态应显示「隧道在线」）

### ③ 匹配手机

点 **匹配手机 · 生成二维码**，手机 App「连接」页扫码。  
同 Wi‑Fi 走局域网；外出自动走 VPS。

---

## 目录结构

```text
deploy/vps-relay/
  install.sh           # 一键安装
  docker-compose.yml
  .env.example
  relay/               # takton-relay 服务
  scripts/gen-token.sh
  README.md
```

## 端口

| 端口 | 用途 |
|------|------|
| 80（可改） | 公网 HTTP → 中继 → PC `:8090` |
| 控制面 | 容器内；PC 用 WSS 出站登记隧道 |

## API（运维）

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/relay/v1/health` | 存活 |
| POST | `/relay/v1/register` | PC 登记（Bearer Token） |
| GET | `/relay/v1/tunnels/{id}/status` | 隧道是否在线 |
| * | `/t/{tunnel_id}/*` | 反代到对应 PC |
| * | `/*` | 单隧道时默认转发 |

## 安全

- `RELAY_TOKEN` 长期密钥，**不要**写进二维码  
- 二维码里的 `vpt` 是 **HMAC 短时票**（`exp.sig`，密钥=RELAY_TOKEN，TTL≈配对窗 300s）  
- 公网 `/t/{id}` 默认要求：`Authorization: Bearer …`（配对后 JWT）**或** `X-Takton-Vpt` / `?vpt=`  
  - 可用 `RELAY_REQUIRE_EDGE_AUTH=0` 关闭（仅可信内网实验）  
- PC 隧道会注入 `x-takton-relay: 1`，**禁止** single_user 免登录把公网流量当本机 admin  
- 生产建议域名 + HTTPS（前面再挂 Caddy/nginx 即可）  
- 轮换 Token：改 `.env` 后 `docker compose up -d`，并更新 PC「远程连接」令牌  
- **不要**把含真实 Token/密码的 `relay-deploy-info.txt` 提交进仓库或打进 release

## 卸载

```bash
cd /opt/takton-vps-relay
docker compose down
# 可选: rm -rf /opt/takton-vps-relay
```
