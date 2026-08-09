# 大陆访问 tevarn.com → 腾讯云 VPS 源站

## 已就绪（VPS 侧）

| 项 | 值 |
|----|-----|
| VPS | `150.158.109.231`（腾讯云） |
| 官网 + 安装包 | `http://150.158.109.231:7000/` |
| 站点目录 | `/var/www/tevarn/` |
| 安装包目录 | `/var/www/tevarn/downloads/` |
| nginx | `sites-enabled/tevarn.com.conf`（`:7000` + `:80` server_name） |

本机已验证：

```text
http://150.158.109.231:7000/                                          → 200 官网
http://150.158.109.231:7000/downloads/Tevarn-Setup-0.4.0-x64.exe     → 200
http://150.158.109.231:7000/downloads/Tevarn-Mobile-0.4.0-engine-fix.apk → 200
```

应用中继 `tevarn-relay` 在 **8787**，**不占用 7000**。

---

## 关键一步：Cloudflare DNS 指到 VPS（必须你点一下）

当前 API Token **不能改 Zone DNS**（403），需要你在 Cloudflare 控制台操作，否则 `tevarn.com` 仍解析到 Cloudflare，大陆依旧难打开。

### 操作（约 2 分钟）

1. 打开 https://dash.cloudflare.com → 域名 **tevarn.com** → **DNS** → **Records**
2. 删除/改掉指向 Cloudflare 代理的 `tevarn.com` / `www` / `dl` 旧记录（橙云 A/AAAA/CNAME）
3. 新增（**代理状态必须是「仅 DNS / 灰云」**，不要点成橙云）：

| 类型 | 名称 | 内容 | 代理 |
|------|------|------|------|
| A | `@` | `150.158.109.231` | **仅 DNS** |
| A | `www` | `150.158.109.231` | **仅 DNS** |
| A | `dl` | `150.158.109.231` | **仅 DNS** |

4. 保存后等 1–5 分钟，验证：

```bash
nslookup tevarn.com 1.1.1.1
# 应返回 150.158.109.231

curl -I http://tevarn.com:7000/
curl -I http://tevarn.com/          # 走 80
```

### 为什么必须灰云？

橙云 = 流量仍进 Cloudflare，大陆打不开的问题还在。  
灰云 = 用户直连你的腾讯云，才是「自动到 VPS」。

---

## 访问方式说明

| URL | 说明 |
|-----|------|
| `http://150.158.109.231:7000/` | 现在就能用（不依赖 DNS） |
| `http://tevarn.com:7000/` | DNS 改完后；**7000 防火墙已开时最稳** |
| `http://tevarn.com/` | DNS 改完后走 80；若遇「未备案」拦截需备案或只用 :7000 |
| `https://tevarn.com/` | 需在 VPS 上再签证书（DNS 指过来后 `certbot`） |

> 未备案域名解析到**境内**服务器时，运营商可能拦截 **80/443**。  
> **7000** 等非标端口通常仍可用。若你希望纯 `https://tevarn.com` 无端口，需要 **ICP 备案** 或把源站放到香港等境外机。

---

## HTTPS（DNS 改完后可选）

SSH 上 VPS：

```bash
sudo apt-get install -y certbot python3-certbot-nginx
sudo certbot --nginx -d tevarn.com -d www.tevarn.com -d dl.tevarn.com
```

证书成功后可在 `tevarn.com.conf` 增加 443 server（certbot 一般会自动改）。

---

## 更新官网/安装包

```bash
# 同步页面
rsync -a website/ ubuntu@150.158.109.231:/var/www/tevarn/

# 换安装包
scp Tevarn-Setup-*.exe Tevarn-Mobile-*.apk ubuntu@150.158.109.231:/var/www/tevarn/downloads/
```

页面会自动用同源 `/downloads/...` 链接（见 `website/index.html` 的 `isSelfHostedOrigin()`）。

---

## 若你提供「Zone DNS Edit」Token

可代为执行 DNS 切换。Token 权限需包含：

- Zone → DNS → Edit  
- 资源：`tevarn.com`
