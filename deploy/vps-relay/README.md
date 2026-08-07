# Takton VPS 中继 — 一行命令安装（Ubuntu）

把家里的 PC 后端通过**出站隧道**挂到公网 VPS，手机扫码即可在 5G 连回家。  
**不需要**给家里做端口映射；PC 只出站。

## 一行命令（推荐）

在 **Ubuntu 22.04+** VPS 上以 root 执行：

```bash
curl -fsSL https://github.com/wu1w/takton/releases/download/v0.5.7-alpha/install-vps-relay.sh | sudo bash
```

装完会**自动打印**（并写入 `/opt/takton-vps-relay/INSTALL_INFO.txt`）：

```text
IP / Host :  x.x.x.x
端口 Port :  80
令牌 Token:  tr_live_…
```

再次查看：

```bash
sudo cat /opt/takton-vps-relay/INSTALL_INFO.txt
```

**云厂商安全组**请放行 TCP `80`（或你自定义的端口）。

### 可选参数

```bash
# 改端口
curl -fsSL https://github.com/wu1w/takton/releases/download/v0.5.7-alpha/install-vps-relay.sh \
  | sudo env RELAY_PUBLIC_PORT=8080 bash

# 固定令牌（重装时复用）
curl -fsSL https://github.com/wu1w/takton/releases/download/v0.5.7-alpha/install-vps-relay.sh \
  | sudo env RELAY_TOKEN='tr_live_your_token_here' bash
```

脚本会自动：安装 Docker（若无）→ 下载中继包 → 生成令牌 → `docker compose up` → 打印 IP/端口/令牌。

> 源码分支同步（开发用）：  
> `curl -fsSL https://raw.githubusercontent.com/wu1w/takton/feature/agent-kernel/deploy/vps-relay/install.sh | sudo bash`

---

## 装好后：PC + 手机

### ① PC Takton

**设置 → 远程连接 → 自有 VPS 中继**

1. 粘贴 Host + Port + Token  
2. **检测连通**  
3. **启用中继**（状态应显示隧道在线）

### ② 匹配手机

点 **匹配手机 · 生成二维码**，手机 App「连接」页扫码。  
同 Wi‑Fi 走局域网；外出自动走 VPS。

---

## 目录与运维

| 路径 | 说明 |
|------|------|
| `/opt/takton-vps-relay` | 安装目录 |
| `/opt/takton-vps-relay/.env` | Token / 端口（权限 600） |
| `/opt/takton-vps-relay/INSTALL_INFO.txt` | 安装结果摘要 |

```bash
cd /opt/takton-vps-relay
docker compose logs -f          # 日志
docker compose ps               # 状态
docker compose down             # 停止
docker compose up -d --build    # 重建启动
```

## 端口

| 端口 | 用途 |
|------|------|
| 80（可改 `RELAY_PUBLIC_PORT`） | 公网 HTTP → 中继 → PC `:8090` |
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
- 二维码里的 `vpt` 是 **HMAC 短时票**（密钥=`RELAY_TOKEN`）  
- 公网 `/t/{id}` 默认要求 Bearer JWT 或 `X-Takton-Vpt`  
- PC 隧道注入 `x-takton-relay`，禁止 single_user 免登录把公网当本机 admin  
- 生产建议域名 + HTTPS（前面挂 Caddy/nginx）  
- 轮换 Token：改 `.env` 后 `docker compose up -d`，并更新 PC 远程连接  

## 卸载

```bash
cd /opt/takton-vps-relay
docker compose down
# 可选: rm -rf /opt/takton-vps-relay
```

## 离线 / 本地目录安装

若已 clone 仓库或解压了 release 中的 `takton-vps-relay-*.zip`：

```bash
cd deploy/vps-relay   # 或解压后的目录
sudo bash install.sh
```
