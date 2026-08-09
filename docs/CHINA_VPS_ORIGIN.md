# 新加坡源站（推荐，无需备案）

## 已部署

| 项 | 值 |
|----|-----|
| 源站 IP | `45.77.170.214`（新加坡 Vultr） |
| 官网 HTTP | http://45.77.170.214/ |
| Setup | http://45.77.170.214/downloads/Tevarn-Setup-0.4.0-x64.exe |
| APK | http://45.77.170.214/downloads/Tevarn-Mobile-0.4.0-engine-fix.apk |
| 目录 | `/var/www/tevarn/` |
| nginx | 监听 **80**（443 被本机 xray 占用，未动） |

境外机房 **不需要 ICP**。大陆访问新加坡 HTTP 一般比 Cloudflare 稳。

## 必须你改的 DNS（当前 API Token 无 Zone DNS 权限）

Cloudflare → tevarn.com → DNS → 记录改为 **仅 DNS（灰云）**：

| 类型 | 名称 | 内容 | 代理 |
|------|------|------|------|
| A | `@` | `45.77.170.214` | **仅 DNS** |
| A | `www` | `45.77.170.214` | **仅 DNS** |
| A | `dl` | `45.77.170.214` | **仅 DNS** |

改完后：
- http://tevarn.com/ → 新加坡官网
- http://tevarn.com/downloads/... → 安装包

## HTTPS 说明

本机 **443 被 xray REALITY 占用**。DNS 改完后可二选一：

1. **HTTP 先用**（下载安装包够用）
2. 再改 xray fallback / 或把 xray 换端口，给 nginx 让出 443 做 certbot

## 应用中继

腾讯云 `150.158.109.231:8787` 的 tevarn-relay **未改**，与官网源站无关。
