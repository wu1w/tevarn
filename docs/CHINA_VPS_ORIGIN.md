# 新加坡源站 + xray SNI 分流

## 架构

```text
用户浏览器
  ├─ http://tevarn.com      → :80  nginx 官网/安装包
  └─ https://tevarn.com     → :443 xray REALITY
                                └─ 非 VLESS 的 TLS（SNI=tevarn.com）
                                     → 127.0.0.1:8443 nginx(HTTPS)

VLESS 客户端（SNI=www.bing.com 等）→ :443 xray 正常代理
```

## 已部署

| 项 | 值 |
|----|-----|
| 源站 IP | `45.77.170.214`（新加坡 Vultr） |
| DNS | 灰云 A → `45.77.170.214`（`@` / `www` / `dl`） |
| 官网 | https://tevarn.com/ · http://tevarn.com/ |
| Setup | https://tevarn.com/downloads/Tevarn-Setup-0.4.0-x64.exe |
| APK | https://tevarn.com/downloads/Tevarn-Mobile-0.4.0-engine-fix.apk |
| 站点目录 | `/var/www/tevarn/` |
| 证书 | `/etc/letsencrypt/live/tevarn.com/`（自动续期） |

## 关键文件

- `/etc/xray/config.json` — `realitySettings.dest = 127.0.0.1:8443`，`serverNames` 含 `tevarn.com` / `www.tevarn.com` / `dl.tevarn.com` / `www.bing.com`
- `/etc/xray/config.json.bak.*` — 修改前备份
- `/etc/nginx/sites-available/tevarn.com.conf` — `:80` 公网 + `127.0.0.1:8443` SSL

## 应用中继

腾讯云 `150.158.109.231:8787` 的 tevarn-relay **独立**，与官网无关。
